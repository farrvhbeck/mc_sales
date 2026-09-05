"""Post permalink'ini ochib comment'larni yig'adi.

Comment'lar muhim: xaridorlar ko'pincha aynan comment'da o'zini oshkor qiladi
("Yes I will buy it"). Feed'da ular ko'rinmaydi.
"""

from __future__ import annotations

import time

from playwright.sync_api import BrowserContext

from .. import db, normalize
from .browser import check_alive, pause
from .graphql_tap import GraphQLTap

# "View more comments" / "N replies" tugmalari
EXPANDERS = [
    "div[role='button']:has-text('View more comments')",
    "div[role='button']:has-text('View previous comments')",
    "div[role='button']:has-text('more comments')",
    "div[role='button']:has-text('View 1 reply')",
    "div[role='button']:has-text('replies')",
]


def fetch_comments(ctx: BrowserContext, post: dict, cfg: dict, dry_run: bool = False) -> int:
    c = cfg["collect"]
    page = ctx.new_page()
    tap = GraphQLTap()
    tap.attach(page)

    try:
        page.goto(post["permalink"], wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        check_alive(page)

        # Comment'larni yoyish
        for _ in range(3):
            clicked = False
            for sel in EXPANDERS:
                try:
                    el = page.locator(sel).first
                    if el.count() and el.is_visible():
                        el.click(timeout=3000)
                        clicked = True
                        time.sleep(2)
                except Exception:
                    continue
            if not clicked:
                break

        # "See more" -- kesilgan post matnini ochish
        try:
            more = page.locator("div[role='button']:has-text('See more')").first
            if more.count() and more.is_visible():
                more.click(timeout=3000)
                time.sleep(1)
        except Exception:
            pass

        pause(c["post_open_pause_seconds"])

        payloads = tap.drain()
        comments: dict[str, dict] = {}
        for payload in payloads:
            for cm in normalize.extract_comments(payload, post["post_id"]):
                comments.setdefault(cm["comment_id"], cm)

        # Post matnining to'liq varianti ham shu javoblarda bo'lishi mumkin
        full_text = None
        for payload in payloads:
            for p in normalize.extract_posts(payload, post["group_id"]):
                if p["post_id"] == post["post_id"] and len(p["text"]) > len(post.get("text") or ""):
                    full_text = p["text"]

        if dry_run:
            return len(comments)

        raw_path = db.save_raw(post["group_id"], f"post-{post['post_id']}", payloads)
        n_new = 0
        with db.connect() as conn:
            if full_text:
                conn.execute("UPDATE posts SET text = ? WHERE post_id = ?",
                             (full_text, post["post_id"]))
            for cm in comments.values():
                db.upsert_person(conn, cm["person_id"], cm.pop("_author_name", None),
                                 cm.pop("_author_url", None))
                if db.upsert_comment(conn, cm):
                    n_new += 1
            conn.execute(
                "UPDATE posts SET comments_fetched_at = ?, raw_path = COALESCE(raw_path, ?) "
                "WHERE post_id = ?",
                (time.time(), raw_path, post["post_id"]),
            )
        return n_new
    finally:
        page.close()


def pending_posts(limit: int, max_age_hours: float = 72.0) -> list[dict]:
    """Comment'i hali olinmagan yoki eskirgan postlar.

    Yangi postlarga comment tez qo'shiladi -- shuning uchun 72 soatdan yosh postlar
    qayta tekshiriladi.
    """
    cutoff = time.time() - max_age_hours * 3600
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT post_id, group_id, permalink, text, created_at, comments_fetched_at
               FROM posts
               WHERE comments_fetched_at IS NULL
                  OR (created_at > ? AND comments_fetched_at < ?)
               ORDER BY COALESCE(created_at, first_seen_at) DESC
               LIMIT ?""",
            (cutoff, time.time() - 6 * 3600, limit),
        ).fetchall()
    return [dict(r) for r in rows]
