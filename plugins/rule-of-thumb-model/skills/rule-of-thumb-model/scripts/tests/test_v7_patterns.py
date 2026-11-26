"""Tests of the v7 change [J-1] of package P5 (round 6 verdict, 2026-11-26). Owner: P5.

[J-1]: references/helper-patterns.md, section "Rank or percentile within a group", gives one pattern for a rank and
a percentile within a group in helper columns: COUNTIFS over the raw columns, {raw:<t>.<group>} and
{raw:<t>.<value>}, with the row's own values from {this:...}, and one check (the ranks largest first and smallest
first have the same sum). The refusal of {h:...} in a helper column names that section (spec._check_placeholder).

- The section's JSON, put into a copy of the starter whose lines are a fixture made for the test (21 lines in 4
  categories, ties, decimals, a negative amount and 0), builds and audits in strict mode (exit 0) without Excel.
- With Excel, check --strict exits 0 and every value of the four helper columns equals the calculation in Python.
- With Excel, a rank that leaves out the group criterion makes the check fail.
- With Excel, ">"&<number> compares numbers to 15 significant digits (the section's statement on decimals).

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v7_patterns.py -q -p no:cacheprovider
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
REFS = SKILL / "references"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import formula  # noqa: E402

PY = [sys.executable, "-P", "-X", "utf8", "-B"]
ROTM_CMD = PY + [str(SCRIPTS / "rotm.py")]
REPLY = "agreed for the v7 rank pattern test of package P5"
SECTION = "## Rank or percentile within a group"
ANCHOR = "rank-or-percentile-within-a-group"
NAMES = (("sales.", "lines."), ("sales_", "lines_"), ("region", "category"))

# (line_id, line_date, category, amount): 21 lines in 4 categories, with ties inside a category, an amount shared
# by two categories (1000), decimals of at most 15 significant digits, a negative amount and 0.
FIXTURE = [
    ("R01", "2026-01-05", "Rent", 1000), ("R02", "2026-01-20", "Rent", 1000), ("R03", "2026-02-03", "Rent", 750.5),
    ("R04", "2026-02-14", "Rent", 2000), ("R05", "2026-03-01", "Rent", 2000), ("R06", "2026-03-30", "Rent", 2000),
    ("S01", "2026-01-09", "Staff", 1234.5678), ("S02", "2026-02-10", "Staff", 99.25),
    ("S03", "2026-02-11", "Staff", 99.25), ("S04", "2026-03-12", "Staff", 0.125), ("S05", "2026-03-31", "Staff", 5000),
    ("U01", "2026-01-15", "Supplies", 300), ("U02", "2026-02-15", "Supplies", 300),
    ("U03", "2026-03-15", "Supplies", 300), ("U04", "2026-03-16", "Supplies", 300),
    ("T01", "2026-01-02", "Travel", 10), ("T02", "2026-01-03", "Travel", -5), ("T03", "2026-02-04", "Travel", 0),
    ("T04", "2026-02-05", "Travel", 20.75), ("T05", "2026-03-06", "Travel", 10), ("T06", "2026-03-07", "Travel", 1000),
]


def _rotm(*args: str, excel_on: bool = False) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if excel_on:
        env.pop("ROTM_NO_EXCEL", None)
    else:
        env["ROTM_NO_EXCEL"] = "1"
    return subprocess.run(ROTM_CMD + list(args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=1800)


def _ok(proc: subprocess.CompletedProcess[str], what: Any) -> None:
    assert proc.returncode == 0, (what, proc.returncode, proc.stdout[-3000:], proc.stderr[-2000:])


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


def _section() -> str:
    text = (REFS / "helper-patterns.md").read_text(encoding="utf-8")
    assert SECTION in text, f"helper-patterns.md has no section {SECTION!r}"
    return text.split(SECTION, 1)[1].split("\n## ", 1)[0]


def _section_json() -> dict[str, Any]:
    blocks = re.findall(r"```json\n(.*?)```", _section(), flags=re.S)
    assert len(blocks) == 1, f"the section holds {len(blocks)} JSON blocks; one is expected"
    return json.loads(blocks[0])


def _for_starter(obj: dict[str, Any]) -> dict[str, Any]:
    """The section's JSON with the starter's names: table lines, group column category."""
    text = json.dumps(obj)
    for old, new in NAMES:
        text = text.replace(old, new)
    return json.loads(text)


def _python_values(rows: list[tuple[str, str, str, float]]) -> dict[str, dict[str, float]]:
    """Rank largest first, rank smallest first, lines in the category and the share, per line id."""
    out: dict[str, dict[str, float]] = {}
    for lid, _d, cat, amt in rows:
        same = [a for _l, _dd, c, a in rows if c == cat]
        rank = 1 + sum(1 for a in same if a > amt)
        out[lid] = {"amount_rank": rank, "amount_rank_up": 1 + sum(1 for a in same if a < amt),
                    "category_n": len(same), "amount_share": rank / len(same)}
    return out


def _model(root: Path, *, rank_expr: str | None = None) -> Path:
    """The starter template with FIXTURE as its lines, the contract proposed from them and agreed on the task-file
    channel, landed, and the section's JSON added (helper columns on h_lines, the check on the checks sheet).
    `rank_expr` replaces the expression of amount_rank (the wrong rank of the failing test)."""
    _ok(_rotm("init", str(root), "--template", "starter", "--name", f"v7_p5_{root.name}"), "init")
    csv_path = root / "data" / "lines.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["line_id", "line_date", "category", "amount"])
        for lid, d, cat, amt in FIXTURE:
            w.writerow([lid, d, cat, repr(amt) if isinstance(amt, float) else amt])
    total = round(sum(a for *_x, a in FIXTURE), 6)
    task = root.parent / f"{root.name}_task.md"
    task.write_text(f"The user's reply to the contract: {REPLY}\n", encoding="utf-8")
    for args in (("contract", "propose", str(root), "--table", "lines", "--csv", str(csv_path), "--force",
                  "--key", "line_id", "--grain", "one row per expense line", "--unit", "amount=KRW",
                  "--type", "amount=number", "--total", f"amount={total}:0.001"),
                 ("contract", "agree", str(root), "--table", "lines", "--reply", REPLY, "--by", "tester",
                  "--channel", "task-file", "--source-file", str(task)),
                 ("land", str(root), "--table", "lines", "--csv", str(csv_path))):
        _ok(_rotm(*args), args)
    pattern = _for_starter(_section_json())
    if rank_expr is not None:
        pattern["helper_columns"][0]["expr"] = rank_expr
    spec_path = root / "model.spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for sheet in spec["sheets"]:
        if sheet["name"] == "h_lines":
            sheet["columns"].extend(pattern["helper_columns"])
        if sheet["name"] == "checks":
            sheet["checks"].append(pattern["check"])
    spec_path.write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def rank_model(tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest) -> Path:
    root = _model(_outside_folder(tmp_path_factory, request, "v7_rank") / "model")
    _ok(_rotm("build", str(root), "--strict"), "build --strict")
    return root


def _helper_values(model: Path) -> dict[str, dict[str, Any]]:
    """Values Excel saved in h_lines for the four pattern columns, per line id (the manifest names the columns)."""
    import openpyxl
    man = json.loads((model / "model.manifest.json").read_text(encoding="utf-8"))
    entry = next(s for s in man["sheets"] if s["name"] == "h_lines")
    cols = {c["id"]: c["col"] for c in entry["columns"]}
    ws = openpyxl.load_workbook(model / "model.xlsx", data_only=True)["h_lines"]
    out: dict[str, dict[str, Any]] = {}
    for r in range(entry["first_row"], entry["last_row"] + 1):
        lid = ws[f"{cols['line_id']}{r}"].value
        out[str(lid)] = {k: ws[f"{cols[k]}{r}"].value for k in ("amount_rank", "amount_rank_up", "category_n",
                                                               "amount_share")}
    return out


# ============================================================================= the section

def test_section_holds_the_pattern_its_check_and_the_reason() -> None:
    """[J-1]: the section exists under its fixed heading (anchor rank-or-percentile-within-a-group), its JSON holds
    four helper columns that read the raw columns and the row's own cells, and one equal check; it names the order
    functions of the catalogue, how ties are ranked, the caveat of ">"& with a number and the design's rule."""
    sec = _section()
    obj = _section_json()
    exprs = [c["expr"] for c in obj["helper_columns"]]
    assert len(exprs) == 4
    assert all("{h:" not in e for e in exprs), exprs
    assert "{raw:sales.region}" in exprs[0] and "{this:amount}" in exprs[0] and "\">\"&" in exprs[0]
    assert obj["check"]["kind"] == "equal"
    for words in ("RANK.EQ", "PERCENTRANK.INC", "F-FUNC-UNKNOWN", "SORT", "F-FUNC-FLAGGED", "COUNTIFS", "MAXIFS",
                  "15 significant digits", "design 6.6", "design 7.1", "ALLOWED_IN", "1, 2, 2 and 4"):
        assert words in sec, words
    from lib import functions
    unknown = ("RANK", "RANK.EQ", "RANK.AVG", "PERCENTRANK", "PERCENTRANK.INC", "PERCENTRANK.EXC", "PERCENTILE",
               "PERCENTILE.INC", "PERCENTILE.EXC", "QUARTILE", "QUARTILE.INC", "QUARTILE.EXC")
    for name in unknown:
        assert {functions.classify(name, p) for p in ("both", "excel", "sheets")} == {"unknown"}, name
        assert name in sec, name
    for name in ("COUNTIFS", "COUNTIF", "SUMPRODUCT", "LARGE", "SMALL", "MEDIAN", "MAXIFS", "MINIFS"):
        assert {functions.classify(name, p) for p in ("both", "excel", "sheets")} == {"allowed"}, name
    for name in ("SORT", "SORTBY"):
        assert {functions.classify(name, p) for p in ("both", "excel", "sheets")} == {"flagged"}, name


