"""Tests for lib/contract.py (design sections 5 and 15; plan F03). Owner: M5."""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lib import common, contract, modelfolder, schemas

SCRIPTS = Path(__file__).resolve().parents[1]
ROTM = SCRIPTS / "rotm.py"


def write_csv(path: Path, header: list[str], rows: list[list[str]], encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def sales_rows(n: int) -> list[list[str]]:
    regions = ["East", "North", "South", "West"]
    out = []
    for i in range(n):
        out.append([f"00{1000 + i // 2}", str(i % 2 + 1), (dt.date(2026, 1, 1) + dt.timedelta(days=i % 28)).isoformat(),
                    regions[i % 4], str(100 + 7.5 * i)])
    return out


SALES_HEADER = ["invoice_id", "line_no", "inv_date", "region", "amount"]


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    return write_csv(tmp_path / "sample" / "sales.csv", SALES_HEADER, sales_rows(30))


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(ROTM), *args], capture_output=True,
                          text=True, encoding="utf-8", cwd=cwd, env=dict(os.environ))


# ----------------------------------------------------------------------------- types and values

@pytest.mark.parametrize("values,expected", [
    (["TRUE", "false", "True"], "boolean"),
    (["1", "-2", "30"], "integer"),
    (["1", "2.5", "-3e2"], "number"),
    (["2026-01-31", "2026-02-28"], "date"),
    (["2026-01-31T10:00:00", "2026-02-28 08:30"], "datetime"),
    (["2026-01-31", "2026-02-28T08:30:00"], "datetime"),
    (["00123", "00456"], "text"),            # leading zeros: an ID stays text
    (["007.5", "1.5"], "text"),
    (["1234567890123456"], "text"),          # 16 digits: more than Excel keeps
    (["1,234", "5"], "text"),                # thousands separators are not numbers
    (["abc", "1"], "text"),
    ([], "text"),
    (["", ""], "text"),
    (["2026-02-30"], "text"),                # not a real date
])
def test_infer_type(values: list[str], expected: str) -> None:
    assert contract.infer_type(values) == expected


@pytest.mark.parametrize("value,ctype,ok,typed", [
    ("12", "integer", True, 12), ("12.0", "integer", True, 12), (12.0, "integer", True, 12), (12.5, "integer", False, None),
    (True, "integer", False, None), ("x", "integer", False, None),
    ("1.5", "number", True, 1.5), (3, "number", True, 3), ("1,5", "number", False, None), ("nan", "number", False, None),
    ("2026-01-31", "date", True, dt.date(2026, 1, 31)), (dt.datetime(2026, 1, 31), "date", True, dt.date(2026, 1, 31)),
    (dt.datetime(2026, 1, 31, 10, 0), "date", False, None), (46053, "date", True, dt.date(2026, 1, 31)),
    ("31/01/2026", "date", False, None),
    ("2026-01-31 10:00:00", "datetime", True, dt.datetime(2026, 1, 31, 10, 0)),
    ("TRUE", "boolean", True, True), (False, "boolean", True, False), (1, "boolean", False, None),
    (123, "text", True, "123"), ("", "number", True, None), (None, "date", True, None),
])
def test_coerce(value, ctype, ok, typed) -> None:
    assert contract.coerce(value, ctype) == (ok, typed)


def test_clean_name() -> None:
    assert contract.clean_name("invoice_id", 0) == "invoice_id"
    assert contract.clean_name("Invoice ID", 0) == "invoice_id"
    assert contract.clean_name("2026 Sales (KRW)", 1) == "c_2026_sales_krw"
    assert contract.clean_name("", 2) == "column_3"
    assert contract.clean_name("매출액", 0) == "매출액"          # Unicode letters are kept (design 5)


# ----------------------------------------------------------------------------- profile and proposal

