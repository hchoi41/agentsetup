from memorylib import db


def test_init_is_idempotent_and_lands_at_v1(db_path):
    db.init(db_path)
    db.init(db_path)          # twice -> no error
    conn = db.connect(db_path)
    assert db.current_version(conn) == 1
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='facts'").fetchone()[0] == 1


def test_migrate_reports_old_new_and_is_noop_when_current(db_path):
    db.init(db_path)
    conn = db.connect(db_path)
    assert db.migrate(conn) == (1, 1)           # already at head


def test_migrate_refuses_downgrade(db_path):
    db.init(db_path)
    conn = db.connect(db_path)
    conn.execute("UPDATE schema_meta SET value='99' WHERE key='schema_version'")
    conn.commit()
    import pytest
    with pytest.raises(Exception):
        db.migrate(conn)


def test_migrate_step_is_atomic_on_crash(db_path, monkeypatch):
    from memorylib import db as dbm
    import pytest
    broken = dbm.V1A_DDL + "; THIS IS NOT VALID SQL"     # force a mid-step failure after the real DDL
    monkeypatch.setattr(dbm, "MIGRATIONS", [(1, broken)])
    with pytest.raises(Exception):
        dbm.init(db_path)
    conn = dbm.connect(db_path)
    assert dbm.current_version(conn) == 0                 # version did NOT advance
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='facts'").fetchone()[0] == 0  # DDL rolled back too
