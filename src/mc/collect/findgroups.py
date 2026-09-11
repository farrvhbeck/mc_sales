"""FB'ning o'z qidiruvidan yangi MC/DOT savdo guruhlarini topadi.

Manba ro'yxati eskiradi: guruhlar o'ladi, yangilari ochiladi. Buni qo'lda
kuzatib bo'lmaydi, shuning uchun qidiruvni kod qiladi -- FB'ning guruh
qidiruvi bo'ylab yurib, GraphQL javobidan guruh nomi va id sini oladi,
keyin har birini ochib **faolligini** o'lchaydi.

Faollik muhim: 92 a'zoli, oxirgi posti besh oy oldin bo'lgan guruh har siklda
brauzer vaqtini yeydi va evaziga hech narsa bermaydi.
"""

from __future__ import annotations

import re
import time

from .. import db, groups as G, normalize
from .browser import human_scroll
from .graphql_tap import GraphQLTap

QUERIES = [
    "mc authority for sale", "mc dot for sale", "trucking authority for sale",
    "mc number for sale", "buy sell mc", "mc dot buy sell", "aged mc",
]

# Nomi mavzuga aloqador bo'lmagan guruhlarni tashlaymiz -- qidiruv ish e'lonlari
# va butunlay begona guruhlarni ham qaytaradi.
RELEVANT = re.compile(r"\bmc\b|\bdot\b|authority|trucking|truck|freight|carrier", re.I)


def search(ctx, queries: list[str] | None = None) -> dict[str, dict]:
    """{group_id: {"id", "name"}} -- qidiruv topgan guruhlar."""
    found: dict[str, dict] = {}
    for q in queries or QUERIES:
        page = ctx.new_page()
        tap = GraphQLTap()
        tap.attach(page)
        try:
            page.goto("https://www.facebook.com/search/groups/?q=" + q.replace(" ", "%20"),
                      wait_until="domcontentloaded", timeout=60000)
            time.sleep(6)
            for _ in range(3):
                human_scroll(page, [1.5, 3.0])
            for payload in tap.drain():
                for node in normalize.walk(payload):
                    if node.get("__typename") != "Group":
                        continue
                    gid, name = node.get("id"), node.get("name")
                    if gid and str(gid).isdigit() and name:
                        found.setdefault(str(gid), {"id": str(gid), "name": name})
        except Exception:
            continue
        finally:
            page.close()
    return found


def probe(ctx, gid: str, name: str) -> dict:
    """Guruhni ochib a'zo soni, ko'rinadigan post va oxirgi post vaqtini oladi."""
    page = ctx.new_page()
    tap = GraphQLTap()
    tap.attach(page)
    row = {"id": gid, "name": name, "members": None, "posts": 0,
           "newest": None, "access": "ochiq"}
    try:
        page.goto(f"https://www.facebook.com/groups/{gid}/?sorting_setting=CHRONOLOGICAL",
                  wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        body = page.inner_text("body")[:6000]
        if "join group" in body.lower() and "write something" not in body.lower():
            row["access"] = "a'zo emas"
        m = re.search(r"([\d.,]+[KM]?)\s+members", body)
        if m:
            row["members"] = m.group(1)
        for _ in range(2):
            human_scroll(page, [1.5, 3.0])
        posts = {}
        for payload in tap.drain():
            for p in normalize.extract_posts(payload, gid):
                posts[p["post_id"]] = p
        row["posts"] = len(posts)
        ts = [p["created_at"] for p in posts.values() if p["created_at"]]
        row["newest"] = max(ts) if ts else None
    except Exception as e:
        row["access"] = f"xato: {str(e)[:40]}"
    finally:
        page.close()
    return row


def run(ctx, max_age_days: int = 45, probe_limit: int = 25) -> list[dict]:
    """Yangi, mavzuga oid va FAOL guruhlarni qaytaradi."""
    known = {g["group_id"] for g in G.all_groups()}
    found = search(ctx)
    cand = [g for g in found.values()
            if g["id"] not in known and RELEVANT.search(g["name"])]

    out = []
    cutoff = time.time() - max_age_days * 86400
    for g in cand[:probe_limit]:
        row = probe(ctx, g["id"], g["name"])
        row["fresh"] = bool(row["newest"] and row["newest"] > cutoff)
        out.append(row)
    out.sort(key=lambda r: -(r["newest"] or 0))

    with db.connect() as conn:
        db.set_health(conn, "findgroups_last_run", time.strftime("%Y-%m-%d %H:%M"))
        db.set_health(conn, "findgroups_last_result",
                      f"{len(found)} topildi, {len(cand)} yangi, "
                      f"{sum(1 for r in out if r['fresh'])} faol")
    return out