def test_profile_counts_nulls_and_keys(tmp_path: Path) -> None:
    rows = [["A1", "x", ""], ["A2", "x", "5"], ["A3", "y", "7"]]
    p = write_csv(tmp_path / "t.csv", ["id", "kind", "qty"], rows)
    prof = contract.profile_csv(p)
    cols = {c["name"]: c for c in prof["columns"]}
    assert prof["rows"] == 3
    assert cols["qty"]["nulls"] == 1 and cols["qty"]["type"] == "integer"
    assert contract.key_candidates(prof, [c["type"] for c in prof["columns"]]) == [["id"]]


def test_key_candidates_pair_and_none(tmp_path: Path) -> None:
    p = write_csv(tmp_path / "t.csv", ["inv", "line", "amount"], [["1", "1", "5"], ["1", "2", "6"], ["2", "1", "7"]])
    prof = contract.profile_csv(p)
    # amount is unique but a number column is never proposed as a key
    assert contract.key_candidates(prof, ["integer", "integer", "number"]) == [["inv", "line"]]
    p2 = write_csv(tmp_path / "u.csv", ["a", "b"], [["1", "1"], ["1", "1"]])
    prof2 = contract.profile_csv(p2)
    assert contract.key_candidates(prof2, ["integer", "integer"]) == []


def test_allowed_values_only_for_repeating_low_cardinality_text(tmp_path: Path) -> None:
    rows = [[f"id{i}", ["East", "West"][i % 2], f"note{i}", "only"] for i in range(20)]
    p = write_csv(tmp_path / "t.csv", ["id", "region", "free", "same"], rows)
    cols = {c["name"]: c for c in contract.infer_columns(p)}
    assert cols["region"]["allowed"] == ["East", "West"]
    assert cols["id"]["allowed"] is None          # unique: the key, not an enumeration
    assert cols["free"]["allowed"] is None        # 20 distinct values: more than 12
    assert cols["same"]["allowed"] is None        # one value only


def test_propose_from_csv_writes_valid_proposed_contract(tmp_path: Path, sample: Path) -> None:
    root = tmp_path / "model"
    path = contract.propose(root, "sales", csv=sample, key="invoice_id,line_no", grain="one row per invoice line",
                            rows="1:100000", units=["amount=KRW", "line_no=count"], purpose="Invoice lines")
    assert modelfolder.is_model_root(root)                   # the marker is written when absent (design D3)
    data = common.read_json(path)
    assert schemas.validate(data, "contract") == []
    assert data["agreement"]["status"] == "proposed"
    assert modelfolder.contract_state(root, "sales") == "proposed"
    cols = {c["name"]: c for c in data["columns"]}
    assert [c["name"] for c in data["columns"]] == SALES_HEADER
    assert cols["invoice_id"]["type"] == "text"              # leading zeros kept
    assert cols["line_no"]["type"] == "integer" and cols["line_no"]["unit"] == "count"
    assert cols["inv_date"]["type"] == "date" and cols["inv_date"]["unit"] == "date"
    assert cols["amount"]["type"] == "number" and cols["amount"]["unit"] == "KRW"
    assert cols["region"]["allowed"] == ["East", "North", "South", "West"]
    assert all(c["null_rule"] == "never" for c in data["columns"])
    assert cols["amount"]["observed"]["sum"] == pytest.approx(sum(100 + 7.5 * i for i in range(30)))
    assert data["expect"]["rows"] == {"min": 1, "max": 100000}
    assert data["key"] == ["invoice_id", "line_no"]
    assert data["raw_sheet"] == "raw_sales" and data["excel_table"] == "tbl_raw_sales"
    md = (root / "contracts" / "sales.contract.md").read_text(encoding="utf-8")
    assert md.rstrip().endswith("Reply 'agreed' or say what to change.")
    for name in SALES_HEADER:
        assert f"| {name} |" in md
    assert "Expected rows: from 1 to 100,000" in md


def test_propose_defaults_say_what_was_guessed(tmp_path: Path, sample: Path) -> None:
    path = contract.propose(tmp_path / "m", "sales", csv=sample)
    data = common.read_json(path)
    notes = " ".join(data["notes"])
    assert "grain proposed from the key" in notes
    assert "expected rows proposed from the sample (30 rows)" in notes
    assert "unit not known for line_no, amount" in notes
    assert "no control total" in notes
    assert data["expect"]["rows"] == {"min": 1, "max": 1000}
    assert data["key"] == ["invoice_id", "line_no"]          # the first unique pair of the sample


