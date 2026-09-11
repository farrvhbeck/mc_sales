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
def login(wait: int = typer.Option(300, help="Login uchun necha soniya kutilsin")):
    """FB brauzerini ochadi — siz qo'lda kirasiz, dastur o'zi sezadi.

    Terminaldan Enter kutmaydi: `c_user` cookie'si paydo bo'lishini kuzatib turadi,
    shuning uchun fonda ham ishlaydi.
    """
    from .collect.browser import browser

    db.init()
    deadline = time.time() + wait
    typer.echo("Brauzer ochilyapti… o'sha oynada Facebook'ga kiring.")

    # Bu yerda oyna ko'rinishi SHART -- foydalanuvchi qo'lda kiradi
    with browser(headless=False, window="visible") as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.facebook.com/", timeout=60000)

        uid = None
        while time.time() < deadline:
            names = {c["name"]: c["value"] for c in ctx.cookies()
                     if "facebook.com" in c.get("domain", "")}
            if names.get("c_user") and names.get("xs"):
                uid = names["c_user"]
                break
            time.sleep(2)

        if uid:
            # Sessiya haqiqatan ishlayotganini tekshiramiz
            try:
                page.goto("https://www.facebook.com/", wait_until="domcontentloaded",
                          timeout=45000)
                time.sleep(3)
                alive = "/login" not in page.url and "checkpoint" not in page.url
            except Exception:
                alive = False
        else:
            alive = False

    with db.connect() as conn:
        db.set_health(conn, "session", "OK" if alive else "LOGIN YO'Q")
        if uid:
            db.set_health(conn, "fb_user_id", uid)

    if alive:
        typer.echo(f"✅ Sessiya saqlandi (FB user id: {uid})")
    elif uid:
        typer.echo(f"⚠️  Cookie bor (id {uid}) lekin sahifa ochilmadi — checkpoint bo'lishi mumkin")
    else:
        typer.echo(f"❌ {wait}s ichida login aniqlanmadi. Qayta urinib ko'ring.")


@app.command()
def collect(
    group: str = typer.Option(None, help="Guruh ID (default: config.yaml dagi hammasi)"),
    since_days: int = typer.Option(None, help="Necha kunlik tarix"),
    comments: bool = typer.Option(None, help="Comment yig'ish (default: config.yaml)"),
    dry_run: bool = typer.Option(False, help="Bazaga yozmaydi"),
    full: bool = typer.Option(False, help="Chuqur skan — tanish postda to'xtamaydi"),
):
    """FB guruhidan post va comment yig'adi.

    Odatda inkremental: tanish postlarga yetganda to'xtaydi. Butun tarixni
    qaytadan olish uchun `--full`.
    """
    from . import groups as G
    from . import runs
    from .collect import discover
    from .collect.browser import SessionDead, browser
    from .collect.feed import sweep_feed
    from .collect.post import fetch_comments, pending_posts
    from .notify import telegram

    db.init()
    cfg = db.load_config()
    groups = [g for g in G.enabled() if not group or g["group_id"] == group]
    days = since_days or cfg["collect"]["backfill_days"]
    since_ts = time.time() - days * 86400
    if comments is None:
        comments = cfg["collect"].get("comments", False)

    try:
        with runs.track("manual") as r, r.step("collect") as st:
            out = {}
            with browser(headless=cfg["collect"]["headless"],
                         window=cfg["collect"].get("window", "auto")) as ctx:
                # Havola bo'yicha qo'shilgan, lekin id si hali aniqlanmagan guruhlar
                # Havola bo'yicha qo'shilgan guruhni aniqlash -- xato bo'lsa ham
                # qolgan yig'ish to'xtamasin
                for g in G.pending():
                    typer.echo(f"→ guruh aniqlanmoqda: {g['url']}")
                    try:
                        res = G.resolve(ctx, g)
                    except SessionDead:
                        raise
                    except Exception as e:
                        G.mark(g["group_id"], "error", f"{type(e).__name__}: {e}"[:200])
                        res = {"ok": False, "message": str(e)[:120]}
                    typer.echo(f"  {res}")
                    if res.get("ok"):
                        groups = [x for x in G.enabled() if not group or x["group_id"] == group]

                for gi, g in enumerate(groups, 1):
                    label = g["name"] or g["group_id"]
                    typer.echo(f"→ [{gi}/{len(groups)}] {label} ({days} kun)")
                    with db.connect() as conn:
                        db.set_health(conn, "collect_progress",
                                      f"guruh {gi}/{len(groups)}: {label}")
                    res = sweep_feed(ctx, g["group_id"], cfg, since_ts,
                                     dry_run=dry_run, full=full)
                    out[g["group_id"]] = res
                    if not dry_run:
                        G.record_run(g["group_id"], res)
                    typer.echo(f"  feed: {res}")

                if discover.is_on(cfg) and not dry_run:
                    typer.echo("→ keng qidiruv (guruhlardan tashqarida)")
                    with db.connect() as conn:
                        db.set_health(conn, "collect_progress", "keng qidiruv")
                    out["search"] = discover.run(ctx, cfg, since_ts)
                    for q, r in out["search"].items():
                        typer.echo(f"  {q}: {r}")

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
        db.set_health(conn, "collect_progress", "")


