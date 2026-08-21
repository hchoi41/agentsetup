from memorylib import db, fts


def test_index_text_is_delete_then_insert_idempotent(db_path):
    db.init(db_path); conn = db.connect(db_path)
    fts.index_text(conn, "event", 1, "p", "alpha"); conn.commit()
    fts.index_text(conn, "event", 1, "p", "beta");  conn.commit()   # refresh same ref
    rows = conn.execute("SELECT content FROM memory_fts WHERE src='event' AND ref_id=1").fetchall()
    assert len(rows) == 1 and rows[0][0] == "beta"


def test_has_cjk_detects_hangul_and_plain_text():
    assert fts.has_cjk("프로젝트") is True
    assert fts.has_cjk("project") is False
    assert fts.has_cjk("cost 예산") is True
