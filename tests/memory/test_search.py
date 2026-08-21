from memorylib import db, store, fts

def setup_rows(conn):
    store.event_add(conn, "decision", "we chose SQLite for memory", project="hub")
    store.fact_add(conn, "the cost-center code is 4200", "finance", subject="budget", project="hub")

def test_search_finds_event_and_fact(db_path):
    db.init(db_path); conn = db.connect(db_path); setup_rows(conn)
    assert any("SQLite" in r["content"] for r in fts.search(conn, "SQLite"))

def test_search_punctuation_never_raises(db_path):
    db.init(db_path); conn = db.connect(db_path); setup_rows(conn)
    for q in ["cost-center", 'Q3 "actuals"', "OR", "C:\\Users", "*"]:
        fts.search(conn, q)   # must not raise

def test_reindex_omits_retired_facts_and_is_idempotent(db_path):
    db.init(db_path); conn = db.connect(db_path)
    old, _ = store.fact_add(conn, "old budget 100", "finance")
    new, _ = store.fact_add(conn, "new budget 120", "finance", supersedes=old)
    fts.reindex_fts(conn); conn.commit()
    assert conn.execute("SELECT count(*) FROM memory_fts WHERE src='fact' AND ref_id=?", (old,)).fetchone()[0] == 0
    # idempotent: a second rebuild keeps the live fact's FTS row at exactly 1 (truncate-rebuild, not append)
    fts.reindex_fts(conn); conn.commit()
    assert conn.execute("SELECT count(*) FROM memory_fts WHERE src='fact' AND ref_id=?", (new,)).fetchone()[0] == 1


def test_search_does_not_match_src_or_project_metadata(db_path):
    db.init(db_path); conn = db.connect(db_path)
    event_id, _ = store.event_add(conn, "note", "lunch plans", project="acme")
    key = ("event", event_id)

    assert key not in {(r["src"], r["ref_id"]) for r in fts.search(conn, "event")}
    assert key not in {(r["src"], r["ref_id"]) for r in fts.search(conn, "acme")}
    assert key in {(r["src"], r["ref_id"]) for r in fts.search(conn, "lunch")}


def test_search_recent_orders_equal_matches_by_timestamp(db_path):
    db.init(db_path); conn = db.connect(db_path)
    old_id, _ = store.event_add(conn, "note", "same match text")
    new_id, _ = store.event_add(conn, "note", "same match text")
    conn.execute("UPDATE events SET ts=datetime('now','-2 days') WHERE id=?", (old_id,))
    conn.commit()

    plain = [r["ref_id"] for r in fts.search(conn, "same match text")]
    recent = [r["ref_id"] for r in fts.search(conn, "same match text", recent=True)]

    assert plain == [old_id, new_id]
    assert recent == [new_id, old_id]
