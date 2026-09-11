"""Localhost dashboard."""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db, fraud, groups as G, runs, search
from ..enrich import fmcsa
from ..collect import discover
from ..ocr import run as ocr_run
from . import auth

BASE = Path(__file__).parent
app = FastAPI(title="MC Lead Engine")
tpl = Jinja2Templates(directory=str(BASE / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
# Parol faqat DASHBOARD_PASSWORD berilganda talab qilinadi.
app.add_middleware(auth.AuthMiddleware)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if not auth.password():
        return RedirectResponse("/", status_code=303)
    return auth.login_page(request.query_params.get("next", "/"))


@app.post("/login")
def login_submit(password: str = Form(""), next: str = Form("/")):
    secret = auth.password()
    if not secret:
        return RedirectResponse("/", status_code=303)
    if secrets.compare_digest(password, secret):
        return auth.sign_in(secret, next)
    return auth.login_page(next, error=True)


@app.post("/logout")
def logout():
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie(auth.COOKIE)
    return r

LEAD_SELECT = """
SELECT l.*, pe.name AS person_name, pe.profile_url, pe.is_suspected_reseller,
       pe.n_sell, pe.n_buy,
       p.ocr_text, p.ocr_conf, p.media_state,
       COALESCE(p.n_media, 0)                                       AS n_media,
       COALESCE(p.text, c.text)                                     AS text,
       COALESCE(p.permalink, sp.permalink)                          AS permalink,
       COALESCE(p.created_at, c.created_at,
                p.first_seen_at, c.first_seen_at)                   AS ts,
       COALESCE(p.group_id, sp.group_id)                            AS group_id
FROM leads l
LEFT JOIN people   pe ON pe.person_id = l.person_id
LEFT JOIN posts    p  ON p.post_id    = l.source_id AND l.source_type = 'post'
LEFT JOIN comments c  ON c.comment_id = l.source_id AND l.source_type = 'comment'
LEFT JOIN posts    sp ON sp.post_id   = l.source_post_id
"""

STATUSES = ["new", "contacted", "qualified", "matched", "closed", "duplicate", "junk"]

# Ko'rinadigan nomlar — status kodini foydalanuvchi tili bilan ajratamiz
STATUS_LABELS = {
    "": "All", "new": "New", "contacted": "Contacted", "qualified": "Qualified",
    "matched": "Matched", "closed": "Closed", "duplicate": "Duplicates", "junk": "Discarded",
}

# Vaqt oraliqlari (soatda). None = ixtiyoriy sana
RANGES = [("", "Any time", None), ("6h", "Last 6 hours", 6), ("24h", "Last 24 hours", 24),
          ("3d", "Last 3 days", 72), ("7d", "Last 7 days", 168),
          ("30d", "Last 30 days", 720), ("custom", "Custom range…", None)]


def _ago(ts: float | None) -> str:
    """How old the post is. Spelled out -- "15k" read as a dollar amount."""
    if not ts:
        return "—"
    h = (time.time() - ts) / 3600
    if h < 1:
        return f"{max(1, int(h * 60))} min"
    if h < 48:
        return f"{int(h)} h"
    d = int(h / 24)
    return f"{d} day" if d == 1 else f"{d} days"


tpl.env.filters["ago"] = _ago
tpl.env.filters["money"] = lambda v: f"${v:,}" if v else "—"
tpl.env.filters["age"] = lambda y: (
    "—" if y is None else (f"{round(y * 12)} mo" if y < 1 else f"{round(y, 1)} yr"))
tpl.env.filters["isnew"] = lambda ts: bool(ts) and (time.time() - ts) < 7200
tpl.env.filters["dur"] = lambda s: (f"{s:.0f}s" if s and s < 90 else
                                    (f"{s/60:.0f}m" if s else "—"))
# Sarlavhadagi holat chizig'i har sahifada ko'rinadi
tpl.env.filters["localtime"] = lambda ts: (
    time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "—")
tpl.env.globals["run_status"] = runs.status
tpl.env.globals["STEP_LABELS"] = runs.STEP_LABELS


def _parse_date(v: str | None) -> float | None:
    if not v:
        return None
    try:
        return time.mktime(time.strptime(v, "%Y-%m-%d"))
    except ValueError:
        return None


def _rows(side: str | None, args: dict) -> list[dict]:
    """Lead qatorlari. `side` None bo'lsa ikkala tomon ham chiqadi.

    Ikkala tomonni birga ko'rsatish kerak, chunki "kuchli leadlar" va
    "yuborishga tayyor" kabi savollar tomonga bog'liq emas.
    """
    side = side or (args.get("side") or "").upper() or None
    where = []
    params: list = []
    if side in ("SELL", "BUY"):
        where.append("l.side = ?")
        params.append(side)
    ts_expr = "COALESCE(p.created_at, c.created_at, p.first_seen_at, c.first_seen_at)"

    if args.get("min_score"):
        where.append("l.score >= ?")
        params.append(int(args["min_score"]))
    if args.get("state"):
        where.append("UPPER(COALESCE(l.state, l.buyer_wants_state)) = ?")
        params.append(args["state"].upper())
    if args.get("status"):
        where.append("l.status = ?")
        params.append(args["status"])
    else:
        where.append("l.status NOT IN ('junk', 'duplicate')")

    # Vaqt oralig'i
    rng = args.get("range", "")
    hours = dict((k, h) for k, _, h in RANGES).get(rng)
    if hours:
        where.append(f"{ts_expr} > ?")
        params.append(time.time() - hours * 3600)
    elif rng == "custom":
        if (frm := _parse_date(args.get("from"))):
            where.append(f"{ts_expr} >= ?")
            params.append(frm)
        if (to := _parse_date(args.get("to"))):
            where.append(f"{ts_expr} < ?")
            params.append(to + 86400)      # kunning oxirigacha

    if args.get("has_contact"):
        where.append("l.contact_value IS NOT NULL")
    if args.get("fit"):
        where.append("l.fit_verdict = ?")
        params.append(args["fit"])
    if args.get("group"):
        where.append("p.group_id = ?")
        params.append(args["group"])
    if args.get("amazon"):
        if args["amazon"] == "any":
            where.append("(l.amazon_status IS NOT NULL OR l.buyer_needs_amazon = 1)")
        else:
            where.append("l.amazon_status = ?")
            params.append(args["amazon"])
    if args.get("package"):
        where.append("(l.includes_bank = 1 AND l.includes_email = 1 AND l.includes_phone = 1)")

    # Aralash ro'yxatda sotuvchining narxi va xaridorning byudjeti bitta ustun
    price_col = ("l.price_usd" if side == "SELL" else
                 "l.buyer_budget_usd" if side == "BUY" else
                 "COALESCE(l.price_usd, l.buyer_budget_usd)")
    if args.get("price_min"):
        where.append(f"{price_col} >= ?")
        params.append(int(args["price_min"]))
    if args.get("price_max"):
        where.append(f"{price_col} <= ?")
        params.append(int(args["price_max"]))
    if args.get("unsent"):
        # "Yuborishga tayyor" -- Telegram'ga hali ketmagan yangi leadlar
        where.append("l.status = 'new' AND l.lead_id NOT IN (SELECT lead_id FROM notified)")
    if args.get("min_age"):
        col = ("l.authority_age_years" if side == "SELL" else
               "l.buyer_min_age_years" if side == "BUY" else
               "COALESCE(l.authority_age_years, l.buyer_min_age_years)")
        where.append(f"{col} >= ?")
        params.append(float(args["min_age"]) / 12)
    if args.get("has_image"):
        where.append("COALESCE(p.n_media, 0) > 0")

    # Qidiruv: FTS5 (bm25 tartibi, rasm matni ham qamraladi). Indeks hali
    # qurilmagan bo'lsa eski LIKE ga tushamiz -- qidiruv hech qachon o'lmaydi.
    match_expr = search.to_match(args["q"]) if args.get("q") else None
    fts = bool(match_expr) and search.ready()
    if args.get("q") and not fts:
        where.append("(COALESCE(p.text, c.text) LIKE ? OR p.ocr_text LIKE ? OR pe.name LIKE ?)")
        params += [f"%{args['q']}%"] * 2 + [f"%{args['q']}%"]

    # Leads that meet the spec always sort above ones that only might.
    fit_rank = ("CASE l.fit_verdict WHEN 'pass' THEN 0 WHEN 'ask' THEN 1 "
                "WHEN 'fail' THEN 3 ELSE 2 END")
    default_order = f"{fit_rank}, l.score DESC NULLS LAST"
    if fts:
        # Qidirilganda mos kelish darajasi birinchi, keyin ball
        default_order = "fts.rank, l.score DESC NULLS LAST"
    order = {
        "fresh": f"{ts_expr} DESC",
        "oldest": f"{ts_expr} ASC",
        "price": f"{price_col} DESC NULLS LAST",
        "price_asc": f"{price_col} ASC NULLS LAST",
        "age": "l.authority_age_years DESC NULLS LAST",
    }.get(args.get("sort", ""), default_order)

    join = ""
    head: list = []
    if fts:
        join = ("\nJOIN (SELECT rowid, bm25(search_index) AS rank FROM search_index "
                "WHERE search_index MATCH ?) fts ON fts.rowid = l.lead_id")
        head.append(match_expr)

    clause = " AND ".join(where) if where else "1=1"
    sql = f"{LEAD_SELECT}{join} WHERE {clause} ORDER BY {order} LIMIT 300"

    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, head + params)]

    for r in rows:
        r["breakdown"] = json.loads(r["score_breakdown"]) if r["score_breakdown"] else []
        r["fit_reasons"] = json.loads(r["fit_reasons"]) if r["fit_reasons"] else []
        r["fit_missing"] = json.loads(r["fit_missing"]) if r["fit_missing"] else []
        r["fmcsa"] = fmcsa.for_lead(r) if r["side"] == "SELL" else None
        r["flags"] = json.loads(r["flags"]) if r.get("flags") else []
    _attach_media(rows)
    alerts = fmcsa.alerts_for([r["lead_id"] for r in rows])
    for r in rows:
        r["alerts"] = alerts.get(r["lead_id"], [])
    return rows


