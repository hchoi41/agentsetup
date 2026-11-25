"""Tests of the v6 changes of package P2 (round 5 verdict, 2026-11-26). Owner: P2.

- [C-sol-3]: the rows a helper sheet must fill end at the last data row of its raw sheet in the workbook, not at the
  last helper row the manifest recorded (audit._manifest_helper_rules, audit._raw_last_row). Inside that boundary an
  empty or typed cell is H-GAP and a differing formula is H-INCONSISTENT; below it, a row that is empty in every
  helper column is no finding and helper formulas are H-BEYOND (the rule check uses for such rows). Tested without
  Google Sheets: a copy of a built workbook changed with openpyxl stands in for the download of
  references/google-sheets.md ('What a refresh does', step 5; 'Models kept in Google Sheets', step 4).
- [A-aera-7]: a run of typed values (one, or two or more adjacent ones) between two cells of a column or a row that
  hold the same relative formula is one '(inferred)' finding for the run (audit._typed_runs); text cells end a run.
- [A-aera-8, A-aera-13]: styles.number_formats_equal is the one comparison of number formats (audit S-NUMFMT,
  theme's plan, T-RESIDUAL and theme --number-formats).
- Supporting [A-aera-5], the audit side: a cell of a text column stored as a logical value is R-TYPE.

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v6_audit.py -q -p no:cacheprovider
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import audit, common, styles  # noqa: E402

PY = [sys.executable, "-P", "-X", "utf8", "-B"]
ROTM_CMD = PY + [str(SCRIPTS / "rotm.py")]
SHIPPED_MODELS = [SKILL / "examples" / "sales_vs_target", SKILL / "templates" / "starter"] + [
    PLUGIN / "templates" / n for n in ("ar_aging", "budget_vs_actual", "price_volume_mix", "rolling_forecast")]
REPLY = "agreed for the v6 audit test of package P2"
HELPER_RULES = ("H-GAP", "H-COVERAGE", "H-BEYOND")


def _rotm(*args: str, excel_on: bool = False) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if excel_on:
        env.pop("ROTM_NO_EXCEL", None)
    else:
        env["ROTM_NO_EXCEL"] = "1"
    return subprocess.run(ROTM_CMD + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=1800)


def _report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rules(report: dict[str, Any], severity: str | None = None) -> list[str]:
    return [f["rule"] for f in report["findings"] if severity is None or f["severity"] == severity]


def _hits(report: dict[str, Any], rule: str) -> list[dict[str, Any]]:
    return [f for f in report["findings"] if f["rule"] == rule]


def _ok(proc: subprocess.CompletedProcess[str], what: Any) -> None:
    assert proc.returncode == 0, (what, proc.stdout[-3000:], proc.stderr[-2000:])


def _outside_folder(factory: pytest.TempPathFactory, request: pytest.FixtureRequest, name: str) -> Path:
    """A module-scoped temporary folder outside the skill and plugin folders (as conftest's outside_tmp)."""
    from conftest import _inside_the_plugin
    folder = factory.mktemp(name)
    if not _inside_the_plugin(folder):
        return folder
    folder = Path(tempfile.mkdtemp(prefix="rotm_outside_"))
    request.addfinalizer(lambda: shutil.rmtree(folder, ignore_errors=True))
    if _inside_the_plugin(folder):
        pytest.skip(f"no temporary folder outside the plugin folder: {folder} lies inside it")
    return folder


# ============================================================================= [C-sol-3] the stand-in for the download

def _static_model(root: Path, *, allow_empty: bool = False) -> Path:
    """The starter template (init --template starter), its contract agreed on the task-file channel (proposed again
    with --allow-empty and rows 0 to 10,000 when `allow_empty`), its sample landed and built without Excel: a static
    model, as a model converted to Google Sheets starts (set-up after test_v5_land.py)."""
    task = root.parent / f"{root.name}_task.md"
    task.write_text(f"The user's reply to the contract: {REPLY}\n", encoding="utf-8")
    steps: list[tuple[str, ...]] = [("init", str(root), "--template", "starter", "--name", f"v6_p2_{root.name}")]
    if allow_empty:
        steps.append(("contract", "propose", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv"),
                      "--force", "--allow-empty", "--rows", "0:10000", "--key", "line_id",
                      "--grain", "one row per expense line", "--unit", "amount=KRW", "--type", "amount=number",
                      "--total", "amount=57514500"))
    steps += [("contract", "agree", str(root), "--table", "lines", "--reply", REPLY, "--by", "tester",
               "--channel", "task-file", "--source-file", str(task)),
              ("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv")),
              ("build", str(root), "--strict")]
    for args in steps:
        _ok(_rotm(*args), args)
    return root


@pytest.fixture(scope="module")
def short_model(tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest) -> Path:
    return _static_model(_outside_folder(tmp_path_factory, request, "v6_short") / "model")


@pytest.fixture(scope="module")
def empty_model(tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest) -> Path:
    return _static_model(_outside_folder(tmp_path_factory, request, "v6_empty") / "model", allow_empty=True)


