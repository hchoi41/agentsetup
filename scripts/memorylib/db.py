import os
import pathlib
import sqlite3


V1A_DDL = """
-- EPISODIC
CREATE TABLE sessions (
  id INTEGER PRIMARY KEY, agent TEXT NOT NULL, project TEXT,
  started_at TEXT NOT NULL DEFAULT (datetime('now')), ended_at TEXT,
  summary TEXT, status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE events (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL DEFAULT (datetime('now')),
  session_id INTEGER REFERENCES sessions(id), project TEXT,
  kind TEXT NOT NULL, content TEXT NOT NULL, source TEXT
);
CREATE INDEX idx_events_ts ON events(ts);
CREATE INDEX idx_events_project ON events(project);
CREATE INDEX idx_events_session ON events(session_id);

-- SEMANTIC
CREATE TABLE facts (
  id INTEGER PRIMARY KEY, subject TEXT, statement TEXT NOT NULL,
  category TEXT NOT NULL, project TEXT, source TEXT,
  confidence REAL DEFAULT 1.0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  superseded_by INTEGER REFERENCES facts(id), valid INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_facts_subject ON facts(subject);
CREATE INDEX idx_facts_category ON facts(category);
CREATE INDEX idx_facts_valid ON facts(valid);

CREATE TABLE artifacts (
  id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, role TEXT NOT NULL,
  project TEXT, title TEXT, mtime TEXT, sha256 TEXT, summary TEXT
);

CREATE TABLE ops_log (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL DEFAULT (datetime('now')),
  op TEXT NOT NULL, agent TEXT, detail TEXT
);

CREATE VIRTUAL TABLE memory_fts USING fts5(
  src UNINDEXED, ref_id UNINDEXED, project UNINDEXED, content,
  tokenize = 'unicode61 remove_diacritics 0'
);

CREATE VIEW events_local AS SELECT *, datetime(ts,'localtime') AS ts_local FROM events;
CREATE VIEW facts_local  AS SELECT *, datetime(created_at,'localtime') AS created_local,
                                      datetime(updated_at,'localtime') AS updated_local FROM facts;
"""


class UnsafeDbLocation(Exception):
    pass


def assert_safe_db_path(db_path: str) -> None:
    p = pathlib.Path(db_path).resolve()
    for name, val in os.environ.items():
        if name.lower().startswith("onedrive") and val:
            try:
                root = pathlib.Path(val).resolve()
                if p == root or root in p.parents:
                    raise UnsafeDbLocation(f"live DB under OneDrive ({name}): {p}")
            except OSError:
                pass
    if any(part.upper() == "13.MEMORY_HDD" for part in p.parts):
        raise UnsafeDbLocation(f"live DB under append-only 13.MEMORY_HDD: {p}")


def connect(db_path: str, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0, isolation_level=None)
    else:
        assert_safe_db_path(db_path)
        conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


MIGRATIONS: list[tuple[int, str]] = [(1, V1A_DDL)]


def _ensure_meta(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    if conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone() is None:
        conn.execute("INSERT INTO schema_meta(key,value) VALUES ('schema_version','0')")
    conn.commit()


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 0


def _statements(ddl: str) -> list[str]:
    # v1a/v1b DDL has no semicolons inside string literals or triggers.
    return [s.strip() for s in ddl.split(";") if s.strip()]


def migrate(conn: sqlite3.Connection) -> tuple[int, int]:
    _ensure_meta(conn)
    cur = current_version(conn)
    head = MIGRATIONS[-1][0]
    if cur > head:
        raise RuntimeError(f"DB schema_version {cur} newer than code {head}; refusing downgrade")
    prev_iso = conn.isolation_level
    conn.isolation_level = None
    try:
        for ver, ddl in MIGRATIONS:
            if ver > cur:
                conn.execute("BEGIN")
                for stmt in _statements(ddl):
                    conn.execute(stmt)
                conn.execute("UPDATE schema_meta SET value=? WHERE key='schema_version'", (str(ver),))
                conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.isolation_level = prev_iso
    return (cur, current_version(conn))


def init(db_path: str) -> None:
    assert_safe_db_path(db_path)
    conn = connect(db_path)
    try:
        migrate(conn)
    finally:
        conn.close()