def _media_of(post_ids: list[str]) -> dict[str, list[dict]]:
    """post_id -> yuklab olingan rasmlar. Bitta so'rov, 300 ta emas."""
    ids = [i for i in dict.fromkeys(post_ids) if i]
    if not ids:
        return {}
    out: dict[str, list[dict]] = {}
    with db.connect() as conn:
        # SQLite o'zgaruvchilar chegarasiga urilmaslik uchun bo'laklab
        for start in range(0, len(ids), 400):
            chunk = ids[start : start + 400]
            q = ",".join("?" * len(chunk))
            for r in conn.execute(
                f"""SELECT post_id, media_id, width, height, ocr_conf FROM post_media
                    WHERE post_id IN ({q}) AND local_path IS NOT NULL
                    ORDER BY created_at""", chunk):
                out.setdefault(r["post_id"], []).append(dict(r))
    return out


def _attach_media(rows: list[dict]) -> None:
    by_post = _media_of([r["source_post_id"] or r["source_id"] for r in rows])
    for r in rows:
        r["media"] = by_post.get(r["source_post_id"] or r["source_id"], [])


def _group_names() -> dict[str, str]:
    """Filtr uchun guruh nomlari -- endi bazadan, chunki ular UI dan boshqariladi."""
    return G.names()


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
        r["flags"] = json.loads(r["flags"]) if r.get("flags") else []
    _attach_media(top)
    alerts = fmcsa.alerts_for([r["lead_id"] for r in top])
    for r in top:
        r["alerts"] = alerts.get(r["lead_id"], [])

    return tpl.TemplateResponse(request, "home.html", {
        "kpi": kpi, "top": top, "health": health,
        "tokens": tokens, "budget": cfg["llm"]["daily_token_budget"] * n_keys,
        "n_keys": n_keys, "page": "home",
    })


