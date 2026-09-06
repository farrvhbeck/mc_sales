"""Localhost dashboard."""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import db, runs
from ..enrich import fmcsa

BASE = Path(__file__).parent
app = FastAPI(title="MC Lead Engine")
tpl = Jinja2Templates(directory=str(BASE / "templates"))

LEAD_SELECT = """
SELECT l.*, pe.name AS person_name, pe.profile_url, pe.is_suspected_reseller,
       pe.n_sell, pe.n_buy,
       COALESCE(p.text, c.text)                                     AS text,
       COALESCE(p.permalink, sp.permalink)                          AS permalink,
       COALESCE(p.created_at, c.created_at,
                p.first_seen_at, c.first_seen_at)                   AS ts
FROM leads l
LEFT JOIN people   pe ON pe.person_id = l.person_id
LEFT JOIN posts    p  ON p.post_id    = l.source_id AND l.source_type = 'post'
LEFT JOIN comments c  ON c.comment_id = l.source_id AND l.source_type = 'comment'
LEFT JOIN posts    sp ON sp.post_id   = l.source_post_id
"""

STATUSES = ["new", "contacted", "qualified", "matched", "closed", "duplicate", "junk"]


def _ago(ts: float | None) -> str:
    """Postning yoshi. To'liq so'z bilan -- "15k" pul summasiga o'xshab ketardi."""
    if not ts:
        return "—"
    h = (time.time() - ts) / 3600
    if h < 1:
        return f"{max(1, int(h * 60))} daq"
    if h < 48:
        return f"{int(h)} soat"
    return f"{int(h / 24)} kun"


tpl.env.filters["ago"] = _ago
tpl.env.filters["money"] = lambda v: f"${v:,}" if v else "—"
tpl.env.filters["age"] = lambda y: (
    "—" if y is None else (f"{round(y * 12)} oy" if y < 1 else f"{round(y, 1)} yil"))
tpl.env.filters["isnew"] = lambda ts: bool(ts) and (time.time() - ts) < 7200
tpl.env.filters["dur"] = lambda s: (f"{s:.0f}s" if s and s < 90 else
                                    (f"{s/60:.0f}d" if s else "—"))
# Sarlavhadagi holat chizig'i har sahifada ko'rinadi
tpl.env.filters["localtime"] = lambda ts: (
    time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "—")
tpl.env.globals["run_status"] = runs.status
tpl.env.globals["STEP_LABELS"] = runs.STEP_LABELS


def _rows(side: str, args: dict) -> list[dict]:
    where = ["l.side = ?"]
    params: list = [side]

    if args.get("min_score"):
        where.append("l.score >= ?")
        params.append(int(args["min_score"]))
    if args.get("state"):
        where.append("(UPPER(COALESCE(l.state, l.buyer_wants_state)) = ?)")
        params.append(args["state"].upper())
    if args.get("status"):
        where.append("l.status = ?")
        params.append(args["status"])
    else:
        where.append("l.status NOT IN ('junk', 'duplicate')")
    if args.get("fresh"):
        where.append("COALESCE(p.created_at, c.created_at, p.first_seen_at, c.first_seen_at) > ?")
        params.append(time.time() - 24 * 3600)
    if args.get("has_contact"):
        where.append("l.contact_value IS NOT NULL")
    if args.get("fit"):
        where.append("l.fit_verdict = ?")
        params.append(args["fit"])
    if args.get("q"):
        where.append("(COALESCE(p.text, c.text) LIKE ? OR pe.name LIKE ?)")
        params += [f"%{args['q']}%", f"%{args['q']}%"]

    # Talabga mos leadlar har doim tepada: sherik "MOS" ni ko'rib pastga
    # tushishiga to'g'ri kelmasin.
    fit_rank = ("CASE l.fit_verdict WHEN 'pass' THEN 0 WHEN 'ask' THEN 1 "
                "WHEN 'fail' THEN 3 ELSE 2 END")
    order = {"fresh": "ts DESC", "price": "l.price_usd DESC"}.get(
        args.get("sort", ""), f"{fit_rank}, l.score DESC"
    )
    sql = f"{LEAD_SELECT} WHERE {' AND '.join(where)} ORDER BY {order} NULLS LAST LIMIT 300"

    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params)]

    for r in rows:
        r["breakdown"] = json.loads(r["score_breakdown"]) if r["score_breakdown"] else []
        r["fit_reasons"] = json.loads(r["fit_reasons"]) if r["fit_reasons"] else []
        r["fit_missing"] = json.loads(r["fit_missing"]) if r["fit_missing"] else []
        r["fmcsa"] = fmcsa.for_lead(r) if r["side"] == "SELL" else None
    return rows


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    with db.connect() as conn:
        day_ago = time.time() - 86400
        q = lambda s, p=(): conn.execute(s, p).fetchone()[0]
        kpi = {
            "new_24h": q("SELECT COUNT(*) FROM leads WHERE created_at > ?", (day_ago,)),
            "sellers": q("SELECT COUNT(*) FROM leads WHERE side='SELL' AND status='new'"),
            "buyers": q("SELECT COUNT(*) FROM leads WHERE side='BUY' AND status='new'"),
            "hot": q("SELECT COUNT(*) FROM leads WHERE status='new' AND score >= 65"),
            "matches": q("SELECT COUNT(*) FROM matches WHERE status='new'"),
        }
        tokens = q(
            "SELECT COALESCE(SUM(prompt_tokens+completion_tokens),0) FROM llm_usage WHERE day=?",
            (time.strftime("%Y-%m-%d"),),
        )
        top = [dict(r) for r in conn.execute(
            LEAD_SELECT + " WHERE l.status = 'new' ORDER BY l.score DESC LIMIT 12"
        )]
        health = db.get_health(conn)

    cfg = db.load_config()
    from ..classify.groq_client import _load_keys
    n_keys = max(1, len(_load_keys()))
    for r in top:
        r["fmcsa"] = fmcsa.for_lead(r) if r["side"] == "SELL" else None

    return tpl.TemplateResponse(request, "home.html", {
        "kpi": kpi, "top": top, "health": health,
        "tokens": tokens, "budget": cfg["llm"]["daily_token_budget"] * n_keys,
        "n_keys": n_keys, "page": "home",
    })