@app.command("ingest-raw")
def ingest_raw(group: str = typer.Option(None, help="Faqat shu guruh")):
    """Saqlangan xom JSON'ni qayta o'qib bazaga yozadi — FB'ga bormasdan.

    Ekstraktor tuzatilganda yoki yaxshilanganda ishlatiladi: hamma narsa
    `data/raw/` da turibdi, qayta scrape qilish shart emas.
    """
    import json

    from . import normalize, runs

    db.init()
    files = sorted(db.RAW_DIR.glob("*/feed-*.json"))
    if group:
        files = [f for f in files if f"-{group}-" in f.name]
    if not files:
        typer.echo("Xom fayl topilmadi.")
        raise typer.Exit(1)

    stats = {"files": len(files), "seen": 0, "new": 0, "skipped_short": 0}
    cfg = db.load_config()
    min_chars = cfg["collect"].get("min_text_chars", 25)

    with runs.track("manual") as r, r.step("collect") as st:
        for path in files:
            gid = path.name.split("-")[1]
            try:
                payloads = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as e:
                typer.echo(f"  {path.name}: o'qib bo'lmadi ({e})")
                continue

            found: dict[str, dict] = {}
            for payload in payloads:
                for p in normalize.extract_posts(payload, gid):
                    found.setdefault(p["post_id"], p)

            rel = f"{path.parent.name}/{path.name}"
            with db.connect() as conn:
                for p in found.values():
                    stats["seen"] += 1
                    if len((p.get("text") or "").strip()) < min_chars and not p.get("media"):
                        stats["skipped_short"] += 1
                        continue
                    if p["person_id"]:
                        db.upsert_person(conn, p["person_id"], p.pop("_author_name", None),
                                         p.pop("_author_url", None))
                    p.pop("_author_name", None)
                    p.pop("_author_url", None)
                    p["raw_path"] = rel
                    if db.upsert_post(conn, p, min_text_chars=min_chars):
                        stats["new"] += 1
            typer.echo(f"  {path.name}: {len(found)} post")
        st["result"] = stats
    typer.echo(stats)


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
def ocr(limit: int = typer.Option(None, help="Nechta rasm o'qilsin"),
        redo: bool = typer.Option(False, help="O'qilganlarini ham qaytadan")):
    """Rasmli postlarning rasmini o'qiydi (OCR).

    Ishonchli o'qilgani odatdagi ro'yxatga tushadi, qolgani `/images` da
    qo'lda ko'riladi.
    """
    from . import runs
    from .ocr.run import run

    db.init()
    with runs.track("manual") as r, r.step("ocr") as st:
        st["result"] = run(limit=limit, redo=redo)
    typer.echo(st["result"])


@app.command()
def enrich(limit: int = 200,
           recheck_only: bool = typer.Option(False, help="Faqat eskirgan leadlarni qayta so'rash")):
    """MC/DOT raqamlarini FMCSA bo'yicha tekshiradi.

    Yangi leadlar tekshiriladi va eskirganlari (2 haftadan oshgan sotuv
    e'lonlari) qaytadan so'raladi -- authority o'lgan yoki sotilgan bo'lishi
    mumkin, buni bilmasdan xaridorga taklif qilish eng qimmat xato.
    """
    from . import runs
    from .audit import run as audit_run
    from .enrich.fmcsa import enrich_pending, recheck, recheck

    db.init()
    with runs.track("manual") as r, r.step("enrich") as st:
        out = {} if recheck_only else enrich_pending(limit=limit)
        st["result"] = out | {"recheck": recheck()}
    typer.echo(st["result"])


