"""Tests of the v5 changes of package P4 (round 4 verdict, 2026-11-25). Owner: P4.

- [A-aera-6, C-sol-3]: `land MODEL --table <t> --csv <file> --force --control <t>.<column>=<source total>` lands a
  refreshed export whose source total changed: the file is validated against the agreed contract, its sum is
  compared with the total given, and the given total is recorded for the snapshot's digest in
  raw/<t>.controls.json (the record an acceptance writes). check and build then take that total. A given total that
  the file's sum does not meet is L-TOTAL and nothing is written. Without --control land behaves as in v4. Tested
  without Google Sheets: a copy of the built workbook whose raw rows (and helper rows, repaired as
  references/google-sheets.md 'What a refresh does' says) are those of the export stands in for the download.
- [A-aera-4, C-sol-2]: a reply in which every sentence is a question is no agreement (contract agree, every channel,
  and the contract gate hook); the hook reads the sentence that holds a reply taken from a longer user message, and
  the whole message, and a refusing sentence that names another contract table does not count against this table.
- [A-aera-16, A-aera-17] part (1): contract propose and land read a date and time with one function
  (land.read_datetime_text): a time with a T and an offset, or a Z, is proposed as datetime and lands with
  L-PRECISION; a value that cannot be cast is one L-TYPE and no L-NULL.
- Supporting [A-aera-2, C-sol-1, C-sol-4] part (2): land.attach_power_query records query_body_sha256 (the body hash
  of queries/<t>.pq.m as it was read for the attach); land --keep-queries and land --csv do not change it.

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v5_land.py -q -p no:cacheprovider
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lib import common, contract, controls, excel, land, manifest, modelfolder
from test_hooks import HOOKS, ROTM, assert_refused, assert_silent, bash, run_hook, write_contract  # noqa: F401

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PY = [sys.executable, "-P", "-X", "utf8", "-B"]
ROTM_CMD = PY + [str(SCRIPTS / "rotm.py")]
BY = "Owner"
hooks_present = pytest.mark.skipif(not (HOOKS / "hooks.json").is_file(),
                                   reason="PLUGIN/hooks is absent (the skill is used without its plugin)")


def _rotm(*args: str, excel_on: bool = False) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if excel_on:
        env.pop("ROTM_NO_EXCEL", None)
    else:
        env["ROTM_NO_EXCEL"] = "1"
    return subprocess.run(ROTM_CMD + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=900)


def _report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _rules(report: dict, severity: str | None = None) -> list[str]:
    return [f["rule"] for f in report["findings"] if severity is None or f["severity"] == severity]


def _csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    return path


# ============================================================================= [A-aera-6, C-sol-3] export with a total

@pytest.fixture(scope="module")
def static_model(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The starter template (init --template starter), its contract shown and agreed, its sample landed, built
    without Excel: a static model, as a model converted to Google Sheets starts (copied from test_v4_docs.py)."""
    root = tmp_path_factory.mktemp("gs_total") / "model"
    for args in (("init", str(root), "--template", "starter"),
                 ("contract", "show", str(root), "--table", "lines"),
                 ("contract", "agree", str(root), "--table", "lines", "--reply", "agreed", "--by", "tester"),
                 ("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv")),
                 ("build", str(root), "--strict")):
        proc = _rotm(*args)
        assert proc.returncode == 0, (args, proc.stdout[-2000:], proc.stderr[-2000:])
    return root


