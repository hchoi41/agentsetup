"""Tests of the v4 changes of package P4, contract, landing, specification and hooks (round 3 verdict).

- A-aera-11: `contract propose` writes the source expression of a column taken from a header as a backtick-quoted
  GoogleSQL identifier when the header is a GoogleSQL reserved keyword or not a plain identifier (`order`,
  `Order Date`); the contract .md shows it; queries/<t>.sql quotes it on both sides of AS; the SQL inside a database
  Power Query writes such an identifier with that connector's quoting; a source expression the user wrote is left
  as written; a plain header stays bare.
- A-aera-12: a contract column whose name is a helper column build adds (row, in_raw, dup_key, dup) or the null
  flag of another column gets note N-HELPER-NAME from `contract propose`, with a suggested destination name, and
  build refuses it (exit 2) with the column and the fix.
- A-aera-4: a reply that postpones the decision is refused by `contract agree` (every channel) and by the contract
  gate hook: no agreement was given; wait for the user's reply. Approvals in any words are still recorded.

The hook tests run each hook as Claude Code does (python -I, the payload on stdin), with the helpers of
test_hooks.py. They are skipped when PLUGIN/hooks is absent (the skill copied alone).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lib import common, contract, land, modelfolder
from lib import spec as specmod
from test_hooks import HOOKS, ROTM, assert_refused, assert_silent, bash, model, run_hook  # noqa: F401

SCRIPTS = Path(__file__).resolve().parents[1]
hooks_present = pytest.mark.skipif(not (HOOKS / "hooks.json").is_file(),
                                   reason="PLUGIN/hooks is absent (the skill is used without its plugin)")
BY = "Owner"


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ROTM_NO_EXCEL"] = "1"
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", env=env, timeout=300)


def _csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(",".join(r) for r in [header, *rows]) + "\n", encoding="utf-8")
    return path


def _md(root: Path, table: str) -> str:
    return (root / "contracts" / f"{table}.contract.md").read_text(encoding="utf-8")


def _cpath(root: Path, table: str) -> Path:
    return modelfolder.contract_path(root, table)


# ============================================================================= A-aera-11: quoted identifiers

def _orders(tmp_path: Path) -> Path:
    """A contract proposed from a sample whose headers are order (reserved), Order Date (a space) and amount."""
    root = tmp_path / "m"
    sample = _csv(tmp_path / "sample" / "orders.csv", ["order", "Order Date", "amount"],
                  [["1", "2026-01-05", "10"], ["2", "2026-01-06", "20"], ["3", "2026-01-07", "30"]])
    contract.propose(root, "orders", csv=sample, key="order", units=["amount=KRW", "order=count"])
    return root


def test_propose_quotes_a_reserved_or_spaced_header_and_the_md_shows_it(tmp_path: Path) -> None:
    root = _orders(tmp_path)
    data = common.read_json(_cpath(root, "orders"))
    cols = {c["name"]: c["source_expression"] for c in data["columns"]}
    assert cols == {"order": "`order`", "order_date": "`Order Date`", "amount": "amount"}
    md = _md(root, "orders")
    assert "| `order` |" in md and "| `Order Date` |" in md and "| amount |" in md


def test_the_googlesql_quotes_the_identifier_on_both_sides_of_as(tmp_path: Path) -> None:
    root = _orders(tmp_path)
    contract.agree(root, "orders", reply="agreed", by=BY)
    r = run_cli("land", str(root), "--table", "orders")
    assert r.returncode == 0, r.stderr
    sql = (root / "queries" / "orders.sql").read_text(encoding="utf-8")
    body = [line for line in sql.splitlines() if not line.startswith("--")]
    assert "  `order` AS `order`," in body
    assert "  `Order Date` AS order_date," in body
    assert "  amount" in body                                    # a plain header stays bare, with no AS
    assert "ORDER BY `order`" in body
    assert not any(line.strip().startswith("order AS") for line in body)


def test_propose_without_a_sample_quotes_a_reserved_name(tmp_path: Path) -> None:
    root = tmp_path / "m"
    contract.propose(root, "orders", columns="order,amount:number", key="order")
    data = common.read_json(_cpath(root, "orders"))
    assert [c["source_expression"] for c in data["columns"]] == ["`order`", "amount"]


@pytest.mark.parametrize("system, quoted, plain", [
    ("SQL Server", "[order] AS [order]", "amount AS [amount]"),
    ("PostgreSQL", '""order"" AS ""order""', 'amount AS ""amount""'),
    ("MySQL", "`order` AS `order`", "amount AS `amount`"),
    ("BigQuery", "`order` AS `order`", "amount AS `amount`"),
])
def test_the_database_power_query_quotes_the_identifier_for_its_connector(tmp_path: Path, system: str, quoted: str,
                                                                          plain: str) -> None:
    root = _orders(tmp_path)
    data = common.read_json(_cpath(root, "orders"))
    data["source"]["system"] = system
    m = land.m_text(data)
    assert quoted in m and plain in m, m
    spaced = {"SQL Server": "[Order Date] AS [order_date]", "PostgreSQL": '""Order Date"" AS ""order_date""',
              "MySQL": "`Order Date` AS `order_date`", "BigQuery": "`Order Date` AS `order_date`"}[system]
    assert spaced in m, m
    if system in ("SQL Server", "PostgreSQL"):
        assert "`" not in m
    # queries/<t>.sql stays GoogleSQL
    assert "  `order` AS `order`," in land.sql_text(data)


def test_a_source_expression_the_user_wrote_is_left_as_written(tmp_path: Path) -> None:
    root = _orders(tmp_path)
    data = common.read_json(_cpath(root, "orders"))
    data["source"]["system"] = "SQL Server"
    data["columns"][0]["source_expression"] = "CAST(o.`order` AS INT64)"
    data["columns"][1]["source_expression"] = "o.order_date"
    sql = land.sql_text(data)
    assert "  CAST(o.`order` AS INT64) AS `order`," in sql and "  o.order_date AS order_date," in sql
    m = land.m_text(data)
    assert "CAST(o.`order` AS INT64) AS [order]" in m and "o.order_date AS [order_date]" in m


# ============================================================================= A-aera-12: helper column names

def _helper_named(tmp_path: Path, columns: str = "id,in_raw:integer,row:integer,amount:number") -> Path:
    root = tmp_path / "m"
    r = run_cli("contract", "propose", str(root), "--table", "sales", "--columns", columns, "--key", "id",
                "--unit", "amount=KRW", "--json", str(tmp_path / "propose.json"))
    assert r.returncode == 0, r.stderr
    (tmp_path / "propose.out").write_text(r.stdout, encoding="utf-8")
    return root


def _note(name: str, helper: str, table: str = "sales") -> str:
    return (f"column '{name}' of '{table}' has the name of a helper column build adds ({helper}); suggested "
            f"destination name: '{name}_src'")


def test_propose_notes_a_column_named_like_a_helper_column(tmp_path: Path) -> None:
    root = _helper_named(tmp_path)
    out = (tmp_path / "propose.out").read_text(encoding="utf-8")
    assert _md(root, "sales") in out                                    # the readable contract is printed in full
    for name in ("in_raw", "row"):
        assert f"note (N-HELPER-NAME): {_note(name, name)}" in out, out
        assert _note(name, name) in _md(root, "sales")                 # under "Points to confirm"
    result = json.loads((tmp_path / "propose.json").read_text(encoding="utf-8"))
    assert f"N-HELPER-NAME: {_note('in_raw', 'in_raw')}" in result["notes"]
    assert "N-HELPER-NAME" not in out.split("note (N-HELPER-NAME)")[0]  # no note for id and amount
    assert not any("'id' of" in n or "'amount' of" in n for n in result["notes"])


def test_propose_notes_a_column_named_like_the_null_flag_of_another(tmp_path: Path) -> None:
    root = tmp_path / "m"
    contract.propose(root, "sales", columns="id,amount:number,amount_null:integer", key="id", units=["amount=KRW"])
    data = common.read_json(_cpath(root, "sales"))
    assert data["columns"][1]["null_rule"] == "allowed"                 # so build adds the flag amount_null
    helper = "amount_null, the null flag of column 'amount'"
    assert _note("amount_null", helper) in data["notes"]
    assert contract.helper_name_clashes(data["columns"]) == [("amount_null", helper)]


def test_propose_gives_no_helper_note_for_other_names(tmp_path: Path) -> None:
    root = tmp_path / "m"
    r = run_cli("contract", "propose", str(root), "--table", "sales", "--columns", "id,rows,in_raw_src,dup2")
    assert r.returncode == 0 and "N-HELPER-NAME" not in r.stdout


def _spec(root: Path) -> None:
    common.write_json(root / "model.spec.json", {"rotm_spec": 1, "name": "helper_names", "title": "Helper names",
                                                 "sheets": [
        {"name": "raw_sales", "type": "raw", "table": "sales"},
        {"name": "h_sales", "type": "helpers", "table": "sales"},
        {"name": "calc", "type": "calc", "blocks": [
            {"id": "tot", "title": "Total", "unit": "KRW", "rows": {"values": ["all"]}, "cell": "SUM({h:sales.amount})",
             "key": True}]},
        {"name": "checks", "type": "checks", "checks": [
            {"id": "tot_ties", "kind": "equal", "label": "Total ties to raw", "actual": "{cell:tot}",
             "expected": "SUM({raw:sales.amount})"}]}]})


def _agreed_and_landed(tmp_path: Path, columns: str, header: list[str], rows: list[list[str]]) -> Path:
    root = _helper_named(tmp_path, columns)
    contract.agree(root, "sales", reply="agreed", by=BY)
    r = run_cli("land", str(root), "--table", "sales", "--csv", str(_csv(tmp_path / "sales.csv", header, rows)))
    assert r.returncode == 0, r.stdout + r.stderr
    _spec(root)
    return root


def test_build_refuses_contract_columns_named_in_raw_and_row(tmp_path: Path) -> None:
    root = _agreed_and_landed(tmp_path, "id,in_raw:integer,row:integer,amount:number",
                              ["id", "in_raw", "row", "amount"], [["A1", "0", "7", "10"], ["A2", "0", "8", "20"]])
    r = run_cli("build", str(root / "model.spec.json"), "--strict")
    text = r.stdout + r.stderr
    assert r.returncode == 2, text
    for name in ("in_raw", "row"):
        assert (f"contract column '{name}' of 'sales' has the name of a helper column build adds ({name})") in text
        assert (f"rename the destination column in contracts/sales.contract.json (for example '{name}_src'), keep "
                "its source expression, then show the contract and get it agreed again") in text
    assert "contract show" in text and "contract agree" in text
    assert not (root / "model.xlsx").exists()                           # nothing was built
    with pytest.raises(common.Refusal) as exc:
        specmod.load_spec(root)
    assert sum("has the name of a helper column build adds" in d for d in exc.value.details) == 2


def test_load_spec_refuses_a_column_named_like_the_null_flag_of_another(tmp_path: Path) -> None:
    root = _agreed_and_landed(tmp_path, "id,amount:number,amount_null:integer", ["id", "amount", "amount_null"],
                              [["A1", "10", "0"], ["A2", "", "1"]])
    with pytest.raises(common.Refusal) as exc:
        specmod.load_spec(root)
    found = [d for d in exc.value.details if "has the name of a helper column" in d]
    assert len(found) == 1 and "contract column 'amount_null' of 'sales'" in found[0]
    assert "(amount_null, the null flag of column 'amount')" in found[0] and "'amount_null_src'" in found[0]


def test_a_renamed_destination_column_loads(tmp_path: Path) -> None:
    root = _agreed_and_landed(tmp_path, "id,in_raw_src:integer,row_src:integer,amount:number",
                              ["id", "in_raw_src", "row_src", "amount"], [["A1", "0", "7", "10"]])
    s = specmod.load_spec(root)
    assert s.helper_columns["sales"][:4] == ["row", "in_raw", "dup_key", "dup"]
    assert "in_raw_src" in s.helper_columns["sales"] and "row_src" in s.helper_columns["sales"]


# ============================================================================= A-aera-4: replies that postpone

POSTPONED = [
    "I will review the column contract tomorrow.",
    "Let me check with finance first.",
    "검토 후 알려줄게",
    "아직",
    "Not yet.",
    "I'll get back to you on the key.",
    "Review it later, please.",
    "I need to look at it first.",
    "We still need to check the totals.",
    "나중에 볼게요",
    "내일 답할게요",
    "확인해 볼게",
]
RECORDED = ["agreed", "looks good, go ahead", "Reviewed, agreed.", "ok, agreed. amount is net of VAT.",
            "Checked the totals, agreed.", "yes, go ahead", "동의합니다", "검토했습니다. 동의합니다"]


def _proposed(tmp_path: Path) -> Path:
    root = tmp_path / "m"
    sample = _csv(tmp_path / "sample" / "sales.csv", ["id", "region", "amount"],
                  [["A1", "East", "10"], ["A2", "West", "20"], ["A3", "East", "30"]])
    contract.propose(root, "sales", csv=sample, key="id", units=["amount=KRW"], totals=["amount=60"])
    return root


@pytest.mark.parametrize("reply", POSTPONED)
def test_a_reply_that_postpones_is_refused(tmp_path: Path, reply: str) -> None:
    root = _proposed(tmp_path)
    why = contract.reads_as_refusal(reply)
    assert why is not None and why.startswith("postpones the decision ('"), why
    before = _cpath(root, "sales").read_bytes()
    with pytest.raises(common.Refusal) as exc:
        contract.agree(root, "sales", reply=reply, by=BY)
    line = exc.value.line()
    assert "postpones the decision ('" in line and "no agreement was given; wait for the user's reply" in line
    assert "wait for the user's reply to contracts/sales.contract.md and record it with" in line
    assert "contract agree" in line
    assert _cpath(root, "sales").read_bytes() == before and modelfolder.contract_state(root, "sales") == "proposed"


def test_the_command_line_refuses_a_reply_that_postpones(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    r = run_cli("contract", "agree", str(root), "--table", "sales", "--reply",
                "I will review the column contract tomorrow.", "--by", BY)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "postpones the decision ('will review')" in r.stderr
    assert "no agreement was given; wait for the user's reply" in r.stderr and "Traceback" not in r.stderr
    assert modelfolder.contract_state(root, "sales") == "proposed"


def test_the_task_file_channel_refuses_a_reply_that_postpones(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    task = tmp_path / "task.md"
    task.write_text("# Task\n\nI will review the column contract tomorrow.\n", encoding="utf-8")
    r = run_cli("contract", "agree", str(root), "--table", "sales", "--reply",
                "I will review the column contract tomorrow.", "--by", BY, "--channel", "task-file",
                "--source-file", str(task))
    assert r.returncode == 2 and "postpones the decision" in r.stderr, r.stderr
    assert "no agreement was given; wait for the user's reply" in r.stderr
    # a short reply that occurs only inside a sentence that postpones is refused too
    task.write_text("# Task\n\nNot agreed by now: I will review the column contract tomorrow.\n", encoding="utf-8")
    with pytest.raises(common.Refusal) as exc:
        contract.agree(root, "sales", reply="agreed", by=BY, channel="task-file", source_file=task)
    assert "reads as a refusal or a change request" in exc.value.line()
    task.write_text("# Task\n\nContract reply: Let me check with finance first.\n", encoding="utf-8")
    with pytest.raises(common.Refusal) as exc:
        contract.agree(root, "sales", reply="Contract reply: Let me check with finance first.", by=BY,
                       channel="task-file", source_file=task)
    assert "postpones the decision ('let me check')" in exc.value.line()
    assert modelfolder.contract_state(root, "sales") == "proposed"


@pytest.mark.parametrize("reply", RECORDED)
def test_approvals_are_still_recorded(tmp_path: Path, reply: str) -> None:
    root = _proposed(tmp_path)
    assert contract.reads_as_refusal(reply) is None, reply
    data = contract.agree(root, "sales", reply=reply, by=BY)
    assert data["agreement"]["reply"] == reply and modelfolder.contract_state(root, "sales") == "agreed"


def test_pre_agreed_words_in_a_task_file_are_recorded(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    task = tmp_path / "task.md"
    task.write_text("# Task\n\nContract agreement: pre-agreed for this test\n\n- Land the CSV after the agreement.\n",
                    encoding="utf-8")
    contract.agree(root, "sales", reply="pre-agreed for this test", by=BY, channel="task-file", source_file=task)
    assert modelfolder.contract_state(root, "sales") == "agreed"


def _transcript(tmp_path: Path, entries: list[dict]) -> Path:
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8")
    return t


def _user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _propose_call(root: Path, table: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_p", "name": "Bash",
         "input": {"command": f'{ROTM} contract propose "{root.as_posix()}" --table {table} --csv data/{table}.csv '
                              "--force"}}]}}


def _agree_cmd(table: str, reply: str, extra: str = "") -> str:
    return f'{ROTM} contract agree . --table {table} --reply "{reply}" --by {BY}{extra}'


@hooks_present
def test_the_hook_refuses_a_reply_that_postpones(model: Path, tmp_path: Path) -> None:
    reply = "I will review the column contract tomorrow."
    t = _transcript(tmp_path, [_propose_call(model, "sales"), _user(reply)])
    out = run_hook("contract_gate", bash(_agree_cmd("sales", reply), model, transcript=t))
    assert_refused(out, "postpones the decision ('will review')", "no agreement was given; wait for the user's reply",
                   "wait for the user's reply to contracts/sales.contract.md and record it with", "contract agree")
    assert len(out.stderr.strip().splitlines()) == 1
    # the task-file channel is refused by the hook too
    out = run_hook("contract_gate", bash(_agree_cmd("sales", reply, " --channel task-file --source-file task.md"),
                                         model, transcript=t))
    assert_refused(out, "postpones the decision", "no agreement was given; wait for the user's reply")


@hooks_present
@pytest.mark.parametrize("reply", ["Reviewed, agreed.", "looks good, go ahead"])
def test_the_hook_lets_approvals_through(model: Path, tmp_path: Path, reply: str) -> None:
    t = _transcript(tmp_path, [_propose_call(model, "sales"), _user(reply)])
    assert_silent(run_hook("contract_gate", bash(_agree_cmd("sales", reply), model, transcript=t)))
