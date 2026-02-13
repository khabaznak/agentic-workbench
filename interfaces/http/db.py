from __future__ import annotations

import sqlite3
from contextlib import contextmanager
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"


def resolve_db_path() -> Path:
    configured = os.getenv("ATRIUM_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return DATA_DIR / "decision_graph.db"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT,
    name TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('question', 'decision', 'task')),
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('open', 'in_progress', 'blocked', 'done')),
    rationale TEXT,
    owner TEXT,
    priority INTEGER,
    context_prompt TEXT,
    external_ref TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS choices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    text TEXT NOT NULL,
    is_chosen INTEGER NOT NULL DEFAULT 0,
    chosen_at TEXT,
    FOREIGN KEY(node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node_id INTEGER NOT NULL,
    to_node_id INTEGER NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('leads_to')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(from_node_id) REFERENCES nodes(id),
    FOREIGN KEY(to_node_id) REFERENCES nodes(id)
);

CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(session_id) REFERENCES sessions(id)
);
"""


def init_db() -> None:
    db_path = resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _run_migrations(conn)
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_external_id
            ON sessions(external_id)
            WHERE external_id IS NOT NULL
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_external_ref
            ON nodes(external_ref)
            WHERE external_ref IS NOT NULL
            """
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_choices_node_label
            ON choices(node_id, label)
            """
        )
        conn.commit()


@contextmanager
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(resolve_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _run_migrations(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "sessions", "external_id", "TEXT")
    _ensure_column(conn, "nodes", "external_ref", "TEXT")


def _ensure_column(
    conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str
) -> None:
    existing = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    names = {row[1] for row in existing}
    if column_name not in names:
        conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
        )
