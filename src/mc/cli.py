"""mc — MC Lead Engine CLI."""

from __future__ import annotations

import time

import typer

from . import db

app = typer.Typer(add_completion=False, help="MC/DOT lead engine")


@app.command()
def init():
    """Bazani yaratadi."""
    db.init()
    typer.echo(f"OK: {db.DB_PATH}")


@app.command()
def login():
    """Burner FB akkaunt uchun brauzerni ochadi — qo'lda login qilasiz.

    Login qilgach brauzerni yopmang, terminalga Enter bosing.
    """
    from .collect.browser import browser, is_logged_in

    db.init()
    with browser(headless=False) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.facebook.com/", timeout=60000)
        typer.echo("Brauzerda burner akkaunt bilan login qiling, keyin shu yerda Enter bosing.")
        input()
        ok = is_logged_in(page)
    typer.echo("✅ Sessiya saqlandi" if ok else "❌ Login tasdiqlanmadi")


@app.command()
def collect(
    group: str = typer.Option(None, help="Guruh ID (default: config.yaml dagi hammasi)"),
    since_days: int = typer.Option(None, help="Necha kunlik tarix"),
    comments: bool = typer.Option(True, help="Postlarni ochib comment yig'ish"),
    dry_run: bool = typer.Option(False, help="Bazaga yozmaydi"),
):
    """FB guruhidan post va comment yig'adi."""
    from . import runs
    from .collect.browser import SessionDead, browser
    from .collect.feed import sweep_feed
    from .collect.post import fetch_comments, pending_posts
    from .notify import telegram

    db.init()
    cfg = db.load_config()
    groups = [g for g in cfg["groups"] if not group or g["id"] == group]
    days = since_days or cfg["collect"]["backfill_days"]
    since_ts = time.time() - days * 86400

    try:
        with runs.track("manual") as r, r.step("collect") as st:
            out = {}
            with browser(headless=cfg["collect"]["headless"]) as ctx:
                for g in groups:
                    typer.echo(f"→ {g['name']} ({days} kun)")
                    res = sweep_feed(ctx, g["id"], cfg, since_ts, dry_run=dry_run)
                    out[g["id"]] = res
                    typer.echo(f"  feed: {res}")

                if comments and not dry_run:
                    todo = pending_posts(cfg["collect"]["max_posts_per_run"])
                    typer.echo(f"→ {len(todo)} ta postdan comment yig'ilyapti")
                    total = 0
                    for i, p in enumerate(todo, 1):
                        try:
                            n = fetch_comments(ctx, p, cfg)
                            total += n
                            typer.echo(f"  [{i}/{len(todo)}] {p['post_id']}: +{n} comment")
                        except SessionDead:
                            raise
                        except Exception as e:
                            typer.echo(f"  [{i}/{len(todo)}] {p['post_id']}: xato {e}")
                    out["comments"] = total
                    typer.echo(f"  jami: +{total} comment")
            st["result"] = out
    except SessionDead as e:
        with db.connect() as conn:
            db.set_health(conn, "session", f"DEAD: {e}")
        telegram.alert(f"FB sessiya tushdi:\n{e}\n\n<code>uv run mc login</code> bilan tiklang.")
        raise typer.Exit(code=2)

    with db.connect() as conn:
        db.set_health(conn, "session", "OK")


@app.command()
def classify(limit: int = 200):
    """Yig'ilgan matnlarni BUY/SELL/NOISE ga ajratadi va faktlarni chiqaradi."""
    from . import runs
    from .classify.run import run

    db.init()
    with runs.track("manual") as r, r.step("classify") as st:
        st["result"] = run(limit=limit)
    typer.echo(st["result"])


@app.command()
def enrich(limit: int = 200):
    """MC/DOT raqamlarini FMCSA bo'yicha tekshiradi."""
    from . import runs
    from .enrich.fmcsa import enrich_pending

    db.init()
    with runs.track("manual") as r, r.step("enrich") as st:
        st["result"] = enrich_pending(limit=limit)
    typer.echo(st["result"])


@app.command()
def score():
    """Ballarni qayta hisoblaydi."""
    from . import runs
    from . import score as scoring

    db.init()
    with runs.track("manual") as r, r.step("score") as st:
        st["result"] = scoring.run()
    typer.echo(st["result"])


@app.command()
def match():
    """Buyer<->Seller juftliklarini topadi."""
    from . import match as matching
    from . import runs

    db.init()
    with runs.track("manual") as r, r.step("match") as st:
        st["result"] = matching.run()
    typer.echo(st["result"])