def _export(model: Path, folder: Path, *, fewer: int) -> tuple[Path, list[dict[str, str]], int]:
    """The CSV export of the refreshed extract: the landed rows without the last `fewer` rows (after
    test_v5_land.py). Returns (file, rows, total of amount)."""
    with open(model / "data" / "lines.csv", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    rows = rows[:len(rows) - fewer]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "export_lines.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path, rows, sum(int(float(r["amount"])) for r in rows)


def _copy_of_the_model_folder(model: Path, copy: Path) -> Path:
    """google-sheets.md 'Models kept in Google Sheets' step 3.1: contracts and raw first, then the manifest, the
    specification and the marker; no workbook (after test_v5_land.py)."""
    copy.mkdir(parents=True)
    for name in ("contracts", "raw"):
        shutil.copytree(model / name, copy / name)
    for name in ("model.manifest.json", "model.spec.json", "rotm_model.json"):
        shutil.copy2(model / name, copy / name)
    assert not (copy / "model.xlsx").exists()
    return copy


def _control_cell(ws: Any) -> str:
    for row in ws.iter_rows():
        for c in row:
            if c.value == "lines: control total of amount":
                return f"D{c.row}"
    raise AssertionError("the inputs sheet has no control total of lines.amount")


def _save_download(model: Path, copy: Path, rows: list[dict[str, str]], total: int, *, clear_below: bool = True,
                   empty: str | None = None) -> Path:
    """The download in place of Google Sheets' (after test_v5_land.py): the built workbook whose raw rows are those
    of the export, whose helper rows below the last raw row were cleared as google-sheets.md 'What a refresh does'
    step 5 says (values gone, rows kept; not with clear_below=False), whose control input cell holds the source's new
    total and, with `empty`, whose helper cell of that coordinate was emptied; saved in the copy under the marker's
    workbook name."""
    import openpyxl
    name = json.loads((copy / "rotm_model.json").read_text(encoding="utf-8"))["workbook"]
    wb = openpyxl.load_workbook(model / "model.xlsx")
    ws = wb["raw_lines"]
    old_last = ws.max_row
    new_last = len(rows) + 1
    assert new_last <= old_last
    for r in range(2, old_last + 1):
        for ci in range(1, 5):
            cell = ws.cell(row=r, column=ci)
            if r > new_last:
                cell.value = None
                continue
            rec = rows[r - 2]
            cell.value = [rec["line_id"], dt.datetime.fromisoformat(rec["line_date"]), rec["category"],
                          int(float(rec["amount"]))][ci - 1]
    for t in ws.tables.values():
        t.ref = f"A1:D{max(2, new_last)}"
        if t.autoFilter is not None:
            t.autoFilter.ref = t.ref
    hs = wb["h_lines"]
    if clear_below:
        for r in range(new_last + 1, hs.max_row + 1):
            for ci in range(1, hs.max_column + 1):
                hs.cell(row=r, column=ci).value = None
    if empty:
        hs[empty].value = None
    ins = wb["inputs"]
    ins[_control_cell(ins)].value = total
    out = copy / name
    wb.save(out)
    return out


def _land_with_control(copy: Path, export: Path, total: int, tmp_path: Path) -> None:
    out = tmp_path / "land.json"
    landed = _rotm("land", str(copy), "--table", "lines", "--csv", str(export), "--force", "--control",
                   f"lines.amount={total}", "--json", str(out))
    _ok(landed, "land --force --control")
    assert _report(out)["data"]["rows"] == len(_read_rows(export))


def _read_rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _route(model: Path, tmp_path: Path, *, fewer: int, clear_below: bool = True,
           empty: str | None = None) -> tuple[Path, list[dict[str, str]], int]:
    """The export with `fewer` rows less, landed with its new total in a copy of the model folder, and the
    download saved in the copy."""
    export, rows, total = _export(model, tmp_path / "export", fewer=fewer)
    copy = _copy_of_the_model_folder(model, tmp_path / "review_copy")
    _land_with_control(copy, export, total, tmp_path)
    _save_download(model, copy, rows, total, clear_below=clear_below, empty=empty)
    return copy, rows, total


def _check_strict_without_excel(copy: Path, tmp_path: Path) -> dict[str, Any]:
    out = tmp_path / "check.json"
    checked = _rotm("check", str(copy), "--strict", "--json", str(out))
    report = _report(out)
    assert checked.returncode == 3 and "not recalculated" in checked.stdout, checked.stdout[-3000:]
    return report


def _audit_strict(copy: Path, tmp_path: Path, name: str = "audit.json") -> tuple[int, dict[str, Any]]:
    out = tmp_path / name
    proc = _rotm("audit", str(copy), "--strict", "--json", str(out))
    assert "Traceback" not in proc.stderr, proc.stderr[-3000:]
    return proc.returncode, _report(out)


def test_shorter_extract_repair_passes_check_and_audit(short_model: Path, tmp_path: Path) -> None:
    """[C-sol-3]: an export with 4 rows fewer landed with its new total, the helper rows below the last raw row
    cleared: check --strict (no Excel) and audit --strict report no H-GAP, H-COVERAGE or H-BEYOND; check has no
    error and audit exits 0. v5 reported 8 H-GAP errors (h_lines rows 58 to 61) with the fix to copy the formulas
    back."""
    copy, rows, _total = _route(short_model, tmp_path, fewer=4)
    assert len(rows) == 56
    check = _check_strict_without_excel(copy, tmp_path)
    assert not [r for r in HELPER_RULES if r in _rules(check)], check["findings"]
    assert not _rules(check, "error"), check["findings"]
    code, rep = _audit_strict(copy, tmp_path)
    assert not [f for f in rep["findings"] if f["rule"] in HELPER_RULES], rep["findings"]
    assert code == 0, [(f["rule"], f["message"]) for f in rep["findings"] if f["severity"] != "info"]


def test_zero_row_repair_passes_check_and_audit(empty_model: Path, tmp_path: Path) -> None:
    """[C-sol-3]: the same with an export of zero rows under a contract agreed with --allow-empty (rows 0 to
    10,000), every helper row cleared: no H-GAP, H-COVERAGE or H-BEYOND; audit --strict exits 0."""
    copy, rows, total = _route(empty_model, tmp_path, fewer=60)
    assert rows == [] and total == 0
    contract = json.loads((copy / "contracts" / "lines.contract.json").read_text(encoding="utf-8"))
    assert contract["refresh"]["allow_empty"] is True and contract["agreement"]["status"] == "agreed"
    check = _check_strict_without_excel(copy, tmp_path)
    assert not [r for r in HELPER_RULES if r in _rules(check)], check["findings"]
    assert not _rules(check, "error"), check["findings"]
    code, rep = _audit_strict(copy, tmp_path)
    assert not [f for f in rep["findings"] if f["rule"] in HELPER_RULES], rep["findings"]
    assert code == 0, [(f["rule"], f["message"]) for f in rep["findings"] if f["severity"] != "info"]


def test_helper_cell_emptied_inside_the_boundary_is_h_gap(short_model: Path, tmp_path: Path) -> None:
    """[C-sol-3]: one helper cell emptied above the last raw row (h_lines!C10) of the repaired download: H-GAP at
    that cell, and the fix names only that cell; no row below the last raw row (57) is named."""
    copy, _rows, _total = _route(short_model, tmp_path, fewer=4, empty="C10")
    code, rep = _audit_strict(copy, tmp_path)
    gaps = _hits(rep, "H-GAP")
    assert code == 1 and len(gaps) == 1, rep["findings"]
    gap = gaps[0]
    assert (gap["sheet"], gap["cell"], gap["cells"], gap["count"]) == ("h_lines", "C10", ["C10"], 1), gap
    assert gap["fix"] == "copy the formula of C2 into C10 (or rebuild)", gap["fix"]
    assert "inside the formula rows 2 to 57 (row 10)" in gap["message"], gap["message"]
    assert not _hits(rep, "H-BEYOND") and not _hits(rep, "H-COVERAGE")


def test_helper_formulas_below_the_last_raw_row_are_h_beyond(short_model: Path, tmp_path: Path) -> None:
    """[C-sol-3]: fewer raw rows and the helper formulas left below the last raw row: one H-BEYOND error that names
    the rows (58 to 61) and the first cell (A58), with the fix to clear them or run extend; no H-GAP. check reports
    H-BEYOND for the same rows."""
    copy, _rows, _total = _route(short_model, tmp_path, fewer=4, clear_below=False)
    code, rep = _audit_strict(copy, tmp_path)
    assert code == 1
    assert not _hits(rep, "H-GAP"), _hits(rep, "H-GAP")
    beyond = _hits(rep, "H-BEYOND")
    assert len(beyond) == 1, rep["findings"]
    f = beyond[0]
    assert (f["severity"], f["sheet"], f["cell"], f["count"]) == ("error", "h_lines", "A58", 32), f
    assert f["message"] == ("helper sheet h_lines: rows 58 to 61 lie below the last raw row of raw_lines (row 57) and "
                            "hold 32 formula cell(s) (first A58); they read empty raw cells as 0"), f["message"]
    assert f["fix"].startswith("clear the contents of h_lines rows 58 to 61 (column A to H; do not delete the "
                               "rows), or run python -P -X utf8 -B "), f["fix"]
    assert f["fix"].endswith(f' extend "{copy.as_posix()}" --table lines (it clears them)'), f["fix"]
    out = tmp_path / "check.json"
    checked = _rotm("check", str(copy), "--strict", "--json", str(out))
    named = _hits(_report(out), "H-BEYOND")
    assert checked.returncode == 1 and named and "rows 58 to 61" in named[0]["message"], checked.stdout[-3000:]


def test_typed_values_below_the_last_raw_row_belong_to_h_beyond(tmp_path: Path) -> None:
    """[C-sol-3]: below the last raw row, typed values in a helper column are named by the same H-BEYOND finding
    (its fix clears them too); a row that is empty in every helper column gives nothing; a helper sheet whose raw
    sheet holds no data row and that has no helper row gives no finding."""
    from test_audit import run, write_model

    def leftovers(s: dict[str, dict[str, Any]]) -> None:
        for coord in ("A6", "B6", "C6"):                 # raw row 6 removed: rows 2 to 5 remain
            del s["raw_sales"][coord]
        s["h_sales"]["C8"] = 500                         # a typed value two rows below the helper rows

    def cleared(s: dict[str, dict[str, Any]]) -> None:
        for coord in ("A6", "B6", "C6"):                 # raw row 6 removed and helper row 6 cleared
            del s["raw_sales"][coord]
            del s["h_sales"][coord]
    rep = run(write_model(tmp_path / "m", leftovers))
    f = [x for x in rep.findings if x.rule == "H-BEYOND"]
    assert len(f) == 1 and f[0].severity == "error", [x.line() for x in rep.findings]
    assert f[0].message == ("helper sheet h_sales: rows 6 to 8 lie below the last raw row of raw_sales (row 5) and "
                            "hold 3 formula cell(s) (first A6) and 1 typed value(s) (first C8); the formulas read "
                            "empty raw cells as 0 and the calculations that read the helper columns include the "
                            "typed values"), f[0].message
    assert f[0].cells == ["A6", "B6", "C6", "C8"]
    assert "clear the contents of h_sales rows 6 to 8 (column A to C; do not delete the rows)" in f[0].fix
    assert not [x for x in rep.findings if x.rule in ("H-GAP", "H-COVERAGE")]
    # every helper cell below the last raw row cleared: no finding
    clean = run(write_model(tmp_path / "c", cleared))
    assert not [x for x in clean.findings if x.rule in HELPER_RULES], [x.line() for x in clean.findings]
    # zero raw rows and no helper row (the manifest's last_row is first_row - 1, as build writes it)
    def nothing(s: dict[str, dict[str, Any]]) -> None:
        for sheet in ("raw_sales", "h_sales"):
            for k in [k for k in s[sheet] if k[0] in "ABC" and k[1:] != "1"]:
                del s[sheet][k]

    def no_rows(m: dict[str, Any]) -> None:
        m["sheets"][2].update(last_row=1, capacity=0)
    zero = run(write_model(tmp_path / "z", nothing, no_rows))
    assert not [x for x in zero.findings if x.rule in HELPER_RULES], [x.line() for x in zero.findings]


@pytest.mark.excel
def test_refresh_and_extend_cycle_audits_strict(tmp_path: Path) -> None:
    """[C-sol-3]: the command cycle of selftest step f27 (written here; the step itself is not run): init from the
    starter, agree, land, build with the raw sheet as a Power Query table over raw/lines.csv, a CSV with 15 rows
    more, check --refresh, extend, check --accept-refresh --control, a CSV with the first 40 rows, check
    --refresh (H-BEYOND; the audit reports H-BEYOND too), extend, check --accept-refresh --control, a rebuild;
    audit --strict exits 0 after each accepted state."""
    root = tmp_path / "f27" / "model"
    root.parent.mkdir(parents=True)
    task = root.parent / "task.md"
    task.write_text(f"The user's reply to the contract: {REPLY}\n", encoding="utf-8")

    def run(*args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
        proc = _rotm(*args, excel_on=True)
        assert proc.returncode == expect, (args, proc.returncode, proc.stdout[-3000:], proc.stderr[-2000:])
        return proc

    def check(*extra: str, expect: int, rule: str | None = None) -> dict[str, Any]:
        out = tmp_path / f"check_{len(list(tmp_path.glob('check_*.json'))):02d}.json"
        run("check", str(root), "--quiet", "--json", str(out), *extra, expect=expect)
        rep = _report(out)
        if rule is not None:
            assert rule in _rules(rep), rep["findings"]
        return rep

    def audit_strict(expect: int = 0) -> dict[str, Any]:
        out = tmp_path / f"audit_{len(list(tmp_path.glob('audit_*.json'))):02d}.json"
        proc = _rotm("audit", str(root), "--strict", "--json", str(out))
        rep = _report(out)
        assert proc.returncode == expect, [(f["rule"], f["message"]) for f in rep["findings"]
                                           if f["severity"] != "info"]
        return rep

    def read(path: Path) -> list[list[str]]:
        with open(path, encoding="utf-8", newline="") as f:
            return list(csv.reader(f))[1:]

    def write(path: Path, rows: list[list[str]]) -> None:
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["line_id", "line_date", "category", "amount"])
            w.writerows(rows)

    def total(rows: list[list[str]]) -> str:
        return str(sum(int(r[3]) for r in rows))

    run("init", str(root), "--template", "starter", "--name", "v6_p2_f27")
    run("contract", "agree", str(root), "--table", "lines", "--reply", REPLY, "--by", "tester", "--channel",
        "task-file", "--source-file", str(task))
    run("land", str(root), "--table", "lines", "--csv", str(root / "data" / "lines.csv"))
    spec_path = root / "model.spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for sh in spec["sheets"]:
        if sh.get("type") == "raw":
            sh["landing"] = "power_query"
    spec_path.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
    raw_csv = root / "raw" / "lines.csv"
    base = read(raw_csv)
    run("build", str(root), "--strict", "--force")
    check(expect=0)
    audit_strict()
    cats = sorted({r[2] for r in base})
    grown = base + [[f"L9{i + 1:03d}", "2026-03-15", cats[i % len(cats)], str(10000 + 500 * i)] for i in range(15)]
    write(raw_csv, grown)
    check("--refresh", expect=1, rule="H-COVERAGE")
    run("extend", str(root))
    check("--accept-refresh", "--control", f"lines.amount={total(grown)}", expect=0)
    audit_strict()
    shrunk = base[:40]
    write(raw_csv, shrunk)
    check("--refresh", expect=1, rule="H-BEYOND")
    before_extend = audit_strict(expect=1)
    beyond = _hits(before_extend, "H-BEYOND")
    assert beyond and "rows 42 to 76" in beyond[0]["message"], before_extend["findings"]
    assert not _hits(before_extend, "H-GAP"), _hits(before_extend, "H-GAP")
    run("extend", str(root))
    after = check("--accept-refresh", "--control", f"lines.amount={total(shrunk)}", expect=0)
    assert "H-BEYOND" not in _rules(after)
    audit_strict()
    run("build", str(root), "--strict")
    check("--strict", expect=0)
    final = audit_strict()
    assert not [f for f in final["findings"] if f["rule"] in HELPER_RULES]


# ============================================================================= [A-aera-7] typed runs (inferred mode)

def _inferred(tmp_path: Path, cells: dict[str, Any], name: str = "w.xlsx") -> common.Report:
    from test_audit import run, write_wb
    return run(write_wb(tmp_path / name, {"calc": cells}))


def _typed_findings(rep: common.Report) -> list[common.Finding]:
    return [f for f in rep.findings if f.rule in ("H-INCONSISTENT", "B-INCONSISTENT") and "typed value" in f.message]


def _column_of_five() -> dict[str, Any]:
    cells: dict[str, Any] = {"A1": "units", "B1": "doubled"}
    for r in range(2, 7):
        cells[f"A{r}"] = 10 * r
        cells[f"B{r}"] = f"=A{r}*2"
    return cells


def test_two_pasted_values_in_a_helper_column_are_one_finding(tmp_path: Path) -> None:
    """[A-aera-7]: a column of five formula rows (B2:B6) in which the second and third (B3, B4) are replaced by
    their values: one H-INCONSISTENT warning, '(inferred)', that names both cells, the range and the formula to
    copy over it. v5 reported nothing for these cells."""
    cells = _column_of_five()
    cells["B3"], cells["B4"] = 60, 80
    found = _typed_findings(_inferred(tmp_path, cells))
    assert len(found) == 1, [f.line() for f in found]
    f = found[0]
    assert (f.rule, f.severity, f.cells, f.count) == ("H-INCONSISTENT", "warning", ["B3", "B4"], 2)
    assert f.message == ("(inferred) 2 typed values in B3:B4 sit inside a column whose cells on both sides hold one "
                         "formula (=A2*2 in B2, the same formula in B5)"), f.message
    assert f.fix == ("copy the formula of B2 over B3:B4; if the values are inputs, put them on the inputs sheet and "
                     "reference them")
    assert f.data["range"] == "B3:B4" and f.data["inferred"] is True


def test_three_pasted_values_in_a_formula_row_are_one_finding(tmp_path: Path) -> None:
    """[A-aera-7]: a formula row C3:H3 (=C2*2 copied across) in which D3, E3 and F3 are replaced by values: one
    B-INCONSISTENT warning, '(inferred)', that names the three cells."""
    cells: dict[str, Any] = {"B2": "units", "B3": "doubled"}
    for col in "CDEFGH":
        cells[f"{col}2"] = 5
        cells[f"{col}3"] = f"={col}2*2"
    cells["D3"], cells["E3"], cells["F3"] = 10, 10, 10
    found = _typed_findings(_inferred(tmp_path, cells))
    assert len(found) == 1, [f.line() for f in found]
    f = found[0]
    assert (f.rule, f.cells, f.count) == ("B-INCONSISTENT", ["D3", "E3", "F3"], 3)
    assert f.message.startswith("(inferred) 3 typed values in D3:F3 sit inside a row whose cells on both sides hold "
                                "one formula (=C2*2 in C3, the same formula in G3)"), f.message
    assert f.fix.startswith("copy the formula of C3 over D3:F3")


def test_one_pasted_value_keeps_its_message(tmp_path: Path) -> None:
    """[A-aera-7]: a single typed value keeps the v5 finding and its message."""
    cells = _column_of_five()
    cells["B4"] = 80
    found = _typed_findings(_inferred(tmp_path, cells))
    assert [(f.rule, f.cells) for f in found] == [("H-INCONSISTENT", ["B4"])]
    assert found[0].message == ("(inferred) the typed value 80 in B4 sits inside a column whose cells on both sides "
                                "hold one formula (=A3*2 in B3)"), found[0].message
    assert found[0].fix == ("copy the formula of B3 into B4; if the value is an input, put it on the inputs sheet "
                            "and reference it")


def test_a_label_row_between_two_parts_of_a_column_gives_no_finding(tmp_path: Path) -> None:
    """[A-aera-7]: text cells are not counted: a label row (B5) between two parts of a column that hold the same
    relative formula gives no finding; a run that holds a text cell gives none either."""
    cells: dict[str, Any] = {"A1": "units", "B1": "doubled"}
    for r in (2, 3, 4, 6, 7, 8):
        cells[f"A{r}"] = 10 * r
        cells[f"B{r}"] = f"=A{r}*2"
    cells["B5"] = "Part two"
    assert _typed_findings(_inferred(tmp_path, cells, "label.xlsx")) == []
    mixed = _column_of_five()
    mixed["B3"], mixed["B4"] = 60, "n/a"
    assert _typed_findings(_inferred(tmp_path, mixed, "mixed.xlsx")) == []


def test_runs_need_one_more_formula_cell_beyond_either_end(tmp_path: Path) -> None:
    """[A-aera-7]: the condition of v5 stays: at least one more cell of the formula above the upper cell or below
    the lower cell (B2 =, B3:B4 typed, B5 = and nothing else gives no finding)."""
    cells: dict[str, Any] = {"A1": "units", "B1": "doubled", "B2": "=A2*2", "B3": 60, "B4": 80, "B5": "=A5*2"}
    for r in range(2, 6):
        cells[f"A{r}"] = 10 * r
    assert _typed_findings(_inferred(tmp_path, cells)) == []


def test_shipped_models_get_no_typed_run_finding(tmp_path: Path) -> None:
    """[A-aera-7]: the six shipped models (copies of their built model.xlsx) give no finding of this rule, audited
    with their manifests and audited as workbooks without one (inferred mode). QA's clean twin is not readable by
    the builder; the orchestrator's evidence step audits it."""
    seen = 0
    for src in SHIPPED_MODELS:
        if not (src / "model.xlsx").is_file():
            continue
        seen += 1
        folder = tmp_path / src.name
        shutil.copytree(src, folder, ignore=shutil.ignore_patterns("*.bak_*"))
        rep = audit.audit_workbook(folder / "model.xlsx", strict=True, record=False)
        assert _typed_findings(rep) == [], (src.name, [f.line() for f in _typed_findings(rep)])
        bare = tmp_path / f"{src.name}_bare"
        bare.mkdir()
        shutil.copy2(src / "model.xlsx", bare / "model.xlsx")
        rep = audit.audit_workbook(bare / "model.xlsx", strict=True, record=False)
        assert rep.data["mode"] == "inferred"
        assert _typed_findings(rep) == [], (src.name, [f.line() for f in _typed_findings(rep)])
    assert seen >= 2


# ============================================================================= [A-aera-8, A-aera-13] number formats

def _worked_example(tmp_path: Path) -> Path:
    src = SKILL / "examples" / "sales_vs_target"
    dest = tmp_path / "sales_vs_target"
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("*.bak_*"))
    return dest


