"""SQLite schema va query helper'lari."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
DB_PATH = DATA / "mc.db"
RAW_DIR = DATA / "raw"

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    person_id            TEXT PRIMARY KEY,
    name                 TEXT,
    profile_url          TEXT,
    first_seen           REAL,
    last_seen            REAL,
    n_sell               INTEGER DEFAULT 0,
    n_buy                INTEGER DEFAULT 0,
    is_suspected_reseller INTEGER DEFAULT 0,
    notes                TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    post_id       TEXT PRIMARY KEY,
    group_id      TEXT,
    person_id     TEXT REFERENCES people(person_id),
    text          TEXT,
    permalink     TEXT,
    created_at    REAL,
    first_seen_at REAL,
    n_comments    INTEGER,
    n_reactions   INTEGER,
    raw_path      TEXT,
    comments_fetched_at REAL
);
CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at DESC);

CREATE TABLE IF NOT EXISTS comments (
    comment_id    TEXT PRIMARY KEY,
    post_id       TEXT REFERENCES posts(post_id),
    person_id     TEXT REFERENCES people(person_id),
    text          TEXT,
    created_at    REAL,
    first_seen_at REAL
);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
CREATE INDEX IF NOT EXISTS idx_comments_person ON comments(person_id);

CREATE TABLE IF NOT EXISTS leads (
    lead_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type    TEXT NOT NULL,         -- 'post' | 'comment'
    source_id      TEXT NOT NULL UNIQUE,
    source_post_id TEXT,
    person_id      TEXT REFERENCES people(person_id),
    side           TEXT,                  -- 'SELL' | 'BUY'

    mc_number           TEXT,
    dot_number          TEXT,
    authority_since     TEXT,
    authority_age_years REAL,
    entity_type         TEXT,
    state               TEXT,
    price_usd           INTEGER,
    price_is_negotiable INTEGER,
    has_amazon          INTEGER,
    amazon_status       TEXT,
    includes_bank       INTEGER,
    includes_email      INTEGER,
    includes_phone      INTEGER,
    has_trucks          INTEGER,
    has_insurance       INTEGER,
    clean_record        INTEGER,

    buyer_budget_usd    INTEGER,
    buyer_wants_state   TEXT,
    buyer_min_age_years REAL,
    buyer_needs_amazon  INTEGER,

    contact_method  TEXT,
    contact_value   TEXT,
    urgency         TEXT,
    llm_confidence  REAL,

    score           INTEGER,
    score_breakdown TEXT,
    fit_verdict     TEXT,        -- 'pass' | 'ask' | 'fail'
    fit_reasons     TEXT,
    fit_missing     TEXT,
    status          TEXT DEFAULT 'new',
    note            TEXT,

    created_at REAL,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_leads_side_score ON leads(side, score DESC);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);

-- Klassifikatsiya navbati: har bir manba elementining holati
CREATE TABLE IF NOT EXISTS classify_state (
    source_id   TEXT PRIMARY KEY,
    source_type TEXT,
    stage       TEXT,        -- 'pending' | 'noise' | 'triaged' | 'extracted' | 'error'
    side_guess  TEXT,
    error       TEXT,
    updated_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_classify_stage ON classify_state(stage);

CREATE TABLE IF NOT EXISTS fmcsa_cache (
    key        TEXT PRIMARY KEY,   -- 'dot:1234567' | 'docket:1075922'
    payload    TEXT,
    ok         INTEGER,
    fetched_at REAL
);

CREATE TABLE IF NOT EXISTS matches (
    match_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_lead_id  INTEGER REFERENCES leads(lead_id),
    seller_lead_id INTEGER REFERENCES leads(lead_id),
    score          INTEGER,
    reasons        TEXT,
    intro_text     TEXT,
    status         TEXT DEFAULT 'new',
    created_at     REAL,
    UNIQUE(buyer_lead_id, seller_lead_id)
);

CREATE TABLE IF NOT EXISTS llm_usage (
    day               TEXT,
    model             TEXT,
    calls             INTEGER DEFAULT 0,
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    PRIMARY KEY (day, model)
);

CREATE TABLE IF NOT EXISTS notified (
    lead_id INTEGER PRIMARY KEY,
    sent_at REAL
);

-- Tizim holati: collector oxirgi yurishi, sessiya tirikmi, xatolar
CREATE TABLE IF NOT EXISTS health (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at REAL
);

-- Har bir yurish va uning bosqichlari: "qachon yangilandi, muvaffaqiyatlimi?"
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger     TEXT,      -- 'loop' | 'manual'
    started_at  REAL,
    finished_at REAL,
    status      TEXT,      -- running | ok | partial | failed | stopped
    summary     TEXT,
    last_error  TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);

CREATE TABLE IF NOT EXISTS run_steps (
    step_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER REFERENCES runs(run_id),
    step        TEXT,
    started_at  REAL,
    finished_at REAL,
    status      TEXT,      -- running | ok | error
    result      TEXT,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_steps_run ON run_steps(run_id);
"""


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


