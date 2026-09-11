"""Firibgarlik signallari: bitta odam ortidagi ko'p MC, bitta MC ortidagi ko'p odam.

Bu guruhlarda eng keng tarqalgan ikki firibgarlik:
  1. Bitta telefon o'nlab har xil MC ni "o'ziniki" qilib sotadi -- vositachi yoki
     o'g'irlangan raqamlar bilan ishlaydigan odam.
  2. Bitta MC raqami bir nechta akkauntdan sotiladi -- kim haqiqiy egasi noma'lum,
     yoki allaqachon sotilgan authority qayta sotilmoqda.

Ikkalasi ham FB profilidan ko'rinmaydi, lekin bizning bazamizda ko'rinadi.
Signal ballga tushadi va sababi UI da ochiq yoziladi.
"""

from __future__ import annotations

import json
import re
import time

from . import db

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def contact_key(value: str | None) -> tuple[str, str] | None:
    """(kind, key) -- taqqoslash uchun normalizatsiya qilingan aloqa.

    Telefon oxirgi 10 raqamgacha qisqaradi: "+1 (305) 555-0101" va "3055550101"
    bir xil odam.
    """
    if not value:
        return None
    v = value.strip()
    if EMAIL_RE.fullmatch(v):
        return ("email", v.lower())
    digits = "".join(ch for ch in v if ch.isdigit())
    if len(digits) >= 10:
        return ("phone", digits[-10:])
    return None


def rebuild_index() -> int:
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT lead_id, person_id, contact_value, mc_number, dot_number
               FROM leads WHERE contact_value IS NOT NULL"""
        ).fetchall()
        conn.execute("DELETE FROM contact_index")
        payload = []
        for r in rows:
            key = contact_key(r["contact_value"])
            if not key:
                continue
            payload.append((key[1], key[0], r["person_id"], r["lead_id"],
                            r["mc_number"] or r["dot_number"]))
        conn.executemany(
            """INSERT OR REPLACE INTO contact_index
               (contact_key, kind, person_id, lead_id, mc_number) VALUES (?, ?, ?, ?, ?)""",
            payload,
        )
    return len(payload)


def _links(conn) -> tuple[dict, dict, dict]:
    """(kalit -> MC to'plami, kalit -> odam to'plami, MC -> odam to'plami)"""
    key_mcs: dict[str, set] = {}
    key_people: dict[str, set] = {}
    for r in conn.execute("SELECT contact_key, person_id, mc_number FROM contact_index"):
        if r["mc_number"]:
            key_mcs.setdefault(r["contact_key"], set()).add(r["mc_number"])
        if r["person_id"]:
            key_people.setdefault(r["contact_key"], set()).add(r["person_id"])

    mc_people: dict[str, set] = {}
    for r in conn.execute(
        """SELECT l.mc_number, l.person_id FROM leads l
           WHERE l.side = 'SELL' AND l.mc_number IS NOT NULL AND l.person_id IS NOT NULL"""
    ):
        mc_people.setdefault(r["mc_number"], set()).add(r["person_id"])
    return key_mcs, key_people, mc_people


def run() -> dict:
    """Har bir SELL lead uchun signallarni hisoblaydi va `leads.flags` ga yozadi.

    Ball `score.py` da hisoblanadi -- u shu bayroqlarni o'qiydi, shunda "nega
    bu lead 40 ball" degan savolga javob bitta joyda qoladi.
    """
    cfg = db.load_config()
    w = cfg.get("fraud") or {}
    n_indexed = rebuild_index()
    stats = {"indexed": n_indexed, "flagged": 0, "phone_many_mc": 0, "mc_many_people": 0}

    with db.connect() as conn:
        key_mcs, key_people, mc_people = _links(conn)
        leads = conn.execute(
            """SELECT l.lead_id, l.person_id, l.contact_value, l.mc_number, l.side,
                      pe.name AS person_name
               FROM leads l LEFT JOIN people pe ON pe.person_id = l.person_id"""
        ).fetchall()
        names = {r["person_id"]: r["person_name"] for r in leads if r["person_id"]}

        updates = []
        for r in leads:
            flags = []
            key = contact_key(r["contact_value"])
            if key:
                mcs = key_mcs.get(key[1]) or set()
                people = key_people.get(key[1]) or set()
                if len(mcs) >= w.get("same_contact_min_mc", 3):
                    flags.append({
                        "code": "contact_many_mc",
                        "points": w.get("same_contact_many_mc", -20),
                        "text": f"This {key[0]} is selling {len(mcs)} different MC numbers",
                    })
                    stats["phone_many_mc"] += 1
                if len(people) >= 2:
                    others = [names.get(p) for p in people if p != r["person_id"]]
                    others = [o for o in others if o]
                    if others:
                        flags.append({
                            "code": "contact_many_accounts",
                            "points": w.get("same_contact_many_accounts", -10),
                            "text": f"Same {key[0]} also posts as {', '.join(others[:3])}",
                        })

            if r["side"] == "SELL" and r["mc_number"]:
                sellers = mc_people.get(r["mc_number"]) or set()
                if len(sellers) >= 2:
                    others = [names.get(p) for p in sellers if p != r["person_id"]]
                    others = [o for o in others if o]
                    flags.append({
                        "code": "mc_many_sellers",
                        "points": w.get("mc_sold_by_many", -25),
                        "text": (f"MC {r['mc_number']} is also being sold by "
                                 f"{', '.join(others[:3]) or 'another account'}"),
                    })
                    stats["mc_many_people"] += 1

            if flags:
                stats["flagged"] += 1
            updates.append((json.dumps(flags, ensure_ascii=False) if flags else None,
                            r["lead_id"]))

        conn.executemany("UPDATE leads SET flags = ? WHERE lead_id = ?", updates)
        db.set_health(conn, "fraud_last_run", time.strftime("%Y-%m-%d %H:%M:%S"))
    return stats


def linked_accounts(person_id: str) -> list[dict]:
    """Shu odam bilan bitta telefon/email ostida turgan boshqa akkauntlar."""
    with db.connect() as conn:
        keys = [r["contact_key"] for r in conn.execute(
            "SELECT DISTINCT contact_key FROM contact_index WHERE person_id = ?", (person_id,))]
        if not keys:
            return []
        q = ",".join("?" * len(keys))
        rows = conn.execute(
            f"""SELECT DISTINCT ci.person_id, ci.contact_key, ci.kind, pe.name
                FROM contact_index ci LEFT JOIN people pe ON pe.person_id = ci.person_id
                WHERE ci.contact_key IN ({q}) AND ci.person_id != ?""",
            (*keys, person_id),
        ).fetchall()
    return [dict(r) for r in rows]