def _set_format(path: Path, sheet: str, coord: str, fmt: str) -> None:
    import openpyxl
    wb = openpyxl.load_workbook(path)
    wb[sheet][coord].number_format = fmt
    wb.save(path)


def test_quoted_percent_sign_is_s_numfmt(tmp_path: Path) -> None:
    """[A-aera-8]: the rate input alert_below (inputs!D8, format key rate, 0.0%) of a copy of the worked example
    given the format 0.0"%" (and 0.0\\%) gets S-NUMFMT at that cell: the percent sign in quotes or after a backslash
    shows the sign without multiplying by 100. v5 counted them equal (quotes and backslashes were left out)."""
    model = _worked_example(tmp_path)
    man = json.loads((model / "model.manifest.json").read_text(encoding="utf-8"))
    assert man["inputs"]["alert_below"] == "inputs!D8"
    for fmt in ('0.0"%"', "0.0\\%"):
        _set_format(model / "model.xlsx", "inputs", "D8", fmt)
        rep = audit.audit_workbook(model / "model.xlsx", strict=True, record=False)
        hits = [f for f in rep.findings if f.rule == "S-NUMFMT"]
        assert [(f.sheet, f.cell) for f in hits] == [("inputs", "D8")], (fmt, [f.line() for f in hits])
        assert hits[0].data == {"number_format": fmt, "expected": "0.0%"}
        assert "input 'alert_below' (rate)" in hits[0].message
    _set_format(model / "model.xlsx", "inputs", "D8", "0.0%")
    rep = audit.audit_workbook(model / "model.xlsx", strict=True, record=False)
    assert not [f for f in rep.findings if f.rule == "S-NUMFMT"]