def _export(model: Path, folder: Path, *, more: int = 0, fewer: int = 0) -> tuple[Path, list[dict], int]:
    """The CSV export of the refreshed extract (after test_v4_docs.py): the landed rows, with `more` rows added at
    the end or the last `fewer` rows left out; the total changes with them. Returns (file, rows, total)."""
    with open(model / "data" / "lines.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if fewer:
        rows = rows[:-fewer]
    for i in range(more):
        rows.append({"line_id": f"L9{i:03d}", "line_date": "2026-03-15", "category": "Rent",
                     "amount": str(100000 * (i + 1))})
    path = folder / "export_lines.csv"
    folder.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path, rows, sum(int(float(r["amount"])) for r in rows)


def _copy_of_the_model_folder(model: Path, copy: Path) -> Path:
    """google-sheets.md 'Models kept in Google Sheets' step 3.1: contracts and raw first, then the manifest, the
    specification and the marker; no workbook (copied from test_v4_docs.py)."""
    copy.mkdir(parents=True)
    for name in ("contracts", "raw"):
        shutil.copytree(model / name, copy / name)
    for name in ("model.manifest.json", "model.spec.json", "rotm_model.json"):
        shutil.copy2(model / name, copy / name)
    assert not (copy / "model.xlsx").exists()
    return copy


def _control_cell(ws) -> str:
    """The inputs-sheet cell that holds the control total of lines.amount (the cell the check lines_total_amount
    links)."""
    for row in ws.iter_rows():
        for c in row:
            if c.value == "lines: control total of amount":
                return f"D{c.row}"
    raise AssertionError("the inputs sheet has no control total of lines.amount")


def _save_download(model: Path, copy: Path, rows: list[dict], total: int | None = None) -> Path:
    """The download in place of Google Sheets' (after test_v4_docs.py): the built workbook whose raw rows are those
    of the export, whose helper rows were repaired for the new row count as google-sheets.md 'What a refresh does'
    says (the last helper row copied down, or the rows below the last raw row cleared), and, with `total`, whose
    control input cell holds the source's new total; saved in the copy under the marker's workbook name."""
    import openpyxl
    from copy import copy as style_copy
    from openpyxl.formula.translate import Translator
    name = json.loads((copy / "rotm_model.json").read_text(encoding="utf-8"))["workbook"]
    wb = openpyxl.load_workbook(model / "model.xlsx")
    ws = wb["raw_lines"]
    old_last = ws.max_row
    new_last = len(rows) + 1
    for r in range(2, max(old_last, new_last) + 1):
        for ci in range(1, 5):
            cell = ws.cell(row=r, column=ci)
            if r > new_last:
                cell.value = None
                continue
            if r > old_last:
                cell._style = style_copy(ws.cell(row=2, column=ci)._style)
            rec = rows[r - 2]
            cell.value = [rec["line_id"], dt.datetime.fromisoformat(rec["line_date"]), rec["category"],
                          int(float(rec["amount"]))][ci - 1]
    for t in ws.tables.values():
        t.ref = f"A1:D{new_last}"
        if t.autoFilter is not None:
            t.autoFilter.ref = t.ref
    hs = wb["h_lines"]
    h_last = hs.max_row
    width = hs.max_column
    if new_last > h_last:                           # more rows: copy the last helper row down
        for r in range(h_last + 1, new_last + 1):
            for ci in range(1, width + 1):
                src = hs.cell(row=h_last, column=ci)
                dst = hs.cell(row=r, column=ci)
                dst._style = style_copy(src._style)
                if isinstance(src.value, str) and src.value.startswith("="):
                    dst.value = Translator(src.value, origin=src.coordinate).translate_formula(dst.coordinate)
                else:
                    dst.value = src.value
    elif new_last < h_last:                         # fewer rows: clear the contents below the last raw row
        for r in range(new_last + 1, h_last + 1):
            for ci in range(1, width + 1):
                hs.cell(row=r, column=ci).value = None
    if total is not None:
        ins = wb["inputs"]
        ins[_control_cell(ins)].value = total
    out = copy / name
    wb.save(out)
    return out


def _bytes_of(root: Path) -> dict[str, bytes]:
    """raw/, the controls files and the manifest, byte for byte."""
    out = {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / "raw").rglob("*") if p.is_file()}
    out["model.manifest.json"] = (root / "model.manifest.json").read_bytes()
    return out


NOT_EXPECTED = ("R-CHANGED", "D-TOTAL", "CT-VERSION", "D-CONTROL-INPUT")


