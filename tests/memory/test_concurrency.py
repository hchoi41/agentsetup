import sqlite3, subprocess, sys, time, pytest
from memorylib import db, store


def test_two_short_writers_both_succeed(db_path):
    db.init(db_path)
    c1, c2 = db.connect(db_path), db.connect(db_path)
    store.fact_add(c1, "from writer 1", "c")
    store.event_add(c2, "note", "from writer 2")
    assert db.connect(db_path).execute("SELECT count(*) FROM facts").fetchone()[0] == 1


def test_writer_held_over_5s_yields_clean_error_not_crash(db_path, tmp_path):
    db.init(db_path)
    holder = tmp_path / "hold.py"
    holder.write_text(
        "import sqlite3,sys,time\n"
        "c=sqlite3.connect(sys.argv[1],timeout=30)\n"
        "c.execute('PRAGMA busy_timeout=30000'); c.execute('BEGIN IMMEDIATE')\n"
        "c.execute(\"INSERT INTO ops_log(op) VALUES('hold')\"); time.sleep(7)\n", encoding="utf-8")
    p = subprocess.Popen([sys.executable, str(holder), db_path])
    time.sleep(1.0)
    try:
        conn = db.connect(db_path)
        with pytest.raises(sqlite3.OperationalError):   # clean error within busy_timeout, NOT a crash/hang
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO ops_log(op) VALUES('blocked')")
    finally:
        p.wait()