@app.command()
def notify(dry_run: bool = False):
    """Yuqori ballli yangi leadlarni Telegram'ga yuboradi."""
    from . import runs
    from .notify import telegram

    db.init()
    with runs.track("manual") as r, r.step("notify") as st:
        st["result"] = telegram.run(dry_run=dry_run)
    typer.echo(st["result"])


@app.command()
def reprocess():
    """Klassifikatsiyani noldan qayta yurgizadi (qayta scrape qilmasdan)."""
    db.init()
    with db.connect() as conn:
        # Tartib muhim: matches va notified leads ga bog'langan
        conn.execute("DELETE FROM matches")
        conn.execute("DELETE FROM notified")
        conn.execute("DELETE FROM leads")
        conn.execute("UPDATE classify_state SET stage = 'pending', side_guess = NULL, error = NULL")
    typer.echo("Navbat tozalandi — endi `mc classify` ni yurgizing.")


@app.command()
def stats():
    """Qisqacha holat."""
    db.init()
    with db.connect() as conn:
        q = lambda s: conn.execute(s).fetchone()[0]
        typer.echo(f"posts:    {q('SELECT COUNT(*) FROM posts')}")
        typer.echo(f"comments: {q('SELECT COUNT(*) FROM comments')}")
        typer.echo(f"people:   {q('SELECT COUNT(*) FROM people')}")
        n_sell = q("SELECT COUNT(*) FROM leads WHERE side = 'SELL'")
        n_buy = q("SELECT COUNT(*) FROM leads WHERE side = 'BUY'")
        typer.echo(f"leads:    {q('SELECT COUNT(*) FROM leads')} (SELL {n_sell}, BUY {n_buy})")
        typer.echo(f"matches:  {q('SELECT COUNT(*) FROM matches')}")
        for r in conn.execute("SELECT stage, COUNT(*) n FROM classify_state GROUP BY stage"):
            typer.echo(f"  classify/{r['stage']}: {r['n']}")
        for r in conn.execute(
            "SELECT model, prompt_tokens + completion_tokens t FROM llm_usage WHERE day = ?",
            (time.strftime("%Y-%m-%d"),)
        ):
            typer.echo(f"  bugungi token {r['model']}: {r['t']}")


def _pidfile(name: str):
    """Ishlayotgan jarayonning PID'ini yozadi, tugaganda o'chiradi."""
    import contextlib
    import os

    @contextlib.contextmanager
    def _cm():
        path = db.DATA / f"{name}.pid"
        db.DATA.mkdir(parents=True, exist_ok=True)
        path.write_text(str(os.getpid()))
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                path.unlink()

    return _cm()


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    """Dashboard'ni ishga tushiradi. To'xtatish: Ctrl+C yoki `mc stop`."""
    import uvicorn

    db.init()
    with _pidfile("web"):
        try:
            uvicorn.run("mc.web.app:app", host=host, port=port, log_level="info")
        except KeyboardInterrupt:
            pass
    typer.echo("\nDashboard to'xtadi.")


@app.command()
def loop(interval: int = None):
    """Doimiy rejim: collect -> classify -> enrich -> score -> match -> notify.

    To'xtatish: Ctrl+C, yoki boshqa terminaldan `uv run mc stop`.
    """
    from .collect.browser import SessionDead

    db.init()
    cfg = db.load_config()
    every = (interval or cfg["collect"]["poll_interval_minutes"]) * 60

    typer.echo(f"Ishga tushdi (har {every // 60} daqiqada). To'xtatish: Ctrl+C\n")

    # `mc stop` SIGTERM yuboradi -- uni ham Ctrl+C kabi tinch to'xtatish qilamiz,
    # aks holda brauzer yopilmay qolishi mumkin.
    import signal

    def _term(_sig, _frm):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _term)

    from . import runs

    with _pidfile("loop"):
        try:
            while True:
                started = time.time()
                with runs.track("loop") as r:
                    for name, fn in _pipeline():
                        typer.echo(f"[{time.strftime('%H:%M:%S')}] {name} …")
                        try:
                            with r.step(name) as s:
                                s["result"] = fn()
                            typer.echo(f"  ✓ {s['result']}")
                        except SessionDead as e:
                            typer.echo(f"  ✗ {e}")
                            with db.connect() as conn:
                                db.set_health(conn, "session", f"DEAD: {e}")
                            telegram_alert(str(e))
                            raise typer.Exit(code=2)
                        except Exception as e:
                            # Bitta bosqich tushsa qolganini to'xtatmaymiz
                            typer.echo(f"  ✗ xato: {type(e).__name__}: {e}")

                sleep = max(60, every - (time.time() - started))
                typer.echo(f"[{time.strftime('%H:%M:%S')}] {int(sleep)}s kutish\n")
                time.sleep(sleep)
        except KeyboardInterrupt:
            typer.echo("\nTo'xtatildi. Yig'ilgan hamma narsa saqlandi — "
                       "`uv run mc loop` bilan qoldigidan davom etadi.")