def test_escaped_hyphens_in_a_date_format_are_equal(tmp_path: Path) -> None:
    """[A-aera-8, A-aera-13]: styles.number_formats_equal: Excel's saved form of a format equals the dictionary's;
    a character with a meaning of its own differs from the same character as a literal; letter case of codes does
    not count. A raw date column whose format Excel saved as yyyy\\-mm\\-dd gets no S-NUMFMT and no theme mismatch."""
    eq = styles.number_formats_equal
    assert eq("yyyy\\-mm\\-dd", "yyyy-mm-dd") and eq('yyyy"-"mm"-"dd', "yyyy-mm-dd")
    assert eq("YYYY-MM-DD", "yyyy-mm-dd") and eq("General", "general") and eq("", "General") and eq(None, "General")
    assert eq('#,##0_);\\(#,##0\\);"-"_)', '#,##0_);(#,##0);"-"_)')
    assert eq('#,##0_);\\(#,##0\\);\\-_)', '#,##0_);(#,##0);"-"_)')
    assert eq('[Red]"FAIL";[Red]"FAIL";"OK"', '[RED]\\F\\A\\I\\L;[Red]"FAIL";"OK"')
    assert eq("mmm\\ yyyy", "mmm yyyy") and eq("0.0 \"KRW\"", "0.0\\ \\K\\R\\W")
    assert not eq("0.0%", '0.0"%"') and not eq("0.0%", "0.0\\%")
    assert not eq("#,##0", '#","##0') and not eq("0.0", '0"."0')
    assert not eq("yyyy-mm-dd", '"yyyy"-mm-dd') and not eq("#,##0", "#,##0,")
    assert eq('"OK"', '"ok"') and eq("[RED]0", "[red]0")      # the verdict: letter case does not count
    assert not eq("0.0%", "0.00%") and not eq("[Red]0", "[Blue]0")
    # the shipped worked example: its raw date columns hold Excel's saved form, and S-NUMFMT reports nothing
    model = _worked_example(tmp_path)
    from lib import dictionary, workbook
    info = workbook.open_workbook(model / "model.xlsx")
    raw = info.sheet("raw_sales")
    assert raw.cell("C2").number_format == "yyyy\\-mm\\-dd"
    d = dictionary.load_dictionary()
    assert d.number_format("date") == "yyyy-mm-dd"
    exp = styles.expected_style(d, "raw", sheet_size=8, format_key="date")
    assert styles.mismatches(raw.cell("C2"), exp, attributes=("number_format",)) == []
    rep = audit.audit_workbook(model / "model.xlsx", strict=True, record=False)
    assert not [f for f in rep.findings if f.rule == "S-NUMFMT"], [f.line() for f in rep.findings]


