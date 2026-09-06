"""Har bir yurishni (run) va uning bosqichlarini yozib boradi.

Maqsad: "oxirgi marta qachon yangilandi va muvaffaqiyatli bo'ldimi?" degan savolga
aniq javob bo'lishi. Tizim jim o'lib qolsa, buni ko'rish kerak.
"""

from __future__ import annotations

import json
import time
import traceback
from contextlib import contextmanager

from . import db

# To'liq siklning bosqichlari, tartibi bilan. Progress shu ro'yxatdan hisoblanadi.
STEP_ORDER = ["collect", "classify", "enrich", "score", "match", "notify"]
STEP_TOTAL = len(STEP_ORDER)

STEP_LABELS = {
    "collect": "Yig'ish",
    "classify": "Tahlil",
    "enrich": "FMCSA",
    "score": "Ball",
    "match": "Match",
    "notify": "Telegram",
}


class Run:
    def __init__(self, run_id: int) -> None:
        self.run_id = run_id
        self.failed = 0
        self.ok = 0

    @contextmanager
    def step(self, name: str):
        started = time.time()
        with db.connect() as conn:
            cur = conn.execute(
                """INSERT INTO run_steps (run_id, step, started_at, status)
                   VALUES (?, ?, ?, 'running')""",
                (self.run_id, name, started),
            )
            step_id = cur.lastrowid

        box: dict = {}
        try:
            yield box
        except Exception as e:
            self.failed += 1
            with db.connect() as conn:
                conn.execute(
                    """UPDATE run_steps SET finished_at = ?, status = 'error',
                                            error = ? WHERE step_id = ?""",
                    (time.time(), f"{type(e).__name__}: {e}"[:500], step_id),
                )
                conn.execute(
                    "UPDATE runs SET last_error = ? WHERE run_id = ?",
                    (f"{name}: {type(e).__name__}: {e}"[:500], self.run_id),
                )
            raise
        else:
            # Ba'zi bosqichlar xatoni ichida yutadi va `errors: N` qaytaradi
            # (masalan classify — bitta batch tushsa ham qolganini davom ettiradi).
            # Bunday holat "muvaffaqiyatli" deb ko'rsatilmasligi kerak.
            result = box.get("result")
            n_err = result.get("errors", 0) if isinstance(result, dict) else 0
            status, err = ("ok", None)
            if n_err:
                self.failed += 1
                status = "warn"
                err = f"{n_err} ta element qayta ishlanmadi"
            else:
                self.ok += 1
            with db.connect() as conn:
                conn.execute(
                    """UPDATE run_steps SET finished_at = ?, status = ?,
                                            result = ?, error = ? WHERE step_id = ?""",
                    (time.time(), status,
                     json.dumps(result, ensure_ascii=False, default=str), err, step_id),
                )
                if err:
                    conn.execute("UPDATE runs SET last_error = ? WHERE run_id = ?",
                                 (f"{name}: {err}", self.run_id))

    def note(self, key: str, value) -> None:
        with db.connect() as conn:
            row = conn.execute("SELECT summary FROM runs WHERE run_id = ?",
                               (self.run_id,)).fetchone()
            summary = json.loads(row["summary"]) if row and row["summary"] else {}
            summary[key] = value
            conn.execute("UPDATE runs SET summary = ? WHERE run_id = ?",
                         (json.dumps(summary, ensure_ascii=False, default=str), self.run_id))


@contextmanager
def track(trigger: str = "loop"):
    """Bitta to'liq yurish. Bosqichlar `with r.step("collect") as s:` bilan."""
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (trigger, started_at, status) VALUES (?, ?, 'running')",
            (trigger, time.time()),
        )
        run = Run(cur.lastrowid)

    status = "ok"
    err = None
    try:
        yield run
    except KeyboardInterrupt:
        status = "stopped"
        raise
    except Exception as e:
        status = "failed"
        err = f"{type(e).__name__}: {e}"[:500]
        traceback.print_exc()
        raise
    finally:
        if status == "ok" and run.failed:
            status = "partial"
        with db.connect() as conn:
            conn.execute(
                """UPDATE runs SET finished_at = ?, status = ?,
                                   last_error = COALESCE(?, last_error)
                   WHERE run_id = ?""",
                (time.time(), status, err, run.run_id),
            )


# --- o'qish ---------------------------------------------------------------


