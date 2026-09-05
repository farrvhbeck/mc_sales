"""Buyer <-> Seller mosligi.

Muhim qoida: comment'da "sotib olaman" deb yozgan odamni **o'sha postdagi** sellerga
match qilish foydasiz -- u postni allaqachon ko'rgan va o'zi yozgan. Uning qiymati
boshqa (u ko'rmagan) va kelajakdagi sellerlarga ulanishida.
"""

from __future__ import annotations

import json
import time

from . import db
from .enrich import fmcsa

SELECT_LEADS = """
SELECT l.*, pe.name AS person_name,
       COALESCE(p.created_at, c.created_at, p.first_seen_at, c.first_seen_at) AS ts
FROM leads l
LEFT JOIN people pe  ON pe.person_id = l.person_id
LEFT JOIN posts p    ON p.post_id = l.source_id AND l.source_type = 'post'
LEFT JOIN comments c ON c.comment_id = l.source_id AND l.source_type = 'comment'
WHERE l.side = ? AND l.status NOT IN ('junk', 'closed')
"""


def _blocked(buyer: dict, seller: dict, commented: set[tuple[str, str]]) -> str | None:
    """Match qilish mumkin emasligining sababi, yoki None."""
    if buyer["source_post_id"] and buyer["source_post_id"] == seller["source_post_id"]:
        return "buyer aynan shu postda yozgan"
    if buyer["person_id"] and buyer["person_id"] == seller["person_id"]:
        return "o'zini o'ziga"
    if (buyer["person_id"], seller["source_post_id"]) in commented:
        return "buyer bu postga allaqachon comment yozgan"
    return None


def _fit(buyer: dict, seller: dict, tol: float, fm: dict | None) -> tuple[int, list] | None:
    """Qattiq filtr + yumshoq ball. None -> mos kelmaydi."""
    reasons: list[str] = []
    pts = 0

    want_state = buyer.get("buyer_wants_state") or buyer.get("state")
    seller_state = (fm or {}).get("state") or seller.get("state")
    if want_state and seller_state:
        if want_state.upper() != seller_state.upper():
            return None
        reasons.append(f"Shtat mos: {want_state}")
        pts += 20
    elif want_state and not seller_state:
        reasons.append(f"Buyer {want_state} so'ragan, seller shtati noma'lum")

    min_age = buyer.get("buyer_min_age_years")
    age = (fm or {}).get("age_years") or seller.get("authority_age_years")
    if min_age and age:
        if age < min_age:
            return None
        reasons.append(f"Yosh talabga javob beradi: {age}y >= {min_age}y")
        pts += 15

    budget = buyer.get("buyer_budget_usd")
    price = seller.get("price_usd")
    if budget and price:
        if price > budget * tol:
            return None
        reasons.append(f"Narx byudjetga sig'adi: ${price:,} <= ${budget:,}")
        pts += 20

    if buyer.get("buyer_needs_amazon"):
        if not seller.get("has_amazon"):
            return None
        reasons.append("Amazon account bor")
        pts += 15

    if fm and fm.get("status") == "ACTIVE":
        reasons.append("FMCSA'da ACTIVE")
        pts += 15

    # Ikkala tomonning sifati
    pts += int(((seller.get("score") or 0) + (buyer.get("score") or 0)) / 10)

    # Yangilik
    if seller.get("ts"):
        hours = (time.time() - seller["ts"]) / 3600
        if hours < 48:
            reasons.append("Seller posti yangi (<48s)")
            pts += 10

    if not reasons:
        return None
    return min(100, pts), reasons


def _intro(buyer: dict, seller: dict, fm: dict | None) -> str:
    bits = []
    if fm:
        bits.append(f"{fm.get('docket') or ''} / DOT {fm.get('dot_number')}".strip(" /"))
        if fm.get("age_years"):
            bits.append(f"{fm['age_years']} yil ({fm.get('added')})")
        if fm.get("status"):
            bits.append(f"FMCSA: {fm['status']}")
        if fm.get("state"):
            bits.append(fm["state"])
    if seller.get("price_usd"):
        bits.append(f"${seller['price_usd']:,}")
    if seller.get("has_amazon"):
        bits.append("Amazon account bor")
    detail = " · ".join(b for b in bits if b) or "tafsilotlar postda"

    return (
        f"Hi {(buyer.get('person_name') or '').split(' ')[0]} — saw you're looking for an "
        f"MC/DOT. There's one available: {detail}. "
        f"Seller: {seller.get('person_name') or 'n/a'}. Want the contact?"
    )


def run(limit_pairs: int = 2000) -> dict:
    cfg = db.load_config()
    tol = cfg["match"]["price_tolerance"]
    max_per_buyer = cfg["match"]["max_per_buyer"]

    with db.connect() as conn:
        buyers = [dict(r) for r in conn.execute(SELECT_LEADS + " ORDER BY l.score DESC", ("BUY",))]
        sellers = [dict(r) for r in conn.execute(SELECT_LEADS + " ORDER BY l.score DESC", ("SELL",))]
        commented = {
            (r["person_id"], r["post_id"])
            for r in conn.execute("SELECT person_id, post_id FROM comments")
        }

    fm_cache: dict[int, dict | None] = {}
    rows = []
    stats = {"buyers": len(buyers), "sellers": len(sellers), "pairs": 0, "blocked": 0}

    for buyer in buyers:
        found = 0
        for seller in sellers:
            if found >= max_per_buyer or len(rows) >= limit_pairs:
                break
            reason = _blocked(buyer, seller, commented)
            if reason:
                stats["blocked"] += 1
                continue
            if seller["lead_id"] not in fm_cache:
                fm_cache[seller["lead_id"]] = fmcsa.for_lead(seller)
            fm = fm_cache[seller["lead_id"]]
            fit = _fit(buyer, seller, tol, fm)
            if not fit:
                continue
            score, reasons = fit
            rows.append(
                (buyer["lead_id"], seller["lead_id"], score,
                 json.dumps(reasons, ensure_ascii=False),
                 _intro(buyer, seller, fm), time.time())
            )
            found += 1
            stats["pairs"] += 1

    with db.connect() as conn:
        conn.executemany(
            """INSERT INTO matches (buyer_lead_id, seller_lead_id, score, reasons,
                                    intro_text, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(buyer_lead_id, seller_lead_id) DO UPDATE SET
                 score = excluded.score, reasons = excluded.reasons,
                 intro_text = excluded.intro_text""",
            rows,
        )
    return stats