def telegram_alert(msg: str) -> None:
    try:
        from .notify import telegram

        telegram.alert(f"FB sessiya tushdi:\n{msg}\n\n<code>uv run mc login</code>")
    except Exception:
        pass


@app.command("runs")
def runs_(limit: int = 10):
    """Oxirgi yurishlar va ularning natijasi."""
    from . import runs as R

    db.init()
    st = R.status()
    typer.echo(f"Holat: {st['label']}"
               + (f" — {st['detail']}" if st.get("detail") else ""))
    if st.get("age") is not None:
        typer.echo(f"Oxirgi muvaffaqiyatli yangilanish: {int(st['age'] // 60)} daqiqa oldin\n")
    else:
        typer.echo("")

    for r in R.latest(limit):
        when = time.strftime("%m-%d %H:%M", time.localtime(r["started_at"]))
        dur = f"{r['duration']:.0f}s" if r["duration"] else "—"
        mark = {"ok": "✓", "partial": "◐", "failed": "✗",
                "running": "…", "stopped": "■"}.get(r["status"], "?")
        typer.echo(f"{mark} {when}  {r['status']:<8} {dur:>6}  {r['trigger']}")
        for s in r["steps"]:
            sm = {"ok": "✓", "warn": "!", "error": "✗", "running": "…"}.get(s["status"], "?")
            line = f"    {sm} {s['step']:<9}"
            if s["status"] in ("error", "warn"):
                line += f" {s['error']}"
            elif s["result"] and s["result"] != "null":
                line += f" {s['result'][:110]}"
            typer.echo(line)


@app.command()
def stop():
    """Ishlayotgan `mc loop` va `mc serve` ni to'xtatadi."""
    import os
    import signal

    stopped = []
    for name, label in [("loop", "dvigatel"), ("web", "dashboard")]:
        path = db.DATA / f"{name}.pid"
        if not path.exists():
            continue
        try:
            pid = int(path.read_text().strip())
        except ValueError:
            path.unlink(missing_ok=True)
            continue

        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            path.unlink(missing_ok=True)   # eskirgan pid fayli
            continue
        except PermissionError:
            typer.echo(f"{label} (pid {pid}) — ruxsat yo'q, qo'lda to'xtating")
            continue

        # tinch to'xtashini kutamiz, bo'lmasa majburlaymiz
        for _ in range(50):
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        else:
            with __import__("contextlib").suppress(OSError):
                os.kill(pid, signal.SIGKILL)

        path.unlink(missing_ok=True)
        stopped.append(f"{label} (pid {pid})")

    if stopped:
        typer.echo("To'xtatildi: " + ", ".join(stopped))
    else:
        typer.echo("Ishlayotgan jarayon topilmadi.")


def _pipeline():
    from . import match as matching
    from . import score as scoring
    from .classify.run import run as classify_run
    from .collect.browser import browser
    from .collect.feed import sweep_feed
    from .collect.post import fetch_comments, pending_posts
    from .enrich.fmcsa import enrich_pending
    from .notify import telegram

    cfg = db.load_config()

    def _collect():
        since_ts = time.time() - cfg["collect"]["backfill_days"] * 86400
        out = {}
        with browser(headless=cfg["collect"]["headless"]) as ctx:
            for g in cfg["groups"]:
                out[g["id"]] = sweep_feed(ctx, g["id"], cfg, since_ts)
            n = 0
            for p in pending_posts(cfg["collect"]["max_posts_per_run"]):
                try:
                    n += fetch_comments(ctx, p, cfg)
                except Exception:
                    continue
            out["comments"] = n
        return out

    return [
        ("collect", _collect),
        ("classify", lambda: classify_run(limit=200, verbose=False)),
        ("enrich", lambda: enrich_pending(limit=100, verbose=False)),
        ("score", scoring.run),
        ("match", matching.run),
        ("notify", telegram.run),
    ]


if __name__ == "__main__":
    app()
