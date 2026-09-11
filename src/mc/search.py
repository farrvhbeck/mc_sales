"""Lead qidiruvi -- SQLite FTS5.

Oldin qidiruv `text LIKE '%q%'` edi: tartiblash yo'q, rasm matni qamralmagan,
va telefon formati boshqacha yozilsa umuman topilmasdi. Endi:

  * bm25 tartibi -- eng mos lead tepada;
  * rasm matni (OCR) ham indeksda;
  * `numbers` ustuni faqat raqamlardan iborat, shuning uchun "(305) 555-0101",
    "305-555-0101" va "3055550101" bir xil natija beradi.

Indeks `score` bosqichidan keyin to'liq qayta quriladi. Lead soni o'n minglab
bo'lganda ham bu bir soniyadan kam -- inkremental qilishga arzimaydi.
"""

from __future__ import annotations

import re

from . import db

DIGITS_RE = re.compile(r"\d+")
# Qidiruv so'zi raqamga o'xshasa (telefon, MC, DOT) -- `numbers` ustunidan qidiramiz
NUMERIC_Q = re.compile(r"^[\d\s()+.\-]{4,}$")


def digits(*values) -> str:
    """Berilgan qiymatlardan raqam bo'laklarini ajratadi."""
    out = []
    for v in values:
        if not v:
            continue
        out.extend(DIGITS_RE.findall(str(v)))
    # Telefonning oxirgi 10 raqami alohida token -- mamlakat kodi bilan yozilgani
    # kodsiz yozilganini topsin.
    extra = []
    for tok in out:
        if len(tok) > 10:
            extra.append(tok[-10:])
    return " ".join(out + extra)


def reindex() -> dict:
    """FTS indeksini noldan quradi."""
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT l.lead_id,
                      COALESCE(p.text, c.text, '')  AS text,
                      COALESCE(p.ocr_text, '')      AS ocr_text,
                      COALESCE(pe.name, '')         AS name,
                      l.mc_number, l.dot_number, l.contact_value, l.state,
                      l.buyer_wants_state, l.price_usd, l.buyer_budget_usd
               FROM leads l
               LEFT JOIN people   pe ON pe.person_id  = l.person_id
               LEFT JOIN posts    p  ON p.post_id     = l.source_id AND l.source_type = 'post'
               LEFT JOIN comments c  ON c.comment_id  = l.source_id AND l.source_type = 'comment'"""
        ).fetchall()

        conn.execute("DELETE FROM search_index")
        conn.executemany(
            "INSERT INTO search_index (rowid, text, name, numbers) VALUES (?, ?, ?, ?)",
            [(
                r["lead_id"],
                " ".join(filter(None, [r["text"], r["ocr_text"],
                                       r["state"], r["buyer_wants_state"]])),
                r["name"],
                digits(r["mc_number"], r["dot_number"], r["contact_value"],
                       r["price_usd"], r["buyer_budget_usd"]),
            ) for r in rows],
        )
    return {"indexed": len(rows)}


def _escape(term: str) -> str:
    """FTS5 sintaksisi foydalanuvchi matnida portlamasin."""
    return '"' + term.replace('"', '""') + '"'


def to_match(q: str) -> str | None:
    """Foydalanuvchi so'rovini FTS5 MATCH ifodasiga aylantiradi.

    Raqamli so'rov -> `numbers` ustuni; aks holda hamma ustun bo'ylab, oxirgi
    so'zga prefiks (yozib tugatmagan bo'lishi mumkin).
    """
    q = (q or "").strip()
    if not q:
        return None
    if NUMERIC_Q.match(q):
        d = "".join(ch for ch in q if ch.isdigit())
        if len(d) >= 4:
            return f"numbers : {_escape(d[-10:] if len(d) > 10 else d)}*"
    terms = [t for t in re.split(r"\s+", q) if t]
    if not terms:
        return None
    parts = [_escape(t) for t in terms[:-1]]
    parts.append(_escape(terms[-1]) + "*")
    return " ".join(parts)


def ready() -> bool:
    with db.connect() as conn:
        return bool(conn.execute("SELECT 1 FROM search_index LIMIT 1").fetchone())