def test_propose_cleans_headers_and_keeps_source_expression(tmp_path: Path) -> None:
    p = write_csv(tmp_path / "t.csv", ["Invoice ID", "Amount (KRW)", "Amount (KRW)"], [["A", "1", "2"], ["B", "3", "4"]])
    data = common.read_json(contract.propose(tmp_path / "m", "t1", csv=p))
    names = [c["name"] for c in data["columns"]]
    assert names == ["invoice_id", "amount_krw", "amount_krw_2"]
    assert data["columns"][0]["source_expression"] == "`Invoice ID`"    # v4, A-aera-11: a GoogleSQL identifier
    assert any("repeats an earlier header" in n for n in data["notes"])


def test_propose_moves_a_never_null_column_first(tmp_path: Path) -> None:
    p = write_csv(tmp_path / "t.csv", ["note", "id"], [["", "a"], ["x", "b"]])
    data = common.read_json(contract.propose(tmp_path / "m", "t1", csv=p, key="id"))
    assert [c["name"] for c in data["columns"]] == ["id", "note"]
    assert any("moved first" in n for n in data["notes"])


def test_propose_from_columns(tmp_path: Path) -> None:
    data = common.read_json(contract.propose(tmp_path / "m", "gl", columns="account:text,period:date,amount:number",
                                             key="account,period", rows="500", units=["amount=KRW"],
                                             totals=["amount=1234.5:0.5"]))
    assert [(c["name"], c["type"]) for c in data["columns"]] == [("account", "text"), ("period", "date"),
                                                                 ("amount", "number")]
    assert data["columns"][0]["null_rule"] == "never" and data["columns"][1]["null_rule"] == "never"
    assert data["expect"] == {"rows": {"exact": 500}, "totals": [
        {"column": "amount", "value": 1234.5, "tolerance": 0.5, "note": "source-side control total given with --total"}]}
    assert schemas.validate(data, "contract") == []


@pytest.mark.parametrize("kwargs,text", [
    ({"table": "Sales"}, "table name 'Sales' is not allowed"),
    ({"csv": None, "columns": None}, "give the columns"),
    ({"columns": "a,b"}, "not both"),
    ({"key": "nope"}, "'nope' is not a column"),
    ({"rows": "10:5"}, "greater than the maximum"),
    ({"rows": "many"}, "is not a row count"),
    ({"rows": "2000000"}, "do not fit on one sheet"),
    ({"types": ["amount=money"]}, "'money' is not a column type"),
    ({"totals": ["region=5"]}, "needs a number column"),
    ({"connector": "odbc"}, "is not known"),
])
def test_propose_refusals(tmp_path: Path, sample: Path, kwargs: dict, text: str) -> None:
    args = {"table": "sales", "csv": sample}
    args.update(kwargs)
    table = args.pop("table")
    with pytest.raises(common.Refusal) as exc:
        contract.propose(tmp_path / "m", table, **args)
    assert text in exc.value.line()
    assert exc.value.fix or exc.value.details


def test_propose_refuses_bad_files(tmp_path: Path) -> None:
    with pytest.raises(common.Refusal, match="not found"):
        contract.propose(tmp_path / "m", "t1", csv=tmp_path / "missing.csv")
    semi = tmp_path / "semi.csv"
    semi.write_text("a;b;c\n1;2;3\n", encoding="utf-8")
    with pytest.raises(common.Refusal, match="not comma-separated"):
        contract.propose(tmp_path / "m", "t1", csv=semi)
    cp = write_csv(tmp_path / "cp.csv", ["지역"], [["동부"]], encoding="cp949")
    with pytest.raises(common.Refusal, match="not UTF-8"):
        contract.propose(tmp_path / "m", "t1", csv=cp)
    assert not (tmp_path / "m").exists()          # nothing written before the file is accepted


