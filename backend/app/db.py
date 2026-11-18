from __future__ import annotations
import sqlite3
from app.config import DB_PATH

SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  face_value REAL NOT NULL,
  min_spend REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS coupon_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  coupon_code TEXT NOT NULL,
  campaign_code TEXT NOT NULL,
  amount REAL NOT NULL DEFAULT 0,
  business_day TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  tag TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS redeem_facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  coupon_code TEXT NOT NULL UNIQUE,
  campaign_code TEXT NOT NULL,
  amount REAL NOT NULL,
  business_day TEXT NOT NULL,
  event_id INTEGER NOT NULL,
  batch_id INTEGER,
  status TEXT NOT NULL DEFAULT 'open',
  tag TEXT NOT NULL DEFAULT '',
  carried_from_day TEXT,
  voided_at TEXT
);
CREATE TABLE IF NOT EXISTS redeem_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  business_day TEXT NOT NULL,
  status TEXT NOT NULL,
  fact_count INTEGER NOT NULL DEFAULT 0,
  total_amount REAL NOT NULL DEFAULT 0,
  sealed_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  tag TEXT,
  leftover_tags TEXT NOT NULL DEFAULT '[]'
);
'''

# Columns added after the first schema version.  Kept explicit so existing
# databases are upgraded in place instead of silently losing the tag gate.
MIGRATIONS = (
    ("coupon_events", "tag", "TEXT NOT NULL DEFAULT ''"),
    ("redeem_facts", "tag", "TEXT NOT NULL DEFAULT ''"),
    ("redeem_facts", "carried_from_day", "TEXT"),
    ("redeem_facts", "voided_at", "TEXT"),
    ("redeem_batches", "tag", "TEXT"),
    ("redeem_batches", "leftover_tags", "TEXT NOT NULL DEFAULT '[]'"),
)

INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_events_day_tag ON coupon_events(business_day, tag)",
    "CREATE INDEX IF NOT EXISTS idx_facts_day_tag_status ON redeem_facts(business_day, tag, status)",
    "CREATE INDEX IF NOT EXISTS idx_facts_status ON redeem_facts(status)",
    "CREATE INDEX IF NOT EXISTS idx_facts_event_id ON redeem_facts(event_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_batches_one_sealed_per_day "
    "ON redeem_batches(business_day) WHERE status = 'sealed'",
)

# The old tag_material pipeline kept a second copy of tags (tag_index), a
# leftover materialization (leftover_material) and an independent gate flag
# (settings: gate_force_closed:*).  They forked away from the open-fact scan
# and caused every tear listed in the incident report.  The single source of
# truth is now the redeem_facts scan, so drop them on startup and never read
# them again.
_DEPRECATED_TABLES = ("tag_index", "leftover_material")
_DEPRECATED_SETTING_PREFIX = "gate_force_closed:"


def _purge_deprecated_pipeline(conn: sqlite3.Connection) -> None:
    for table in _DEPRECATED_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(
        "DELETE FROM settings WHERE key LIKE ?",
        (_DEPRECATED_SETTING_PREFIX + "%",),
    )


def connect(immediate: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if immediate:
        conn.execute("BEGIN IMMEDIATE")
    return conn


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, column, ddl in MIGRATIONS:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _ensure_columns(conn)
        _purge_deprecated_pipeline(conn)
        for statement in INDEX_STATEMENTS:
            try:
                conn.execute(statement)
            except sqlite3.IntegrityError:
                # A pre-tag database may already hold several sealed batches
                # for one day; the unique index cannot express the invariant
                # there.  Application logic (every write holds BEGIN IMMEDIATE)
                # still enforces the day gate, so carry on with the other
                # indexes.
                if "idx_batches_one_sealed_per_day" not in statement:
                    raise
        conn.commit()
    finally:
        conn.close()
