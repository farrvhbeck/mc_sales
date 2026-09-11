"""Kuzatilayotgan guruhlar -- bazada, dashboard'dan boshqariladi.

`config.yaml` endi faqat **boshlang'ich ro'yxat**: baza bo'sh bo'lsa bir marta
ko'chiriladi. Undan keyin guruh qo'shish/o'chirish `/groups` sahifasidan bo'ladi,
chunki guruh ro'yxati kodga emas, ishga tegishli -- uni o'zgartirish uchun fayl
tahrirlab, jarayonni qayta ishga tushirish kerak emas.

Havola bo'yicha qo'shish: raqamli id havolada bo'lsa darhol olinadi, slug bo'lsa
(`/groups/mcforsale/`) guruh `pending` bo'lib turadi va keyingi yig'ish siklida
brauzer uni ochib haqiqiy id va nomini aniqlaydi.
"""

from __future__ import annotations

import re
import time

from . import db

# https://www.facebook.com/groups/1234567890/?ref=...  |  m.facebook.com  |  fb.com
URL_RE = re.compile(r"(?:facebook|fb)\.com/groups/([^/?#\s]+)", re.IGNORECASE)
NUMERIC = re.compile(r"^\d{6,}$")

SEARCH_GROUP = "search"          # keng qidiruvdan kelgan postlar shu "guruh"da


def parse(raw: str) -> dict | None:
    """Havola yoki id -> {"group_id", "slug", "url"}.

    Qo'llab-quvvatlanadi: to'liq havola, mobil havola, yalang'och id, yalang'och
    slug. Tushunmasa None.
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    m = URL_RE.search(raw)
    token = m.group(1) if m else raw.strip("/ ")
    token = token.split("?")[0].split("#")[0].strip("/")
    if not token:
        return None

    if NUMERIC.match(token):
        return {"group_id": token, "slug": None,
                "url": f"https://www.facebook.com/groups/{token}/"}
    # Slug: harf, raqam, nuqta, tire, pastki chiziq
    if not re.fullmatch(r"[\w.\-]{3,100}", token):
        return None
    return {"group_id": f"slug:{token}", "slug": token,
            "url": f"https://www.facebook.com/groups/{token}/"}


def seed_from_config() -> int:
    """Baza bo'sh bo'lsa config.yaml dagi guruhlarni ko'chiradi."""
    cfg = db.load_config()
    with db.connect() as conn:
        have = conn.execute("SELECT COUNT(*) FROM fb_groups").fetchone()[0]
        if have:
            return 0
        now = time.time()
        rows = [(g["id"], g.get("name"), f"https://www.facebook.com/groups/{g['id']}/",
                 None, 1, "ok", now) for g in cfg.get("groups", [])]
        conn.executemany(
            """INSERT OR IGNORE INTO fb_groups
               (group_id, name, url, slug, enabled, status, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""", rows)
    return len(rows)


def all_groups() -> list[dict]:
    seed_from_config()
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM fb_groups ORDER BY enabled DESC, name COLLATE NOCASE")]