def _status_counts(side: str) -> dict[str, int]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) n FROM leads WHERE side = ? GROUP BY status", (side,)
        ).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    counts["_all"] = sum(n for s, n in counts.items() if s not in ("junk", "duplicate"))
    return counts


@app.get("/sellers", response_class=HTMLResponse)
def sellers(request: Request):
    args = dict(request.query_params)
    return tpl.TemplateResponse(request, "leads.html", {
        "rows": _rows("SELL", args), "side": "SELL", "counts": _status_counts("SELL"),
        "args": args, "statuses": STATUSES, "page": "sellers",
    })


@app.get("/buyers", response_class=HTMLResponse)
def buyers(request: Request):
    args = dict(request.query_params)
    return tpl.TemplateResponse(request, "leads.html", {
        "rows": _rows("BUY", args), "side": "BUY", "counts": _status_counts("BUY"),
        "args": args, "statuses": STATUSES, "page": "buyers",
    })


@app.get("/matches", response_class=HTMLResponse)
def matches(request: Request):
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute("""
            SELECT m.*,
                   bp.name AS buyer_name, sp.name AS seller_name,
                   b.score AS buyer_score, s.score AS seller_score,
                   s.mc_number, s.dot_number, s.price_usd,
                   b.buyer_budget_usd, b.buyer_wants_state,
                   bsp.permalink AS buyer_link, ssp.permalink AS seller_link
            FROM matches m
            JOIN leads b ON b.lead_id = m.buyer_lead_id
            JOIN leads s ON s.lead_id = m.seller_lead_id
            LEFT JOIN people bp ON bp.person_id = b.person_id
            LEFT JOIN people sp ON sp.person_id = s.person_id
            LEFT JOIN posts bsp ON bsp.post_id = b.source_post_id
            LEFT JOIN posts ssp ON ssp.post_id = s.source_post_id
            WHERE m.status != 'junk'
            ORDER BY m.score DESC LIMIT 200
        """)]
    for r in rows:
        r["reasons"] = json.loads(r["reasons"]) if r["reasons"] else []
    return tpl.TemplateResponse(request, "matches.html", {
        "rows": rows, "page": "matches",
    })


@app.get("/people/{person_id}", response_class=HTMLResponse)
def person(request: Request, person_id: str):
    with db.connect() as conn:
        p = conn.execute("SELECT * FROM people WHERE person_id = ?", (person_id,)).fetchone()
        leads = [dict(r) for r in conn.execute(
            LEAD_SELECT + " WHERE l.person_id = ? ORDER BY ts DESC", (person_id,)
        )]
    return tpl.TemplateResponse(request, "person.html", {
        "p": dict(p) if p else None, "leads": leads, "page": "",
    })


@app.get("/health", response_class=HTMLResponse)
def health(request: Request):
    with db.connect() as conn:
        h = db.get_health(conn)
        usage = [dict(r) for r in conn.execute(
            "SELECT * FROM llm_usage ORDER BY day DESC, key_label, model LIMIT 30"
        )]
        stages = [dict(r) for r in conn.execute(
            "SELECT stage, COUNT(*) n FROM classify_state GROUP BY stage"
        )]
        counts = {
            k: conn.execute(f"SELECT COUNT(*) FROM {k}").fetchone()[0]
            for k in ("posts", "comments", "people", "leads", "matches")
        }
        errors = [dict(r) for r in conn.execute(
            "SELECT source_id, error FROM classify_state WHERE stage='error' LIMIT 20"
        )]
    return tpl.TemplateResponse(request, "health.html", {
        "h": h, "usage": usage, "stages": stages, "runs": runs.latest(25),
        "counts": counts, "errors": errors, "page": "health",
    })


@app.post("/lead/{lead_id}/status")
def set_status(lead_id: int, status: str = Form(...), back: str = Form("/")):
    with db.connect() as conn:
        conn.execute("UPDATE leads SET status = ?, updated_at = ? WHERE lead_id = ?",
                     (status, time.time(), lead_id))
    return RedirectResponse(back, status_code=303)


@app.post("/lead/{lead_id}/note")
def set_note(lead_id: int, note: str = Form(""), back: str = Form("/")):
    with db.connect() as conn:
        conn.execute("UPDATE leads SET note = ? WHERE lead_id = ?", (note, lead_id))
    return RedirectResponse(back, status_code=303)


@app.post("/match/{match_id}/status")
def set_match_status(match_id: int, status: str = Form(...), back: str = Form("/matches")):
    with db.connect() as conn:
        conn.execute("UPDATE matches SET status = ? WHERE match_id = ?", (status, match_id))
    return RedirectResponse(back, status_code=303)