@app.command()
def audit(sample: int = typer.Option(None, help="Nechta post tekshirilsin"),
          force: bool = typer.Option(True, help="Vaqti kelmagan bo'lsa ham")):
    """T0 regex filtri nechta haqiqiy leadni tashlab yuborayotganini o'lchaydi.

    Tashlanganlardan namuna olib LLM'ga yuboradi. Topilgan leadlar navbatga
    qaytariladi, ya'ni audit o'zini oqlaydi.
    """
    from . import audit as A

    db.init()
    typer.echo(A.run(sample=sample, force=force))


@app.command("groups")
def groups_(add: str = typer.Option(None, help="Havola yoki guruh id qo'shish"),
            remove: str = typer.Option(None, help="Guruh id ni ro'yxatdan chiqarish"),
            on: str = typer.Option(None, help="Guruhni yoqish"),
            off: str = typer.Option(None, help="Guruhni vaqtincha to'xtatish")):
    """Kuzatilayotgan guruhlar ro'yxati (dashboard: /groups)."""
    from . import groups as G

    db.init()
    if add:
        typer.echo(G.add(add)["message"])
    if remove:
        G.remove(remove)
        typer.echo(f"{remove} olib tashlandi")
    if on:
        G.set_enabled(on, True)
    if off:
        G.set_enabled(off, False)

    st = G.stats()
    for g in G.all_groups():
        s = st.get(g["group_id"], {})
        mark = "●" if g["enabled"] else "○"
        status = "" if g["status"] == "ok" else f" [{g['status']}]"
        typer.echo(f" {mark} {(g['name'] or g['group_id'])[:44]:<44} "
                   f"{s.get('posts', 0):>5} post  {s.get('leads', 0):>4} lead{status}")
        if g["status"] == "no_access" and g["note"]:
            typer.echo(f"     {g['note']}")

    from .collect import discover
    cfg = db.load_config()
    typer.echo(f"\n Keng qidiruv: {'yoqilgan' if discover.is_on(cfg) else 'o‘chiq'}"
               f" ({len(discover.queries(cfg))} so'rov)")


@app.command()
def score():
    """Ballarni qayta hisoblaydi."""
    from . import runs
    from . import score as scoring

    db.init()
    with runs.track("manual") as r, r.step("score") as st:
        st["result"] = scoring.run() | scoring.dedupe()
    typer.echo(st["result"])


@app.command()
def fraud():
    """Firibgarlik signallari: bitta telefon ortidagi ko'p MC va aksincha."""
    from . import fraud as fr
    from . import runs

    db.init()
    with runs.track("manual") as r, r.step("fraud") as st:
        st["result"] = fr.run()
    typer.echo(st["result"])


@app.command("reindex")
def reindex():
    """Qidiruv indeksini qayta quradi."""
    from . import search

    db.init()
    typer.echo(search.reindex())


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
        # Matnsiz rasmli postlar OCR'siz LLM'ga bormasin
        conn.execute("""
            UPDATE classify_state SET stage = 'awaiting_ocr'
            WHERE source_type = 'post' AND source_id IN (
                SELECT post_id FROM posts
                WHERE COALESCE(n_media, 0) > 0
                  AND LENGTH(TRIM(COALESCE(text, ''))) < 25
                  AND COALESCE(ocr_text, '') = '')""")
        conn.execute("""
            UPDATE classify_state SET stage = 'media_review'
            WHERE source_type = 'post' AND source_id IN (
                SELECT post_id FROM posts WHERE media_state = 'review')""")
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
            "SELECT key_label, model, prompt_tokens + completion_tokens t "
            "FROM llm_usage WHERE day = ? ORDER BY key_label, model",
            (time.strftime("%Y-%m-%d"),)
        ):
            typer.echo(f"  bugungi token [{r['key_label']}] {r['model']}: {r['t']}")

    from .classify.groq_client import GroqClient
    try:
        for b in GroqClient(db.load_config()).budget_report():
            typer.echo(f"  budjet {b['key']}: {b['remaining']:,} qoldi "
                       f"({b['spent']:,} sarflandi)")
    except Exception as e:
        typer.echo(f"  budjet: {e}")


