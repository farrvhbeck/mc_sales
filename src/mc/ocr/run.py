"""Rasmlarni o'qish va "topga chiqadimi yoki qo'lda ko'riladimi" hukmi.

Qoida sodda: OCR ishonchli o'qigan va o'qigani sotuv/xarid postiga o'xshasa --
post odatdagi LLM kaskadiga tushadi va ballga qarab ro'yxat tepasiga chiqadi.
Aks holda post `/images` sahifasining "Needs review" tabida qoladi: rasm
odamning o'ziga ko'rsatiladi, u matnni tuzatib bir bosishda navbatga qaytaradi.
"""

from __future__ import annotations

import time

from .. import db
from ..classify import prefilter
from . import engine


def _verdict(text: str, conf: float, cfg: dict) -> bool:
    """Matn LLM'ga yuborishga yaraydimi?"""
    o = cfg.get("ocr") or {}
    if conf < o.get("min_conf", 0.72):
        return False
    if len((text or "").strip()) < o.get("min_chars", 25):
        return False
    return prefilter.keep(text)


def run(limit: int | None = None, redo: bool = False, verbose: bool = True) -> dict:
    cfg = db.load_config()
    o = cfg.get("ocr") or {}
    limit = limit or o.get("max_per_run", 60)
    stats = {"read": 0, "ok": 0, "review": 0, "errors": 0, "skipped": 0}

    where = "local_path IS NOT NULL" + ("" if redo else " AND ocr_at IS NULL")
    with db.connect() as conn:
        todo = [dict(r) for r in conn.execute(
            f"SELECT media_id, post_id, local_path FROM post_media WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ?", (limit,))]

    if not todo:
        return stats

    if not engine.available():
        stats["skipped"] = len(todo)
        with db.connect() as conn:
            db.set_health(conn, "ocr", "rapidocr o'rnatilmagan — `uv pip install -e .`")
        return stats

    touched: set[str] = set()
    for i, m in enumerate(todo, 1):
        path = db.MEDIA_DIR / m["local_path"]
        try:
            res = engine.read(path)
            text = engine.digits_fix(res["text"])
            with db.connect() as conn:
                conn.execute(
                    """UPDATE post_media SET ocr_text = ?, ocr_conf = ?, ocr_engine = ?,
                                             ocr_at = ?, ocr_error = NULL
                       WHERE media_id = ?""",
                    (text, res["conf"], res["engine"], time.time(), m["media_id"]),
                )
            stats["read"] += 1
        except Exception as e:
            with db.connect() as conn:
                conn.execute(
                    "UPDATE post_media SET ocr_at = ?, ocr_error = ? WHERE media_id = ?",
                    (time.time(), f"{type(e).__name__}: {e}"[:200], m["media_id"]),
                )
            stats["errors"] += 1
        touched.add(m["post_id"])
        if verbose and i % 5 == 0:
            print(f"  OCR {i}/{len(todo)}")

    for post_id in touched:
        verdict = apply_post(post_id, cfg)
        stats[verdict] = stats.get(verdict, 0) + 1

    with db.connect() as conn:
        db.set_health(conn, "ocr_last_run", time.strftime("%Y-%m-%d %H:%M:%S"))
        db.set_health(conn, "ocr_last_result", str(stats))
    return stats


def apply_post(post_id: str, cfg: dict | None = None) -> str:
    """Postning barcha rasm matnini birlashtirib hukm chiqaradi.

    `ok`     -- klassifikatsiya navbatiga qo'shiladi
    `review` -- `/images` da qo'lda ko'riladi
    """
    cfg = cfg or db.load_config()
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT ocr_text, ocr_conf FROM post_media "
            "WHERE post_id = ? AND ocr_text IS NOT NULL ORDER BY created_at",
            (post_id,),
        ).fetchall()
        text = "\n".join((r["ocr_text"] or "").strip() for r in rows if (r["ocr_text"] or "").strip())
        # Ishonch -- eng uzun matnni bergan rasmniki: qaror shu rasmga tayanadi.
        conf = 0.0
        best = 0
        for r in rows:
            n = len((r["ocr_text"] or "").strip())
            if n >= best:
                best, conf = n, (r["ocr_conf"] or 0.0)

        good = _verdict(text, conf, cfg)
        state = "ok" if good else "review"
        conn.execute(
            "UPDATE posts SET ocr_text = ?, ocr_conf = ?, media_state = ? WHERE post_id = ?",
            (text or None, conf, state, post_id),
        )
        # Faqat OCR kutayotgan postning navbatini o'zgartiramiz. Matni allaqachon
        # yetarli bo'lgan (va ehtimol tekshirilgan) post qayta ko'rilmaydi.
        row = conn.execute(
            "SELECT stage FROM classify_state WHERE source_id = ?", (post_id,)
        ).fetchone()
        if row and row["stage"] in ("awaiting_ocr", "media_review"):
            conn.execute(
                "UPDATE classify_state SET stage = ?, updated_at = ? WHERE source_id = ?",
                ("pending" if good else "media_review", time.time(), post_id),
            )
    return state


def promote(post_id: str, text: str | None = None) -> None:
    """Odam tasdiqladi (yoki matnni qo'lda tuzatdi) -- navbatga qaytaramiz."""
    with db.connect() as conn:
        if text is not None:
            conn.execute("UPDATE posts SET ocr_text = ? WHERE post_id = ?",
                         (text.strip() or None, post_id))
        conn.execute("UPDATE posts SET media_state = 'ok' WHERE post_id = ?", (post_id,))
        conn.execute(
            """INSERT INTO classify_state (source_id, source_type, stage, updated_at)
               VALUES (?, 'post', 'pending', ?)
               ON CONFLICT(source_id) DO UPDATE SET stage = 'pending', updated_at = excluded.updated_at""",
            (post_id, time.time()),
        )


def discard(post_id: str) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE posts SET media_state = 'none' WHERE post_id = ?", (post_id,))
        conn.execute(
            "UPDATE classify_state SET stage = 'noise', updated_at = ? WHERE source_id = ?",
            (time.time(), post_id),
        )
