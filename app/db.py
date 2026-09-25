"""SQLite connection handling and schema."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("WRITE_DB", BASE_DIR / "data" / "write.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT 'Untitled',
    content_json  TEXT NOT NULL,
    content_text  TEXT NOT NULL DEFAULT '',
    word_count    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS revisions (
    id            TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    content_json  TEXT NOT NULL,
    content_text  TEXT NOT NULL DEFAULT '',
    word_count    INTEGER NOT NULL DEFAULT 0,
    note          TEXT NOT NULL DEFAULT '',
    is_major      INTEGER NOT NULL DEFAULT 0,
    origin        TEXT NOT NULL DEFAULT 'auto',   -- create | auto | manual | restore
    created_at    TEXT NOT NULL,
    UNIQUE (document_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_revisions_doc ON revisions(document_id, seq);

CREATE TABLE IF NOT EXISTS annotations (
    id              TEXT PRIMARY KEY,
    document_id     TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL DEFAULT 'note',
    body            TEXT NOT NULL DEFAULT '',
    suggested_text  TEXT NOT NULL DEFAULT '',
    quote           TEXT NOT NULL DEFAULT '',
    anchor_from     INTEGER NOT NULL DEFAULT 0,
    anchor_to       INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'open',  -- open | resolved | accepted | dismissed | orphaned
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    resolved_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_annotations_doc ON annotations(document_id, status, anchor_from);

CREATE TABLE IF NOT EXISTS rules (
    id              TEXT PRIMARY KEY,
    number          INTEGER NOT NULL UNIQUE,
    text            TEXT NOT NULL,
    tag             TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    fired_count     INTEGER NOT NULL DEFAULT 0,
    dismissed_count INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS passes (
    id                TEXT PRIMARY KEY,
    slug              TEXT UNIQUE,
    name              TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    rule_mode         TEXT NOT NULL DEFAULT 'all',      -- all | tag | none
    rule_tag          TEXT NOT NULL DEFAULT '',
    instructions      TEXT NOT NULL DEFAULT '',
    allowed_kinds     TEXT NOT NULL DEFAULT 'suggest,cut,question,note',
    general_findings  INTEGER NOT NULL DEFAULT 1,
    model             TEXT NOT NULL DEFAULT '',
    effort            TEXT NOT NULL DEFAULT '',
    is_primary        INTEGER NOT NULL DEFAULT 0,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id               TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    pass_id          TEXT,
    pass_name        TEXT NOT NULL DEFAULT '',
    model            TEXT NOT NULL DEFAULT '',
    effort           TEXT NOT NULL DEFAULT '',
    scope            TEXT NOT NULL DEFAULT 'document',
    status           TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | failed | cancelled
    revision_id      TEXT,
    summary          TEXT NOT NULL DEFAULT '',
    rules_snapshot   TEXT NOT NULL DEFAULT '',
    windows_total    INTEGER NOT NULL DEFAULT 0,
    windows_done     INTEGER NOT NULL DEFAULT 0,
    findings_count   INTEGER NOT NULL DEFAULT 0,
    suppressed_count INTEGER NOT NULL DEFAULT 0,
    error            TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_doc ON runs(document_id, created_at);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added after the first schema shipped. Applied idempotently by migrate().
NEW_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "documents": [("version", "INTEGER NOT NULL DEFAULT 0")],
    "runs": [
        ("provider", "TEXT NOT NULL DEFAULT 'codex'"),
        ("content_json", "TEXT NOT NULL DEFAULT ''"),
        ("document_title", "TEXT NOT NULL DEFAULT ''"),
    ],
    "passes": [("provider", "TEXT NOT NULL DEFAULT ''")],
    "annotations": [
        ("run_id", "TEXT"),
        ("author", "TEXT NOT NULL DEFAULT 'you'"),
        ("rule_id", "TEXT"),
        ("rule_number", "INTEGER"),
        ("paragraph_hint", "INTEGER"),
        ("confidence", "TEXT NOT NULL DEFAULT ''"),
        ("dismiss_reason", "TEXT NOT NULL DEFAULT ''"),
        ("fingerprint", "TEXT NOT NULL DEFAULT ''"),
    ],
}


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    for table, columns in NEW_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    # Fact checks became their own kind; the shipped Copy edit pass should be allowed to use it.
    conn.execute("UPDATE passes SET allowed_kinds = allowed_kinds || ',check' "
                 "WHERE slug = 'copy-edit' AND allowed_kinds NOT LIKE '%check%'")
    # The "praise" kind was removed: fold old rows into notes and drop it from pass presets.
    conn.execute("UPDATE annotations SET kind = 'note' WHERE kind = 'praise'")
    for row in conn.execute("SELECT id, allowed_kinds FROM passes WHERE allowed_kinds LIKE '%praise%'").fetchall():
        kinds = [k for k in row[1].split(",") if k and k != "praise"] or ["note"]
        conn.execute("UPDATE passes SET allowed_kinds = ? WHERE id = ?", (",".join(kinds), row[0]))


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    migrate(conn)


def _connection(*, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def get_conn() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one connection per request, wrapped in a transaction."""
    yield from _connection()


def get_write_conn() -> Iterator[sqlite3.Connection]:
    """Serialize short document writes before reading their version.

    A deferred read followed by a write can fail with SQLITE_BUSY_SNAPSHOT
    instead of observing another tab's committed version.
    """
    yield from _connection(immediate=True)
