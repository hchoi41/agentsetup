"""Tests of the v7 build changes (package P3 of the v7 plan). Owner: P3.

Entries: [A-aera-3, C-sol-2] (a rebuild of a workbook whose raw sheet holds a query table that rotm.py attached keeps
the earlier workbook and its manifest as <stem>.bak_<time>.xlsx and <stem>.manifest.bak_<time>.json until every
such query is attached again; W-PQ-NOT-ATTACHED names the copies); supporting W-CHECK-GUARD ([A-aera-4, A-aera-13]
part 1, build side), I-SHEETS-ONLY ([A-aera-8] part 1) and I-TEXT-WIDTH ([J-2, J-3, J-4] part 1). Tests marked
excel drive Excel through lib.excel. The names of the four [A-aera-3, C-sol-2] tests are fixed by the v7 plan
(section 5.3) and quoted by the documents.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from lib import build, check, common, functions, manifest
from test_build import make_model
from test_hooks import HOOKS, ROTM, assert_silent, bash, fake_rotm, post, run_hook, stop  # noqa: F401
from test_v4_build import _make_query_table
from test_v5_build import _shipped_folders, copy_model, read_spec, sheet_of, write_spec
import test_check as tc

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
STARTER = SKILL / "templates" / "starter"


def out(proc) -> str:
    return ((proc.stdout or "") + (proc.stderr or ""))[-6000:]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def kept_copies(root: Path, stem: str = "model") -> tuple[list[Path], list[Path]]:
    """(workbook copies, manifest copies) in the model folder, by the names of v7 plan 5.2."""
    return (sorted(p for p in root.glob(f"{stem}.bak_*.xlsx")),
            sorted(p for p in root.glob(f"{stem}.manifest.bak_*.json")))


def same_stamp(wb_copy: Path, man_copy: Path) -> bool:
    return wb_copy.name.split(".bak_", 1)[1][:-len(".xlsx")] == man_copy.name.split(".bak_", 1)[1][:-len(".json")]


def pq_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if "W-PQ-NOT-ATTACHED" in line]


def rotm_attached_sales(root: Path, *, owned: bool = True) -> Path:
    """The build fixture (test_build.make_model) built without Excel, its raw sheet turned into a query table of
    q_sales and its manifest entry recorded as land --power-query records it (contract hash, query_name q_sales,
    no query_attach): a query rotm.py attached, made without Excel (as test_v4_build does). With `owned`, the
    status record of step land is written as land --power-query writes it (input and output hash), so build owns
    the workbook; without it the workbook counts as changed after rotm.py wrote it (--force keeps a copy)."""
    from lib import modelfolder
    make_model(root)
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    input_sha = common.file_sha256(wb)
    _make_query_table(wb, "tbl_raw_sales", "q_sales")
    data = manifest.load_manifest(wb)
    e = manifest.raw_sheet(data, "sales")
    c = common.read_json(root / "contracts" / "sales.contract.json")
    e.update(landing="power_query", query_name="q_sales", query_contract_sha256=c["agreement"]["contract_sha256"])
    manifest.write_manifest(wb, data)
    if owned:
        output_sha = common.file_sha256(wb)
        modelfolder.record_step(root, wb.as_posix(), "land", {"exit_code": 0, "table": "sales", "power_query": True,
                                                              "sha256": output_sha, "input_sha256": input_sha,
                                                              "output_sha256": output_sha})
        assert output_sha in build._known_hashes(root, wb)
    assert check.query_tables(wb) == {"raw_sales": "q_sales"}
    return wb


# ============================================================================= [A-aera-3, C-sol-2] with Excel

def _attached_starter(tmp_path: Path) -> Path:
    from test_v6_check import attached_starter
    root = attached_starter(tmp_path)
    assert check.query_tables(root / "model.xlsx") == {"raw_lines": "q_lines"}
    return root


def _source_unreachable(root: Path) -> None:
    """queries/lines.pq.m reads a CSV that does not exist, stamped with land --keep-queries (the cold reviewer's
    case)."""
    path = root / "queries" / "lines.pq.m"
    text = path.read_text(encoding="utf-8")
    assert '\\raw\\lines.csv"' in text, text
    path.write_text(text.replace('\\raw\\lines.csv"', '\\raw\\lines_missing.csv"'), encoding="utf-8", newline="\n")
    r = tc.run_cli("land", str(root), "--table", "lines", "--keep-queries")
    assert r.returncode == 0, out(r)


@pytest.mark.excel
def test_failed_attach_keeps_the_earlier_workbook_and_manifest(tmp_path: Path) -> None:
    root = _attached_starter(tmp_path)
    wb, man = root / "model.xlsx", root / "model.manifest.json"
    _source_unreachable(root)
    earlier_wb, earlier_man = wb.read_bytes(), man.read_bytes()
    reb = tc.run_cli("build", str(root), "--strict", "--force")
    assert reb.returncode == 1, out(reb)
    (wb_copy,), (man_copy,) = kept_copies(root)
    assert same_stamp(wb_copy, man_copy), (wb_copy.name, man_copy.name)
    assert check.query_tables(wb_copy) == {"raw_lines": "q_lines"}           # the kept workbook holds its query
    assert wb_copy.read_bytes() == earlier_wb
    assert man_copy.read_bytes() == earlier_man                               # the kept manifest, byte for byte
    assert check.query_tables(wb) == {}                                       # the new workbook: static values
    lines = pq_lines(out(reb))
    assert len(lines) == 1, out(reb)
    line = lines[0]
    assert "raw_lines holds static values: the query was not attached" in line
    assert (f"; the earlier workbook with its live query is kept as {wb_copy.name} and its manifest as "
            f"{man_copy.name}") in line
    assert (f"Fix: to go back, copy {wb_copy.name} over model.xlsx and {man_copy.name} over model.manifest.json "
            "(the user copies them by hand: the raw-sheet guard refuses a shell command that copies over a "
            f"workbook); to go on, make the source reachable and run rotm.py land \"{root.as_posix()}\" --table lines "
            "--power-query") in line
    # going back: the copies put over the new files are a pair build owns again (no --force needed)
    shutil.copyfile(wb_copy, wb)
    shutil.copyfile(man_copy, man)
    assert common.file_sha256(wb) in build._known_hashes(root, wb)
    assert check.query_tables(wb) == {"raw_lines": "q_lines"}


@pytest.mark.excel
def test_successful_rebuild_leaves_no_kept_copy(tmp_path: Path) -> None:
    root = _attached_starter(tmp_path)
    reb = tc.run_cli("build", str(root), "--strict", "--force")
    assert reb.returncode == 0, out(reb)
    assert "raw_lines keeps its Power Query table" in out(reb)
    assert kept_copies(root) == ([], [])
    assert not list(root.glob("*.bak_*")) and not list(root.glob("*.tmp*"))
    assert check.query_tables(root / "model.xlsx") == {"raw_lines": "q_lines"}


@pytest.mark.excel
def test_rebuild_without_excel_keeps_the_copies(tmp_path: Path) -> None:
    root = _attached_starter(tmp_path)                                       # Excel: the attach of the set-up
    wb, man = root / "model.xlsx", root / "model.manifest.json"
    earlier_man = man.read_bytes()
    reb = tc.run_cli("build", str(root), "--strict", "--force", env_extra={"ROTM_NO_EXCEL": "1"})
    assert reb.returncode == 1, out(reb)
    (wb_copy,), (man_copy,) = kept_copies(root)
    assert check.query_tables(wb_copy) == {"raw_lines": "q_lines"}
    assert man_copy.read_bytes() == earlier_man
    assert check.query_tables(wb) == {}
    (line,) = pq_lines(out(reb))
    assert "raw_lines holds static values: Excel is not available" in line
    assert f"kept as {wb_copy.name} and its manifest as {man_copy.name}" in line
    assert line.endswith(f"--table lines --power-query on a PC with Excel"), line


# ============================================================================= [A-aera-3, C-sol-2] without Excel

def test_hooks_do_not_act_on_the_kept_copies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                             fake_rotm: tuple[dict, Path]) -> None:
    """After a rebuild that keeps the copies (here: Excel not available), the PostToolUse and Stop hooks, run as
    Claude Code runs them (python -I, the payload on stdin), audit and name model.xlsx only."""
    if not (HOOKS / "hooks.json").is_file():
        pytest.skip("PLUGIN/hooks is absent (the skill is used without its plugin)")
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = tmp_path / "kept_model"
    rotm_attached_sales(root)
    env, log = fake_rotm
    payload = bash(f'{ROTM} build "{root.as_posix()}" --force --strict', root, sid="sess-kept")
    assert_silent(run_hook("raw_guard", payload, env=env))
    proc = tc.run_cli("build", str(root), "--force", "--strict")
    assert proc.returncode == 1, out(proc)
    wbs, mans = kept_copies(root)
    assert len(wbs) == 1 and len(mans) == 1, out(proc)
    after = run_hook("audit_on_write", post(payload), env=env)
    assert after.code == 0 and "Traceback" not in after.stderr, after
    assert "model.xlsx" in after.stdout and ".bak_" not in after.stdout + after.stderr, after
    audited = [line.split()[1] for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("audit ")]
    assert [Path(a).name for a in audited] == ["model.xlsx"], audited
    fin = run_hook("finish_check", stop(root, sid="sess-kept"))
    assert fin.code == 0 and "Traceback" not in fin.stderr, fin
    assert "model.xlsx" in fin.stdout and ".bak_" not in fin.stdout + fin.stderr, fin
    through = run_hook("finish_check", stop(root, sid="sess-kept", active=True))
    assert ".bak_" not in through.stdout + through.stderr, through


def test_rebuild_without_excel_keeps_the_copies_of_a_query_rotm_attached(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """The same without Excel at all: a query table made in the package (test_v4_build) stands for the attach."""
    from lib import excel
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = tmp_path / "m"
    wb = rotm_attached_sales(root)
    earlier_wb, earlier_man = wb.read_bytes(), manifest.manifest_path(wb).read_bytes()
    rep = build.build(root / "model.spec.json", strict=True)                 # build owns the workbook: no --force
    assert rep.exit_code == 1
    (wb_copy,), (man_copy,) = kept_copies(root)
    assert same_stamp(wb_copy, man_copy)
    assert wb_copy.read_bytes() == earlier_wb and man_copy.read_bytes() == earlier_man
    (f,) = [x for x in rep.findings if x.rule == "W-PQ-NOT-ATTACHED"]
    reason = excel.excel_status().reason
    assert f.message == (f"raw_sales holds static values: Excel is not available ({reason}); the earlier workbook "
                         f"with its live query is kept as {wb_copy.name} and its manifest as {man_copy.name}")
    assert f.fix == (f"to go back, copy {wb_copy.name} over model.xlsx and {man_copy.name} over model.manifest.json "
                     "(the user copies them by hand: the raw-sheet guard refuses a shell command that copies over a "
                     f"workbook); to go on, make the source reachable and run rotm.py land \"{root.as_posix()}\" "
                     "--table sales --power-query on a PC with Excel")
    assert f.data["kept_workbook"] == wb_copy.name and f.data["kept_manifest"] == man_copy.name
    assert check.query_tables(wb) == {}


def test_no_power_query_keeps_the_copies_with_the_texts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = tmp_path / "m"
    rotm_attached_sales(root)
    rep = build.build(root / "model.spec.json", force=True, power_query=False)
    (wb_copy,), (man_copy,) = kept_copies(root)
    (f,) = [x for x in rep.findings if x.rule == "W-PQ-NOT-ATTACHED"]
    assert f.message.startswith("raw_sales holds static values: --no-power-query was given; the earlier workbook "
                                f"with its live query is kept as {wb_copy.name}")
    assert f.fix.startswith(f"to go back, copy {wb_copy.name} over model.xlsx") and f.fix.endswith("--power-query")


def test_copies_are_removed_when_every_query_is_attached_again(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """The removal, without Excel: land.attach_power_query stands in for a successful attach and finds the copies
    already written (they are made before the workbook is replaced)."""
    from types import SimpleNamespace
    from lib import excel, land
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = tmp_path / "m"
    rotm_attached_sales(root)
    seen: list[tuple[list[Path], list[Path]]] = []

    def attach(target, model_root, table):
        seen.append(kept_copies(root))
        return common.Report("land", str(target))
    monkeypatch.setattr(excel, "excel_status", lambda: SimpleNamespace(available=True, reason=""))
    monkeypatch.setattr(land, "attach_power_query", attach)
    rep = build.build(root / "model.spec.json", strict=True)
    assert rep.exit_code == 0, [x.line() for x in rep.findings]
    assert len(seen) == 1 and len(seen[0][0]) == 1 and len(seen[0][1]) == 1, seen
    assert kept_copies(root) == ([], [])
    assert not [n for n in rep.notes if "was kept as" in n], rep.notes
    # a workbook changed after rotm.py wrote it (--force): the workbook copy is also the copy --force keeps (v2), so
    # it stays and is named in the note; the manifest copy is removed
    root2 = tmp_path / "m2"
    rotm_attached_sales(root2, owned=False)
    root = root2
    rep = build.build(root2 / "model.spec.json", force=True, strict=True)
    assert rep.exit_code == 0, [x.line() for x in rep.findings]
    wbs, mans = kept_copies(root2)
    assert len(wbs) == 1 and mans == [], (wbs, mans)
    assert f"the previous model.xlsx was kept as {wbs[0].name}" in rep.notes


def test_no_copy_for_a_workbook_without_a_live_query(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    # a static model
    root = make_model(tmp_path / "a")
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    assert build.build(root / "model.spec.json", strict=True, force=True).exit_code == 0
    assert not list(root.glob("*.bak_*"))
    # the manifest says power_query, but the workbook holds no query table (an earlier attach failed)
    root = make_model(tmp_path / "b")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    data = manifest.load_manifest(wb)
    manifest.raw_sheet(data, "sales").update(landing="power_query", query_name="q_sales")
    manifest.write_manifest(wb, data)
    rep = build.build(root / "model.spec.json", force=True)
    assert [x.rule for x in rep.findings] == ["W-PQ-NOT-ATTACHED"], [x.line() for x in rep.findings]
    assert "kept as" not in rep.findings[0].message
    assert not list(root.glob("*.bak_*"))


def test_a_refused_rebuild_keeps_no_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal before the workbook is replaced (here: the output is held open, so it cannot be replaced) writes
    nothing and leaves no copy."""
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = tmp_path / "m"
    wb = rotm_attached_sales(root)
    before = sha(wb), sha(manifest.manifest_path(wb))

    def held_open(tmp: Path, target: Path) -> None:
        raise common.Refusal(f"{target.name} cannot be replaced: another program holds it open (Excel?)")
    monkeypatch.setattr(build, "_replace_target", held_open)
    with pytest.raises(common.Refusal):
        build.build(root / "model.spec.json")
    assert (sha(wb), sha(manifest.manifest_path(wb))) == before
    assert not list(root.glob("*.bak_*"))


