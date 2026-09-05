"""T0 -> T1 -> T2 kaskadi."""

from __future__ import annotations

import time

from .. import db
from . import prefilter
from .groq_client import BudgetExhausted, GroqClient
from .schema import EXTRACT_SCHEMA, EXTRACT_SYSTEM, TRIAGE_SYSTEM

EXTRACT_FIELDS = [
    "mc_number", "dot_number", "authority_since", "authority_age_years", "entity_type",
    "state", "price_usd", "price_is_negotiable", "has_amazon", "amazon_status",
    "includes_bank", "includes_email", "includes_phone", "has_trucks",
    "has_insurance", "clean_record", "buyer_budget_usd", "buyer_wants_state",
    "buyer_min_age_years", "buyer_needs_amazon", "contact_method", "contact_value",
    "urgency",
]


def _pending(conn, limit: int) -> list[dict]:
    """Klassifikatsiya kutayotgan post va comment'lar, yangisi birinchi."""
    rows = conn.execute(
        """SELECT cs.source_id, cs.source_type,
                  COALESCE(p.text, c.text)                       AS text,
                  COALESCE(p.person_id, c.person_id)             AS person_id,
                  COALESCE(p.post_id, c.post_id)                 AS source_post_id,
                  COALESCE(p.created_at, c.created_at)           AS created_at,
                  COALESCE(p.first_seen_at, c.first_seen_at)     AS first_seen_at
           FROM classify_state cs
           LEFT JOIN posts p    ON p.post_id = cs.source_id AND cs.source_type = 'post'
           LEFT JOIN comments c ON c.comment_id = cs.source_id AND cs.source_type = 'comment'
           WHERE cs.stage IN ('pending', 'triaged')
           ORDER BY COALESCE(p.created_at, c.created_at, p.first_seen_at, c.first_seen_at) DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows if r["text"]]


def _mark(conn, source_id: str, stage: str, side: str | None = None, error: str | None = None):
    conn.execute(
        """UPDATE classify_state SET stage = ?, side_guess = COALESCE(?, side_guess),
                                     error = ?, updated_at = ?
           WHERE source_id = ?""",
        (stage, side, error, time.time(), source_id),
    )


def run(limit: int = 200, verbose: bool = True) -> dict:
    cfg = db.load_config()
    client = GroqClient(cfg)
    stats = {"scanned": 0, "noise_t0": 0, "noise_t1": 0, "extracted": 0,
             "errors": 0, "budget_stop": False}

    with db.connect() as conn:
        items = _pending(conn, limit)

    if not items:
        return stats
    stats["scanned"] = len(items)

    # --- T0: bepul regex ---
    survivors = []
    with db.connect() as conn:
        for it in items:
            if it["source_id"] and not prefilter.keep(it["text"]):
                _mark(conn, it["source_id"], "noise")
                stats["noise_t0"] += 1
            else:
                survivors.append(it)

    # --- T1: arzon triage (batch) ---
    batch_size = cfg["llm"]["triage_batch_size"]
    to_extract: list[dict] = []
    for start in range(0, len(survivors), batch_size):
        batch = survivors[start : start + batch_size]
        numbered = "\n\n".join(
            f"[{i}] ({b['source_type']}) {b['text'][:900]}" for i, b in enumerate(batch)
        )
        try:
            out = client.chat(
                cfg["llm"]["triage_model"], TRIAGE_SYSTEM, numbered, max_tokens=900,
                reasoning_effort=cfg["llm"].get("reasoning_effort")
            )
        except BudgetExhausted:
            stats["budget_stop"] = True
            break
        except Exception as e:
            stats["errors"] += len(batch)
            with db.connect() as conn:
                for b in batch:
                    _mark(conn, b["source_id"], "error", error=str(e)[:200])
            continue

        sides = {}
        for row in (out.get("items") or out.get("results") or []):
            try:
                sides[int(row["i"])] = str(row.get("side", "NOISE")).upper()
            except (KeyError, TypeError, ValueError):
                continue

        with db.connect() as conn:
            for i, b in enumerate(batch):
                side = sides.get(i, "NOISE")
                if side in ("SELL", "BUY"):
                    _mark(conn, b["source_id"], "triaged", side=side)
                    b["_side"] = side
                    to_extract.append(b)
                else:
                    _mark(conn, b["source_id"], "noise", side="NOISE")
                    stats["noise_t1"] += 1
        if verbose:
            print(f"  T1 {start + len(batch)}/{len(survivors)} -> "
                  f"{len(to_extract)} lead, {client.remaining_today()} token qoldi")

    # --- T2: strukturali ekstraksiya ---
    for b in to_extract:
        hints = prefilter.regex_hints(b["text"])
        prompt = (
            f"Item type: {b['source_type']}\n"
            f"Preliminary label: {b['_side']}\n"
            f"Regex found -> MC: {hints['mc_numbers']}, DOT: {hints['dot_numbers']}, "
            f"phone: {hints['phones']}, email: {hints['emails']}\n"
            f"Current year: 2026\n\n"
            f"TEXT:\n{b['text'][:3000]}"
        )
        try:
            data = client.chat(
                cfg["llm"]["extract_model"], EXTRACT_SYSTEM, prompt,
                json_schema=EXTRACT_SCHEMA, max_tokens=1600,
                reasoning_effort=cfg["llm"].get("reasoning_effort"),
            )
        except BudgetExhausted:
            stats["budget_stop"] = True
            break
        except Exception as e:
            stats["errors"] += 1
            with db.connect() as conn:
                _mark(conn, b["source_id"], "error", error=str(e)[:200])
            continue

        side = str(data.get("side", b["_side"])).upper()
        with db.connect() as conn:
            if side not in ("SELL", "BUY"):
                _mark(conn, b["source_id"], "noise", side="NOISE")
                stats["noise_t1"] += 1
                continue

            # Regex topgan raqam LLM'nikidan ustun -- u yerda xato bo'lmaydi
            mc = (hints["mc_numbers"] or [None])[0] or data.get("mc_number")
            dot = (hints["dot_numbers"] or [None])[0] or data.get("dot_number")
            contact_value = data.get("contact_value")
            contact_method = data.get("contact_method")
            if hints["phones"] and not contact_value:
                contact_value, contact_method = hints["phones"][0], "phone"
            if hints["emails"] and not contact_value:
                contact_value, contact_method = hints["emails"][0], "email"

            payload = {k: data.get(k) for k in EXTRACT_FIELDS}
            payload.update(
                mc_number=str(mc) if mc else None,
                dot_number=str(dot) if dot else None,
                contact_value=contact_value,
                contact_method=contact_method,
            )
            for k in ("price_is_negotiable", "has_amazon", "has_trucks",
                      "has_insurance", "clean_record", "buyer_needs_amazon",
                      "includes_bank", "includes_email", "includes_phone"):
                if payload[k] is not None:
                    payload[k] = int(bool(payload[k]))

            now = time.time()
            payload.update(
                source_type=b["source_type"], source_id=b["source_id"],
                source_post_id=b["source_post_id"], person_id=b["person_id"],
                side=side, llm_confidence=data.get("confidence"),
                created_at=now, updated_at=now,
            )
            cols = ["source_type", "source_id", "source_post_id", "person_id", "side",
                    "llm_confidence", "created_at", "updated_at", *EXTRACT_FIELDS]
            updatable = ["side", "updated_at", "llm_confidence", *EXTRACT_FIELDS]
            conn.execute(
                f"""INSERT INTO leads ({', '.join(cols)})
                    VALUES ({', '.join(':' + c for c in cols)})
                    ON CONFLICT(source_id) DO UPDATE SET
                      {', '.join(f'{c} = excluded.{c}' for c in updatable)}""",
                payload,
            )
            _mark(conn, b["source_id"], "extracted", side=side)
            stats["extracted"] += 1

    _refresh_person_counts()
    return stats


def _refresh_person_counts() -> None:
    """Reseller/lowballer aniqlash uchun odam bo'yicha hisob."""
    with db.connect() as conn:
        conn.execute("""
            UPDATE people SET
              n_sell = (SELECT COUNT(*) FROM leads l
                        WHERE l.person_id = people.person_id AND l.side = 'SELL'),
              n_buy  = (SELECT COUNT(*) FROM leads l
                        WHERE l.person_id = people.person_id AND l.side = 'BUY')
        """)
        conn.execute("UPDATE people SET is_suspected_reseller = (n_sell >= 5)")
