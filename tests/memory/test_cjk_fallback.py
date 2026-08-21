from memorylib import db, store, fts

def test_korean_query_returns_hit_via_fallback(db_path):
    db.init(db_path); conn = db.connect(db_path)
    store.fact_add(conn, "이번 분기 예산은 확정되었다", "finance", subject="예산")
    assert len(fts.search(conn, "예산")) >= 1

def test_retired_korean_fact_not_returned_by_fallback(db_path):
    db.init(db_path); conn = db.connect(db_path)
    old, _ = store.fact_add(conn, "이 사실은 폐기되었다 예산", "finance")
    store.fact_add(conn, "최신 예산 사실", "finance", supersedes=old)
    hits = fts.search(conn, "폐기되었다")
    assert all(r["ref_id"] != old or r["src"] != "fact" for r in hits)   # H3: valid=1 honored

def test_fallback_treats_percent_literally(db_path):
    db.init(db_path); conn = db.connect(db_path)
    store.fact_add(conn, "예산 50% 달성", "finance")
    store.fact_add(conn, "예산 5000 목표", "finance")
    # query has a CJK token (routes through _cjk_fallback) AND a literal '%';
    # '%' must be ESCAPEd, so the '5000' row must NOT match.
    hits = fts.search(conn, "예산 50%")
    assert any("50%" in r["content"] for r in hits)
    assert all("5000" not in r["content"] for r in hits)

def test_subjectless_korean_fact_found_via_fallback(db_path):
    db.init(db_path); conn = db.connect(db_path)
    store.fact_add(conn, "예산 협의가 완료되었다", "finance")   # subject defaults to None — COALESCE guard (H2)
    assert len(fts.search(conn, "협의")) >= 1