def test_pattern_formulas_stay_within_the_rules() -> None:
    """[J-1]: every expression of the section's JSON, with the placeholders as build writes them, has depth 2 or
    less and breaks no formula rule (no typed number other than 0 and 1)."""
    obj = _section_json()
    samples = {"raw": "raw_sales!$F:$F", "this": "F2", "h": "h_sales!$G:$G"}
    texts = [c["expr"] for c in obj["helper_columns"]] + [obj["check"]["actual"], obj["check"]["expected"]]
    for expr in texts:
        f = "=" + re.sub(r"\{([a-z]+):[^{}]*\}", lambda m: samples[m.group(1)], expr)
        assert formula.depth(f) <= 2, f
        assert formula.check_formula(f, depth_limit=2, length_limit=100) == [], f


def test_refusal_of_h_in_a_helper_column_names_the_section(outside_tmp: Path) -> None:
    """[J-1]: a helper column COUNTIFS({h:t.group},{this:group},{h:t.amount},">"&{this:amount})+1 is refused (exit 2);
    the fix names references/helper-patterns.md, "Rank or percentile within a group", a heading of that file.
    v6 named no pattern."""
    root = outside_tmp / "model"
    _ok(_rotm("init", str(root), "--template", "starter", "--name", "v7_p5_refusal"), "init")
    spec_path = root / "model.spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    for sheet in spec["sheets"]:
        if sheet["name"] == "h_lines":
            sheet["columns"].append({"id": "amount_rank", "label": "Rank", "expr":
                                     "COUNTIFS({h:lines.category},{this:category},{h:lines.amount},\">\"&{this:amount})+1"})
    spec_path.write_text(json.dumps(spec, indent=1), encoding="utf-8")
    proc = _rotm("build", str(root), "--strict")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out[-3000:]
    lines = [ln for ln in out.splitlines() if "{h:lines.category}" in ln]
    assert lines, out[-3000:]
    assert 'references/helper-patterns.md, "Rank or percentile within a group"' in lines[0], lines[0]
    assert "a helper column reads only its own row of its own sheet" in lines[0], lines[0]
    text = (REFS / "helper-patterns.md").read_text(encoding="utf-8")
    assert re.search(r"^## Rank or percentile within a group$", text, flags=re.M)


