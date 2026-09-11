"""T0 regex filtrining recall auditi.

`prefilter.SIGNAL` postlarning katta qismini LLM'gacha o'ldiradi -- bu arzon va
to'g'ri. Lekin u **jim eskiradi**: guruhda til o'zgaradi ("authority" o'rniga
yangi jargon), regex esa o'sha-o'sha qolib, haqiqiy leadlarni tashlab yuboradi.
Buni hech kim sezmaydi, chunki tashlangan post hech qayerda ko'rinmaydi.

Shuning uchun vaqti-vaqti bilan regex tashlaganlardan namuna olinadi va arzon
T1 triage'iga yuboriladi. Natija ikki foyda beradi:
  1. o'lchov -- "regex oxirgi 50 tadan 3 tasini noto'g'ri tashlagan" (6%);
  2. topilgan leadlar navbatga qaytariladi, ya'ni audit o'zini oqlaydi.
"""

from __future__ import annotations

import json
import random
import time

from . import db
from .classify.groq_client import BudgetExhausted, GroqClient
from .classify.schema import TRIAGE_SYSTEM


def _sample(n: int) -> list[dict]:
    """T0 tashlagan postlar. T1 tashlaganlari emas -- ularni LLM allaqachon ko'rgan.

    Farq `side_guess` da: T1 o'ldirgan bo'lsa u 'NOISE' bo'lib yoziladi, T0 esa
    LLM'gacha yetkazmagani uchun bo'sh qoladi.
    """
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT cs.source_id, cs.source_type,
                      TRIM(COALESCE(p.text, c.text, '') ||
                           CASE WHEN p.ocr_text IS NOT NULL AND p.ocr_text != ''
                                THEN char(10) || '[image text] ' || p.ocr_text
                                ELSE '' END) AS text
               FROM classify_state cs
               LEFT JOIN posts p    ON p.post_id = cs.source_id AND cs.source_type = 'post'
               LEFT JOIN comments c ON c.comment_id = cs.source_id AND cs.source_type = 'comment'
               WHERE cs.stage = 'noise' AND cs.side_guess IS NULL
               ORDER BY cs.updated_at DESC LIMIT 500"""
        ).fetchall()
    items = [dict(r) for r in rows if (r["text"] or "").strip()]
    random.shuffle(items)
    return items[:n]


def due(cfg: dict | None = None) -> bool:
    cfg = cfg or db.load_config()
    every = (cfg.get("audit") or {}).get("every_days", 7) * 86400
    last = db.get_setting("audit_last_at") or 0
    return (time.time() - last) >= every


def run(sample: int | None = None, force: bool = False, verbose: bool = True) -> dict:
    cfg = db.load_config()
    a = cfg.get("audit") or {}
    if not force and not due(cfg):
        return {"skipped": "hali vaqti emas"}

    items = _sample(sample or a.get("sample_size", 50))
    if not items:
        return {"skipped": "tekshiradigan post yo'q"}

    client = GroqClient(cfg)
    batch_size = cfg["llm"]["triage_batch_size"]
    missed: list[dict] = []
    checked = 0

    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        numbered = "\n\n".join(
            f"[{i}] ({b['source_type']}) {b['text'][:900]}" for i, b in enumerate(batch))
        try:
            out = client.chat(cfg["llm"]["triage_model"], TRIAGE_SYSTEM, numbered,
                              max_tokens=900,
                              reasoning_effort=cfg["llm"].get("reasoning_effort"))
        except BudgetExhausted:
            break
        except Exception:
            continue

        checked += len(batch)
        for row in (out.get("items") or out.get("results") or []):
            try:
                i, side = int(row["i"]), str(row.get("side", "NOISE")).upper()
            except (KeyError, TypeError, ValueError):
                continue
            if side in ("SELL", "BUY") and i < len(batch):
                missed.append({"source_id": batch[i]["source_id"], "side": side,
                               "text": batch[i]["text"][:160]})

    # Topilganlarni navbatga qaytaramiz -- audit o'zini oqlasin
    with db.connect() as conn:
        for m in missed:
            conn.execute(
                "UPDATE classify_state SET stage = 'pending', updated_at = ? WHERE source_id = ?",
                (time.time(), m["source_id"]))

    rate = round(100 * len(missed) / checked, 1) if checked else 0.0
    result = {"checked": checked, "missed": len(missed), "miss_rate_pct": rate,
              "requeued": len(missed)}

    db.set_setting("audit_last_at", time.time())
    db.set_setting("audit_last", {**result, "at": time.strftime("%Y-%m-%d %H:%M"),
                                  "examples": missed[:5]})
    with db.connect() as conn:
        db.set_health(conn, "audit_last_result", json.dumps(result))

    if verbose:
        print(f"  T0 auditi: {checked} tadan {len(missed)} tasi aslida lead ({rate}%)")
        for m in missed[:5]:
            print(f"    {m['side']}: {m['text'][:90]}")
    return result
