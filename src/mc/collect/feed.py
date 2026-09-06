"""Guruh feed'ini xronologik tartibda aylanib chiqadi."""

from __future__ import annotations

import time

from playwright.sync_api import BrowserContext

from .. import db, normalize
from .browser import SessionDead, check_alive, human_scroll, pause
from .graphql_tap import GraphQLTap


def _known_ids(group_id: str) -> set[str]:
    with db.connect() as conn:
        return {r["post_id"] for r in conn.execute(
            "SELECT post_id FROM posts WHERE group_id = ?", (group_id,))}


def sweep_feed(
    ctx: BrowserContext,
    group_id: str,
    cfg: dict,
    since_ts: float,
    dry_run: bool = False,
    full: bool = False,
) -> dict:
    """Feed'ni scroll qilib yangi postlarni yig'adi.

    Odatiy rejimda **inkremental**: bir necha scroll ketma-ket yangi post bermasa
    to'xtaydi. Har 15 daqiqada 40 marta scroll qilish shart emas -- postlarning
    95% i allaqachon bazada. Kamroq scroll = akkaunt uchun kamroq yuk.

    `full=True` -- chuqur skan, erta to'xtamaydi (backfill uchun).
    """
    c = cfg["collect"]
    known = set() if full else _known_ids(group_id)
    quiet_limit = c.get("stop_after_quiet_scrolls", 3)
    quiet = 0
    page = ctx.new_page()
    tap = GraphQLTap()
    tap.attach(page)

    url = f"https://www.facebook.com/groups/{group_id}/?sorting_setting=CHRONOLOGICAL"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    check_alive(page)

    all_posts: dict[str, dict] = {}
    oldest = time.time()
    raw_batches: list = []

    scrolls = 0
    for i in range(c["max_scrolls_per_run"]):
        human_scroll(page, c["scroll_pause_seconds"])
        scrolls = i + 1
        batch = tap.drain()

        fresh_this_scroll = 0
        if batch:
            raw_batches.extend(batch)
            for payload in batch:
                for p in normalize.extract_posts(payload, group_id):
                    if p["post_id"] not in all_posts:
                        all_posts[p["post_id"]] = p
                        if p["post_id"] not in known:
                            fresh_this_scroll += 1
                    if p["created_at"]:
                        oldest = min(oldest, p["created_at"])

        # backfill chegarasidan o'tdikmi?
        if oldest < since_ts and all_posts:
            break

        # Inkremental to'xtash: ketma-ket bir necha scroll yangilik bermasa,
        # demak feed'ning ko'rgan qismiga yetdik.
        if not full:
            quiet = 0 if fresh_this_scroll else quiet + 1
            if quiet >= quiet_limit and all_posts:
                break

        if i % 5 == 4:
            check_alive(page)

    # DOM fallback: GraphQL'dan hech nima chiqmasa
    dom_used = False
    if not all_posts:
        dom_used = True
        try:
            for d in page.evaluate(normalize.DOM_POSTS_JS):
                if not d.get("post_id"):
                    continue
                all_posts[d["post_id"]] = {
                    "post_id": d["post_id"],
                    "group_id": group_id,
                    "person_id": d.get("person_id"),
                    "text": d.get("text", ""),
                    "permalink": f"https://www.facebook.com/groups/{group_id}/posts/{d['post_id']}/",
                    "created_at": normalize.parse_aria_date(d.get("created_label")),
                    "n_comments": None,
                    "n_reactions": None,
                    "raw_path": None,
                    "_author_name": d.get("author_name"),
                    "_author_url": None,
                }
        except Exception:
            pass

    page.close()

    # Rasmli/qisqa postlar hozircha o'tkazib yuboriladi (OCR keyingi versiyada)
    min_chars = c.get("min_text_chars", 25)
    fresh, skipped = [], 0
    for p in all_posts.values():
        if p["created_at"] is not None and p["created_at"] < since_ts:
            continue
        if len((p.get("text") or "").strip()) < min_chars:
            skipped += 1
            continue
        fresh.append(p)

    result = {
        "scrolls": scrolls,
        "seen": len(all_posts),
        "fresh": len(fresh),
        "skipped_short": skipped,
        "new": 0,
        "oldest": oldest,
        "dom_fallback": dom_used,
    }
    if dry_run:
        return result

    raw_path = db.save_raw(group_id, "feed", raw_batches) if raw_batches else None

    with db.connect() as conn:
        for p in fresh:
            if p["person_id"]:
                db.upsert_person(conn, p["person_id"], p.pop("_author_name", None),
                                 p.pop("_author_url", None))
            else:
                p.pop("_author_name", None)
                p.pop("_author_url", None)
            p["raw_path"] = raw_path
            if db.upsert_post(conn, p):
                result["new"] += 1
        db.set_health(conn, "feed_last_run", time.strftime("%Y-%m-%d %H:%M:%S"))
        db.set_health(conn, "feed_last_result", str(result))
    return result
