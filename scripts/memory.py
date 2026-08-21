import argparse
import json
import pathlib
import sqlite3
import sys

from memorylib import db, fts, read, store


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "memory_runtime" / "db" / "memory.db"


def _row_dict(row) -> dict:
    return dict(row)


def _print_rows(rows) -> None:
    for row in rows:
        print(json.dumps(_row_dict(row), ensure_ascii=False))


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False))


def _warn_secrets(hits: list[str]) -> None:
    if hits:
        print(f"secret-pattern warning: matched {', '.join(hits)}")


def _prepare_write_path(db_path: str) -> None:
    db.assert_safe_db_path(db_path)
    pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)


def _log_op(db_path: str, op: str, detail: str | None = None) -> None:
    conn = db.connect(db_path)
    try:
        store.log_op(conn, op, detail=detail)
        conn.commit()
    finally:
        conn.close()


def _cmd_init(args) -> int:
    _prepare_write_path(args.db)
    db.init(args.db)
    _log_op(args.db, "init")
    print(f"initialized {args.db}")
    return 0


def _cmd_session_start(args) -> int:
    _prepare_write_path(args.db)
    conn = db.connect(args.db)
    try:
        session_id = store.session_start(conn, args.agent, project=args.project)
        print(f"session_id={session_id}")
        return 0
    finally:
        conn.close()


def _cmd_session_end(args) -> int:
    conn = db.connect(args.db)
    try:
        store.session_end(conn, args.session_id, summary=args.summary)
        print(f"session_id={args.session_id} ended")
        return 0
    finally:
        conn.close()


def _cmd_event_add(args) -> int:
    conn = db.connect(args.db)
    try:
        event_id, hits = store.event_add(
            conn,
            args.kind,
            args.content,
            project=args.project,
            session_id=args.session,
        )
        print(f"event_id={event_id}")
        _warn_secrets(hits)
        return 0
    finally:
        conn.close()


def _cmd_fact_add(args) -> int:
    conn = db.connect(args.db)
    try:
        fact_id, hits = store.fact_add(
            conn,
            args.statement,
            args.category,
            subject=args.subject,
            project=args.project,
            confidence=args.confidence,
            supersedes=args.supersedes,
        )
        print(f"fact_id={fact_id}")
        _warn_secrets(hits)
        return 0
    finally:
        conn.close()


def _cmd_search(args) -> int:
    conn = db.connect(args.db)
    try:
        rows = fts.search(
            conn,
            args.text,
            recent=args.recent,
            project=args.project,
            kind=args.kind,
            limit=args.limit,
        )
        _print_rows(rows)
        store.log_op(
            conn,
            "search",
            detail=f"text={args.text}; project={args.project}; kind={args.kind}; limit={args.limit}",
        )
        conn.commit()
        return 0
    finally:
        conn.close()


def _cmd_brief(args) -> int:
    conn = db.connect(args.db, readonly=True)
    try:
        result = read.brief(conn, project=args.project, days=args.days)
        _print_json({
            "facts": [_row_dict(row) for row in result["facts"]],
            "events": [_row_dict(row) for row in result["events"]],
        })
        return 0
    finally:
        conn.close()


def _cmd_recent(args) -> int:
    conn = db.connect(args.db, readonly=True)
    try:
        _print_rows(read.recent(conn, project=args.project, days=args.days))
        return 0
    finally:
        conn.close()


def _cmd_reindex_fts(args) -> int:
    conn = db.connect(args.db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            fts.reindex_fts(conn)
            store.log_op(conn, "reindex_fts")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        print("reindexed memory_fts")
        return 0
    finally:
        conn.close()


def _cmd_query(args) -> int:
    _print_rows(read.run_query(args.db, args.sql))
    return 0


def _cmd_stats(args) -> int:
    conn = db.connect(args.db, readonly=True)
    try:
        counts = {}
        for name in ("sessions", "events", "facts", "artifacts", "memory_fts", "ops_log"):
            counts[name] = conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
        _print_json(counts)
        return 0
    finally:
        conn.close()


def _cmd_log(args) -> int:
    conn = db.connect(args.db, readonly=True)
    try:
        rows = conn.execute(
            "SELECT * FROM ops_log ORDER BY id DESC LIMIT ?",
            (args.n,),
        ).fetchall()
        _print_rows(rows)
        return 0
    finally:
        conn.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memory")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init")
    init_p.set_defaults(func=_cmd_init)

    session_p = sub.add_parser("session")
    session_sub = session_p.add_subparsers(dest="session_command", required=True)
    session_start = session_sub.add_parser("start")
    session_start.add_argument("--agent", required=True)
    session_start.add_argument("--project")
    session_start.set_defaults(func=_cmd_session_start)
    session_end = session_sub.add_parser("end")
    session_end.add_argument("session_id", type=int)
    session_end.add_argument("--summary")
    session_end.set_defaults(func=_cmd_session_end)

    event_p = sub.add_parser("event")
    event_sub = event_p.add_subparsers(dest="event_command", required=True)
    event_add = event_sub.add_parser("add")
    event_add.add_argument("--kind", required=True)
    event_add.add_argument("--content", required=True)
    event_add.add_argument("--project")
    event_add.add_argument("--session", type=int)
    event_add.set_defaults(func=_cmd_event_add)

    fact_p = sub.add_parser("fact")
    fact_sub = fact_p.add_subparsers(dest="fact_command", required=True)
    fact_add = fact_sub.add_parser("add")
    fact_add.add_argument("--statement", required=True)
    fact_add.add_argument("--category", required=True)
    fact_add.add_argument("--subject")
    fact_add.add_argument("--project")
    fact_add.add_argument("--confidence", type=float, default=1.0)
    fact_add.add_argument("--supersedes", type=int)
    fact_add.set_defaults(func=_cmd_fact_add)

    search_p = sub.add_parser("search")
    search_p.add_argument("text")
    search_p.add_argument("--recent", action="store_true")
    search_p.add_argument("--project")
    search_p.add_argument("--kind")
    search_p.add_argument("--limit", type=int, default=20)
    search_p.set_defaults(func=_cmd_search)

    brief_p = sub.add_parser("brief")
    brief_p.add_argument("--project")
    brief_p.add_argument("--days", type=int, default=7)
    brief_p.set_defaults(func=_cmd_brief)

    recent_p = sub.add_parser("recent")
    recent_p.add_argument("--project")
    recent_p.add_argument("--days", type=int, default=7)
    recent_p.set_defaults(func=_cmd_recent)

    reindex_p = sub.add_parser("reindex-fts")
    reindex_p.set_defaults(func=_cmd_reindex_fts)

    query_p = sub.add_parser("query")
    query_p.add_argument("sql")
    query_p.set_defaults(func=_cmd_query)

    stats_p = sub.add_parser("stats")
    stats_p.set_defaults(func=_cmd_stats)

    log_p = sub.add_parser("log")
    log_p.add_argument("--n", type=int, default=20)
    log_p.set_defaults(func=_cmd_log)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except sqlite3.Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
