import sqlite3


CJK_RANGES = [
    (0xAC00, 0xD7A3),
    (0x1100, 0x11FF),
    (0x3130, 0x318F),
    (0x4E00, 0x9FFF),
    (0x3040, 0x309F),
    (0x30A0, 0x30FF),
]


def has_cjk(text: str) -> bool:
    return any(any(lo <= ord(c) <= hi for lo, hi in CJK_RANGES) for c in text)


def index_text(conn, src, ref_id, project, content) -> None:
    conn.execute("DELETE FROM memory_fts WHERE src=? AND ref_id=?", (src, ref_id))
    conn.execute(
        "INSERT INTO memory_fts(src,ref_id,project,content) VALUES (?,?,?,?)",
        (src, ref_id, project, content),
    )


def delete_fts(conn, src, ref_id) -> None:
    conn.execute("DELETE FROM memory_fts WHERE src=? AND ref_id=?", (src, ref_id))


def fact_content(subject, statement) -> str:
    return (f"{subject or ''} {statement}").strip()


def phrase_query(text: str) -> str:
    toks = text.split()
    if not toks:
        return '""'
    return " ".join('"' + t.replace('"', '""') + '"' for t in toks)


def _esc(tok: str) -> str:
    return tok.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_clause(expr: str, count: int) -> str:
    return " AND ".join([f"{expr} LIKE ? ESCAPE '\\'"] * count)


def _cjk_fallback(conn, text, project, kind, need) -> list[sqlite3.Row]:
    toks = [t for t in text.split() if t]
    if not toks or need <= 0:
        return []

    like_params = [f"%{_esc(t)}%" for t in toks]
    out = []

    fact_where = ["valid=1"]
    fact_params = []
    if project is not None:
        fact_where.append("project=?")
        fact_params.append(project)
    fact_where.append(_like_clause("(COALESCE(subject,'')||' '||statement)", len(toks)))
    fact_params.extend(like_params)
    out += conn.execute(
        "SELECT 'fact' AS src, id AS ref_id, project, "
        "COALESCE(subject,'')||' '||statement AS content, updated_at AS ts "
        f"FROM facts WHERE {' AND '.join(fact_where)} LIMIT 200",
        fact_params,
    ).fetchall()

    event_where = []
    event_params = []
    if project is not None:
        event_where.append("project=?")
        event_params.append(project)
    if kind is not None:
        event_where.append("kind=?")
        event_params.append(kind)
    event_where.append(_like_clause("content", len(toks)))
    event_params.extend(like_params)
    out += conn.execute(
        "SELECT 'event' AS src, id AS ref_id, project, content, ts "
        f"FROM events WHERE {' AND '.join(event_where)} LIMIT 200",
        event_params,
    ).fetchall()

    artifact_where = []
    artifact_params = []
    if project is not None:
        artifact_where.append("project=?")
        artifact_params.append(project)
    artifact_where.append(_like_clause("(title||' '||IFNULL(summary,''))", len(toks)))
    artifact_params.extend(like_params)
    out += conn.execute(
        "SELECT 'artifact' AS src, id AS ref_id, project, "
        "title||' '||IFNULL(summary,'') AS content, mtime AS ts "
        f"FROM artifacts WHERE {' AND '.join(artifact_where)} LIMIT 200",
        artifact_params,
    ).fetchall()

    seen = set()
    rows = []
    for row in sorted(out, key=lambda r: r["ts"] or "", reverse=True):
        key = (row["src"], row["ref_id"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
        if len(rows) >= need:
            break
    return rows


def search(conn, text, *, recent=False, project=None, kind=None, limit=20) -> list[sqlite3.Row]:
    if limit <= 0:
        return []
    sql = [
        "SELECT src, ref_id, project, content",
        "FROM memory_fts",
        "WHERE memory_fts MATCH ?",
    ]
    params = [phrase_query(text)]
    if project is not None:
        sql.append("AND project=?")
        params.append(project)
    if kind is not None:
        sql.append("AND (src != 'event' OR ref_id IN (SELECT id FROM events WHERE kind=?))")
        params.append(kind)
    if recent:
        sql.append(
            "ORDER BY ROUND(bm25(memory_fts), 6), "
            "COALESCE(CASE src "
            "WHEN 'event' THEN (SELECT ts FROM events WHERE events.id=memory_fts.ref_id) "
            "WHEN 'fact' THEN (SELECT updated_at FROM facts WHERE facts.id=memory_fts.ref_id) "
            "WHEN 'artifact' THEN (SELECT mtime FROM artifacts WHERE artifacts.id=memory_fts.ref_id) "
            "END, '') DESC, bm25(memory_fts), src, ref_id LIMIT ?"
        )
    else:
        sql.append("ORDER BY bm25(memory_fts) LIMIT ?")
    params.append(limit)
    try:
        rows = conn.execute(" ".join(sql), params).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if has_cjk(text) and len(rows) < limit:
        seen = {(row["src"], row["ref_id"]) for row in rows}
        for row in _cjk_fallback(conn, text, project, kind, limit - len(rows)):
            key = (row["src"], row["ref_id"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            if len(rows) >= limit:
                break
    return rows


def reindex_fts(conn) -> None:
    conn.execute("DELETE FROM memory_fts")
    for row in conn.execute("SELECT id, project, content FROM events"):
        index_text(conn, "event", row["id"], row["project"], row["content"])
    for row in conn.execute("SELECT id, project, subject, statement FROM facts WHERE valid=1"):
        index_text(conn, "fact", row["id"], row["project"], fact_content(row["subject"], row["statement"]))
    for row in conn.execute("SELECT id, project, title, summary FROM artifacts"):
        content = (f"{row['title'] or ''} {row['summary'] or ''}").strip()
        index_text(conn, "artifact", row["id"], row["project"], content)
