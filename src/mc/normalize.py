"""Xom GraphQL JSON -> posts / comments / people.

FB javobining aniq shakli ogohlantirishsiz o'zgaradi, shuning uchun bu yerda qattiq
yo'l (`data.node.group_feed.edges[...]`) ishlatilmaydi. O'rniga butun daraxt bo'ylab
yuriladi va "post'ga o'xshash" / "comment'ga o'xshash" tugunlar signal bo'yicha
topiladi. Bu shakl o'zgarganda ham ishlashda davom etadi.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

DIGITS = re.compile(r"^\d{6,}$")


def walk(obj: Any) -> Iterator[dict]:
    """Daraxtdagi barcha dict'lar bo'ylab yuradi."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def _text(node: Any) -> str | None:
    """`{"text": "..."}` yoki to'g'ridan-to'g'ri satrni matnga aylantiradi."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        t = node.get("text")
        if isinstance(t, str):
            return t
    return None


def _actor(node: dict) -> tuple[str | None, str | None, str | None]:
    """(person_id, name, profile_url)"""
    for key in ("actors", "author", "owner", "comet_sections"):
        v = node.get(key)
        if key == "actors" and isinstance(v, list) and v:
            v = v[0]
        if isinstance(v, dict):
            pid = v.get("id") or v.get("actor_id")
            name = v.get("name")
            url = v.get("url") or v.get("profile_url")
            if pid and name:
                return str(pid), name, url
    return None, None, None


def _int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return None


def extract_posts(payload: Any, group_id: str) -> list[dict]:
    """Post'ga o'xshash tugunlarni chiqaradi.

    Signal: `creation_time` (unix) + matn + raqamli `post_id`.
    """
    out: dict[str, dict] = {}
    for node in walk(payload):
        created = _int(node.get("creation_time") or node.get("created_time"))
        if not created or created < 1_000_000_000:
            continue

        pid = node.get("post_id") or node.get("legacy_story_hideable_id") or node.get("id")
        pid = str(pid) if pid is not None else None
        if not pid or not DIGITS.match(pid):
            continue

        text = _text(node.get("message")) or _text(node.get("message_preferred_body"))
        if not text or not text.strip():
            continue

        person_id, name, url = _actor(node)

        fb = node.get("feedback") or {}
        n_comments = None
        n_reactions = None
        if isinstance(fb, dict):
            n_comments = _int(
                (fb.get("total_comment_count"))
                or ((fb.get("comment_rendering_instance") or {}) if isinstance(fb.get("comment_rendering_instance"), dict) else {}).get("count")
            )
            rc = fb.get("reaction_count") or fb.get("reactors")
            if isinstance(rc, dict):
                n_reactions = _int(rc.get("count"))

        out[pid] = {
            "post_id": pid,
            "group_id": group_id,
            "person_id": person_id,
            "text": text.strip(),
            "permalink": f"https://www.facebook.com/groups/{group_id}/posts/{pid}/",
            "created_at": float(created),
            "n_comments": n_comments,
            "n_reactions": n_reactions,
            "raw_path": None,
            "_author_name": name,
            "_author_url": url,
        }
    return list(out.values())


def extract_comments(payload: Any, post_id: str) -> list[dict]:
    """Comment'ga o'xshash tugunlar.

    Signal: `body.text` + `created_time` + author.
    """
    out: dict[str, dict] = {}
    for node in walk(payload):
        body = node.get("body")
        text = _text(body)
        if not text or not text.strip():
            continue
        created = _int(node.get("created_time") or node.get("creation_time"))
        if not created or created < 1_000_000_000:
            continue

        cid = node.get("legacy_fbid") or node.get("id")
        if cid is None:
            continue
        cid = str(cid)

        person_id, name, url = _actor(node)
        if not person_id:
            continue

        out[cid] = {
            "comment_id": cid,
            "post_id": post_id,
            "person_id": person_id,
            "text": text.strip(),
            "created_at": float(created),
            "_author_name": name,
            "_author_url": url,
        }
    return list(out.values())


# --- DOM fallback --------------------------------------------------------

# Selektorlar 2026-09-06 da real FB guruhida brauzerda tekshirilgan.
# `div[data-ad-rendering-role="story_message"]` -- post matni (faqat matn, chrome'siz).
# Vaqt belgisi ekranda CSS bilan aralashtirilgan, lekin havolaning `aria-label` ida
# toza sana turadi: "Saturday, September 5, 2026 at 1:34 AM".
DOM_POSTS_JS = r"""
() => {
  const gid = location.pathname.match(/groups\/(\d+)/)?.[1] || null;
  const out = [];
  for (const msg of document.querySelectorAll('div[data-ad-rendering-role="story_message"]')) {
    const txt = (msg.innerText || '').trim();
    if (!txt) continue;

    // Postning o'zigacha ko'tarilamiz
    let story = msg;
    for (let i = 0; i < 12 && story; i++) {
      if (story.querySelector && story.querySelector('a[href*="/posts/"]')) break;
      story = story.parentElement;
    }
    if (!story) continue;

    const html = story.innerHTML;
    const pid = (html.match(/groups\/\d+\/posts\/(\d+)/) || [])[1];
    if (!pid) continue;

    let created = null;
    for (const a of story.querySelectorAll('a[href*="/posts/"]')) {
      const al = a.getAttribute('aria-label');
      if (al && /\d{4}/.test(al)) { created = al; break; }
    }
    const uid = (html.match(/\/user\/(\d+)/) || [])[1] || null;
    let name = null;
    const h = story.querySelector('h3, h4, strong');
    if (h) name = (h.innerText || '').trim().split('\n')[0] || null;

    out.push({post_id: pid, group_id: gid, text: txt,
              person_id: uid, author_name: name, created_label: created});
  }
  return out;
}
"""

DOM_COMMENTS_JS = r"""
() => {
  const out = [];
  for (const el of document.querySelectorAll('div[role="article"]')) {
    const aria = el.getAttribute('aria-label') || '';
    const m = aria.match(/^Comment by (.+?) (\d+ \w+ ago|[\w ]+)$/);
    if (!m) continue;
    const html = el.innerHTML;
    const uid = (html.match(/\/user\/(\d+)/) || [])[1] || null;
    out.push({author: m[1], rel_time: m[2], person_id: uid,
              text: (el.innerText || '').trim()});
  }
  return out;
}
"""


def parse_aria_date(label: str | None) -> float | None:
    """FB'ning `aria-label` sanasi -> unix. Vizual timestamp aralashtirilgan bo'lsa ham
    bu havolada toza turadi: "Saturday, September 5, 2026 at 1:34 AM".
    """
    if not label:
        return None
    from datetime import datetime

    txt = re.sub(r"^\w+day,\s*", "", label.strip())
    for fmt in ("%B %d, %Y at %I:%M %p", "%d %B %Y at %I:%M %p",
                "%B %d, %Y", "%B %d at %I:%M %p"):
        try:
            dt = datetime.strptime(txt, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=datetime.now().year)
            return dt.timestamp()
        except ValueError:
            continue
    return None


def parse_relative_time(rel: str, now: float) -> float | None:
    """"2 hours ago" / "5h" / "1 day ago" -> unix timestamp."""
    rel = rel.strip().lower()
    m = re.match(r"(\d+)\s*(second|minute|hour|day|week|month|year|s|m|h|d|w|y)", rel)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    mult = {
        "second": 1, "s": 1,
        "minute": 60, "m": 60,
        "hour": 3600, "h": 3600,
        "day": 86400, "d": 86400,
        "week": 604800, "w": 604800,
        "month": 2592000,
        "year": 31536000, "y": 31536000,
    }.get(unit)
    return now - n * mult if mult else None