def _land_with_control(copy: Path, export: Path, total: int, tmp_path: Path) -> dict:
    given = f"lines.amount={total}"
    out = tmp_path / "land.json"
    landed = _rotm("land", str(copy), "--table", "lines", "--csv", str(export), "--force", "--control", given,
                   "--json", str(out))
    assert landed.returncode == 0, landed.stdout[-3000:] + landed.stderr[-2000:]
    report = _report(out)
    record = controls.load(copy, "lines")
    digest = report["data"]["digest"]
    entry = next(e for e in record["accepted"] if e["digest"] == digest)
    assert entry["given"] == [given], entry
    assert entry["rows"] == report["data"]["rows"]
    assert entry["totals"] == [{"column": "amount", "value": total, "tolerance": 0}], entry
    agreed = common.read_json(copy / "contracts" / "lines.contract.json")         # v9: under the agreement in force
    assert entry["contract_sha256"] == agreed["agreement"]["contract_sha256"]
    assert controls.totals_for(copy, "lines", digest, agreed) == entry["totals"]
    notes = "\n".join(report["notes"])
    assert (f"recorded the source total amount = {land._fmt(total)} for this snapshot in raw/lines.controls.json "
            f"(given: '{given}')") in notes, notes
    # the snapshot is the export's rows and the manifest names its digest
    assert manifest.csv_digest(copy / "raw" / "lines.csv", ["text", "date", "text", "number"])[0] == digest
    assert manifest.raw_sheet(json.loads((copy / "model.manifest.json").read_text(encoding="utf-8")),
                              "lines")["digest"] == digest
    return report


def _route(static_model: Path, tmp_path: Path, *, more: int = 0, fewer: int = 0) -> tuple[Path, list[dict], int]:
    export, rows, total = _export(static_model, tmp_path / "export", more=more, fewer=fewer)
    contract_total = json.loads((static_model / "contracts" / "lines.contract.json").read_text(
        encoding="utf-8"))["expect"]["totals"][0]["value"]
    assert total != contract_total
    copy = _copy_of_the_model_folder(static_model, tmp_path / "review_copy")
    _land_with_control(copy, export, total, tmp_path)
    _save_download(static_model, copy, rows, total)
    return copy, rows, total


def _check_without_excel(copy: Path, tmp_path: Path) -> dict:
    out = tmp_path / "check.json"
    checked = _rotm("check", str(copy), "--strict", "--json", str(out))
    report = _report(out)
    found = [r for r in NOT_EXPECTED if r in _rules(report)]
    assert not found, (found, checked.stdout[-3000:])
    assert not _rules(report, "error"), checked.stdout[-3000:]
    assert checked.returncode == 3 and "not recalculated" in checked.stdout, checked.stdout[-3000:]
    return report


def test_export_with_more_rows_and_a_new_total_lands_and_checks(static_model: Path, tmp_path: Path) -> None:
    """[A-aera-6, C-sol-3]: an export with 3 rows more and a changed total: land --force --control exits 0 and
    records the total for the snapshot; check --strict of the download (those rows, the helper rows copied down,
    the new total in the control input cell) reports no R-CHANGED, D-TOTAL, CT-VERSION or D-CONTROL-INPUT."""
    copy, rows, total = _route(static_model, tmp_path, more=3)
    assert len(rows) == 63
    _check_without_excel(copy, tmp_path)


def test_export_with_fewer_rows_and_a_new_total_lands_and_checks(static_model: Path, tmp_path: Path) -> None:
    """[A-aera-6, C-sol-3]: the same with 4 rows fewer (the helper rows below the last raw row cleared)."""
    copy, rows, total = _route(static_model, tmp_path, fewer=4)
    assert len(rows) == 56
    _check_without_excel(copy, tmp_path)


@pytest.mark.excel
def test_export_with_more_rows_and_a_new_total_checks_in_excel(static_model: Path, tmp_path: Path) -> None:
    """[A-aera-6, C-sol-3], with Excel: check --strict recalculates the download and exits 0."""
    copy, _rows, _total = _route(static_model, tmp_path, more=3)
    out = tmp_path / "check.json"
    checked = _rotm("check", str(copy), "--strict", "--json", str(out), excel_on=True)
    report = _report(out)
    assert checked.returncode == 0, checked.stdout[-4000:] + checked.stderr[-2000:]
    assert not [r for r in NOT_EXPECTED if r in _rules(report)]
    assert report["verified"].get("recalculated") is True, report["verified"]