def test_hand_attached_query_keeps_the_temporary_file_form(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A query attached by hand is not given the copies: build refuses without Excel and changes nothing (the v4
    form the verdict accepts; test_v4_build.py tests it with Excel)."""
    from test_v4_build import mark_hand_attached
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = make_model(tmp_path / "m")
    wb = root / "model.xlsx"
    assert build.build(root / "model.spec.json", strict=True).exit_code == 0
    _make_query_table(wb, "tbl_raw_sales", "q_by_hand")
    mark_hand_attached(wb, "q_by_hand")
    before = sha(wb), sha(manifest.manifest_path(wb))
    with pytest.raises(common.Refusal) as exc:
        build.build(root / "model.spec.json", force=True)
    assert "attached by hand" in exc.value.message and "nothing was written" in exc.value.message
    assert (sha(wb), sha(manifest.manifest_path(wb))) == before
    assert not list(root.glob("*.bak_*"))


# ============================================================================= W-CHECK-GUARD (build side)

def _starter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change=None) -> Path:
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = copy_model(STARTER, tmp_path / "starter", monkeypatch)
    if change is not None:
        data = read_spec(root)
        change(data)
        write_spec(root, data)
    return root


def _guarded(data: dict) -> None:
    sheet_of(data, "inputs")["items"].append({"id": "divisor", "label": "Divisor of the guarded check", "value": 1,
                                              "type": "number", "unit": "factor", "source": "test"})
    sheet_of(data, "checks")["checks"] += [
        {"id": "guarded", "kind": "equal", "label": "Raw total over the divisor, guarded",
         "actual": "IFERROR(SUM({raw:lines.amount})/{in:divisor},0)", "expected": "{blk:spend.total}/{in:divisor}"},
        {"id": "guarded_na", "kind": "equal", "label": "Grid total against the raw total, guarded by IFNA",
         "actual": "{blk:spend.total}", "expected": "IFNA(SUM({raw:lines.amount}),0)"},
        {"id": "guarded_zero", "kind": "zero", "label": "Difference of grid and raw totals, guarded",
         "actual": "IFERROR({blk:spend.total}-SUM({raw:lines.amount}),0)"}]


def test_check_with_iferror_or_ifna_gets_w_check_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _starter(tmp_path, monkeypatch, _guarded)
    data = read_spec(root)
    ci = data["sheets"].index(sheet_of(data, "checks"))
    n = len(sheet_of(data, "checks")["checks"])
    rep = build.build(root / "model.spec.json", strict=True, force=True)
    assert rep.exit_code == 1                                                 # strict: the warning fails the build
    got = {(f.data["check"], f.data["field"]): f for f in rep.findings if f.rule == "W-CHECK-GUARD"}
    assert set(got) == {("guarded", "actual"), ("guarded_na", "expected"), ("guarded_zero", "actual")}, got
    f = got[("guarded", "actual")]
    path = f"sheets[{ci}].checks[{n - 3}].actual"
    assert f.severity == "warning" and f.sheet == "checks"
    assert f.message == (f"check guarded: its actual expression holds IFERROR ({path}); references/checks.md rule 4: "
                         "an error in an operand must show in the check's result")
    assert f.fix == ("remove IFERROR or IFNA from the check; for a division that can be by zero, take the zero case "
                     "on the other side, for example --(<denominator><>0)*<numerator>")
    assert got[("guarded_na", "expected")].message.startswith("check guarded_na: its expected expression holds IFNA ")
    assert f"checks[{n - 1}].actual" in got[("guarded_zero", "actual")].message
    cli = tc.run_cli("build", str(root), "--strict", "--force")
    assert cli.returncode == 1 and "W-CHECK-GUARD" in out(cli), out(cli)


def test_check_without_a_guard_gets_no_w_check_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _starter(tmp_path, monkeypatch)
    rep = build.build(root / "model.spec.json", strict=True, force=True)
    assert rep.exit_code == 0 and not [f for f in rep.findings if f.rule == "W-CHECK-GUARD"]


# ============================================================================= I-SHEETS-ONLY

def _join_helper(data: dict) -> None:
    data["product"] = "sheets"
    sheet_of(data, "h_lines")["columns"].append(
        {"id": "tag", "label": "Line and category", "expr": 'JOIN("-",{this:line_id},{this:category})',
         "format": "text"})


def test_sheets_only_function_gets_i_sheets_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _starter(tmp_path, monkeypatch, _join_helper)
    proc = tc.run_cli("build", str(root), "--strict", "--force")
    assert proc.returncode == 0, out(proc)
    text = out(proc)
    wb = root / "model.xlsx"
    h = manifest.helper_sheet(manifest.load_manifest(wb), "lines")
    col = next(c for c in h["columns"] if c["id"] == "tag")["col"]
    rows = h["last_row"] - h["first_row"] + 1
    assert rows > 1
    assert (f"I-SHEETS-ONLY: JOIN is a function only Google Sheets has: {rows:,} formula cell(s), first "
            f"h_lines!{col}2; Excel cannot calculate it, so rotm.py check does not recalculate this workbook") in text
    assert "Fix: use the & operator if the model is to be checked in Excel" in text
    assert functions.replacement("JOIN") == "the & operator"


def test_no_i_sheets_only_without_such_a_function(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def sheets_profile(data: dict) -> None:
        data["product"] = "sheets"
    root = _starter(tmp_path, monkeypatch, sheets_profile)
    rep = build.build(root / "model.spec.json", strict=True, force=True)
    assert rep.exit_code == 0 and not [f for f in rep.findings if f.rule == "I-SHEETS-ONLY"]


# ============================================================================= I-TEXT-WIDTH

LONG_LABEL = ("First month of the model, written as the last day of that month, for every block of the calc sheet")


def _long_label(data: dict) -> None:
    item = sheet_of(data, "inputs")["items"][0]
    assert item["id"] == "start_month"
    item["label"] = LONG_LABEL[:90]


def test_input_label_of_90_characters_gets_i_text_width(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert len(LONG_LABEL[:90]) == 90
    root = _starter(tmp_path, monkeypatch, _long_label)
    rep = build.build(root / "model.spec.json", strict=True, force=True)
    assert rep.exit_code == 0                                                 # information: strict exit unchanged
    notes = [f for f in rep.findings if f.rule == "I-TEXT-WIDTH"]
    assert len(notes) == 1, [f.line() for f in rep.findings]
    f = notes[0]
    data = read_spec(root)
    ii = data["sheets"].index(sheet_of(data, "inputs"))
    assert f.severity == "info" and f.sheet == "inputs" and f.cell.startswith("B")
    room = f.data["room"]
    assert 0 < room < 90 and f.data["length"] == 90 and f.data["path"] == f"sheets[{ii}].items[0].label"
    width = f.data["column_width"]
    assert room == int(width * build.TEXT_ROOM_FACTOR[(9, False)] + 1e-9)    # the unit cell C is filled: no run-on
    assert f.message == (f"sheets[{ii}].items[0].label: the text '{LONG_LABEL[:40]}...' is 90 characters long; its "
                         f"cell inputs!{f.cell} has room for about {room} characters (column width "
                         f"{build._width_text(width)}), so it is cut off on the sheet")
    assert f.fix == f"shorten it to at most {room} characters, or move the detail into a note"
    cli = tc.run_cli("build", str(root), "--strict", "--force")
    assert cli.returncode == 0 and "I-TEXT-WIDTH" in out(cli), out(cli)


def test_text_that_runs_on_over_empty_cells_counts_their_width(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a unit the label runs on over the empty unit cell; a check label (column C of the checks sheet,
    followed by the Actual cell) does not."""
    def change(data: dict) -> None:
        _long_label(data)
        sheet_of(data, "inputs")["items"][0].pop("unit", None)
        chk = sheet_of(data, "checks")["checks"][0]
        chk["label"] = (chk["label"] + ", read from the grid total and the raw sheet's amount column, both in KRW "
                        "as landed")
    root = _starter(tmp_path, monkeypatch, change)
    rep = build.build(root / "model.spec.json", strict=True, force=True)
    assert rep.exit_code == 0
    notes = {f.sheet: f for f in rep.findings if f.rule == "I-TEXT-WIDTH"}
    f = notes["inputs"]
    assert f.data["free_cells"] == 1 and ", and 1 free cell(s) to its right)" in f.message, f.message
    assert f.data["room"] > int(f.data["column_width"] * build.TEXT_ROOM_FACTOR[(9, False)])
    c = notes["checks"]
    assert c.cell.startswith("C") and c.data["free_cells"] == 0 and c.data["length"] > c.data["room"]


def test_measured_factors_are_recorded() -> None:
    assert build.TEXT_ROOM_FACTOR == {(9, False): 1.2, (9, True): 1.15, (8, False): 1.35, (8, True): 1.2}
    assert build.text_room_factor(8, False) == 1.35 and build.text_room_factor(9.0, True) == 1.15
    assert build.text_room_factor(None, False) == 1.2


# ============================================================================= the six shipped models

@pytest.mark.parametrize("folder", _shipped_folders(), ids=lambda p: p.name)
def test_shipped_models_get_no_new_build_finding(folder: Path, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """I-5: the six shipped specifications get no I-TEXT-WIDTH, no W-CHECK-GUARD and no I-SHEETS-ONLY (copies;
    built without Excel)."""
    if not (folder / "model.spec.json").is_file():
        pytest.skip(f"{folder.name} is not in this copy (the skill folder copied alone)")
    monkeypatch.setenv("ROTM_NO_EXCEL", "1")
    root = copy_model(folder, tmp_path / folder.name, monkeypatch)
    rep = build.build(root / "model.spec.json", force=True)
    assert rep.exit_code in (0, 1), [f.line() for f in rep.findings]
    new = [f.line() for f in rep.findings if f.rule in ("I-TEXT-WIDTH", "W-CHECK-GUARD", "I-SHEETS-ONLY")]
    assert new == [], new
