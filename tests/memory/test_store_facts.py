from memorylib import db, store


def test_fact_add_and_supersede_retires_old_and_drops_its_fts(db_path):
    db.init(db_path); conn = db.connect(db_path)
    old, _ = store.fact_add(conn, "budget is $100k", "finance", subject="Q3")
    new, _ = store.fact_add(conn, "budget is $120k", "finance", subject="Q3", supersedes=old)
    row = conn.execute("SELECT valid, superseded_by FROM facts WHERE id=?", (old,)).fetchone()
    assert row[0] == 0 and row[1] == new
    # retired fact's FTS row is gone; new fact's is present
    assert conn.execute("SELECT count(*) FROM memory_fts WHERE src='fact' AND ref_id=?", (old,)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM memory_fts WHERE src='fact' AND ref_id=?", (new,)).fetchone()[0] == 1