@app.command()
def doctor():
    """Muhit tayyormi? Har bir shart va nima qilish kerakligi.

    Yangi kompyuterga o'rnatganda birinchi shu buyruq yuritiladi.
    """
    import platform

    db.init()
    cfg = db.load_config()
    ok = True

    def line(good: bool, label: str, detail: str = "", required: bool = True) -> None:
        nonlocal ok
        if required:
            ok = ok and good
        mark = "✅" if good else ("❌" if required else "ℹ️ ")
        typer.echo(f"  {mark} {label}" + (f" — {detail}" if detail else ""))

    typer.echo(f"\nTizim: {platform.system()} {platform.release()}, Python {platform.python_version()}\n")

    # OCR
    from .ocr import engine
    line(engine.available(), "OCR (rapidocr)",
         "" if engine.available() else "`uv pip install -e .` bilan o'rnating")

    # Brauzer oynasi
    from .collect import display
    mode = cfg["collect"].get("window", "auto")
    hidden_ok = mode == "visible" or platform.system() != "Linux" or display.xvfb_available()
    line(hidden_ok, f"Brauzer oynasi (window: {mode})", display.describe())

    # Playwright brauzeri. Drayverni ishga tushirmaymiz -- kesh katalogiga qaraymiz,
    # aks holda tekshiruv uchun butun brauzer stek ko'tariladi.
    import os
    from pathlib import Path as _P

    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
             _P.home() / ".cache" / "ms-playwright",
             _P.home() / "Library" / "Caches" / "ms-playwright",
             _P.home() / "AppData" / "Local" / "ms-playwright"]
    found = next((r for r in roots if r and _P(r).exists()
                  and any(_P(r).glob("chromium*"))), None)
    line(bool(found), "Playwright Chromium",
         str(found) if found else "`uv run playwright install chromium`")

    # FB sessiya
    with db.connect() as conn:
        h = db.get_health(conn)
    sess = (h.get("session") or {}).get("value", "")
    line(sess == "OK", "FB sessiya", sess or "`uv run mc login`")

    # Groq kalitlari
    from .classify.groq_client import _load_keys
    keys = _load_keys()
    line(bool(keys), f"Groq kalitlari: {len(keys)} ta",
         "" if keys else ".env da GROQ_API_KEY yo'q")

    # Telegram -- ixtiyoriy
    db.load_env()
    tg = bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))
    line(tg, "Telegram xabarnoma",
         "" if tg else "ixtiyoriy — .env da token/chat id yo'q", required=False)

    # Baza
    with db.connect() as conn:
        n_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        n_media = conn.execute(
            "SELECT COUNT(*) FROM post_media WHERE local_path IS NOT NULL").fetchone()[0]
        n_index = conn.execute("SELECT COUNT(*) FROM search_index").fetchone()[0]
    typer.echo(f"\n  Baza: {n_posts} post, {n_media} rasm diskda, {n_index} lead indeksda")

    typer.echo("\n" + ("Hammasi tayyor." if ok else
                       "Yuqoridagi ❌ larni to'g'rilang, keyin `uv run mc start`."))


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


def _alive(name: str) -> int | None:
    """Pidfile'dagi jarayon tirikmi? Tirik bo'lsa pid qaytaradi."""
    import os

    path = db.DATA / f"{name}.pid"
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError):
        path.unlink(missing_ok=True)
        return None
    except PermissionError:
        return pid


@app.command()
def start(
    port: int = typer.Option(8000, help="Dashboard porti"),
    web: bool = typer.Option(True, help="Dashboard ham ishga tushsinmi"),
    engine: bool = typer.Option(True, help="Dvigatel (loop) ham ishga tushsinmi"),
):
    """Hammasini fonda ishga tushiradi: dvigatel + dashboard.

    Terminalni yopsangiz ham ishlashda davom etadi. To'xtatish: `mc stop`.
    """
    import subprocess
    import sys

    db.init()
    db.DATA.mkdir(parents=True, exist_ok=True)

    plan = []
    if engine:
        plan.append(("loop", ["loop"], "engine"))
    if web:
        plan.append(("web", ["serve", "--port", str(port)], "dashboard"))

    started, running = [], []
    for name, args, label in plan:
        pid = _alive(name)
        if pid:
            running.append(f"{label} (pid {pid})")
            continue
        log = db.DATA / f"{name}.log"
        with open(log, "a") as fh:
            subprocess.Popen(
                [sys.executable, "-m", "mc.cli", *args],
                stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True,   # terminal yopilsa ham o'lmasin
                cwd=str(db.ROOT),
            )
        started.append((name, label, log))

    # Bola pidfile yozguncha kutamiz
    for _ in range(30):
        if all(_alive(n) for n, _, _ in started):
            break
        time.sleep(0.2)

    for name, label, log in started:
        pid = _alive(name)
        if pid:
            typer.echo(f"✅ {label} ishga tushdi (pid {pid}) — log: {log}")
        else:
            typer.echo(f"❌ {label} ishga tushmadi. Sababi: {log}")
    for r in running:
        typer.echo(f"ℹ️  {r} allaqachon ishlayapti")

    if web and _alive("web"):
        typer.echo(f"\n   Dashboard:  http://127.0.0.1:{port}")
    typer.echo("   To'xtatish: uv run mc stop")