def test_wrong_control_is_l_total_and_writes_nothing(static_model: Path, tmp_path: Path) -> None:
    """[A-aera-6, C-sol-3]: a --control total that the export's sum does not meet is L-TOTAL (exit 1); raw/, the
    controls file and the manifest stay byte for byte, also when a controls file exists."""
    export, rows, total = _export(static_model, tmp_path / "export", more=2)
    copy = _copy_of_the_model_folder(static_model, tmp_path / "review_copy")
    before = _bytes_of(copy)
    out = tmp_path / "land.json"
    landed = _rotm("land", str(copy), "--table", "lines", "--csv", str(export), "--force", "--control",
                   f"lines.amount={total + 1}", "--json", str(out))
    report = _report(out)
    assert landed.returncode == 1, landed.stdout[-2000:]
    errors = [f for f in report["findings"] if f["severity"] == "error"]
    assert [f["rule"] for f in errors] == ["L-TOTAL"], errors
    assert errors[0]["message"] == (f"export_lines.csv: the sum of amount is {land._fmt(total)}; the source total "
                                    f"given with --control is {land._fmt(total + 1)} (tolerance 0); nothing was "
                                    "written"), errors[0]["message"]
    assert errors[0]["fix"] == ("the export and the total must come from the same refresh: compare the extract "
                                "with its source and land it again")
    assert _bytes_of(copy) == before
    assert not controls.path(copy, "lines").exists()
    # with a controls file in place: a right total first, then a wrong one for another export
    _land_with_control(copy, export, total, tmp_path)
    before = _bytes_of(copy)
    other, _rows2, total2 = _export(static_model, tmp_path / "export2", fewer=3)
    landed = _rotm("land", str(copy), "--table", "lines", "--csv", str(other), "--force", "--control",
                   f"lines.amount={total2 - 500}")
    assert landed.returncode == 1 and "L-TOTAL" in landed.stdout, landed.stdout[-2000:]
    assert _bytes_of(copy) == before


def test_later_build_takes_the_recorded_total(static_model: Path, tmp_path: Path) -> None:
    """[A-aera-6, C-sol-3] (2): after land --control, build --strict --force of the folder writes the recorded total
    into the control input cell, and check reports no D-TOTAL."""
    import openpyxl
    export, rows, total = _export(static_model, tmp_path / "export", more=3)
    copy = _copy_of_the_model_folder(static_model, tmp_path / "review_copy")
    _land_with_control(copy, export, total, tmp_path)
    built = _rotm("build", str(copy), "--strict", "--force")
    assert built.returncode == 0, built.stdout[-3000:] + built.stderr[-2000:]
    ws = openpyxl.load_workbook(copy / "model.xlsx")["inputs"]
    assert ws[_control_cell(ws)].value == total
    # v5 integrator (request v5_P4 R1): the source cell names the command that recorded the total
    source = str(ws[f"E{int(_control_cell(ws)[1:])}"].value or "")
    assert ("raw/lines.controls.json, recorded for this snapshot by rotm.py check --accept-refresh or rotm.py land "
            "--control") in source, source
    _check_without_excel(copy, tmp_path)


def test_land_without_control_behaves_as_before(static_model: Path, tmp_path: Path) -> None:
    """[A-aera-6, C-sol-3]: without --control, a changed total is L-TOTAL against the contract's control total, as
    in v4, and nothing is written; an export whose total equals the contract's lands with no controls file."""
    export, _rows, total = _export(static_model, tmp_path / "export", more=1)
    copy = _copy_of_the_model_folder(static_model, tmp_path / "review_copy")
    before = _bytes_of(copy)
    out = tmp_path / "land.json"
    landed = _rotm("land", str(copy), "--table", "lines", "--csv", str(export), "--force", "--json", str(out))
    assert landed.returncode == 1, landed.stdout[-2000:]
    errors = [f for f in _report(out)["findings"] if f["severity"] == "error"]
    assert [f["rule"] for f in errors] == ["L-TOTAL"]
    assert "the contract's control total is 57,514,500" in errors[0]["message"]
    assert _bytes_of(copy) == before
    same = _rotm("land", str(copy), "--table", "lines", "--csv", str(static_model / "data" / "lines.csv"))
    assert same.returncode == 0 and "nothing changed" in same.stdout, same.stdout[-2000:]
    assert not controls.path(copy, "lines").exists()