# ============================================================================= build and audit, without Excel

def test_rank_pattern_builds_and_audits_strict(rank_model: Path) -> None:
    """[J-1]: the section's JSON in a starter copy with the fixture (21 lines, 4 categories, ties) builds in strict
    mode (the fixture's build) and audits in strict mode with exit 0; the four columns are on h_lines."""
    proc = _rotm("audit", str(rank_model), "--strict", "--no-record")
    _ok(proc, "audit --strict")
    man = json.loads((rank_model / "model.manifest.json").read_text(encoding="utf-8"))
    ids = [c["id"] for s in man["sheets"] if s["name"] == "h_lines" for c in s["columns"]]
    assert {"amount_rank", "amount_rank_up", "category_n", "amount_share"} <= set(ids), ids
    assert "lines_rank_sums" in [c["id"] for c in man["checks"]]
    assert len({c for _l, _d, c, _a in FIXTURE}) >= 3
    assert any(n > 1 for n in Counter((c, a) for _l, _d, c, a in FIXTURE).values())


# ============================================================================= with Excel

@pytest.mark.excel
def test_rank_pattern_equals_python_in_excel(rank_model: Path, tmp_path: Path) -> None:
    """[J-1] (Excel): on a copy of the fixture model, check --strict exits 0 (the check lines_rank_sums holds) and
    every value of the four helper columns equals the calculation in Python on the fixture."""
    model = tmp_path / "model"
    shutil.copytree(rank_model, model)
    out = tmp_path / "check.json"
    proc = _rotm("check", str(model), "--strict", "--json", str(out), excel_on=True)
    if proc.returncode == 5:
        pytest.skip("Excel busy (exit 5): run again later")
    _ok(proc, "check --strict")
    got = _helper_values(model)
    want = _python_values(FIXTURE)
    assert set(got) == set(want), (sorted(got), sorted(want))
    for lid, values in want.items():
        for key, value in values.items():
            assert got[lid][key] == pytest.approx(value, abs=1e-12), (lid, key, got[lid][key], value)