@app.get("/api/status")
def api_status():
    """Jonli widget uchun. Har necha soniyada so'raladi, shuning uchun yengil."""
    rs = runs.status()
    last = rs.get("last") or {}

    with db.connect() as conn:
        q = lambda s, p=(): conn.execute(s, p).fetchone()[0]
        queue = {
            "classify": q("SELECT COUNT(*) FROM classify_state "
                          "WHERE stage IN ('pending', 'triaged')"),
            "score": q("SELECT COUNT(*) FROM leads WHERE score IS NULL"),
            "enrich": q("SELECT COUNT(*) FROM leads WHERE score IS NOT NULL "
                        "AND (mc_number IS NOT NULL OR dot_number IS NOT NULL)"),
            "notify": q("SELECT COUNT(*) FROM leads WHERE status = 'new' AND score >= 60 "
                        "AND lead_id NOT IN (SELECT lead_id FROM notified)"),
        }
        totals = {
            "posts": q("SELECT COUNT(*) FROM posts"),
            "leads": q("SELECT COUNT(*) FROM leads"),
            "matches": q("SELECT COUNT(*) FROM matches"),
        }
        tokens = q("SELECT COALESCE(SUM(prompt_tokens+completion_tokens),0) "
                   "FROM llm_usage WHERE day = ?", (time.strftime("%Y-%m-%d"),))
        # Har bir bosqichning ENG OXIRGI natijasi -- oxirgi yurishniki emas.
        # Qo'lda bitta buyruq (masalan `mc match`) yurgizilsa, o'sha yurishda
        # faqat bitta bosqich bo'ladi va qolganlari "hech qachon ishlamagan"
        # bo'lib ko'rinardi, holbuki ular oldinroq muvaffaqiyatli o'tgan.
        steps_done = {}
        for r in conn.execute(
            """SELECT rs.step, rs.status, rs.finished_at, rs.error
               FROM run_steps rs
               WHERE rs.step_id = (SELECT MAX(step_id) FROM run_steps
                                   WHERE step = rs.step)"""
        ):
            steps_done[r["step"]] = {
                "status": r["status"],
                "at": r["finished_at"],
                "error": r["error"],
            }

    cfg = db.load_config()
    from ..classify.groq_client import _load_keys
    budget = cfg["llm"]["daily_token_budget"] * max(1, len(_load_keys()))

    now = time.time()
    steps = []
    for name in runs.STEP_ORDER:
        info = steps_done.get(name) or {}
        at = info.get("at")
        steps.append({
            "name": name,
            "label": runs.STEP_LABELS.get(name, name),
            "status": info.get("status", "never"),
            "age_seconds": (now - at) if at else None,
            "error": info.get("error"),
        })

    return {
        "state": rs["state"], "label": rs["label"], "detail": rs.get("detail") or "",
        "step": rs.get("step") or sum(1 for s in steps if s["status"] != "pending"),
        "step_total": runs.STEP_TOTAL,
        "steps": steps,
        "age_seconds": rs.get("age"),
        "queue": queue, "totals": totals,
        "tokens": tokens, "budget": budget,
    }