def test_control_needs_csv_and_the_landed_table(static_model: Path, tmp_path: Path) -> None:
    """[A-aera-6, C-sol-3]: --control needs --csv, its table must be the table landed, and its column follows the
    rule of check --control (a number column of the contract); each refusal is exit 2 and writes nothing."""
    export, _rows, total = _export(static_model, tmp_path / "export", more=1)
    copy = _copy_of_the_model_folder(static_model, tmp_path / "review_copy")
    before = _bytes_of(copy)
    assert not (copy / "queries").exists()
    r = _rotm("land", str(copy), "--table", "lines", "--control", f"lines.amount={total}")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "--control needs --csv: a source total belongs to the extract landed with it" in r.stderr
    cases = [(f"sales.amount={total}", "--control sales.amount=%d: table 'sales' is not the table landed "
              "(--table lines)" % total),
             ("lines.category=5", "column 'category' has type text; a control total needs a number column"),
             ("lines.amont=5", "the contract of 'lines' has no column 'amont'; did you mean 'amount'?"),
             ("lines.amount=abc", "'abc' is not a number")]
    for given, words in cases:
        r = _rotm("land", str(copy), "--table", "lines", "--csv", str(export), "--force", "--control", given)
        assert r.returncode == 2, (given, r.stdout + r.stderr)
        assert words in r.stderr, (given, r.stderr)
        assert "Traceback" not in r.stderr
    assert _bytes_of(copy) == before
    assert not (copy / "queries").exists()                  # no query file was written either


def test_control_with_the_same_rows_records_the_total(static_model: Path, tmp_path: Path) -> None:
    """[A-aera-6, C-sol-3]: --control with the rows already landed (same digest) records the total for that
    snapshot; raw/lines.csv is not written again."""
    copy = _copy_of_the_model_folder(static_model, tmp_path / "review_copy")
    snap = (copy / "raw" / "lines.csv").read_bytes()
    r = _rotm("land", str(copy), "--table", "lines", "--csv", str(static_model / "data" / "lines.csv"),
              "--control", "lines.amount=57,514,500")
    assert r.returncode == 0, r.stdout[-2000:]
    assert (copy / "raw" / "lines.csv").read_bytes() == snap
    entry = controls.load(copy, "lines")["accepted"][0]
    assert entry["given"] == ["lines.amount=57,514,500"] and entry["totals"][0]["value"] == 57514500


# ============================================================================= [A-aera-4, C-sol-2] the agreement guard

def _proposed(tmp_path: Path) -> Path:
    root = tmp_path / "m"
    sample = _csv(tmp_path / "sample" / "sales.csv", ["id", "region", "amount"],
                  [["A1", "East", "10"], ["A2", "West", "20"], ["A3", "East", "30"]])
    contract.propose(root, "sales", csv=sample, key="id", units=["amount=KRW"], totals=["amount=60"])
    return root


QUESTIONS = ["What does the amount column include?", "금액 컬럼에 무엇이 들어가나요?", "금액 컬럼에 무엇이 들어가나요",
             "Is amount net of VAT? Does it include returns?", "What does amount include, and is it net of VAT?",
             "Can you go ahead？"]


@pytest.mark.parametrize("reply", QUESTIONS)
def test_a_reply_that_is_only_a_question_is_refused(tmp_path: Path, reply: str) -> None:
    root = _proposed(tmp_path)
    why = contract.reads_as_refusal(reply)
    assert why is not None and why.startswith(f"{contract.QUESTION_REASON}, not an answer ('"), why
    before = modelfolder.contract_path(root, "sales").read_bytes()
    with pytest.raises(common.Refusal) as exc:
        contract.agree(root, "sales", reply=reply, by=BY)
    line = exc.value.line()
    assert "is a question, not an answer ('" in line
    assert "; no agreement was given; answer the question and wait for the user's reply" in line
    assert "answer the user's question, then wait for their reply to contracts/sales.contract.md and record it with" \
        in line and "contract agree" in line
    assert modelfolder.contract_path(root, "sales").read_bytes() == before


RECORDED = ["agreed", "looks good, go ahead", "Reviewed, agreed.", "Looks good, can you go ahead?",
            "Agreed. Can you land it now?", "네, 진행하시죠"]


@pytest.mark.parametrize("reply", RECORDED)
def test_a_statement_with_or_without_a_question_is_recorded(tmp_path: Path, reply: str) -> None:
    root = _proposed(tmp_path)
    assert contract.reads_as_refusal(reply) is None, reply
    data = contract.agree(root, "sales", reply=reply, by=BY)
    assert data["agreement"]["reply"] == reply and modelfolder.contract_state(root, "sales") == "agreed"


