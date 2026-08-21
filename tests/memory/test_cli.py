import memory


def test_cli_init_then_fact_then_search(db_path, capsys):
    assert memory.main(["--db", db_path, "init"]) == 0
    assert memory.main(["--db", db_path, "fact", "add", "--statement", "CLI works", "--category", "test"]) == 0
    assert memory.main(["--db", db_path, "search", "CLI"]) == 0
    assert "CLI works" in capsys.readouterr().out


def test_cli_secret_warning_prints(db_path, capsys):
    memory.main(["--db", db_path, "init"])
    memory.main(["--db", db_path, "event", "add", "--kind", "note",
                 "--content", "token " + "AKIA" + "ABCDEFGHIJKLMNOP"])
    assert "secret" in capsys.readouterr().out.lower()


def test_cli_query_denials_return_error_code(db_path):
    assert memory.main(["--db", db_path, "init"]) == 0
    for stmt in [
        "DROP TABLE facts",
        "INSERT INTO facts(statement,category) VALUES('x','c')",
        "ATTACH DATABASE 'x.db' AS x",
        "PRAGMA journal_mode=DELETE",
        "SELECT 1; DROP TABLE facts",
    ]:
        assert memory.main(["--db", db_path, "query", stmt]) == 1
