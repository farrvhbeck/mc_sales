"""Deterministik, tushuntiriladigan ball.

LLM ball qo'ymaydi -- u faqat faktlarni ajratadi. Ball shu yerda og'irliklar bilan
hisoblanadi, `score_breakdown` UI da ochib ko'rsatiladi va og'irliklar `config.yaml`
dan sozlanadi. "Nega bu lead 85 ball?" degan savolga javob bo'lishi kerak.
"""

from __future__ import annotations

import json
import time

from . import db
from .enrich import fmcsa


def _freshness(ts: float | None, max_pts: float, halflife_h: float) -> float:
    if not ts:
        return max_pts * 0.25          # vaqt noma'lum -- o'rtacha jazо
    hours = max(0.0, (time.time() - ts) / 3600)
    return max_pts * (0.5 ** (hours / halflife_h))


def score_seller(lead: dict, w: dict, person: dict | None, fm: dict | None) -> tuple[int, list]:
    b: list[tuple[str, float]] = []

    has_number = bool(lead.get("mc_number") or lead.get("dot_number"))
    if has_number and fm:
        active = (fm.get("status") == "ACTIVE")
        auth_ok = fm.get("authority_status") in (None, "ACTIVE")
        if active and auth_ok:
            b.append(("FMCSA: authority ACTIVE", w["mc_verified_active"]))
        else:
            b.append((f"FMCSA: {fm.get('status')}/{fm.get('authority_status')} — o'lik authority",
                      w["mc_inactive_penalty"]))
    elif has_number and not fm:
        b.append(("MC/DOT raqami FMCSA'da topilmadi", w["mc_inactive_penalty"] / 2))
    else:
        b.append(("MC/DOT raqami berilmagan", 0))

    # Yosh -- da'voga emas, FMCSA'ga ishonamiz
    age = (fm or {}).get("age_years")
    claimed = lead.get("authority_age_years")
    if age is None:
        age = claimed
        age_src = "da'vo"
    else:
        age_src = "FMCSA"
    if age:
        if age >= 10:
            pts = w["age_10y"]
        elif age >= 5:
            pts = w["age_5y"]
        elif age >= 2:
            pts = w["age_2y"]
        else:
            pts = 0
        b.append((f"Authority yoshi {age} yil ({age_src})", pts))
        if claimed and fm and fm.get("age_years") and claimed - fm["age_years"] > 2:
            b.append((f"Yosh bo'rttirilgan: da'vo {claimed}y, FMCSA {fm['age_years']}y", -10))

    if lead.get("price_usd"):
        b.append((f"Narx aytilgan: ${lead['price_usd']:,}", w["price_stated"]))

    if lead.get("contact_method") in ("phone", "email", "whatsapp") and lead.get("contact_value"):
        b.append((f"To'g'ridan-to'g'ri kontakt ({lead['contact_method']})", w["direct_contact"]))

    perks = [k for k in ("has_amazon", "has_insurance", "clean_record") if lead.get(k)]
    if perks:
        b.append((f"Qo'shimcha: {', '.join(perks)}", w["has_perks"]))

    ts = lead.get("created_at_src")
    b.append(("Yangilik", round(_freshness(ts, w["freshness_max"], w["freshness_halflife_hours"]), 1)))

    if person and person.get("is_suspected_reseller"):
        b.append((f"Serial sotuvchi ({person['n_sell']} ta post)", w["reseller_penalty"]))

    if fm and fm.get("phone") and lead.get("contact_value"):
        posted = "".join(ch for ch in lead["contact_value"] if ch.isdigit())
        if posted and len(posted) >= 10 and posted[-10:] != (fm["phone"] or "")[-10:]:
            b.append(("Telefon FMCSA'dagidan farq qiladi (identity theft signali)",
                      w["phone_mismatch_penalty"]))

    total = sum(p for _, p in b)
    return max(0, min(100, round(total))), b


def score_buyer(lead: dict, w: dict, person: dict | None) -> tuple[int, list]:
    b: list[tuple[str, float]] = []

    conf = lead.get("llm_confidence") or 0.5
    b.append((f"Aniq xarid niyati (ishonch {conf:.0%})", round(w["clear_intent"] * conf, 1)))

    if lead.get("buyer_budget_usd"):
        b.append((f"Byudjet: ${lead['buyer_budget_usd']:,}", w["budget_stated"]))
    if lead.get("buyer_wants_state") or lead.get("state"):
        b.append((f"Shtat: {lead.get('buyer_wants_state') or lead.get('state')}",
                  w["state_stated"]))
    if lead.get("buyer_min_age_years") or lead.get("buyer_needs_amazon"):
        b.append(("Aniq talab berilgan", w["specific_requirements"]))
    if lead.get("contact_method") in ("phone", "email", "whatsapp") and lead.get("contact_value"):
        b.append((f"To'g'ridan-to'g'ri kontakt ({lead['contact_method']})", w["direct_contact"]))

    ts = lead.get("created_at_src")
    b.append(("Yangilik", round(_freshness(ts, w["freshness_max"], w["freshness_halflife_hours"]), 1)))

    if person and (person.get("n_buy") or 0) >= 8:
        b.append((f"Har kuni yozuvchi ({person['n_buy']} ta)", w["lowballer_penalty"]))

    total = sum(p for _, p in b)
    return max(0, min(100, round(total))), b


def run(limit: int = 5000) -> dict:
    cfg = db.load_config()
    ws, wb = cfg["score"]["seller"], cfg["score"]["buyer"]
    stats = {"scored": 0}

    with db.connect() as conn:
        rows = conn.execute(
            """SELECT l.*,
                      COALESCE(p.created_at, c.created_at,
                               p.first_seen_at, c.first_seen_at) AS created_at_src
               FROM leads l
               LEFT JOIN posts p    ON p.post_id = l.source_id AND l.source_type = 'post'
               LEFT JOIN comments c ON c.comment_id = l.source_id AND l.source_type = 'comment'
               ORDER BY l.updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        people = {r["person_id"]: dict(r) for r in conn.execute("SELECT * FROM people")}

    updates = []
    for r in rows:
        lead = dict(r)
        person = people.get(lead.get("person_id"))
        if lead["side"] == "SELL":
            fm = fmcsa.for_lead(lead)
            score, breakdown = score_seller(lead, ws, person, fm)
        else:
            score, breakdown = score_buyer(lead, wb, person)
        updates.append((score, json.dumps(breakdown, ensure_ascii=False), lead["lead_id"]))
        stats["scored"] += 1

    with db.connect() as conn:
        conn.executemany(
            "UPDATE leads SET score = ?, score_breakdown = ? WHERE lead_id = ?", updates
        )
    return stats