@app.command()
def share(port: int = typer.Option(8000, help="Dashboard porti"),
          wait: int = typer.Option(90, help="Havola uchun necha soniya kutilsin"),
          keep_alive: bool = typer.Option(True, help="Tunnel uzilsa o'zi qayta ochsin")):
    """Dashboardni internetga chiqaradi va havolani beradi.

    Cloudflare tunnel ishlatiladi -- akkaunt kerak emas, router sozlash kerak emas.
    Parol majburiy: himoyasiz dashboard internetga chiqarilmaydi.
    """
    import os
    import re
    import subprocess
    import sys

    from .web import auth

    db.init()

    # 1) Parolsiz chiqarmaymiz
    if not auth.password():
        pw = auth.new_password()
        env = db.ROOT / ".env"
        text = env.read_text() if env.exists() else ""
        if "DASHBOARD_PASSWORD" not in text:
            env.write_text(text.rstrip("\n") + f"\n\n# Dashboard'ga kirish paroli\nDASHBOARD_PASSWORD={pw}\n")
        typer.echo(f"Parol yaratildi va .env ga yozildi:\n\n    {pw}\n")
        os.environ["DASHBOARD_PASSWORD"] = pw
    else:
        typer.echo(f"Mavjud parol ishlatiladi (.env dagi DASHBOARD_PASSWORD).\n")

    # 2) Dashboard ishlayotganini ta'minlaymiz
    if not _alive("web"):
        typer.echo("Dashboard ishga tushirilyapti…")
        log = db.DATA / "web.log"
        with open(log, "a") as fh:
            subprocess.Popen(
                [sys.executable, "-m", "mc.cli", "serve", "--port", str(port)],
                stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True, cwd=str(db.ROOT),
            )
        for _ in range(40):
            time.sleep(0.25)
            if _alive("web"):
                break

    # 3) Tunnel
    if not (db.ROOT / "bin" / "cloudflared").exists():
        typer.echo("bin/cloudflared topilmadi. Yuklab oling:\n"
                   "  curl -Lo bin/cloudflared https://github.com/cloudflare/cloudflared/"
                   "releases/latest/download/cloudflared-linux-amd64 && chmod +x bin/cloudflared")
        raise typer.Exit(1)

    typer.echo("Tunnel ochilyapti…")
    url = _start_tunnel(port, wait)
    if not url:
        typer.echo(f"Havola {wait}s ichida chiqmadi. Log: {db.DATA / 'tunnel.log'}")
        raise typer.Exit(1)

    # 4) Kuzatuvchi: bepul tunnel ogohlantirishsiz uziladi, uni tiklab turamiz
    if keep_alive and not _alive("tunwatch"):
        with open(db.DATA / "tunwatch.log", "a") as fh:
            subprocess.Popen(
                [sys.executable, "-m", "mc.cli", "tunnel-watch", "--port", str(port)],
                stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True, cwd=str(db.ROOT),
            )
        for _ in range(20):
            time.sleep(0.25)
            if _alive("tunwatch"):
                break

    typer.echo("\n" + "─" * 56)
    typer.echo(f"  Havola:  {url}")
    typer.echo(f"  Parol:   {os.environ['DASHBOARD_PASSWORD']}")
    typer.echo("─" * 56)
    if _alive("tunwatch"):
        typer.echo("\n  Kuzatuvchi yoqilgan — tunnel uzilsa o'zi qayta ochadi.")
        typer.echo("  Havola o'zgarsa `uv run mc link` ko'rsatadi.")
    typer.echo("\nTunnel fonda ishlayapti. To'xtatish: uv run mc stop")
    typer.echo("Diqqat: bu havola shu kompyuter yoniq turgandagina ishlaydi.")