def test_propose_refuses_columns_with_bad_names(tmp_path: Path) -> None:
    with pytest.raises(common.Refusal, match="not a valid column name"):
        contract.propose(tmp_path / "m", "t1", columns="invoice id,amount")
    with pytest.raises(common.Refusal, match="'amout' is not a column"):
        contract.propose(tmp_path / "m", "t1", columns="id,amount", types=["amout=number"])


def test_propose_inside_another_model_folder_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "m"
    modelfolder.init_model(root)
    with pytest.raises(common.Refusal, match="lies inside the model folder"):
        contract.propose(root / "raw", "t1", columns="id")


def test_propose_existing_needs_force_and_force_resets_agreement(tmp_path: Path, sample: Path) -> None:
    root = tmp_path / "m"
    contract.propose(root, "sales", csv=sample)
    with pytest.raises(common.Refusal, match="exists"):
        contract.propose(root, "sales", csv=sample)
    contract.agree(root, "sales", reply="agreed", by="Max")
    assert modelfolder.contract_state(root, "sales") == "agreed"
    contract.propose(root, "sales", csv=sample, force=True, units=["amount=KRW"])
    data = common.read_json(modelfolder.contract_path(root, "sales"))
    assert modelfolder.contract_state(root, "sales") == "proposed"
    assert data["agreement"]["history"][0]["by"] == "Max"


# ----------------------------------------------------------------------------- agree

def test_agree_session_records_the_users_words(tmp_path: Path, sample: Path) -> None:
    root = tmp_path / "m"
    contract.propose(root, "sales", csv=sample)
    data = contract.agree(root, "sales", reply="  ok, agreed. amount is net of VAT.  ", by="Max")
    ag = data["agreement"]
    assert ag["status"] == "agreed" and ag["reply"] == "ok, agreed. amount is net of VAT."
    assert ag["by"] == "Max" and ag["channel"] == "session" and ag["recorded_by"] == "rotm.py contract agree"
    assert ag["contract_sha256"] == modelfolder.contract_hash(data)
    assert modelfolder.contract_state(root, "sales") == "agreed"
    md = (root / "contracts" / "sales.contract.md").read_text(encoding="utf-8")
    assert "Agreed by Max on " in md and "'ok, agreed. amount is net of VAT.'" in md
    assert "Reply 'agreed' or say what to change." not in md


@pytest.mark.parametrize("reply,by,text", [
    ("", "Max", "--reply is empty"),
    ("   ", "Max", "--reply is empty"),
    ("agreed", "", "--by is empty"),
    ("no, the amount should be gross", "Max", "reads as a refusal"),
    ("Not yet, change region", "Max", "reads as a refusal"),
    ("아니요, 단위를 바꿔 주세요", "Max", "reads as a refusal"),
])
def test_agree_refuses_without_an_agreement(tmp_path: Path, sample: Path, reply: str, by: str, text: str) -> None:
    root = tmp_path / "m"
    contract.propose(root, "sales", csv=sample)
    with pytest.raises(common.Refusal) as exc:
        contract.agree(root, "sales", reply=reply, by=by)
    assert text in exc.value.line()
    assert modelfolder.contract_state(root, "sales") == "proposed"   # never recorded


def test_agree_task_file_needs_the_reply_in_the_file(tmp_path: Path, sample: Path) -> None:
    root = tmp_path / "m"
    contract.propose(root, "sales", csv=sample)
    task = tmp_path / "task.md"
    task.write_text("# Task\n\nThe user replied to the contract:\n\n> agreed, the amount is\n> net of VAT\n",
                    encoding="utf-8")
    with pytest.raises(common.Refusal, match="needs --source-file"):
        contract.agree(root, "sales", reply="agreed", by="Max", channel="task-file")
    with pytest.raises(common.Refusal, match="the reply is not in task.md"):
        contract.agree(root, "sales", reply="yes, go ahead", by="Max", channel="task-file", source_file=task)
    assert modelfolder.contract_state(root, "sales") == "proposed"
    data = contract.agree(root, "sales", reply="agreed, the amount is", by="Max", channel="task-file",
                          source_file=task)
    assert data["agreement"]["channel"] == "task-file"
    assert data["agreement"]["source_file"].endswith("task.md")
    assert modelfolder.contract_state(root, "sales") == "agreed"


