"""FMCSA tekshiruvi -- postdagi da'voni rasmiy ma'lumot bilan solishtiradi.

Manba: data.transportation.gov Socrata API (dataset `az4n-8mr2`, FMCSA Census).
API key kerak emas. mobile.fmcsa.dot.gov (QCMobile) va safer.fmcsa.dot.gov ikkalasi
ham 403 qaytaradi, shuning uchun aynan shu manba ishlatiladi.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime

import httpx

from .. import db

BASE = "https://data.transportation.gov/resource/az4n-8mr2.json"
DIGITS = re.compile(r"\D")

STATUS = {"A": "ACTIVE", "I": "INACTIVE"}


def _clean(num: str | None) -> str | None:
    if not num:
        return None
    n = DIGITS.sub("", str(num))
    return n.lstrip("0") or None


def _parse_date(s: str | None) -> date | None:
    if not s or len(s) < 8:
        return None
    try:
        return datetime.strptime(s[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _fetch(params: dict) -> dict | None:
    with httpx.Client(timeout=30) as c:
        for attempt in range(3):
            try:
                r = c.get(BASE, params=params)
            except httpx.HTTPError:
                time.sleep(2**attempt)
                continue
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code != 200:
                return None
            rows = r.json()
            return rows[0] if rows else None
    return None


def lookup(dot: str | None = None, mc: str | None = None) -> dict | None:
    """DOT yoki MC docket bo'yicha carrier snapshot. Cache'lanadi."""
    dot, mc = _clean(dot), _clean(mc)
    if not dot and not mc:
        return None
    key = f"dot:{dot}" if dot else f"docket:{mc}"
    cfg = db.load_config()
    max_age = cfg["fmcsa"]["cache_days"] * 86400

    with db.connect() as conn:
        row = conn.execute("SELECT * FROM fmcsa_cache WHERE key = ?", (key,)).fetchone()
        if row and time.time() - row["fetched_at"] < max_age:
            return json.loads(row["payload"]) if row["ok"] else None

    params = {"dot_number": dot} if dot else {"docket1": mc}
    raw = _fetch(params)

    result = summarize(raw) if raw else None
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO fmcsa_cache (key, payload, ok, fetched_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, ok=excluded.ok,
                                              fetched_at=excluded.fetched_at""",
            (key, json.dumps(result) if result else "null", 1 if result else 0, time.time()),
        )
    return result


def summarize(raw: dict) -> dict:
    added = _parse_date(raw.get("add_date"))
    age_years = round((date.today() - added).days / 365.25, 1) if added else None
    docket = None
    if raw.get("docket1"):
        docket = f"{raw.get('docket1prefix', 'MC')}{raw['docket1']}"

    return {
        "dot_number": raw.get("dot_number"),
        "mc_number": raw.get("docket1"),
        "docket": docket,
        "legal_name": raw.get("legal_name"),
        "status": STATUS.get(raw.get("status_code", ""), raw.get("status_code")),
        "authority_status": STATUS.get(
            raw.get("docket1_status_code", ""), raw.get("docket1_status_code")
        ),
        "added": added.isoformat() if added else None,
        "age_years": age_years,
        "state": raw.get("phy_state"),
        "city": raw.get("phy_city"),
        "phone": _clean(raw.get("phone")),
        "power_units": raw.get("power_units"),
        "total_drivers": raw.get("total_drivers"),
        "safety_rating": raw.get("safety_rating"),
        "safety_rating_date": raw.get("safety_rating_date"),
        "crash_rate": raw.get("recordable_crash_rate"),
        "classdef": raw.get("classdef"),
        "mcs150_date": raw.get("mcs150_date"),
    }


def enrich_pending(limit: int = 200, verbose: bool = True) -> dict:
    """MC/DOT raqami bor, lekin hali tekshirilmagan lead'lar."""
    stats = {"checked": 0, "found": 0, "not_found": 0}
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT lead_id, mc_number, dot_number FROM leads
               WHERE (mc_number IS NOT NULL OR dot_number IS NOT NULL)
               ORDER BY updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    for r in rows:
        stats["checked"] += 1
        res = lookup(dot=r["dot_number"], mc=r["mc_number"])
        if res:
            stats["found"] += 1
        else:
            stats["not_found"] += 1
        if verbose and stats["checked"] % 25 == 0:
            print(f"  FMCSA {stats['checked']}/{len(rows)}")
        time.sleep(0.3)   # Socrata'ga hurmat
    return stats


# --- eskirgan leadlarni qayta tekshirish ---------------------------------


WATCH_FIELDS = [
    ("status", "Carrier status"),
    ("authority_status", "Authority"),
]