@pytest.mark.excel
def test_rank_check_fails_for_a_rank_without_its_group(outside_tmp: Path) -> None:
    """[J-1] (Excel): the same fixture with the category criterion left out of amount_rank (a rank over the whole
    table) builds in strict mode, but check --strict fails, and the failing check is lines_rank_sums."""
    model = _model(outside_tmp / "model", rank_expr="COUNTIFS({raw:lines.amount},\">\"&{this:amount})+1")
    _ok(_rotm("build", str(model), "--strict"), "build --strict")
    out = outside_tmp / "check.json"
    proc = _rotm("check", str(model), "--strict", "--json", str(out), excel_on=True)
    if proc.returncode == 5:
        pytest.skip("Excel busy (exit 5): run again later")
    assert proc.returncode == 1, proc.stdout[-3000:]
    rep = json.loads(out.read_text(encoding="utf-8"))
    failing = [f for f in rep["findings"] if f["rule"] == "C-FAIL"]
    assert any("lines_rank_sums" in f["message"] for f in failing), [f["message"] for f in rep["findings"]]


@pytest.mark.excel
def test_rank_criteria_compare_numbers_to_15_significant_digits(tmp_path: Path) -> None:
    """[J-1] (Excel): the section's statement on ">"&<number>. The pattern's three formulas over a small raw table:
    Excel's ranks and tie counts equal Python's on the values rounded to 15 significant digits; for values that
    agree in their first 15 significant digits (0.1+0.2 and 0.3; 1/3 and 0.333333333333333; 1234567890123456 and
    1234567890123457) they differ from Python's exact comparison; ">"&(0.1+0.2) is the text ">0.3"."""
    import openpyxl

    from lib import excel
    pairs = [("a", 0.1 + 0.2), ("a", 0.3), ("b", 1 / 3), ("b", 0.333333333333333), ("c", 1234.5678),
             ("c", 1234.5678), ("c", 99.125), ("d", 1234567890123456.0), ("d", 1234567890123457.0),
             ("e", -0.5), ("e", 0.0), ("e", 2.75)]
    wb = openpyxl.Workbook()
    raw = wb.active
    raw.title = "raw_t"
    raw.append(["grp", "value"])
    for g, v in pairs:
        raw.append([g, v])
    h = wb.create_sheet("h_t")
    h.append(["grp", "value", "criterion", "rank", "rank_up", "ties"])
    for r in range(2, len(pairs) + 2):
        h.append([f"=raw_t!A{r}", f"=raw_t!B{r}", f'=">"&B{r}',
                  f'=COUNTIFS(raw_t!$A:$A,A{r},raw_t!$B:$B,">"&B{r})+1',
                  f'=COUNTIFS(raw_t!$A:$A,A{r},raw_t!$B:$B,"<"&B{r})+1',
                  f"=COUNTIFS(raw_t!$A:$A,A{r},raw_t!$B:$B,B{r})"])
    src = tmp_path / "criteria.xlsx"
    wb.save(src)
    done = tmp_path / "criteria_recalc.xlsx"
    try:
        excel.recalc(src, out=done)
    except Exception as exc:  # noqa: BLE001
        if "busy" in str(exc).lower():
            pytest.skip(f"Excel busy: {exc}")
        raise
    ws = openpyxl.load_workbook(done, data_only=True)["h_t"]

    def ranks(values: list[tuple[str, float]]) -> list[tuple[int, int, int]]:
        return [(1 + sum(1 for g2, v2 in values if g2 == g and v2 > v), 1 + sum(1 for g2, v2 in values if g2 == g
                 and v2 < v), sum(1 for g2, v2 in values if g2 == g and v2 == v)) for g, v in values]
    rounded = [(g, float(format(v, ".15g"))) for g, v in pairs]
    excel_values = [(ws.cell(r, 4).value, ws.cell(r, 5).value, ws.cell(r, 6).value) for r in range(2, len(pairs) + 2)]
    assert excel_values == ranks(rounded), excel_values
    exact = ranks(pairs)
    differ = {pairs[i][0] for i in range(len(pairs)) if excel_values[i] != exact[i]}
    assert differ == {"a", "b", "d"}, differ
    assert ws.cell(2, 3).value == ">0.3"
    assert ws.cell(9, 3).value == ">1234567890123460"