def test_agree_task_file_accepts_wrapped_lines(tmp_path: Path, sample: Path) -> None:
    root = tmp_path / "m"
    contract.propose(root, "sales", csv=sample)
    task = tmp_path / "task.md"
    task.write_text("Reply: ok,\n  agreed as\n  proposed\n", encoding="utf-8")
    contract.agree(root, "sales", reply="ok, agreed as proposed", by="Max", channel="task-file", source_file=task)
    assert modelfolder.contract_state(root, "sales") == "agreed"


def test_agree_example_channel_only_inside_the_plugin(outside_tmp: Path, sample: Path, monkeypatch) -> None:
    # v5: outside_tmp is outside the plugin wherever --basetemp points (conftest.py)
    monkeypatch.delenv("ROTM_EXAMPLE_ROOTS", raising=False)
    root = outside_tmp / "m"
    contract.propose(root, "sales", csv=sample)
    with pytest.raises(common.Refusal, match="only for the examples and templates"):
        contract.agree(root, "sales", reply="example data", by="architect", channel="example")
    monkeypatch.setenv("ROTM_EXAMPLE_ROOTS", str(outside_tmp))
    contract.agree(root, "sales", reply="example data", by="architect", channel="example")
    assert modelfolder.contract_state(root, "sales") == "agreed"


def test_agree_missing_invalid_and_not_a_model(tmp_path: Path, sample: Path) -> None:
    with pytest.raises(common.Refusal, match="not a model folder"):
        contract.agree(tmp_path / "nothing", "sales", reply="agreed", by="Max")
    root = tmp_path / "m"
    modelfolder.init_model(root)
    with pytest.raises(common.Refusal, match="has no contract"):
        contract.agree(root, "sales", reply="agreed", by="Max")
    modelfolder.contract_path(root, "sales").write_text("{not json", encoding="utf-8")
    with pytest.raises(common.Refusal, match="not valid"):
        contract.agree(root, "sales", reply="agreed", by="Max")


def test_agreed_then_edited_is_stale_and_can_be_agreed_again(tmp_path: Path, sample: Path) -> None:
    root = tmp_path / "m"
    contract.propose(root, "sales", csv=sample)
    contract.agree(root, "sales", reply="agreed", by="Max")
    path = modelfolder.contract_path(root, "sales")
    data = common.read_json(path)
    data["grain"] = "one row per invoice"           # an edit after the agreement
    common.write_json(path, data)
    assert modelfolder.contract_state(root, "sales") == "stale"
    assert any(r["state"] == "stale" for r in contract.status_all(root))
    # the .md still shows the agreed version: the reply is recorded only after it shows the edit (v3, C-sol-3)
    with pytest.raises(common.Refusal, match="changed after it was last shown"):
        contract.agree(root, "sales", reply="yes, agreed with the new grain", by="Max")
    contract.show(root, "sales")
    again = contract.agree(root, "sales", reply="yes, agreed with the new grain", by="Max")
    assert modelfolder.contract_state(root, "sales") == "agreed"
    assert again["agreement"]["history"][-1]["state"] == "stale"


def test_agree_twice_keeps_the_first_agreement(tmp_path: Path, sample: Path) -> None:
    root = tmp_path / "m"
    contract.propose(root, "sales", csv=sample)
    first = contract.agree(root, "sales", reply="agreed", by="Max")
    second = contract.agree(root, "sales", reply="agreed again", by="Someone")
    assert second["agreement"] == first["agreement"]


def test_the_tool_never_writes_agreed_by_itself(tmp_path: Path, sample: Path) -> None:
    root = tmp_path / "m"
    contract.propose(root, "sales", csv=sample)
    contract.propose(root, "sales", csv=sample, force=True)
    contract.show(root, "sales")
    contract.status_all(root)
    assert modelfolder.contract_state(root, "sales") == "proposed"
    assert common.read_json(modelfolder.contract_path(root, "sales"))["agreement"]["reply"] is None