def _cached(key: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT payload, ok FROM fmcsa_cache WHERE key = ?",
                           (key,)).fetchone()
    if not row or not row["ok"]:
        return None
    return json.loads(row["payload"])


def refresh(dot: str | None, mc: str | None) -> tuple[dict | None, dict | None]:
    """Cache'ni chetlab o'tib qayta so'raydi. (eski, yangi) qaytaradi."""
    dot, mc = _clean(dot), _clean(mc)
    if not dot and not mc:
        return None, None
    key = f"dot:{dot}" if dot else f"docket:{mc}"
    old = _cached(key)

    raw = _fetch({"dot_number": dot} if dot else {"docket1": mc})
    new = summarize(raw) if raw else None
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO fmcsa_cache (key, payload, ok, fetched_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, ok=excluded.ok,
                                              fetched_at=excluded.fetched_at""",
            (key, json.dumps(new) if new else "null", 1 if new else 0, time.time()),
        )
    return old, new


def _changes(old: dict | None, new: dict | None) -> list[str]:
    """Diqqatga sazovor o'zgarishlar -- hammasi emas, qaror o'zgartiradiganlari."""
    if old and not new:
        return ["Disappeared from the FMCSA register"]
    if not old or not new:
        return []
    out = []
    for field, label in WATCH_FIELDS:
        a, b = old.get(field), new.get(field)
        if a and b and a != b:
            out.append(f"{label}: {a} → {b}")
    return out


def recheck(limit: int | None = None, verbose: bool = True) -> dict:
    """Sotuvda turgan eski leadlarni FMCSA'da qayta so'raydi.

    Sotuvchi e'lonini qo'yganidan keyin authority o'lishi yoki sotilib ketishi
    mumkin. Buni bilmasdan xaridorga taklif qilish -- eng qimmat xato, shuning
    uchun eskirgan leadlar qayta tekshiriladi va o'zgarish darhol ko'rsatiladi.
    """
    cfg = db.load_config()
    r = cfg.get("recheck") or {}
    after = r.get("after_days", 14) * 86400
    limit = limit or r.get("max_per_run", 40)
    cutoff = time.time() - after

    stats = {"checked": 0, "changed": 0, "gone": 0}
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT l.lead_id, l.mc_number, l.dot_number, l.status,
                      COALESCE(p.created_at, p.first_seen_at) AS ts
               FROM leads l
               LEFT JOIN posts p ON p.post_id = COALESCE(l.source_post_id, l.source_id)
               WHERE l.side = 'SELL'
                 AND (l.mc_number IS NOT NULL OR l.dot_number IS NOT NULL)
                 AND l.status IN ('new', 'contacted', 'qualified', 'matched')
                 AND COALESCE(p.created_at, p.first_seen_at, 0) < ?
               ORDER BY ts ASC LIMIT ?""",
            (cutoff, limit),
        ).fetchall()

    for row in rows:
        stats["checked"] += 1
        old, new = refresh(row["dot_number"], row["mc_number"])
        for text in _changes(old, new):
            stats["changed"] += 1
            if "Disappeared" in text:
                stats["gone"] += 1
            with db.connect() as conn:
                conn.execute(
                    """INSERT INTO lead_alerts (lead_id, kind, text, created_at)
                       VALUES (?, 'fmcsa_change', ?, ?)""",
                    (row["lead_id"], text, time.time()))
            if verbose:
                print(f"  lead {row['lead_id']}: {text}")
        time.sleep(0.3)

    with db.connect() as conn:
        db.set_health(conn, "recheck_last_run", time.strftime("%Y-%m-%d %H:%M:%S"))
        db.set_health(conn, "recheck_last_result", str(stats))
    return stats


def alerts_for(lead_ids: list[int]) -> dict[int, list[dict]]:
    """lead_id -> ogohlantirishlar (UI uchun)."""
    ids = [i for i in dict.fromkeys(lead_ids) if i]
    if not ids:
        return {}
    out: dict[int, list[dict]] = {}
    with db.connect() as conn:
        for start in range(0, len(ids), 400):
            chunk = ids[start : start + 400]
            q = ",".join("?" * len(chunk))
            for r in conn.execute(
                f"""SELECT lead_id, kind, text, created_at FROM lead_alerts
                    WHERE lead_id IN ({q}) ORDER BY created_at DESC""", chunk):
                out.setdefault(r["lead_id"], []).append(dict(r))
    return out


def for_lead(lead: dict) -> dict | None:
    """Lead uchun cache'dan (yoki kerak bo'lsa tarmoqdan) FMCSA yozuvi."""
    if not lead.get("mc_number") and not lead.get("dot_number"):
        return None
    return lookup(dot=lead.get("dot_number"), mc=lead.get("mc_number"))