def _start_tunnel(port: int, wait: int = 90, attempts: int = 3) -> str | None:
    """cloudflared'ni ishga tushirib, ISHLAYOTGAN havolani qaytaradi.

    Havolani logdan olish yetarli emas: Cloudflare chekkasi hostname'ni tunnel
    bilan bog'laguncha bir necha soniya ketadi, va ba'zan umuman bog'lamaydi
    (HTTP 530). Shuning uchun qaytarishdan oldin havolani sinab ko'ramiz va
    ishlamasa yangi tunnel bilan qaytadan urinamiz.
    """
    import contextlib
    import os
    import re
    import signal
    import subprocess

    binary = db.ROOT / "bin" / "cloudflared"
    if not binary.exists():
        return None

    log = db.DATA / "tunnel.log"
    pidfile = db.DATA / "tunnel.pid"

    def _kill_existing():
        if not pidfile.exists():
            return
        with contextlib.suppress(Exception):
            pid = int(pidfile.read_text())
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        pidfile.unlink(missing_ok=True)
        time.sleep(3)

    for attempt in range(1, attempts + 1):
        _kill_existing()
        log.write_text("")
        with open(log, "a") as fh:
            proc = subprocess.Popen(
                [str(binary), "tunnel", "--no-autoupdate",
                 "--url", f"http://127.0.0.1:{port}"],
                stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True, cwd=str(db.ROOT),
            )
        pidfile.write_text(str(proc.pid))

        url = None
        deadline = time.time() + wait
        while time.time() < deadline and url is None:
            time.sleep(1)
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", log.read_text())
            if m:
                url = m.group(0)

        # Havola chiqdi -- endi u haqiqatan javob berishini kutamiz
        if url:
            ready = time.time() + 75
            while time.time() < ready:
                if _tunnel_ok(url):
                    with db.connect() as conn:
                        db.set_health(conn, "share_url", url)
                    return url
                time.sleep(3)
            typer.echo(f"  urinish {attempt}: {url} javob bermadi, yangisi ochilyapti")

    return None


def _tunnel_ok(url: str) -> bool:
    import httpx

    try:
        r = httpx.get(f"{url}/login", timeout=20, follow_redirects=False)
        return r.status_code < 500
    except Exception:
        return False


@app.command("tunnel-watch")
def tunnel_watch(port: int = 8000, every: int = 60):
    """Tunnel tirikligini kuzatadi va o'lsa qayta ochadi.

    Cloudflare'ning bepul tunneli ogohlantirishsiz uzilib qoladi. Bu jarayon
    har daqiqada havolani tekshiradi; javob bermasa yangisini ochadi va
    Telegram sozlangan bo'lsa yangi havolani yuboradi.
    """
    import os
    import signal

    from .notify import telegram

    db.init()

    def _term(_s, _f):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _term)

    with _pidfile("tunwatch"):
        try:
            while True:
                with db.connect() as conn:
                    row = conn.execute(
                        "SELECT value FROM health WHERE key = 'share_url'").fetchone()
                url = row["value"] if row else None

                if not url or not _tunnel_ok(url):
                    typer.echo(f"[{time.strftime('%H:%M:%S')}] tunnel javob bermadi — qayta ochilyapti")
                    old = db.DATA / "tunnel.pid"
                    if old.exists():
                        with __import__("contextlib").suppress(Exception):
                            os.killpg(os.getpgid(int(old.read_text())), signal.SIGTERM)
                    new = _start_tunnel(port)
                    if new:
                        typer.echo(f"[{time.strftime('%H:%M:%S')}] yangi havola: {new}")
                        if new != url:
                            telegram.alert(
                                f"Dashboard havolasi o'zgardi:\n{new}\n\n"
                                f"Eskisi ishlamay qoldi.")
                    else:
                        typer.echo("qayta ochib bo'lmadi — keyingi urinish")
                time.sleep(every)
        except KeyboardInterrupt:
            typer.echo("\nKuzatuv to'xtatildi.")


