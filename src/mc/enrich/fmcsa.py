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


def for_lead(lead: dict) -> dict | None:
    """Lead uchun cache'dan (yoki kerak bo'lsa tarmoqdan) FMCSA yozuvi."""
    if not lead.get("mc_number") and not lead.get("dot_number"):
        return None
    return lookup(dot=lead.get("dot_number"), mc=lead.get("mc_number"))