def _status_counts(side: str | None) -> dict[str, int]:
    with db.connect() as conn:
        if side:
            rows = conn.execute(
                "SELECT status, COUNT(*) n FROM leads WHERE side = ? GROUP BY status",
                (side,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT status, COUNT(*) n FROM leads GROUP BY status").fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    counts["_all"] = sum(n for s, n in counts.items() if s not in ("junk", "duplicate"))
    return counts


@app.get("/sellers", response_class=HTMLResponse)
def sellers(request: Request):
    args = dict(request.query_params)
    return tpl.TemplateResponse(request, "leads.html", {
        "rows": _rows("SELL", args), "side": "SELL", "counts": _status_counts("SELL"),
        "args": args, "statuses": STATUSES, "status_labels": STATUS_LABELS,
        "ranges": RANGES, "groups": _group_names(), "page": "sellers",
    })


@app.get("/buyers", response_class=HTMLResponse)
def buyers(request: Request):
    args = dict(request.query_params)
    return tpl.TemplateResponse(request, "leads.html", {
        "rows": _rows("BUY", args), "side": "BUY", "counts": _status_counts("BUY"),
        "args": args, "statuses": STATUSES, "status_labels": STATUS_LABELS,
        "ranges": RANGES, "groups": _group_names(), "page": "buyers",
    })


@app.get("/leads", response_class=HTMLResponse)
def leads_both(request: Request):
    """Sotuvchi va xaridor bitta ro'yxatda.

    "Kuchli leadlar" va "yuborishga tayyor" ikkala tomonni ham qamraydi --
    ularni faqat sotuvchilar sahifasiga olib borish son bilan ro'yxatni
    bir-biriga to'g'ri kelmaydigan qilib qo'yardi.
    """
    args = dict(request.query_params)
    return tpl.TemplateResponse(request, "leads.html", {
        "rows": _rows(None, args), "side": None,
        "counts": _status_counts((args.get("side") or "").upper() or None),
        "args": args, "statuses": STATUSES, "status_labels": STATUS_LABELS,
        "ranges": RANGES, "groups": _group_names(), "page": "leads",
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
    # Sellers are the scarce side (roughly 1 seller per 3 buyers), so the page is
    # organised around the seller: one card per authority, its buyers nested.
    by_seller: dict[int, dict] = {}
    for r in rows:
        r["reasons"] = json.loads(r["reasons"]) if r["reasons"] else []
        g = by_seller.setdefault(r["seller_lead_id"], {
            "seller_lead_id": r["seller_lead_id"], "name": r["seller_name"],
            "score": r["seller_score"], "mc_number": r["mc_number"],
            "dot_number": r["dot_number"], "price_usd": r["price_usd"],
            "link": r["seller_link"], "alt_buyers": r["alt_buyers"],
            "buyers": [], "best": 0,
        })
        g["buyers"].append(r)
        g["best"] = max(g["best"], r["score"] or 0)

    groups = sorted(by_seller.values(), key=lambda g: -g["best"])
    for g in groups:
        g["buyers"].sort(key=lambda r: -(r["score"] or 0))

    return tpl.TemplateResponse(request, "matches.html", {
        "groups": groups, "n_pairs": len(rows), "page": "matches",
    })


@app.get("/people/{person_id}", response_class=HTMLResponse)
def person(request: Request, person_id: str):
    with db.connect() as conn:
        p = conn.execute("SELECT * FROM people WHERE person_id = ?", (person_id,)).fetchone()
        leads = [dict(r) for r in conn.execute(
            LEAD_SELECT + " WHERE l.person_id = ? ORDER BY ts DESC", (person_id,)
        )]
    for r in leads:
        r["flags"] = json.loads(r["flags"]) if r.get("flags") else []
    return tpl.TemplateResponse(request, "person.html", {
        "p": dict(p) if p else None, "leads": leads, "page": "",
        "linked": fraud.linked_accounts(person_id),
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
    cfg = db.load_config()
    group_rows = G.all_groups()
    st = G.stats()
    for g in group_rows:
        g.update(st.get(g["group_id"], {"posts": 0, "leads": 0}))

    return tpl.TemplateResponse(request, "health.html", {
        "h": h, "usage": usage, "stages": stages, "runs": runs.latest(25),
        "counts": counts, "errors": errors, "page": "health",
        "audit": db.get_setting("audit_last"),
        "group_rows": group_rows, "broad_on": discover.is_on(cfg),
    })


# --- Rasmlar --------------------------------------------------------------


@app.get("/media/{media_id}")
def media_file(media_id: str):
    """Yuklab olingan rasmni beradi. Parol himoyasi middleware orqali."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT local_path FROM post_media WHERE media_id = ?", (media_id,)
        ).fetchone()
    if not row or not row["local_path"]:
        return HTMLResponse("Rasm topilmadi", status_code=404)
    path = (db.MEDIA_DIR / row["local_path"]).resolve()
    # Yo'l bazadan keladi, lekin baribir media katalogidan chiqib ketmasin
    if not str(path).startswith(str(db.MEDIA_DIR.resolve())) or not path.exists():
        return HTMLResponse("Rasm topilmadi", status_code=404)
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400"})


IMAGE_TABS = [("review", "Needs review"), ("ok", "Read OK"), ("", "All")]


@app.get("/images", response_class=HTMLResponse)
def images(request: Request):
    """Rasmli postlar. OCR ishonchsiz o'qiganlari birinchi tabda turadi.

    Bu sahifaning maqsadi -- OCR xato qilganda ish to'xtamasligi: rasm odamning
    o'ziga ko'rsatiladi, u matnni tuzatib navbatga qaytaradi.
    """
    tab = request.query_params.get("tab", "review")
    where = "COALESCE(p.n_media, 0) > 0"
    params: list = []
    if tab == "review":
        where += " AND COALESCE(p.media_state, 'review') = 'review'"
    elif tab == "ok":
        where += " AND p.media_state = 'ok'"

    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            f"""SELECT p.post_id, p.text, p.ocr_text, p.ocr_conf, p.media_state,
                       p.permalink, p.group_id,
                       COALESCE(p.created_at, p.first_seen_at) AS ts,
                       pe.name AS person_name, pe.person_id,
                       cs.stage,
                       l.lead_id, l.side, l.score
                FROM posts p
                LEFT JOIN people pe ON pe.person_id = p.person_id
                LEFT JOIN classify_state cs ON cs.source_id = p.post_id
                LEFT JOIN leads l ON l.source_id = p.post_id
                WHERE {where}
                ORDER BY ts DESC LIMIT 200""", params)]
        counts = {}
        for key, cond in [("review", "COALESCE(media_state, 'review') = 'review'"),
                          ("ok", "media_state = 'ok'"), ("", "1=1")]:
            counts[key] = conn.execute(
                f"SELECT COUNT(*) FROM posts WHERE COALESCE(n_media, 0) > 0 AND {cond}"
            ).fetchone()[0]
        by_post = _media_of([r["post_id"] for r in rows])
        for r in rows:
            r["media"] = by_post.get(r["post_id"], [])

    return tpl.TemplateResponse(request, "images.html", {
        "rows": rows, "tab": tab, "tabs": IMAGE_TABS, "counts": counts,
        "groups": _group_names(), "page": "images",
    })


@app.post("/media/{post_id}/text")
def media_text(post_id: str, text: str = Form(""), back: str = Form("/images")):
    """Odam OCR matnini tuzatdi -> post klassifikatsiya navbatiga qaytadi."""
    ocr_run.promote(post_id, text)
    return RedirectResponse(back, status_code=303)


@app.post("/media/{post_id}/discard")
def media_discard(post_id: str, back: str = Form("/images")):
    ocr_run.discard(post_id)
    return RedirectResponse(back, status_code=303)


# --- Guruhlar va keng qidiruv --------------------------------------------


@app.get("/groups", response_class=HTMLResponse)
def groups_page(request: Request):
    cfg = db.load_config()
    rows = G.all_groups()
    st = G.stats()
    for r in rows:
        r.update(st.get(r["group_id"], {"posts": 0, "leads": 0, "last_post": None}))

    search_stats = st.get(G.SEARCH_GROUP, {"posts": 0, "leads": 0, "last_post": None})
    with db.connect() as conn:
        health = db.get_health(conn)

    return tpl.TemplateResponse(request, "groups.html", {
        "rows": rows, "page": "groups",
        "broad_on": discover.is_on(cfg),
        "queries": discover.queries(cfg),
        "search_stats": search_stats,
        "search_last": (health.get("search_last_run") or {}).get("value"),
        "search_every": (cfg.get("search") or {}).get("every_hours", 6),
        "search_next_min": round(discover.next_run_in(cfg) / 60),
        "message": request.query_params.get("m"),
    })


@app.post("/groups/add")
def groups_add(url: str = Form(""), back: str = Form("/groups")):
    res = G.add(url)
    return RedirectResponse(f"{back}?m={quote(res['message'])}", status_code=303)


@app.post("/groups/{group_id}/toggle")
def groups_toggle(group_id: str, on: str = Form("1"), back: str = Form("/groups")):
    G.set_enabled(group_id, on == "1")
    return RedirectResponse(back, status_code=303)


@app.post("/groups/{group_id}/remove")
def groups_remove(group_id: str, back: str = Form("/groups")):
    G.remove(group_id)
    return RedirectResponse(f"{back}?m={quote('Guruh ro‘yxatdan chiqarildi')}",
                            status_code=303)


@app.post("/groups/search")
def groups_search(on: str = Form(""), queries: str = Form(""), back: str = Form("/groups")):
    """Keng qidiruvni yoqish/o'chirish va so'rovlarni tahrirlash."""
    db.set_setting("broad_search", on == "1")
    qs = [q.strip() for q in queries.splitlines() if q.strip()]
    if qs:
        db.set_setting("search_queries", qs)
    msg = ("Keng qidiruv yoqildi — keyingi siklda guruhlardan tashqarida ham qidiradi"
           if on == "1" else "Keng qidiruv o‘chirildi — faqat tanlangan guruhlar")
    return RedirectResponse(f"{back}?m={quote(msg)}", status_code=303)


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
