"""Telegram xabarnomasi -- faqat chegaradan yuqori, faqat bir marta."""

from __future__ import annotations

import json
import os
import time

import httpx

from .. import db
from ..enrich import fmcsa


def _send(text: str) -> bool:
    db.load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": False},
            timeout=20,
        )
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def alert(text: str) -> bool:
    """Tizim ogohlantirishi (masalan: FB sessiya tushdi)."""
    return _send(f"⚠️ <b>MC Lead Engine</b>\n{text}")


def _format(lead: dict, fm: dict | None) -> str:
    side = "🟢 SOTUVCHI" if lead["side"] == "SELL" else "🔵 XARIDOR"
    lines = [f"{side} · <b>{lead['score']}</b> ball", ""]

    if lead.get("person_name"):
        lines.append(f"👤 {lead['person_name']}")

    if lead["side"] == "SELL":
        if fm:
            ok = "✅" if fm.get("status") == "ACTIVE" else "❌"
            lines.append(
                f"{ok} {fm.get('docket') or 'MC —'} / DOT {fm.get('dot_number')} · "
                f"{fm.get('age_years')} yil · {fm.get('state') or '—'}"
            )
            lines.append(f"   <i>{fm.get('legal_name') or ''}</i>")
        elif lead.get("mc_number") or lead.get("dot_number"):
            lines.append(f"⚠️ MC {lead.get('mc_number') or '—'} / DOT {lead.get('dot_number') or '—'} — FMCSA'da topilmadi")
        if lead.get("price_usd"):
            lines.append(f"💰 ${lead['price_usd']:,}")
    else:
        want = lead.get("buyer_wants_state") or lead.get("state")
        if want:
            lines.append(f"📍 {want}")
        if lead.get("buyer_budget_usd"):
            lines.append(f"💰 byudjet ${lead['buyer_budget_usd']:,}")
        if lead.get("buyer_min_age_years"):
            lines.append(f"📅 min {lead['buyer_min_age_years']} yil")

    if lead.get("contact_value"):
        lines.append(f"📞 {lead['contact_method']}: <code>{lead['contact_value']}</code>")

    if lead.get("source_type") == "comment":
        lines.append("💬 <i>comment'dan</i>")

    lines += ["", f"<i>{(lead.get('text') or '')[:300]}</i>", ""]
    if lead.get("permalink"):
        lines.append(lead["permalink"])
    return "\n".join(lines)


def send_alerts(dry_run: bool = False) -> int:
    """FMCSA'da o'zgargan leadlar haqida xabar.

    Bu yangi lead emas, **eskisining o'zgarishi**: siz bog'lanib turgan
    sotuvchining authority'si o'lgan bo'lishi mumkin. Shuning uchun alohida
    yuboriladi va ball chegarasiga bog'liq emas.
    """
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT a.alert_id, a.lead_id, a.text, l.mc_number, l.dot_number,
                      l.status, pe.name AS person_name,
                      (SELECT permalink FROM posts
                       WHERE post_id = COALESCE(l.source_post_id, l.source_id)) AS permalink
               FROM lead_alerts a
               JOIN leads l ON l.lead_id = a.lead_id
               LEFT JOIN people pe ON pe.person_id = l.person_id
               WHERE a.notified_at IS NULL
               ORDER BY a.created_at LIMIT 20""")]

    sent = 0
    for r in rows:
        text = "\n".join(filter(None, [
            "🔁 <b>O'zgarish</b>",
            "",
            f"👤 {r['person_name'] or '—'} · MC {r['mc_number'] or '—'}",
            f"⚠️ {r['text']}",
            f"Lead holati: {r['status']}",
            "",
            r["permalink"] or "",
        ]))
        if dry_run:
            print(text)
            print("-" * 60)
            sent += 1
            continue
        if _send(text):
            with db.connect() as conn:
                conn.execute("UPDATE lead_alerts SET notified_at = ? WHERE alert_id = ?",
                             (time.time(), r["alert_id"]))
            sent += 1
            time.sleep(1)
    return sent


def run(dry_run: bool = False) -> dict:
    cfg = db.load_config()
    n = cfg["notify"]
    stats = {"sent": 0, "skipped": 0, "alerts": 0}
    stats["alerts"] = send_alerts(dry_run=dry_run)

    with db.connect() as conn:
        rows = conn.execute(
            """SELECT l.*, pe.name AS person_name,
                      COALESCE(p.text, c.text) AS text,
                      COALESCE(p.permalink,
                               (SELECT permalink FROM posts WHERE post_id = l.source_post_id))
                        AS permalink
               FROM leads l
               LEFT JOIN people pe  ON pe.person_id = l.person_id
               LEFT JOIN posts p    ON p.post_id = l.source_id AND l.source_type = 'post'
               LEFT JOIN comments c ON c.comment_id = l.source_id AND l.source_type = 'comment'
               WHERE l.lead_id NOT IN (SELECT lead_id FROM notified)
                 AND l.status = 'new'
                 AND ((l.side = 'SELL' AND l.score >= ?) OR (l.side = 'BUY' AND l.score >= ?))
               ORDER BY l.score DESC LIMIT 20""",
            (n["sell_threshold"], n["buy_threshold"]),
        ).fetchall()

    for r in rows:
        lead = dict(r)
        fm = fmcsa.for_lead(lead) if lead["side"] == "SELL" else None
        text = _format(lead, fm)
        if dry_run:
            print(text)
            print("-" * 60)
            stats["sent"] += 1
            continue
        if _send(text):
            with db.connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO notified (lead_id, sent_at) VALUES (?, ?)",
                    (lead["lead_id"], time.time()),
                )
            stats["sent"] += 1
            time.sleep(1)
        else:
            stats["skipped"] += 1
    return stats