def latest(limit: int = 20) -> list[dict]:
    with db.connect() as conn:
        runs = [dict(r) for r in conn.execute(
            "SELECT * FROM runs ORDER BY run_id DESC LIMIT ?", (limit,)
        )]
        if not runs:
            return []
        ids = tuple(r["run_id"] for r in runs)
        marks = ",".join("?" * len(ids))
        steps: dict[int, list] = {}
        for s in conn.execute(
            f"SELECT * FROM run_steps WHERE run_id IN ({marks}) ORDER BY step_id", ids
        ):
            steps.setdefault(s["run_id"], []).append(dict(s))
    for r in runs:
        r["steps"] = steps.get(r["run_id"], [])
        r["summary"] = json.loads(r["summary"]) if r["summary"] else {}
        r["duration"] = (r["finished_at"] - r["started_at"]) if r["finished_at"] else None
    return runs


def status() -> dict:
    """Sarlavhadagi chiziq uchun umumiy holat."""
    cfg = db.load_config()
    interval = cfg["collect"]["poll_interval_minutes"] * 60

    with db.connect() as conn:
        last = conn.execute(
            "SELECT * FROM runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        last_ok = conn.execute(
            "SELECT * FROM runs WHERE status IN ('ok','partial') ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        session = conn.execute(
            "SELECT value FROM health WHERE key = 'session'"
        ).fetchone()

    if not last:
        return {"state": "never", "label": "Hali ishga tushmagan",
                "detail": "`uv run mc loop` ni ishga tushiring"}

    last = dict(last)
    last_ok = dict(last_ok) if last_ok else None
    age = time.time() - (last_ok["finished_at"] or last_ok["started_at"]) if last_ok else None
    sess = (session["value"] if session else "") or ""

    if sess.startswith("DEAD"):
        return {"state": "dead", "label": "Facebook sessiya tushdi",
                "detail": "`uv run mc login` bilan qayta kiring",
                "age": age, "last": last}

    if last["status"] == "running":
        cur = _progress(last["run_id"])
        return {"state": "running", "label": "Hozir ishlayapti",
                "detail": cur["detail"], "step": cur["index"],
                "step_total": STEP_TOTAL, "step_label": cur["label"],
                "age": age, "last": last}

    if last["status"] == "failed":
        return {"state": "failed", "label": "Oxirgi yurish muvaffaqiyatsiz",
                "detail": last["last_error"] or "", "age": age, "last": last}

    # Eskirganmi? 3 sikl o'tsa muammo bor
    if age is not None and age > interval * 3:
        return {"state": "stale", "label": "Yangilanmayapti",
                "detail": "Dvigatel to'xtagan bo'lishi mumkin — `uv run mc loop`",
                "age": age, "last": last}

    if last["status"] == "partial":
        return {"state": "partial", "label": "Qisman bajarildi",
                "detail": last["last_error"] or "", "age": age, "last": last}

    if last["status"] == "stopped":
        return {"state": "stopped", "label": "Qo'lda to'xtatilgan",
                "detail": "`uv run mc loop` bilan davom ettiring", "age": age, "last": last}

    return {"state": "ok", "label": "Ishlayapti", "detail": "",
            "age": age, "last": last}


def _progress(run_id: int) -> dict:
    """Hozir qaysi bosqich, nechanchisi, va ichida nima bo'layapti."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT step FROM run_steps WHERE run_id = ? AND status = 'running' "
            "ORDER BY step_id DESC LIMIT 1", (run_id,)
        ).fetchone()
        done = conn.execute(
            "SELECT COUNT(*) n FROM run_steps WHERE run_id = ? AND status != 'running'",
            (run_id,)
        ).fetchone()["n"]
        sub = conn.execute(
            "SELECT value FROM health WHERE key = 'collect_progress'"
        ).fetchone()

    if not row:
        return {"index": done, "label": "", "detail": ""}

    step = row["step"]
    index = (STEP_ORDER.index(step) + 1) if step in STEP_ORDER else done + 1
    label = STEP_LABELS.get(step, step)
    detail = f"{index}/{STEP_TOTAL} · {label}"
    # Yig'ish uzoq davom etadi -- qaysi guruhda ekanini ham ko'rsatamiz
    if step == "collect" and sub and sub["value"]:
        detail += f" — {sub['value']}"
    return {"index": index, "label": label, "detail": detail}