def test_a_task_file_that_holds_only_a_question_is_refused(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    task = tmp_path / "task.md"
    task.write_text("What does the amount column include?\n", encoding="utf-8")
    with pytest.raises(common.Refusal) as exc:
        contract.agree(root, "sales", reply="What does the amount column include?", by=BY, channel="task-file",
                       source_file=task)
    assert "is a question, not an answer" in exc.value.line()
    r = _rotm("contract", "agree", str(root), "--table", "sales", "--reply", "What does the amount column include?",
              "--by", BY, "--channel", "task-file", "--source-file", str(task))
    assert r.returncode == 2 and "is a question, not an answer" in r.stderr, r.stderr
    assert modelfolder.contract_state(root, "sales") == "proposed"


def test_pre_agreed_words_in_a_task_file_are_still_recorded(tmp_path: Path) -> None:
    root = _proposed(tmp_path)
    task = tmp_path / "task.md"
    task.write_text("# Task\n\nContract agreement: pre-agreed for this test\n\n- What does the model show?\n"
                    "- Land the CSV after the agreement.\n", encoding="utf-8")
    contract.agree(root, "sales", reply="pre-agreed for this test", by=BY, channel="task-file", source_file=task)
    assert modelfolder.contract_state(root, "sales") == "agreed"


def test_reply_refusal_reads_the_sentence_and_the_whole_message() -> None:
    """contract.reply_refusal: the function the hook's session channel and the task-file channel use."""
    msg = "Sales is agreed. Please change the unit in target."
    assert contract.reply_refusal(msg, "Sales is agreed", table="sales", tables=["sales", "target"]) is None
    held = contract.reply_refusal(msg, "Sales is agreed", table="target", tables=["sales", "target"])
    assert held is not None and held[1].startswith(contract.CHANGE_REASON), held
    held = contract.reply_refusal("I have not agreed. Please leave it proposed.", "agreed", table="sales",
                                  tables=["sales"])
    assert held is not None and held[0] == "I have not agreed." and "not agreed" in held[1], held
    assert contract.reply_refusal("Reviewed the columns. Agreed.", "Agreed", table="sales", tables=["sales"]) is None


def _transcript(tmp_path: Path, entries: list[dict]) -> Path:
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8")
    return t


def _user(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _agree_cmd(table: str, reply: str, extra: str = "") -> str:
    return f'{ROTM} contract agree . --table {table} --reply "{reply}" --by {BY}{extra}'


@pytest.fixture
def two_tables(tmp_path: Path) -> Path:
    """A model folder with the contracts sales and target, both proposed."""
    root = tmp_path / "two_model"
    modelfolder.init_model(root, name="two_model")
    write_contract(root, "sales", agreed=False)
    write_contract(root, "target", agreed=False)
    return root


@hooks_present
def test_the_hook_refuses_a_word_taken_out_of_a_refusal(two_tables: Path, tmp_path: Path) -> None:
    t = _transcript(tmp_path, [_user("I have not agreed. Please leave it proposed.")])
    out = run_hook("contract_gate", bash(_agree_cmd("sales", "agreed"), two_tables, transcript=t))
    assert_refused(out, "'I have not agreed.'", "says 'not agreed'", "no agreement may be recorded")
    assert len(out.stderr.strip().splitlines()) == 1


@hooks_present
def test_the_hook_refuses_a_question(two_tables: Path, tmp_path: Path) -> None:
    reply = "What does the amount column include?"
    t = _transcript(tmp_path, [_user(reply)])
    out = run_hook("contract_gate", bash(_agree_cmd("sales", reply), two_tables, transcript=t))
    assert_refused(out, "is a question, not an answer", "answer the question and wait for the user's reply",
                   "answer the user's question, then wait for their reply to contracts/sales.contract.md")
    out = run_hook("contract_gate", bash(_agree_cmd("sales", reply, " --channel task-file --source-file task.md"),
                                         two_tables, transcript=t))
    assert_refused(out, "is a question, not an answer")


@hooks_present
@pytest.mark.parametrize("reply", ["agreed", "looks good, go ahead", "Reviewed, agreed.",
                                   "Looks good, can you go ahead?"])
def test_the_hook_lets_approvals_through(two_tables: Path, tmp_path: Path, reply: str) -> None:
    t = _transcript(tmp_path, [_user(reply)])
    assert_silent(run_hook("contract_gate", bash(_agree_cmd("sales", reply), two_tables, transcript=t)))


@hooks_present
def test_the_hook_reads_the_table_a_refusing_sentence_names(two_tables: Path, tmp_path: Path) -> None:
    t = _transcript(tmp_path, [_user("Sales is agreed. Please change the unit in target.")])
    assert_silent(run_hook("contract_gate", bash(_agree_cmd("sales", "Sales is agreed"), two_tables, transcript=t)))
    out = run_hook("contract_gate", bash(_agree_cmd("target", "Sales is agreed"), two_tables, transcript=t))
    assert_refused(out, "asks for a change ('please change')", "no agreement may be recorded")
    # recorded for sales by rotm.py (the hook let it through)
    contract.show(two_tables, "sales")
    data = contract.agree(two_tables, "sales", reply="Sales is agreed", by=BY)
    assert modelfolder.contract_state(two_tables, "sales") == "agreed" and data["agreement"]["reply"] == \
        "Sales is agreed"


@hooks_present
def test_the_hook_takes_a_later_clean_message(two_tables: Path, tmp_path: Path) -> None:
    """A refusal first and a plain agreement later: the later message counts."""
    t = _transcript(tmp_path, [_user("I have not agreed yet."), _user("Now it is agreed.")])
    assert_silent(run_hook("contract_gate", bash(_agree_cmd("sales", "agreed"), two_tables, transcript=t)))


# ============================================================================= [A-aera-16, A-aera-17] (1) dates and times

def test_datetime_with_an_offset_is_proposed_and_lands(tmp_path: Path) -> None:
    root = tmp_path / "ev"
    sample = _csv(tmp_path / "sample" / "events.csv", ["event_id", "at_kst", "at_utc", "amount"],
                  [["E1", "2026-11-25T09:30:00+09:00", "2026-11-25T00:30:00Z", "10"],
                   ["E2", "2026-11-25T10:30:00+09:00", "2026-11-25T01:30:00Z", "20"],
                   ["E3", "2026-11-25T11:45:10+09:00", "2026-11-25T02:45:10Z", "30"]])
    path = contract.propose(root, "events", csv=sample, key="event_id", units=["amount=KRW"], totals=["amount=60"])
    types = {c["name"]: c["type"] for c in json.loads(Path(path).read_text(encoding="utf-8"))["columns"]}
    assert types["at_kst"] == "datetime" and types["at_utc"] == "datetime", types
    task = tmp_path / "task.md"
    task.write_text("# Task\n\nContract agreement: pre-agreed for this test\n", encoding="utf-8")
    contract.agree(root, "events", reply="pre-agreed for this test", by=BY, channel="task-file", source_file=task)
    out = tmp_path / "land.json"
    r = _rotm("land", str(root), "--table", "events", "--csv", str(sample), "--json", str(out))
    assert r.returncode == 0, r.stdout[-3000:]
    report = _report(out)
    assert not _rules(report, "error"), report["findings"]
    zones = {f["data"]["column"] for f in report["findings"] if f["rule"] == "L-PRECISION"
             and f["data"]["reason"] == "zone"}
    assert zones == {"at_kst", "at_utc"}, report["findings"]
    with open(root / "raw" / "events.csv", encoding="utf-8", newline="") as f:
        snap = list(csv.reader(f))
    assert snap[1][1:3] == ["2026-11-25T09:30:00", "2026-11-25T00:30:00"]          # the time as written


def test_every_text_proposed_as_datetime_lands() -> None:
    texts = ["2026-11-25T09:30:00+09:00", "2026-11-25T09:30+0900", "2026-11-25 09:30:00Z", "2026-11-25T09:30:00.25",
             "2026-11-25 09:30:00 UTC", "2026-11-25T09:30:00-05:30", "2026-11-25", "2026-11-25 09:30 +09:00",
             "2026/11/25 09:30", "2026-11-25T25:00:00", "2026-11-25T09:30:00+24:00", "25.11.2026 09:30"]
    for text in texts:
        proposed = contract.infer_type([text])
        value, problem = land.parse_value(text, "datetime")
        if proposed == "datetime":
            assert problem is None and value is not None, (text, problem)
        value2, _changes = land.read_datetime_text(text)
        assert (value2 is not None) == (problem is None), text
    assert contract.infer_type(["2026-11-25 09:30 +09:00"]) == "text"          # a form land cannot keep
    assert land.read_datetime_text("2026-11-25T09:30:00+09:00") == (dt.datetime(2026, 11, 25, 9, 30), ["zone"])
    assert land.read_datetime_text("2026-11-25T09:30:00.5Z") == (dt.datetime(2026, 11, 25, 9, 30), ["fraction", "zone"])
    assert land.precision_changes("2026-11-25T09:30:00-05:30", "datetime") == ["zone"]


def test_uncastable_datetime_is_one_l_type_and_no_l_null(tmp_path: Path) -> None:
    root = tmp_path / "ev"
    sample = _csv(tmp_path / "sample" / "events.csv", ["event_id", "at", "amount"],
                  [["E1", "2026-11-25T09:30:00", "10"], ["E2", "2026-11-25T10:30:00", "20"]])
    contract.propose(root, "events", csv=sample, key="event_id", units=["amount=KRW"])
    contract.agree(root, "events", reply="agreed", by=BY)
    bad = _csv(tmp_path / "bad.csv", ["event_id", "at", "amount"],
               [["E1", "not a time", "10"], ["E2", "2026-11-25T10:30:00", "20"]])
    rep = land.land_csv(root, "events", bad)
    rules = [f.rule for f in rep.findings if f.severity == "error"]
    assert rules == ["L-TYPE"], [f.line() for f in rep.findings]
    typ = next(f for f in rep.findings if f.rule == "L-TYPE")
    assert typ.count == 1 and "'not a time'" in typ.message
    assert not (root / "raw" / "events.csv").exists()


# ============================================================================= supporting: the attached query's hash

def _fake_attach(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(excel, "excel_status", lambda: excel.ExcelStatus(available=True, version="16.0"))

    def pq_attach(workbook, *, sheet, table_name, query_name, m_text, **_kw):
        sent.append(m_text)
        return excel.JobResult(ok=True, pid=None, steps=[{"op": "pq_attach", "query": query_name,
                                                          "table": table_name, "range": "A1:D61"}])
    monkeypatch.setattr(excel, "pq_attach", pq_attach)
    return sent


def _raw_entry(root: Path) -> dict:
    return manifest.raw_sheet(json.loads((root / "model.manifest.json").read_text(encoding="utf-8")), "lines")


def test_attach_records_the_query_body_hash(static_model: Path, tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "model"
    shutil.copytree(static_model, root)
    sent = _fake_attach(monkeypatch)
    assert "query_body_sha256" not in _raw_entry(root)
    land.attach_power_query(root / "model.xlsx", root, "lines")
    text = (root / "queries" / "lines.pq.m").read_text(encoding="utf-8")
    entry = _raw_entry(root)
    assert entry["query_body_sha256"] == land.query_body_sha256(text)
    assert len(entry["query_body_sha256"]) == 64 and entry["landing"] == "power_query"
    assert land.MODEL_FOLDER in text and land.MODEL_FOLDER not in sent[0]       # hashed before it is filled in
    stamp = common.read_query_stamp(text)
    assert stamp is not None and stamp["body_sha256"] == entry["query_body_sha256"]


def test_keep_queries_does_not_change_the_recorded_hash(static_model: Path, tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "model"
    shutil.copytree(static_model, root)
    _fake_attach(monkeypatch)
    land.attach_power_query(root / "model.xlsx", root, "lines")
    recorded = _raw_entry(root)["query_body_sha256"]
    path = root / "queries" / "lines.pq.m"
    path.write_text(path.read_text(encoding="utf-8") + "// edited by hand after the attach\n", encoding="utf-8")
    assert land.query_body_sha256(path.read_text(encoding="utf-8")) != recorded
    r = _rotm("land", str(root), "--table", "lines", "--keep-queries")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert _raw_entry(root)["query_body_sha256"] == recorded
    export, _rows, total = _export(root, tmp_path / "export", more=1)
    r = _rotm("land", str(root), "--table", "lines", "--csv", str(export), "--force", "--control",
              f"lines.amount={total}")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    entry = _raw_entry(root)
    assert entry["query_body_sha256"] == recorded and entry["rows"] == 61    # the digest moved, the hash did not