@app.command()
def link():
    """Hozirgi havolani ko'rsatadi va ishlayotganini tekshiradi."""
    from .web import auth

    db.init()
    with db.connect() as conn:
        row = conn.execute("SELECT value FROM health WHERE key = 'share_url'").fetchone()
    url = row["value"] if row else None
    if not url:
        typer.echo("Havola yo'q. `uv run mc share` bilan oching.")
        raise typer.Exit(1)

    ok = _tunnel_ok(url)
    typer.echo(f"  {url}   {'✅ ishlayapti' if ok else '❌ javob bermayapti'}")
    if auth.password():
        typer.echo(f"  Parol: {auth.password()}")
    if not ok:
        typer.echo("\n`uv run mc share` bilan yangisini oching.")
        raise typer.Exit(1)


@app.command()
def stop():
    """Ishlayotgan `mc loop`, `mc serve` va tunnel'ni to'xtatadi."""
    import os
    import signal

    stopped = []
    for name, label in [("loop", "engine"), ("web", "dashboard"),
                        ("tunwatch", "tunnel watchdog"), ("tunnel", "tunnel")]:
        path = db.DATA / f"{name}.pid"
        if not path.exists():
            continue
        try:
            pid = int(path.read_text().strip())
        except ValueError:
            path.unlink(missing_ok=True)
            continue

        # `mc start` jarayonni alohida sessiyada ochadi, ya'ni u guruh yetakchisi.
        # Butun guruhga signal yuboramiz -- aks holda Playwright ochgan Chromium
        # yetim bo'lib qoladi.
        def _signal(sig):
            try:
                os.killpg(os.getpgid(pid), sig)
            except (ProcessLookupError, PermissionError, OSError):
                os.kill(pid, sig)

        try:
            _signal(signal.SIGTERM)
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
            with __import__("contextlib").suppress(OSError, ProcessLookupError):
                _signal(signal.SIGKILL)

        path.unlink(missing_ok=True)
        stopped.append(f"{label} (pid {pid})")

    if stopped:
        typer.echo("To'xtatildi: " + ", ".join(stopped))
    else:
        typer.echo("Ishlayotgan jarayon topilmadi.")


def _pipeline():
    from . import groups as G
    from . import match as matching
    from . import score as scoring
    from . import search
    from .classify.run import run as classify_run
    from .collect import discover
    from .fraud import run as fraud_run
    from .ocr.run import run as ocr_run
    from .collect.browser import browser
    from .collect.feed import sweep_feed
    from .collect.post import fetch_comments, pending_posts
    from .audit import run as audit_run
    from .enrich.fmcsa import enrich_pending, recheck
    from .notify import telegram

    cfg = db.load_config()

    def _collect():
        since_ts = time.time() - cfg["collect"]["backfill_days"] * 86400
        out = {}
        with browser(headless=cfg["collect"]["headless"],
                     window=cfg["collect"].get("window", "auto")) as ctx:
            for g in G.pending():
                try:
                    out.setdefault("resolved", []).append(G.resolve(ctx, g))
                except Exception as e:
                    G.mark(g["group_id"], "error", f"{type(e).__name__}: {e}"[:200])
            active = G.enabled()
            for gi, g in enumerate(active, 1):
                with db.connect() as conn:
                    db.set_health(conn, "collect_progress",
                                  f"guruh {gi}/{len(active)}: {g['name'] or g['group_id']}")
                res = sweep_feed(ctx, g["group_id"], cfg, since_ts)
                out[g["group_id"]] = res
                G.record_run(g["group_id"], res)

            if discover.is_on(cfg):
                with db.connect() as conn:
                    db.set_health(conn, "collect_progress", "keng qidiruv")
                out["search"] = discover.run(ctx, cfg, since_ts)
        with db.connect() as conn:
            db.set_health(conn, "collect_progress", "")
            if cfg["collect"].get("comments", False):
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
        ("ocr", lambda: ocr_run(verbose=False)),
        ("classify", lambda: classify_run(limit=200, verbose=False)
                              | {"audit": audit_run(verbose=False)}),
        ("enrich", lambda: enrich_pending(limit=100, verbose=False)
                           | {"recheck": recheck(verbose=False)}),
        # Firibgarlik ballga ta'sir qiladi, shuning uchun score'dan oldin
        ("fraud", fraud_run),
        ("score", lambda: scoring.run() | scoring.dedupe() | search.reindex()),
        ("match", matching.run),
        ("notify", telegram.run),
    ]


if __name__ == "__main__":
    app()
