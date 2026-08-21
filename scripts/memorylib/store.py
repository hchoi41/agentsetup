from . import fts, secrets


def _rollback(conn) -> None:
    try:
        conn.execute("ROLLBACK")
    except Exception:
        pass


def log_op(conn, op, agent=None, detail=None) -> None:
    conn.execute(
        "INSERT INTO ops_log(op,agent,detail) VALUES (?,?,?)",
        (op, agent, detail),
    )


def session_start(conn, agent, project=None) -> int:
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "INSERT INTO sessions(agent,project) VALUES (?,?)",
            (agent, project),
        )
        session_id = cur.lastrowid
        log_op(conn, "session_start", agent=agent, detail=f"session_id={session_id}; project={project}")
        conn.execute("COMMIT")
        return session_id
    except Exception:
        _rollback(conn)
        raise


def session_end(conn, session_id, summary=None) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE sessions SET ended_at=datetime('now'), summary=?, status='closed' WHERE id=?",
            (summary, session_id),
        )
        log_op(conn, "session_end", detail=f"session_id={session_id}")
        conn.execute("COMMIT")
    except Exception:
        _rollback(conn)
        raise


def event_add(conn, kind, content, project=None, session_id=None, source=None) -> tuple[int, list[str]]:
    hits = secrets.scan_secret(content)
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "INSERT INTO events(session_id,project,kind,content,source) VALUES (?,?,?,?,?)",
            (session_id, project, kind, content, source),
        )
        eid = cur.lastrowid
        fts.index_text(conn, "event", eid, project, content)
        log_op(conn, "event_add", detail=f"event_id={eid}; kind={kind}; project={project}")
        conn.execute("COMMIT")
        return eid, hits
    except Exception:
        _rollback(conn)
        raise


def fact_add(conn, statement, category, subject=None, project=None, confidence=1.0, supersedes=None) -> tuple[int, list[str]]:
    hits = secrets.scan_secret(statement)
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "INSERT INTO facts(subject,statement,category,project,confidence) VALUES (?,?,?,?,?)",
            (subject, statement, category, project, confidence),
        )
        fact_id = cur.lastrowid
        fts.index_text(conn, "fact", fact_id, project, fts.fact_content(subject, statement))
        if supersedes is not None:
            conn.execute(
                "UPDATE facts SET valid=0, superseded_by=? WHERE id=?",
                (fact_id, supersedes),
            )
            fts.delete_fts(conn, "fact", supersedes)
        log_op(conn, "fact_add", detail=f"fact_id={fact_id}; category={category}; project={project}")
        conn.execute("COMMIT")
        return fact_id, hits
    except Exception:
        _rollback(conn)
        raise