def enabled() -> list[dict]:
    """Yig'iladigan guruhlar -- id hali aniqlanmaganlari chiqmaydi."""
    seed_from_config()
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT * FROM fb_groups
               WHERE enabled = 1 AND status != 'no_access' AND group_id NOT LIKE 'slug:%'
               ORDER BY added_at""")]


def pending() -> list[dict]:
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM fb_groups WHERE group_id LIKE 'slug:%' AND enabled = 1")]


def add(raw: str, name: str | None = None) -> dict:
    """Havola yoki id bo'yicha guruh qo'shadi. Natija: {"ok", "message", "group"}."""
    parsed = parse(raw)
    if not parsed:
        return {"ok": False, "message": "Havolani tushunmadim. "
                "Namuna: https://www.facebook.com/groups/1234567890/"}

    with db.connect() as conn:
        row = conn.execute("SELECT * FROM fb_groups WHERE group_id = ? OR slug = ?",
                           (parsed["group_id"], parsed["slug"])).fetchone()
        if row:
            if not row["enabled"]:
                conn.execute("UPDATE fb_groups SET enabled = 1 WHERE group_id = ?",
                             (row["group_id"],))
                return {"ok": True, "message": f"{row['name'] or row['group_id']} qayta yoqildi",
                        "group": dict(row)}
            return {"ok": False, "message": "Bu guruh allaqachon ro'yxatda",
                    "group": dict(row)}

        status = "pending" if parsed["slug"] else "ok"
        conn.execute(
            """INSERT INTO fb_groups (group_id, name, url, slug, enabled, status, added_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)""",
            (parsed["group_id"], name, parsed["url"], parsed["slug"], status, time.time()))

    msg = ("Qo'shildi. Guruh nomi va id si keyingi yig'ishda aniqlanadi."
           if parsed["slug"] else "Qo'shildi. Keyingi siklda yig'ila boshlaydi.")
    return {"ok": True, "message": msg, "group": parsed}


def set_enabled(group_id: str, on: bool) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE fb_groups SET enabled = ? WHERE group_id = ?",
                     (1 if on else 0, group_id))


def remove(group_id: str) -> None:
    """Guruhni ro'yxatdan chiqaradi. Yig'ilgan postlar joyida qoladi."""
    with db.connect() as conn:
        conn.execute("DELETE FROM fb_groups WHERE group_id = ?", (group_id,))


def record_run(group_id: str, result: dict) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE fb_groups SET last_collect_at = ?, last_result = ?, status = 'ok' "
            "WHERE group_id = ?", (time.time(), str(result), group_id))


def mark(group_id: str, status: str, note: str | None = None) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE fb_groups SET status = ?, note = COALESCE(?, note) "
                     "WHERE group_id = ?", (status, note, group_id))


def names() -> dict[str, str]:
    """UI filtri uchun: group_id -> nom."""
    out = {g["group_id"]: (g["name"] or g["group_id"]) for g in all_groups()}
    out[SEARCH_GROUP] = "Broad search"
    return out


def stats() -> dict[str, dict]:
    """Guruh bo'yicha post va lead soni -- `/groups` sahifasida ko'rsatiladi."""
    with db.connect() as conn:
        posts = {r["group_id"]: r["n"] for r in conn.execute(
            "SELECT group_id, COUNT(*) n FROM posts GROUP BY group_id")}
        leads = {r["group_id"]: r["n"] for r in conn.execute(
            """SELECT p.group_id, COUNT(*) n FROM leads l
               JOIN posts p ON p.post_id = COALESCE(l.source_post_id, l.source_id)
               GROUP BY p.group_id""")}
        last = {r["group_id"]: r["ts"] for r in conn.execute(
            "SELECT group_id, MAX(COALESCE(created_at, first_seen_at)) ts "
            "FROM posts GROUP BY group_id")}
    return {gid: {"posts": posts.get(gid, 0), "leads": leads.get(gid, 0),
                  "last_post": last.get(gid)}
            for gid in set(posts) | set(leads) | set(names())}


# --- slug -> id aniqlash (brauzer kerak) ---------------------------------

# Guruh sahifasida id bir necha ko'rinishda uchraydi; qaysi biri chiqsa o'shani olamiz.
ID_PATTERNS = [
    re.compile(r'"groupID"\s*:\s*"(\d{6,})"'),
    re.compile(r'"group_id"\s*:\s*"(\d{6,})"'),
    re.compile(r'/groups/(\d{6,})/'),
]

NO_ACCESS = ["join group", "you must be a member", "this content isn't available",
             "content not found", "isn't available right now"]


def resolve(ctx, group: dict, pause_rng: list[float] | None = None) -> dict:
    """Brauzer orqali slug'ni haqiqiy id va nomga aylantiradi."""
    from .collect.browser import check_alive

    page = ctx.new_page()
    try:
        page.goto(group["url"], wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        check_alive(page)

        html = page.content()
        gid = None
        for pat in ID_PATTERNS:
            m = pat.search(html)
            if m:
                gid = m.group(1)
                break

        body = ""
        try:
            body = page.inner_text("body", timeout=5000)[:3000].lower()
        except Exception:
            pass

        name = None
        try:
            title = page.title()
            name = re.sub(r"\s*\|\s*Facebook\s*$", "", title).strip() or None
        except Exception:
            pass

        if not gid:
            reason = ("guruh yopiq yoki a'zo emassiz"
                      if any(k in body for k in NO_ACCESS) else "id topilmadi")
            mark(group["group_id"], "no_access", reason)
            return {"ok": False, "message": reason}

        with db.connect() as conn:
            exists = conn.execute("SELECT 1 FROM fb_groups WHERE group_id = ?", (gid,)).fetchone()
            if exists:
                conn.execute("DELETE FROM fb_groups WHERE group_id = ?", (group["group_id"],))
            else:
                conn.execute(
                    """UPDATE fb_groups SET group_id = ?, name = COALESCE(?, name),
                                            status = 'ok', url = ?
                       WHERE group_id = ?""",
                    (gid, name, f"https://www.facebook.com/groups/{gid}/", group["group_id"]))
        return {"ok": True, "group_id": gid, "name": name}
    finally:
        page.close()