NEW_COLUMNS = [
    ("leads", "amazon_status", "TEXT"),
    ("leads", "includes_bank", "INTEGER"),
    ("leads", "includes_email", "INTEGER"),
    ("leads", "includes_phone", "INTEGER"),
    ("leads", "fit_verdict", "TEXT"),
    ("leads", "fit_reasons", "TEXT"),
    ("leads", "fit_missing", "TEXT"),
    ("posts", "group_name", "TEXT"),
]


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # Eski bazani yangi ustunlar bilan to'ldirish
        for table, col, typ in NEW_COLUMNS:
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def load_config() -> dict[str, Any]:
    with open(ROOT / "config.yaml") as fh:
        return yaml.safe_load(fh)


def load_env() -> None:
    """`.env` ni process env ga yuklaydi (python-dotenv bo'lmasa ham ishlaydi)."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


# --- yozish helper'lari ---------------------------------------------------


def upsert_person(conn, person_id: str, name: str | None, profile_url: str | None) -> None:
    now = time.time()
    conn.execute(
        """INSERT INTO people (person_id, name, profile_url, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(person_id) DO UPDATE SET
             name = COALESCE(excluded.name, people.name),
             profile_url = COALESCE(excluded.profile_url, people.profile_url),
             last_seen = excluded.last_seen""",
        (person_id, name, profile_url, now, now),
    )


def upsert_post(conn, post: dict[str, Any]) -> bool:
    """True qaytaradi agar bu yangi post bo'lsa."""
    now = time.time()
    cur = conn.execute("SELECT 1 FROM posts WHERE post_id = ?", (post["post_id"],))
    is_new = cur.fetchone() is None
    conn.execute(
        """INSERT INTO posts (post_id, group_id, person_id, text, permalink,
                              created_at, first_seen_at, n_comments, n_reactions, raw_path)
           VALUES (:post_id, :group_id, :person_id, :text, :permalink,
                   :created_at, :first_seen_at, :n_comments, :n_reactions, :raw_path)
           ON CONFLICT(post_id) DO UPDATE SET
             text        = COALESCE(NULLIF(excluded.text, ''), posts.text),
             n_comments  = COALESCE(excluded.n_comments, posts.n_comments),
             n_reactions = COALESCE(excluded.n_reactions, posts.n_reactions),
             created_at  = COALESCE(posts.created_at, excluded.created_at)""",
        {"first_seen_at": now, **post},
    )
    if is_new:
        conn.execute(
            """INSERT OR IGNORE INTO classify_state (source_id, source_type, stage, updated_at)
               VALUES (?, 'post', 'pending', ?)""",
            (post["post_id"], now),
        )
    return is_new


def upsert_comment(conn, c: dict[str, Any]) -> bool:
    now = time.time()
    cur = conn.execute("SELECT 1 FROM comments WHERE comment_id = ?", (c["comment_id"],))
    is_new = cur.fetchone() is None
    conn.execute(
        """INSERT INTO comments (comment_id, post_id, person_id, text, created_at, first_seen_at)
           VALUES (:comment_id, :post_id, :person_id, :text, :created_at, :first_seen_at)
           ON CONFLICT(comment_id) DO UPDATE SET
             text = COALESCE(NULLIF(excluded.text, ''), comments.text)""",
        {"first_seen_at": now, **c},
    )
    if is_new:
        conn.execute(
            """INSERT OR IGNORE INTO classify_state (source_id, source_type, stage, updated_at)
               VALUES (?, 'comment', 'pending', ?)""",
            (c["comment_id"], now),
        )
    return is_new


def set_health(conn, key: str, value: Any) -> None:
    conn.execute(
        """INSERT INTO health (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, json.dumps(value) if not isinstance(value, str) else value, time.time()),
    )


def get_health(conn) -> dict[str, dict[str, Any]]:
    return {
        r["key"]: {"value": r["value"], "updated_at": r["updated_at"]}
        for r in conn.execute("SELECT * FROM health")
    }


def save_raw(group_id: str, kind: str, payload: Any) -> str:
    """Xom JSON ni diskka yozadi va nisbiy yo'lni qaytaradi."""
    day = time.strftime("%Y-%m-%d")
    d = RAW_DIR / day
    d.mkdir(parents=True, exist_ok=True)
    name = f"{kind}-{group_id}-{int(time.time() * 1000)}.json"
    (d / name).write_text(json.dumps(payload, ensure_ascii=False))
    return f"{day}/{name}"