def test_audit_and_theme_use_the_one_comparison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[A-aera-8, A-aera-13]: audit S-NUMFMT and styles.mismatches (theme's plan, T-RESIDUAL and --number-formats)
    call styles.number_formats_equal."""
    calls: list[tuple[Any, Any]] = []
    real = styles.number_formats_equal

    def spy(a: Any, b: Any) -> bool:
        calls.append((a, b))
        return real(a, b)
    monkeypatch.setattr(styles, "number_formats_equal", spy)
    model = _worked_example(tmp_path)
    audit.audit_workbook(model / "model.xlsx", strict=True, record=False, rules=["S-NUMFMT"])
    assert ("yyyy\\-mm\\-dd", "yyyy-mm-dd") in calls
    calls.clear()

    class Cell:
        number_format = "yyyy\\-mm\\-dd"
    assert styles.mismatches(Cell(), {"number_format": "yyyy-mm-dd"}, attributes=("number_format",)) == []
    assert calls == [("yyyy\\-mm\\-dd", "yyyy-mm-dd")]
    calls.clear()
    from lib import theme
    rep = theme.theme_workbook(model / "model.xlsx", number_formats=True, dry_run=True, strict=True)
    assert ("yyyy\\-mm\\-dd", "yyyy-mm-dd") in calls, "theme's plan did not use styles.number_formats_equal"
    # the raw date columns that Excel saved as yyyy\-mm\-dd are not planned for a change (v5 planned every one)
    assert rep.data["sheets"]["raw_sales"]["changed_cells"] == 0, rep.data["sheets"]["raw_sales"]
    assert rep.data["sheets"]["raw_target"]["changed_cells"] == 0, rep.data["sheets"]["raw_target"]


@pytest.mark.excel
def test_theme_number_formats_after_an_excel_save_leaves_no_residual(tmp_path: Path) -> None:
    """[A-aera-13]: a copy of the worked example (its v5 model.xlsx) saved through Excel; theme --number-formats
    --strict exits 0 with no T-RESIDUAL, and a second run on its result changes no cell. v5 reported T-RESIDUAL
    on the date columns of raw_sales and raw_target (yyyy\\-mm\\-dd)."""
    from lib import excel
    model = _worked_example(tmp_path)
    wb = model / "model.xlsx"
    res = excel.recalc(wb, save=True)
    assert res.ok, res.message()
    out1, out2 = tmp_path / "theme1.json", tmp_path / "theme2.json"
    first = _rotm("theme", str(wb), "--number-formats", "--strict", "--json", str(out1), excel_on=True)
    rep1 = _report(out1)
    assert "T-RESIDUAL" not in _rules(rep1), _hits(rep1, "T-RESIDUAL")
    assert first.returncode == 0, [(f["rule"], f["message"]) for f in rep1["findings"] if f["severity"] != "info"]
    result = Path(rep1["data"]["output"])
    assert result.is_file()
    second = _rotm("theme", str(result), "--number-formats", "--strict", "-o", str(tmp_path / "again.xlsx"),
                   "--json", str(out2), excel_on=True)
    rep2 = _report(out2)
    assert second.returncode == 0, [(f["rule"], f["message"]) for f in rep2["findings"] if f["severity"] != "info"]
    assert rep2["data"]["cells_changed"] == 0, rep2["data"]["sheets"]
    assert "T-RESIDUAL" not in _rules(rep2)


def test_shipped_models_audit_strict(tmp_path: Path) -> None:
    """[A-aera-8, A-aera-13]: copies of the six shipped model folders (their built model.xlsx) audit in strict mode
    with exit 0 under the new comparison (the plugin's templates are skipped when the skill folder is used alone)."""
    seen = []
    for src in SHIPPED_MODELS:
        if not (src / "model.xlsx").is_file():
            continue
        folder = tmp_path / src.name
        shutil.copytree(src, folder, ignore=shutil.ignore_patterns("*.bak_*"))
        rep = audit.audit_workbook(folder / "model.xlsx", strict=True, record=False)
        assert rep.exit_code == 0, (src.name, [f.line() for f in rep.findings if f.severity != "info"])
        seen.append(src.name)
    assert "sales_vs_target" in seen and "starter" in seen, seen


# ============================================================================= supporting [A-aera-5]: R-TYPE (audit)

def test_logical_value_in_a_text_column_is_r_type(tmp_path: Path) -> None:
    """Supporting [A-aera-5], audit side, without Excel: a cell of a text column (raw_sales!A3, invoice_id) that
    the workbook stores as the logical value TRUE is R-TYPE at that cell. v5 left it unreported."""
    from test_audit import run, write_model

    def logical(s: dict[str, dict[str, Any]]) -> None:
        s["raw_sales"]["A3"] = True
    rep = run(write_model(tmp_path / "m", logical))
    hits = [f for f in rep.findings if f.rule == "R-TYPE"]
    assert len(hits) == 1, [f.line() for f in rep.findings]
    assert hits[0].message == ("1 cell(s) of text column invoice_id in raw_sales are stored as logical values (first "
                               "A3: TRUE)"), hits[0].message
    assert (hits[0].sheet, hits[0].cell, hits[0].severity) == ("raw_sales", "A3", "error")


@pytest.mark.excel
def test_logical_value_saved_by_excel_is_r_type(tmp_path: Path) -> None:
    """Supporting [A-aera-5], audit side (Excel): a model whose text column line_id holds the text TRUE in its first
    row; after the build that raw cell is set to the logical value with openpyxl and the file is saved through Excel;
    audit --strict does not exit 0 and names the cell under R-TYPE."""
    import openpyxl

    from lib import excel
    root = tmp_path / "model"
    task = tmp_path / "task.md"
    task.write_text(f"The user's reply to the contract: {REPLY}\n", encoding="utf-8")
    _ok(_rotm("init", str(root), "--template", "starter", "--name", "v6_p2_logical"), "init")
    _ok(_rotm("contract", "agree", str(root), "--table", "lines", "--reply", REPLY, "--by", "tester", "--channel",
              "task-file", "--source-file", str(task)), "agree")
    rows = _read_rows(root / "data" / "lines.csv")
    rows[0]["line_id"] = "TRUE"
    sample = tmp_path / "lines_true.csv"
    with open(sample, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    _ok(_rotm("land", str(root), "--table", "lines", "--csv", str(sample)), "land")
    _ok(_rotm("build", str(root), "--strict"), "build")
    wb_path = root / "model.xlsx"
    wb = openpyxl.load_workbook(wb_path)
    assert wb["raw_lines"]["A2"].value == "TRUE"
    wb["raw_lines"]["A2"].value = True
    wb.save(wb_path)
    res = excel.recalc(wb_path, save=True)
    assert res.ok, res.message()
    assert openpyxl.load_workbook(wb_path)["raw_lines"]["A2"].value is True
    out = tmp_path / "audit.json"
    proc = _rotm("audit", str(root), "--strict", "--json", str(out))
    rep = _report(out)
    assert proc.returncode != 0
    hits = _hits(rep, "R-TYPE")
    assert [(f["sheet"], f["cell"]) for f in hits] == [("raw_lines", "A2")], rep["findings"]
    assert "stored as logical values (first A2: TRUE)" in hits[0]["message"]