def test_status_all_and_show(tmp_path: Path, sample: Path) -> None:
    root = tmp_path / "m"
    contract.propose(root, "sales", csv=sample)
    contract.propose(root, "target", columns="region:text,month:date,target:number", key="region,month")
    contract.agree(root, "target", reply="agreed", by="Max")
    rows = {r["table"]: r for r in contract.status_all(root)}
    assert rows["sales"]["state"] == "proposed" and rows["sales"]["by"] is None
    assert rows["target"]["state"] == "agreed" and rows["target"]["by"] == "Max"
    md = root / "contracts" / "sales.contract.md"
    md.write_text("stale copy", encoding="utf-8")
    text, rewritten = contract.show(root, "sales")
    assert rewritten and md.read_text(encoding="utf-8") == text
    assert contract.show(root, "sales")[1] is False


def test_render_md_lists_totals_and_escapes_pipes() -> None:
    c = {"rotm_contract": 1, "table": "t1", "grain": "one row per x", "key": ["x"],
         "columns": [{"name": "x", "type": "text", "null_rule": "never", "description": "a|b"},
                     {"name": "v", "type": "number", "null_rule": "allowed", "unit": None}],
         "expect": {"rows": {"exact": 3}, "totals": [{"column": "v", "value": 1234567.5, "tolerance": 0}]}}
    md = contract.render_md(c)
    assert "a\\|b" in md
    assert "Expected rows: exactly 3" in md
    assert "Control total: sum of v = 1,234,567.5 (tolerance 0)" in md
    assert "| 2 | v | number | ? | default | allowed |" in md          # the unit is asked for; no format key


# ----------------------------------------------------------------------------- command line

def test_cli_propose_agree_show_status(tmp_path: Path, sample: Path) -> None:
    root = tmp_path / "model"
    r = run_cli("contract", "propose", str(root), "--table", "sales", "--csv", str(sample), "--key",
                "invoice_id,line_no", "--unit", "amount=KRW", "--rows", "1:100000")
    assert r.returncode == 0, r.stderr
    assert "# Column contract: sales" in r.stdout and "Reply 'agreed' or say what to change." in r.stdout
    assert "contract agree" in r.stdout and "status proposed, not agreed" in r.stdout
    r = run_cli("contract", "agree", str(root), "--table", "sales", "--reply", "no thanks", "--by", "Max")
    assert r.returncode == 2 and "Fix:" in r.stderr and "Traceback" not in r.stderr
    r = run_cli("contract", "agree", str(root), "--table", "sales", "--reply", "agreed", "--by", "Max")
    assert r.returncode == 0 and "recorded: contract 'sales' version 1 agreed by Max" in r.stdout
    r = run_cli("contract", "agree", str(root), "--table", "sales", "--reply", "agreed", "--by", "Max")
    assert r.returncode == 0 and "already agreed" in r.stdout
    r = run_cli("contract", "show", str(root), "--table", "sales")
    assert r.returncode == 0 and "Agreed by Max" in r.stdout
    r = run_cli("contract", "status", str(root), "--json", "-")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["contracts"][0]["state"] == "agreed" and "sales" in r.stderr


def test_cli_propose_json_is_alone_on_stdout(tmp_path: Path, sample: Path) -> None:
    r = run_cli("contract", "propose", str(tmp_path / "m"), "--table", "sales", "--csv", str(sample), "--json", "-")
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["state"] == "proposed" and payload["contract"]["table"] == "sales"
    assert "# Column contract: sales" in r.stderr


def test_cli_refusal_and_usage(tmp_path: Path) -> None:
    r = run_cli("contract", "propose", str(tmp_path / "m"), "--table", "Bad Name", "--columns", "a")
    assert r.returncode == 2 and "is not allowed" in r.stderr and "Traceback" not in r.stderr
    r = run_cli("contract", "agree", str(tmp_path / "m"), "--table", "sales")
    assert r.returncode == 2                                        # argparse: --reply and --by are required
