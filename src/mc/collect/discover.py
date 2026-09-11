"""Keng qidiruv: FB'ning global post qidiruvi manba sifatida.

Guruh feed'i faqat **a'zo bo'lgan** 8 guruhni ko'radi. `/search/posts/?q=...` esa
butun ochiq Facebook bo'ylab qidiradi -- biz bilmagan guruhlardagi va sahifadagi
e'lonlar ham chiqadi. Bu eng arzon "ko'proq lead" manbai: qo'shimcha guruhga
a'zo bo'lish, ya'ni qo'shimcha ban riski talab qilmaydi.

Yoqish/o'chirish dashboard'dagi tugmadan (`/groups`). O'chiq bo'lsa tizim
faqat tanlangan guruhlardan yig'adi.

Postlar `group_id = 'search'` bilan saqlanadi va `posts.source_query` da qaysi
so'rovdan kelgani yoziladi -- keyin filtrda ajratib ko'rish mumkin.
"""

from __future__ import annotations

import time
from urllib.parse import quote_plus

from playwright.sync_api import BrowserContext

from .. import db, normalize
from ..groups import SEARCH_GROUP
from .browser import check_alive, human_scroll
from .feed import download_media
from .graphql_tap import GraphQLTap

DEFAULT_QUERIES = [
    "mc authority for sale",
    "selling my mc authority",
    "mc number for sale",
    "looking to buy mc authority",
]


def queries(cfg: dict) -> list[str]:
    """Sozlamadagi so'rovlar (dashboard'dan tahrirlanadi), bo'lmasa config."""
    saved = db.get_setting("search_queries")
    if saved:
        return [q for q in saved if q.strip()]
    return (cfg.get("search") or {}).get("queries") or DEFAULT_QUERIES


def is_on(cfg: dict) -> bool:
    saved = db.get_setting("broad_search")
    if saved is not None:
        return bool(saved)
    return bool((cfg.get("search") or {}).get("enabled", False))


def due(cfg: dict) -> bool:
    """Keng qidiruv vaqti keldimi?

    Guruh feed'i har siklda tekshiriladi -- u yerda yangi post har daqiqada
    paydo bo'ladi. Qidiruv esa boshqacha: u moslik bo'yicha tartiblangan va
    natijasi soatlab o'zgarmaydi, shuning uchun har siklda takrorlash brauzer
    vaqtini bekorga yeydi. Shuning uchun o'z intervali bor.
    """
    hours = (cfg.get("search") or {}).get("every_hours", 6)
    last = db.get_setting("search_last_at") or 0
    return (time.time() - last) >= hours * 3600


def next_run_in(cfg: dict) -> float:
    """Keyingi qidiruvgacha necha soniya qolgani (UI uchun)."""
    hours = (cfg.get("search") or {}).get("every_hours", 6)
    last = db.get_setting("search_last_at") or 0
    return max(0.0, last + hours * 3600 - time.time())


def sweep_search(ctx: BrowserContext, query: str, cfg: dict, since_ts: float) -> dict:
    """Bitta so'rov bo'yicha qidiruv natijalarini aylanib chiqadi."""
    c = cfg["collect"]
    s = cfg.get("search") or {}
    max_scrolls = s.get("max_scrolls_per_query", 12)
    quiet_limit = c.get("stop_after_quiet_scrolls", 3)

    with db.connect() as conn:
        known = {r["post_id"] for r in conn.execute("SELECT post_id FROM posts")}

    page = ctx.new_page()
    tap = GraphQLTap()
    tap.attach(page)

    url = f"https://www.facebook.com/search/posts/?q={quote_plus(query)}"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    check_alive(page)

    found: dict[str, dict] = {}
    raw_batches: list = []
    quiet = 0
    scrolls = 0

    for i in range(max_scrolls):
        human_scroll(page, c["scroll_pause_seconds"])
        scrolls = i + 1
        batch = tap.drain()
        fresh_this_scroll = 0
        if batch:
            raw_batches.extend(batch)
            for payload in batch:
                for p in normalize.extract_posts(payload, SEARCH_GROUP):
                    # Qidiruv natijasi guruhga tegishli bo'lmasligi mumkin --
                    # "guruh" havolasini yasash noto'g'ri bo'lardi
                    if f"/groups/{SEARCH_GROUP}/" in (p["permalink"] or ""):
                        p["permalink"] = f"https://www.facebook.com/{p['post_id']}"
                    if p["post_id"] not in found:
                        found[p["post_id"]] = p
                        if p["post_id"] not in known:
                            fresh_this_scroll += 1

        quiet = 0 if fresh_this_scroll else quiet + 1
        if quiet >= quiet_limit and found:
            break
        if i % 5 == 4:
            check_alive(page)

    page.close()

    # Qidiruv natijasi vaqt bo'yicha tartiblanmagan -- eskisini o'zimiz kesamiz
    min_chars = c.get("min_text_chars", 25)
    fresh = []
    for p in found.values():
        if p["created_at"] is not None and p["created_at"] < since_ts:
            continue
        if len((p.get("text") or "").strip()) < min_chars and not p.get("media"):
            continue
        fresh.append(p)

    result = {"query": query, "scrolls": scrolls, "seen": len(found),
              "fresh": len(fresh), "new": 0}
    raw_path = db.save_raw(SEARCH_GROUP, "search", raw_batches) if raw_batches else None

    with db.connect() as conn:
        for p in fresh:
            if p["person_id"]:
                db.upsert_person(conn, p["person_id"], p.pop("_author_name", None),
                                 p.pop("_author_url", None))
            else:
                p.pop("_author_name", None)
                p.pop("_author_url", None)
            p["raw_path"] = raw_path
            if db.upsert_post(conn, p, min_text_chars=min_chars):
                result["new"] += 1
            conn.execute(
                "UPDATE posts SET source_query = COALESCE(source_query, ?) WHERE post_id = ?",
                (query, p["post_id"]))

    result["images"] = download_media(ctx, c)
    return result


def run(ctx: BrowserContext, cfg: dict, since_ts: float) -> dict:
    """Yoqilgan bo'lsa har bir so'rov bo'yicha qidiradi.

    Qidiruv natijasi vaqt bo'yicha emas, mos kelish bo'yicha tartiblangan:
    feed'ning 7 kunlik oynasi bu yerda natijalarning ~90% ini tashlab
    yuboradi. Shuning uchun `search.backfill_days` alohida.
    """
    import time as _t

    days = (cfg.get("search") or {}).get("backfill_days")
    if days:
        since_ts = _t.time() - days * 86400
    out: dict = {}
    for q in queries(cfg):
        try:
            out[q] = sweep_search(ctx, q, cfg, since_ts)
        except Exception as e:
            out[q] = {"error": f"{type(e).__name__}: {e}"[:200]}
    db.set_setting("search_last_at", time.time())
    with db.connect() as conn:
        db.set_health(conn, "search_last_run", time.strftime("%Y-%m-%d %H:%M:%S"))
        db.set_health(conn, "search_last_result", str(out))
    return out
