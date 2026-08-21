import sqlite3

from . import db


def _authorizer(action, arg1, arg2, *_):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION:
        name = (arg1 or arg2 or "").lower()
        if name == "load_extension":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _local_day_predicate(column: str) -> str:
    return f"datetime({column},'localtime') >= date('now','localtime', printf('-%d days', ?))"


def recent(conn, project=None, days=7) -> list[sqlite3.Row]:
    where = [_local_day_predicate("ts")]
    params = [days]
    if project is not None:
        where.append("project=?")
        params.append(project)
    return conn.execute(
        f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY ts DESC, id DESC",
        params,
    ).fetchall()


def brief(conn, project=None, days=7) -> dict:
    fact_where = ["valid=1", _local_day_predicate("updated_at")]
    fact_params = [days]
    event_where = [_local_day_predicate("ts")]
    event_params = [days]
    if project is not None:
        fact_where.append("project=?")
        fact_params.append(project)
        event_where.append("project=?")
        event_params.append(project)

    facts = conn.execute(
        f"SELECT * FROM facts WHERE {' AND '.join(fact_where)} ORDER BY updated_at DESC, id DESC",
        fact_params,
    ).fetchall()
    events = conn.execute(
        f"SELECT * FROM events WHERE {' AND '.join(event_where)} ORDER BY ts DESC, id DESC",
        event_params,
    ).fetchall()
    return {"facts": facts, "events": events}


def run_query(db_path, sql) -> list[sqlite3.Row]:
    conn = db.connect(db_path, readonly=True)
    conn.set_authorizer(_authorizer)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()
