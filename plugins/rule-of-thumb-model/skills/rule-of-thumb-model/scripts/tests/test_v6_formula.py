"""v6 tests of package P1 (r5 verdict [A-aera-6]): the argument counts of 29 listed functions (rule F-ARGS, error)
and the X-OPEN fix text that names the causes the strict audit finds. No Excel.

- formula.ARG_COUNTS holds the counts of Microsoft's function reference; a call of a listed function with a count
  outside them is one F-ARGS error. A function outside the list is not counted; an empty argument counts.
- build refuses F-ARGS at the JSON path of the expression (I-1: needs P3's "F-ARGS" in build.REFUSED_ISSUES).
- audit reports F-ARGS at the cell.
- The formulas of the six shipped models and of the foreign workbooks (ROTM_FOREIGN_DIR) give no F-ARGS: Excel
  saved them, so a finding there would be an error of the table.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lib import excel
from lib import formula as F

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
DATA = Path(__file__).resolve().parent / "data" / "formula"
STARTER = SKILL / "templates" / "starter"
SHIPPED = {"sales_vs_target": SKILL / "examples" / "sales_vs_target", "starter": STARTER,
           **{n: PLUGIN / "templates" / n for n in ("ar_aging", "budget_vs_actual", "price_volume_mix",
                                                     "rolling_forecast")}}

# The 29 functions of the verdict entry, with the counts of plan section 5.1 (Microsoft's function reference).
LISTED = {"IF": (2, 3), "IFERROR": (2, 2), "IFNA": (2, 2), "AND": (1, 255), "OR": (1, 255), "NOT": (1, 1),
          "ABS": (1, 1), "ROUND": (2, 2), "ROUNDUP": (2, 2), "ROUNDDOWN": (2, 2), "INT": (1, 1), "MOD": (2, 2),
          "SUMIFS": (3, 255), "COUNTIFS": (2, 254), "AVERAGEIFS": (3, 255), "MAXIFS": (3, 253), "MINIFS": (3, 253),
          "SUMIF": (2, 3), "COUNTIF": (2, 2), "AVERAGEIF": (2, 3), "INDEX": (2, 4), "MATCH": (2, 3),
          "XLOOKUP": (3, 6), "EOMONTH": (2, 2), "EDATE": (2, 2), "DATE": (3, 3), "YEAR": (1, 1), "MONTH": (1, 1),
          "DAY": (1, 1)}
PAIRS = {"SUMIFS", "COUNTIFS", "AVERAGEIFS", "MAXIFS", "MINIFS"}


def f_args(formula: str) -> list[F.Issue]:
    return [i for i in F.check_formula(formula) if i.rule == "F-ARGS"]


def call(name: str, n: int) -> str:
    return f"={name}({','.join(['A1'] * n)})"


def rotm(*args: object, env: dict[str, str] | None = None, timeout: float = 600) -> subprocess.CompletedProcess[str]:
    full = dict(os.environ)
    full.update(env or {})
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *[str(a) for a in args]],
                          capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=full)


# ============================================================================= the rule (fixed names, plan 5.3)

def test_if_with_four_arguments_is_f_args() -> None:
    found = f_args("=IF(A1>0,1,0,1)")
    assert len(found) == 1, found
    i = found[0]
    assert i.severity == "error"
    assert i.message == "IF has 4 argument(s); it takes 2 or 3 (IF(A1>0,1,0,1))", i.message
    assert i.fix == 'give IF 2 or 3 arguments (references/model-spec-format.md, "Argument counts")', i.fix
    assert i.data == {"function": "IF", "count": 4, "allowed": "2 or 3", "min": 2, "max": 3, "start": 1, "end": 15}
    assert F.analyze("=IF(A1>0,1,0,1)").formula[i.data["start"]:i.data["end"]] == "IF(A1>0,1,0,1)"


def test_listed_functions_pass_at_their_smallest_and_largest_count() -> None:
    assert set(F.ARG_COUNTS) == set(LISTED), sorted(set(F.ARG_COUNTS) ^ set(LISTED))
    for name, (low, high) in LISTED.items():
        rule = F.ARG_COUNTS[name]
        assert (rule.min, rule.max) == (low, high), name
        assert rule.pairs == (name in PAIRS), name
        for n in (low, high):
            text = call(name, n)
            a = F.analyze(text)
            assert a.ok and a.incomplete is None and len(a.calls[0].args) == n, (text, a.error)
            assert f_args(text) == [], (name, n)
        for n in (low - 1, high + 1):
            if n < 0:
                continue
            found = f_args(call(name, n))
            assert len(found) == 1 and found[0].data["count"] == n, (name, n, found)
            assert found[0].data["function"] == name and found[0].data["allowed"] == rule.text
    # the pair rules: a criteria range with each criterion
    for text, n in (("=SUMIFS(A:A,B:B,1,C:C)", 4), ("=AVERAGEIFS(A:A,B:B,1,C:C)", 4), ("=MAXIFS(A:A,B:B,1,C:C)", 4),
                    ("=MINIFS(A:A,B:B,1,C:C)", 4), ("=COUNTIFS(A:A,1,B:B)", 3), ("=SUMIFS(A:A,B:B)", 2)):
        found = f_args(text)
        assert len(found) == 1 and found[0].data["count"] == n, (text, found)
    for text in ("=SUMIFS(A:A,B:B,1)", "=SUMIFS(A:A,B:B,1,C:C,2)", "=COUNTIFS(A:A,1)", "=COUNTIFS(A:A,1,B:B,2)",
                 "=MAXIFS(A:A,B:B,1,C:C,2)", "=MINIFS(A:A,B:B,1)", "=AVERAGEIFS(A:A,B:B,1)"):
        assert f_args(text) == [], text
    assert F.ARG_COUNTS["SUMIFS"].text == ("a range and then pairs of a criteria range and its criterion "
                                           "(3, 5, 7, ... up to 255)")
    assert F.ARG_COUNTS["MAXIFS"].text.endswith("(3, 5, 7, ... up to 253)")
    assert F.ARG_COUNTS["COUNTIFS"].text == "pairs of a criteria range and its criterion (2, 4, 6, ... up to 254)"
    assert {F.ARG_COUNTS[n].text for n in ("IF", "SUMIF", "AVERAGEIF", "MATCH")} == {"2 or 3"}
    assert F.ARG_COUNTS["INDEX"].text == "2 to 4" and F.ARG_COUNTS["XLOOKUP"].text == "3 to 6"
    assert F.ARG_COUNTS["AND"].text == "1 to 255" and F.ARG_COUNTS["DATE"].text == "exactly 3"
    assert F.ARG_COUNTS["ROUND"].text == "exactly 2" and F.ARG_COUNTS["DAY"].text == "exactly 1"


def _starter_with_helper(root_parent: Path, monkeypatch: pytest.MonkeyPatch, expr: str) -> Path:
    from lib import spec as specmod
    dest = root_parent / "starter"
    monkeypatch.setenv(specmod.EXAMPLE_ROOTS_ENV, str(root_parent))
    shutil.copytree(STARTER, dest, ignore=shutil.ignore_patterns("*.xlsx", "*.manifest.json", "reports", "lift",
                                                                 "__pycache__", "*.pyc"))
    path = dest / "model.spec.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    helpers = next(s for s in data["sheets"] if s["type"] == "helpers")
    helpers["columns"].append({"id": "flag", "label": "Flag", "expr": expr, "format": "count"})
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return dest


def test_build_refuses_if_with_four_arguments_at_its_json_path(outside_tmp: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """I-1 (plan section 7): needs this package's F-ARGS and P3's "F-ARGS" in build.REFUSED_ISSUES."""
    root = _starter_with_helper(outside_tmp, monkeypatch, "IF({this:amount}>0,1,0,1)")
    data = json.loads((root / "model.spec.json").read_text(encoding="utf-8"))
    si = next(i for i, s in enumerate(data["sheets"]) if s["type"] == "helpers")
    ci = len(data["sheets"][si]["columns"]) - 1
    proc = rotm("build", root, "--force", env={"ROTM_NO_EXCEL": "1"})
    out = proc.stdout + proc.stderr
    assert proc.returncode == 2, out[-3000:]
    assert not (root / "model.xlsx").exists(), out[-3000:]
    path = f"sheets[{si}].columns[{ci}].expr"
    lines = [x for x in out.splitlines() if path in x and "IF has 4 argument(s); it takes 2 or 3" in x]
    assert lines, out[-3000:]


def test_audit_strict_reports_f_args_at_the_cell(tmp_path: Path) -> None:
    import openpyxl
    book = openpyxl.Workbook()
    ws = book.active
    ws.title = "Calc"
    ws["A1"], ws["B1"] = "Amount", "Flag"
    ws["A2"] = 5
    ws["B2"] = "=IF(A2>0,1,0,1)"
    path = tmp_path / "args.xlsx"
    book.save(path)
    proc = rotm("audit", path, "--strict", "--json", "-", env={"ROTM_NO_EXCEL": "1"})
    assert proc.returncode == 1, (proc.stdout[-2000:], proc.stderr[-2000:])
    report = json.loads(proc.stdout)
    found = [f for f in report["findings"] if f["rule"] == "F-ARGS"]
    assert len(found) == 1, report["findings"]
    f = found[0]
    assert f["severity"] == "error" and f["sheet"] == "Calc", f
    assert "B2" in (f.get("cells") or [f.get("cell")]), f
    assert f["message"].startswith("IF has 4 argument(s); it takes 2 or 3"), f


def _formulas_of(path: Path) -> list[tuple[str, str, str]]:
    from lib import workbook
    info = workbook.open_workbook(path, with_values=False, with_styles=False)
    return [(s.name, c.coord, c.formula) for s in info.sheets for c in s.formulas() if c.formula]


def test_shipped_and_foreign_formulas_give_no_f_args(tmp_path: Path) -> None:
    from lib import common
    books = {n: p / "model.xlsx" for n, p in SHIPPED.items() if (p / "model.xlsx").is_file()}
    assert {"sales_vs_target", "starter"} <= set(books), sorted(books)   # the templates of PLUGIN/templates
    # are absent when the skill folder is copied alone; they are read when they are there
    found: list[tuple[str, str, str, str]] = []
    counts: dict[str, int] = {}
    for name, path in books.items():
        cells = _formulas_of(path)
        counts[name] = len(cells)
        assert cells, name
        found += [(name, f"{s}!{c}", t, i.message) for s, c, t in cells for i in f_args(t)]
    foreign = os.environ.get("ROTM_FOREIGN_DIR")
    read = unreadable = foreign_formulas = 0
    if foreign:
        for src in sorted(p for p in Path(foreign).glob("*.xls*") if p.is_file()):
            copy = tmp_path / src.name
            shutil.copy2(src, copy)                     # never read the copies in place
            try:
                cells = _formulas_of(copy)
            except common.Refusal:
                unreadable += 1
                continue
            read += 1
            foreign_formulas += len(cells)
            found += [(src.name, f"{s}!{c}", t, i.message) for s, c, t in cells for i in f_args(t)]
    print(f"\nF-ARGS scan: shipped models {len(books)} ({sum(counts.values())} formula cells: "
          + ", ".join(f"{k} {v}" for k, v in counts.items()) + f"); foreign workbooks read {read}, unreadable "
          f"{unreadable}, formula cells {foreign_formulas}")
    assert found == [], found[:20]


def test_every_formula_of_helper_patterns_gives_no_f_args() -> None:
    """Every formula of references/helper-patterns.md (each code span that starts with '=', the "Before" rows
    too) gives no F-ARGS. test_references.py checks the "After" rows and the specification expressions of the
    document for every rule."""
    import re
    text = (SKILL / "references" / "helper-patterns.md").read_text(encoding="utf-8")
    formulas = [m.group(1) for m in re.finditer(r"`(=[^`]+)`", text)]
    assert len(formulas) >= 10, len(formulas)
    bad = [(f, [i.message for i in f_args(f)]) for f in formulas if f_args(f)]
    assert bad == [], bad


# ============================================================================= more cases of plan 6.1

def test_an_empty_argument_counts_and_stays_allowed() -> None:
    assert f_args("=IF(A1,,1)") == []
    assert len(F.analyze("=IF(A1,,1)").calls[0].args) == 3
    assert [i.data["count"] for i in f_args("=IF(A1,,1,)")] == [4]
    assert [i.data["count"] for i in f_args("=ROUND(A1)")] == [1]
    # every form Excel 16 opened in the v4 measurement (tests/data/formula/incomplete_examples.json) passes
    accepted = json.loads((DATA / "incomplete_examples.json").read_text(encoding="utf-8"))["accepted"]
    for ex in accepted:
        assert f_args(ex["formula"]) == [], ex["formula"]
    # a call without arguments has 0
    assert [i.data["count"] for i in f_args("=ABS()")] == [0]
    assert f_args("=TODAY()") == []


def test_a_function_outside_the_list_is_not_counted() -> None:
    assert f_args("=VLOOKUP(A1,B:C,2,0,1)") == []
    assert f_args("=SUM()") == [] and f_args("=CHOOSE(1)") == []
    assert "VLOOKUP" not in F.ARG_COUNTS and "SUM" not in F.ARG_COUNTS


def test_arguments_split_only_at_the_calls_own_commas() -> None:
    cases = {
        "=IF(SUM(A1,B1,C1),MAX(A1,B1),0)": {"IF": 3, "SUM": 3, "MAX": 2},          # nested calls
        "=INDEX({1,2,3},2)": {"INDEX": 2},                                          # array constant
        "=MATCH(2,{1,2;3,4},0)": {"MATCH": 3},
        '=IF(A1,"a,b","c,d")': {"IF": 3},                                           # quoted commas
        '=COUNTIF(A:A,"x,y")': {"COUNTIF": 2},
        "=ROUND('a,b'!A1,0)": {"ROUND": 2},                                          # quoted sheet name
        "=INDEX((A1:B2,C1:D2),1,1,2)": {"INDEX": 4},                               # range union
        "=ABS((A1,B1))": {"ABS": 1},
        "=IF(A1, 1, 0)": {"IF": 3},                                                  # spaces
        "=_xlfn.XLOOKUP(A1,B:B,C:C)": {"XLOOKUP": 3},                                # file prefix
        "=A1:INDEX(B:B,3)": {"INDEX": 2},                                            # range prefix
    }
    for text, expected in cases.items():
        a = F.analyze(text)
        assert a.ok, (text, a.error)
        got = {c.name: len(c.args) for c in a.calls}
        assert got == expected, (text, got)
        assert f_args(text) == [], text
    # a wrong count inside or around a nested call names that call only
    inner = f_args("=SUM(ROUND(A1),1)")
    assert [(i.data["function"], i.data["count"]) for i in inner] == [("ROUND", 1)]
    outer = f_args("=ROUND(SUM(A1,B1),0,1)")
    assert [(i.data["function"], i.data["count"]) for i in outer] == [("ROUND", 3)]
    assert [i.data["count"] for i in f_args("=_xlfn.XLOOKUP(A1,B:B)")] == [2]


def test_one_issue_per_wrong_call_and_the_rule_order() -> None:
    found = f_args("=IF(A1,1,0,1)+IF(A2,1,0,1)+ROUND(A1)")
    assert [(i.data["function"], i.data["count"]) for i in found] == [("IF", 4), ("IF", 4), ("ROUND", 1)]
    assert F.RULE_ORDER[F.RULE_ORDER.index("F-PARSE") + 1] == "F-ARGS"
    rules = [i.rule for i in F.check_formula("=IF(A1,1,0,1)*0.05")]
    assert rules.index("F-ARGS") < rules.index("F-LITERAL")              # errors first
    # long call text is shortened to 100 characters in the message; data keeps the place
    long_call = "=IF(" + ",".join(["A1"] * 40) + ")"
    i = f_args(long_call)[0]
    assert i.message.endswith("...)") and len(i.message) < 160, i.message
    assert i.data["end"] == len(long_call)


def test_an_incomplete_formula_is_not_counted() -> None:
    issues = F.check_formula("=IF(A1,1,0,1")
    assert [i.rule for i in issues if i.rule in ("F-PARSE", "F-ARGS")] == ["F-PARSE"]


def test_fix_texts_of_each_form() -> None:
    assert f_args("=ABS()")[0].fix == 'give ABS exactly 1 argument (references/model-spec-format.md, "Argument counts")'
    assert f_args("=ROUND(A1)")[0].fix == ('give ROUND exactly 2 arguments (references/model-spec-format.md, '
                                           '"Argument counts")')
    assert f_args("=SUMIFS(A:A,B:B)")[0].fix == (
        "give SUMIFS a range and then pairs of a criteria range and its criterion (3, 5, 7, ... up to 255 arguments) "
        '(references/model-spec-format.md, "Argument counts")')


def test_formula_command_prints_f_args() -> None:
    proc = rotm("formula", "=IF(A1>0,1,0,1)")
    assert proc.returncode == 1, proc.stderr
    assert "F-ARGS" in proc.stdout and "IF has 4 argument(s); it takes 2 or 3" in proc.stdout, proc.stdout
    ok = rotm("formula", "=IF(A1>0,1,0)")
    assert ok.returncode == 0 and "F-ARGS" not in ok.stdout, ok.stdout


# ============================================================================= X-OPEN fix (plan 5.1)

def test_x_open_fix_names_f_parse_f_args_and_excel(tmp_path: Path) -> None:
    book = tmp_path / "m" / "model.xlsx"
    causes = ("the most likely cause is a formula Excel rejects; the strict audit finds two such causes, an "
              "incomplete formula (F-PARSE) and a listed function with too few or too many arguments (F-ARGS): run ")
    tail = "; for another cause, open model.xlsx in Excel and read the message Excel shows"
    with_model = excel.x_open_finding(book, ["Excel could not open it"], tmp_path / "m").fix
    assert with_model.startswith(causes), with_model
    assert (f'audit "{book.as_posix()}" --strict, correct the expression in model.spec.json, rebuild (') in with_model
    # v6 integrator (request v6_P8 R1): the rebuild between the correction and the check
    assert f'build "{(tmp_path / "m").as_posix()}" --force), then run ' in with_model
    assert with_model.endswith(f'check "{(tmp_path / "m").as_posix()}" again{tail}'), with_model
    alone = excel.x_open_finding(book, ["Excel could not open it"]).fix
    assert alone.startswith(causes), alone
    assert f'audit "{book.as_posix()}" --strict, correct that formula, then run ' in alone
    assert alone.endswith(f'recalc "{book.as_posix()}" again{tail}'), alone
