"""Tests for lib/formula.py: the counting rule, length, literals, references, relative form, file form,
the formula-level rules and the `formula` subcommand (design sections 8, 14, 15). Owner: CORE-F."""
from __future__ import annotations

import dataclasses
import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

from lib import formula as F
from lib import functions as FN

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
DATA = Path(__file__).resolve().parent / "data" / "formula"
EXAMPLES = json.loads((DATA / "counting_examples.json").read_text(encoding="utf-8"))["examples"]


def rules(formula: str, **kw) -> list[str]:
    return [i.rule for i in F.check_formula(formula, **kw)]


def issue(formula: str, rule: str, **kw) -> F.Issue:
    found = [i for i in F.check_formula(formula, **kw) if i.rule == rule]
    assert found, f"{rule} not reported for {formula}"
    return found[0]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), "formula", *args],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)


# ----------------------------------------------------------------------------- the counting rule (design 8.1)

@pytest.mark.parametrize("ex", EXAMPLES, ids=[e["formula"] for e in EXAMPLES])
def test_counting_examples(ex: dict) -> None:
    a = F.analyze(ex["formula"])
    assert a.ok, a.error
    assert a.depth == ex["depth"]
    assert list(a.chain) == ex["chain"]
    assert F.depth(ex["formula"]) == ex["depth"]
    parts = [p.text for p in F.over_limit_parts(ex["formula"], 2)]
    assert parts == ex.get("parts", [])
    assert ("F-DEPTH" in rules(ex["formula"])) == (ex["depth"] > 2)


def test_depth_1_2_3() -> None:
    assert F.depth("=SUM(A1:A3)") == 1
    assert F.depth("=INDEX(A:A,MATCH(B1,C:C,0))") == 2
    assert F.depth("=IFERROR(INDEX(A:A,MATCH(B1,C:C,0)),0)") == 3
    assert "F-DEPTH" not in rules("=INDEX(A:A,MATCH(B1,C:C,0))")
    assert "F-DEPTH" in rules("=IFERROR(INDEX(A:A,MATCH(B1,C:C,0)),0)")


def test_every_function_counts_no_exempt_list() -> None:
    for wrapper in ("VALUE", "N", "NOT", "ROUND", "IFERROR", "AND", "OR", "TEXT"):
        assert F.depth(f"={wrapper}(SUM(INDEX(A:A,1)))") == 3, wrapper


def test_text_containing_brackets_and_function_names() -> None:
    a = F.analyze('=IF(A1="a(b",1,0)&"))"&"SUM(MAX(MIN("')
    assert a.depth == 1
    assert a.functions == ("IF",)
    assert [x.value for x in a.literals if x.kind == "text"] == ["a(b", "))", "SUM(MAX(MIN("]
    assert F.analyze('="say ""(hi)"""').depth == 0
    assert F.analyze('="say ""(hi)"""').literals[0].value == 'say "(hi)"'


def test_quoted_sheet_names_with_brackets_do_not_count() -> None:
    a = F.analyze("=SUM('Sales (2026)'!A1:A3)")
    assert a.depth == 1
    assert a.refs[0].sheet == "Sales (2026)"


def test_lambda_and_let_names_are_not_references() -> None:
    a = F.analyze("=LET(rate,inputs!$D$6,qty,B2,rate*qty)")
    assert a.depth == 1
    assert [r.text for r in a.refs] == ["inputs!$D$6", "B2"]
    assert "F-NAME" not in rules("=LET(rate,inputs!$D$6,qty,B2,rate*qty)")
    b = F.analyze("=MAP(A1:A3,LAMBDA(v,v*2))")
    assert [r.text for r in b.refs] == ["A1:A3"]
    c = F.analyze("=_xlfn.LET(_xlpm.x,1,_xlpm.x+1)")
    assert c.formula == "=LET(x,1,x+1)"
    assert c.refs == ()


def test_operators_and_unary_minus_do_not_count() -> None:
    assert F.depth("=-A1^2&B1<>C1") == 0
    assert F.depth("=--(A1>=0)*-(B1<=0)") == 0
    assert F.depth("=5%*A1") == 0


def test_call_offsets_and_parents() -> None:
    f = "=IFERROR(INDEX(A:A,MATCH(B1,C:C,0)),0)"
    a = F.analyze(f)
    assert [(c.name, c.level, c.parent) for c in a.calls] == [("IFERROR", 1, None), ("INDEX", 2, 0), ("MATCH", 3, 1)]
    assert [a.call_text(i) for i in range(3)] == [f[1:], "INDEX(A:A,MATCH(B1,C:C,0))", "MATCH(B1,C:C,0)"]
    assert [f[s:e] for s, e in a.calls[2].args] == ["B1", "C:C", "0"]
    assert a.calls[0].args and F.analyze("=ROW()").calls[0].args == ()
    g = "=IF(A1,,0)"
    assert [g[s:e] for s, e in F.analyze(g).calls[0].args] == ["A1", "", "0"]


def test_over_limit_parts_fields() -> None:
    parts = F.over_limit_parts("=ROUND(MAX(MIN(SUM(A1:A9),B1),0),2)", 2)
    assert len(parts) == 1
    p = parts[0]
    assert (p.text, p.level, p.remaining_depth) == ("MIN(SUM(A1:A9),B1)", 3, 2)
    assert p.chain == ("ROUND", "MAX", "MIN", "SUM")
    assert "=ROUND(MAX(MIN(SUM(A1:A9),B1),0),2)"[p.start:p.end] == p.text


def test_over_limit_part_still_over_the_limit() -> None:
    f = "=ROUND(MAX(MIN(SUM(ABS(A1)),B1),0),1)"     # depth 5: the part MIN>SUM>ABS has depth 3
    p = F.over_limit_parts(f, 2)[0]
    assert p.remaining_depth == 3
    i = issue(f, "F-DEPTH")
    assert "split it again" in i.fix
    assert F.over_limit_parts(f, 5) == []
    assert [x.text for x in F.over_limit_parts(f, 0)] == [f[1:]]
    with pytest.raises(ValueError):
        F.over_limit_parts(f, -1)


def test_depth_message_matches_design_example() -> None:
    f = "=IFERROR(INDEX(inputs!$C$10:$C$13,MATCH($B7,inputs!$B$10:$B$13,0)),0)"
    i = issue(f, "F-DEPTH")
    assert i.severity == "error"
    assert i.message == "nesting depth 3 is over the limit 2 (IFERROR > INDEX > MATCH)"
    assert i.fix == "move MATCH($B7,inputs!$B$10:$B$13,0) into a helper cell and reference that cell"
    assert i.data["chain"] == ["IFERROR", "INDEX", "MATCH"] and i.data["part"] == "MATCH($B7,inputs!$B$10:$B$13,0)"


def test_depth_limit_is_one_setting() -> None:
    f = "=IFERROR(INDEX(A:A,MATCH(B1,C:C,0)),0)"
    assert "F-DEPTH" in rules(f)
    assert "F-DEPTH" not in rules(f, depth_limit=3)
    assert "F-DEPTH" in rules("=SUM(A1)", depth_limit=0)


def test_constants_match_the_dictionary() -> None:
    data = json.loads((SKILL / "references" / "style_dictionary.json").read_text(encoding="utf-8"))
    assert data["rules"]["depth_limit"]["value"] == F.DEPTH_LIMIT_DEFAULT
    assert data["rules"]["length_limit"]["value"] == F.LENGTH_LIMIT_DEFAULT
    assert tuple(data["rules"]["allowed_literals"]["value"]) == tuple(F.ALLOWED_LITERALS_DEFAULT)


# ----------------------------------------------------------------------------- pure link, length, line break

@pytest.mark.parametrize("f,expected", [
    ("=calc!D7", True), ("=+A1", True), ("='My sheet'!$A$1", True), ("= A1 ", True), ("=raw_sales!$F:$F", True),
    ("=A1+0", False), ("=-A1", False), ("=SUM(A1)", False), ("=#REF!", False), ("=Sheet1!#REF!", False),
    ('="x"', False), ("=1", False), ("=(A1)", False)])
def test_pure_link(f: str, expected: bool) -> None:
    assert F.analyze(f).is_pure_link is expected


def test_length_counts_display_text_including_equals() -> None:
    assert F.analyze("=SUM(A1)").length == 8
    assert F.analyze("SUM(A1)").length == 8                       # '=' added
    assert F.analyze("=_xlfn.XLOOKUP(A1,B:B,C:C)").length == len("=XLOOKUP(A1,B:B,C:C)")
    assert F.analyze("=SUM(A1,\n B1)").length == 13


def test_length_rule_at_the_limit() -> None:
    at_limit = "=" + "+".join(["A1"] * 33)                        # 1 + 33*2 + 32 = 99 characters
    at_limit += "1"                                               # 100
    assert len(at_limit) == 100
    assert "F-LENGTH" not in rules(at_limit)
    over = at_limit + "0"
    i = issue(over, "F-LENGTH")
    assert i.severity == "warning" and "101 characters" in i.message
    assert "F-LENGTH" not in rules(over, length_limit=101)


def test_length_fix_names_the_largest_argument() -> None:
    inner = "SUMIFS(raw_sales!$F:$F,raw_sales!$C:$C,$B7,raw_sales!$D:$D,D$6,raw_sales!$E:$E,inputs!$D$9)"
    f = f"=IF($C7=1,{inner},inputs!$D$7)"
    i = issue(f, "F-LENGTH")
    assert i.fix == f"move {inner} into a helper cell and reference that cell"
    g = "=" + "+".join(f"A{n}" for n in range(1, 40))
    assert "split it into helper cells of at most 100 characters" in issue(g, "F-LENGTH").fix


def test_excel_limits() -> None:
    long = "=" + "+".join(["A1"] * 2800)
    assert len(long) > 8192
    assert "F-EXCEL-LIMIT" in rules(long)
    deep = "=" + "ABS(" * 65 + "A1" + ")" * 65
    assert F.depth(deep) == 65
    msgs = [i.message for i in F.check_formula(deep) if i.rule == "F-EXCEL-LIMIT"]
    assert msgs == ["nesting depth 65 is over Excel's limit of 64 levels"]
    assert "F-EXCEL-LIMIT" not in rules("=" + "ABS(" * 64 + "A1" + ")" * 64)


def test_line_break_rule() -> None:
    assert "F-LINEBREAK" in rules("=SUM(A1,\nB1)")
    assert "F-LINEBREAK" in rules("=SUM(A1,\r\nB1)")
    assert "F-LINEBREAK" not in rules('="line one\nline two"')
    a = F.analyze("=SUM(A1,\r\n  B1)")
    assert a.has_line_break and [r.text for r in a.refs] == ["A1", "B1"]
    assert a.formula[a.refs[1].start:a.refs[1].end] == "B1"


# ----------------------------------------------------------------------------- numeric literals (design 8.3)

def test_literals_inside_references_are_not_numbers() -> None:
    for f in ("=Sheet2!B12+A100", "=raw_2026!A1*'2026 plan'!C3", "=LOG10(A1)", "=SUM($1:$3)", "=ATAN2(A1,B1)",
              "=COUNTA(raw_t!$A:$A)", '=IF(A1="12",B1,C1)', "=Table1[Col2]"):
        assert [x for x in F.analyze(f).literals if x.kind == "number"] == [], f
        assert "F-LITERAL" not in rules(f), f


def test_literals_outside_references() -> None:
    i = issue("=A1*0.05", "F-LITERAL")
    assert i.severity == "warning" and i.data["value"] == 0.05
    assert i.message == "the number 0.05 is typed into the formula; allowed typed numbers: 0 and 1"
    assert "input cell on the inputs sheet" in i.fix
    j = issue("=VLOOKUP(A1,B:C,2,0)", "F-LITERAL")
    assert (j.data["function"], j.data["arg_index"]) == ("VLOOKUP", 3)
    assert "argument 3 of VLOOKUP" in j.message


@pytest.mark.parametrize("f,value", [("=A1*5%", 0.05), ("=-12*A1", -12.0), ("=A1--12", -12.0), ("=1E3*A1", 1000.0),
                                     ("=A1/.5", 0.5), ("=+-+7", -7.0), ("=--3", 3.0)])
def test_literal_values_with_sign_and_percent(f: str, value: float) -> None:
    nums = [x for x in F.analyze(f).literals if x.kind == "number"]
    assert len(nums) == 1 and nums[0].value == pytest.approx(value)
    assert "F-LITERAL" in rules(f)


def test_allowed_literals() -> None:
    for f in ("=A1*1", "=-1*A1", "=A1+0", "=MATCH(B1,C:C,0)", "=EOMONTH(A1,1)", "=COUNTA(raw!$A:$A)-1", "=A1*100%"):
        assert "F-LITERAL" not in rules(f), f
    assert "F-LITERAL" in rules("=A1/12")
    assert "F-LITERAL" not in rules("=A1/12", allowed_literals=(0, 1, 12))
    assert "F-LITERAL" not in rules("=-A1/12", allowed_literals=(0, 1, 12))


def test_one_literal_issue_per_value_and_array_numbers_once() -> None:
    assert rules("=A1*2+B1*2+C1*3").count("F-LITERAL") == 2
    assert issue("=A1*2+B1*2", "F-LITERAL").data["count"] == 2
    found = rules("=SUM({1,2;3,4})")
    assert found == ["F-ARRAY"]
    assert F.analyze("=SUM({1,2;3,4})").has_array


def test_text_logical_and_error_literals() -> None:
    a = F.analyze('=IF(A1=TRUE,"x",#N/A)')
    kinds = [(x.kind, x.value) for x in a.literals]
    assert kinds == [("logical", True), ("text", "x"), ("error", "#N/A")]
    assert "F-LITERAL" not in rules('=IF(A1=TRUE,"x",#N/A)')


def test_names_that_python_reads_as_numbers_are_names() -> None:
    a = F.analyze("=inf*nan")
    assert [r.kind for r in a.refs] == ["name", "name"]
    assert [x for x in a.literals if x.kind == "number"] == []


# ----------------------------------------------------------------------------- references (design 8.4)

def test_cross_sheet_references() -> None:
    a = F.analyze("=inputs!$D$6*raw_sales!$F:$F+'My sheet'!A1+'it''s'!B2:C3+Sheet1:Sheet3!A1")
    got = [(r.text, r.sheet, r.workbook, r.kind, r.a1) for r in a.refs]
    assert got == [("inputs!$D$6", "inputs", None, "cell", "$D$6"),
                   ("raw_sales!$F:$F", "raw_sales", None, "column", "$F:$F"),
                   ("'My sheet'!A1", "My sheet", None, "cell", "A1"),
                   ("'it''s'!B2:C3", "it's", None, "range", "B2:C3"),
                   ("Sheet1:Sheet3!A1", "Sheet1:Sheet3", None, "cell", "A1")]
    f = a.formula
    assert all(f[r.start:r.end] == r.text for r in a.refs)


def test_whole_column_and_row_references() -> None:
    a = F.analyze("=SUM(A:A)+SUM($B:$D)+SUM(1:1)+SUM($2:$5)+SUM(A2:A)")
    assert [(r.a1, r.kind) for r in a.refs] == [("A:A", "column"), ("$B:$D", "column"), ("1:1", "row"),
                                                ("$2:$5", "row"), ("A2:A", "range")]


def test_reference_kinds() -> None:
    a = F.analyze("=Rate*tbl[Amount]+Table1[@Amount]+[@Qty]+#REF!+Sheet1!#REF!+A1#+XFD1048576+XFE1")
    kinds = {r.text: r.kind for r in a.refs}
    assert kinds == {"Rate": "name", "tbl[Amount]": "structured", "Table1[@Amount]": "structured",
                     "[@Qty]": "structured", "Sheet1!#REF!": "error", "A1#": "range", "XFD1048576": "cell",
                     "XFE1": "name"}


def test_external_references() -> None:
    a = F.analyze("=[1]Sheet1!B2+'C:/x/[Book.xlsx]Data'!A1+[1]!rate+'[0]local'!A1")
    got = [(r.workbook, r.sheet, r.kind) for r in a.refs]
    assert got == [("[1]", "Sheet1", "cell"), ("C:/x/[Book.xlsx]", "Data", "cell"), ("[1]", None, "name"),
                   (None, "local", "cell")]
    assert rules("=[1]Sheet1!B2").count("F-EXTERNAL") == 1
    assert "F-EXTERNAL" not in rules("=raw!B2")
    assert "F-NAME" not in rules("=[1]!rate")


def test_function_range_prefix_is_a_reference() -> None:
    a = F.analyze("=SUM(A1:INDEX(B:B,3))")
    assert [r.text for r in a.refs] == ["A1", "B:B"]
    assert a.functions == ("SUM", "INDEX")
    assert a.calls[1].start == a.formula.index("INDEX")


def test_references_function() -> None:
    refs = F.references("=SUMIFS(h_sales!$G:$G,h_sales!$C:$C,$B7)")
    assert [(r.sheet, r.a1) for r in refs] == [("h_sales", "$G:$G"), ("h_sales", "$C:$C"), (None, "$B7")]


# ----------------------------------------------------------------------------- relative form (design 8.4)

def test_relative_form_design_examples() -> None:
    assert F.relative_form("=D7", "E8") == "=R[-1]C[-1]"
    assert F.relative_form("=$D$7", "E8") == "=R7C4"
    assert F.relative_form("=SUM($D:$D)", "E8") == "=SUM(C4)"
    assert F.relative_form("=SUM(D:F)", "E8") == "=SUM(C[-1]:C[1])"
    assert F.relative_form("=$B8+D$6+E8+$1:$1", "E8") == "=RC2+R6C[-1]+RC+R1"
    assert F.relative_form("=raw_sales!D2", "calc!E8") == "=raw_sales!R[-6]C[-1]"


def test_relative_form_equal_for_copied_formulas() -> None:
    col = [F.relative_form(f"=INDEX(raw_sales!$C:$C,A{r})*inputs!$D$7", f"D{r}") for r in range(2, 6)]
    assert len(set(col)) == 1
    row = [F.relative_form(f"=SUMIFS(h!$G:$G,h!$C:$C,$B7,h!$D:$D,{c}$6)", f"{c}7") for c in "DEFG"]
    assert len(set(row)) == 1
    assert F.relative_form("=A2*2", "B2") != F.relative_form("=A3*2", "B2")
    assert F.relative_form("=_xlfn.XLOOKUP(A1,B:B,C:C)", "D1") == F.relative_form("=xlookup(a1,b:b,c:c)", "D1")
    assert F.relative_form("='raw'!A1", "B1") == F.relative_form("=raw!A1", "B1")


def test_relative_form_keeps_text_and_names() -> None:
    assert F.relative_form('=IF(A1="D7",rate,0)', "B1") == '=IF(RC[-1]="D7",RATE,0)'
    with pytest.raises(ValueError):
        F.relative_form("=A1", "not a cell")


# ----------------------------------------------------------------------------- file form (design 6.6)

@pytest.mark.parametrize("display,file", [
    ("=XLOOKUP(A1,B:B,C:C)", "=_xlfn.XLOOKUP(A1,B:B,C:C)"),
    ("=FILTER(A1:A3,B1:B3>0)", "=_xlfn._xlws.FILTER(A1:A3,B1:B3>0)"),
    ("=SORT(A1:A3)", "=_xlfn._xlws.SORT(A1:A3)"),
    ("=MAXIFS(A:A,B:B,C1)+IFNA(D1,0)", "=_xlfn.MAXIFS(A:A,B:B,C1)+_xlfn.IFNA(D1,0)"),
    ("=LET(x,1,y,x+1,x+y)", "=_xlfn.LET(_xlpm.x,1,_xlpm.y,_xlpm.x+1,_xlpm.x+_xlpm.y)"),
    ("=MAP(A1:A3,LAMBDA(v,v*2))", "=_xlfn.MAP(A1:A3,_xlfn.LAMBDA(_xlpm.v,_xlpm.v*2))"),
    ("=SUM(A1#)", "=SUM(_xlfn.ANCHORARRAY(A1))"),
    ("=@A1:A3", "=_xlfn.SINGLE(A1:A3)"),
    ("=SUM(A1:A3)", "=SUM(A1:A3)"),
    ('="_xlfn. stays"&CONCAT(A1,B1)', '="_xlfn. stays"&_xlfn.CONCAT(A1,B1)'),
    ("=SUM(A1:XLOOKUP(B1,C:C,D:D))", "=SUM(A1:_xlfn.XLOOKUP(B1,C:C,D:D))"),
])
def test_to_file_formula_and_back(display: str, file: str) -> None:
    assert F.to_file_formula(display) == file
    assert F.to_file_formula(file) == file                       # idempotent
    assert F.strip_xlfn(file) == display
    assert F.analyze(file).formula == display


def test_to_file_formula_without_equals_keeps_shape() -> None:
    assert F.to_file_formula("XLOOKUP(A1,B:B,C:C)") == "_xlfn.XLOOKUP(A1,B:B,C:C)"


def test_stored_spill_reference_with_a_line_break() -> None:
    """Seen in a real file: the line break sits inside the ANCHORARRAY parentheses."""
    f = "=SUMIFS(_xlfn.ANCHORARRAY(raw!$F$6),_xlfn.ANCHORARRAY(\nraw!$C$6),B2)"
    a = F.analyze(f)
    assert a.formula == "=SUMIFS(raw!$F$6#,\nraw!$C$6#,B2)"
    assert a.depth == 1 and a.functions == ("SUMIFS",)
    assert F.strip_xlfn("=_xlfn.SINGLE( A1:A3 )") == "= @A1:A3 "


def test_user_defined_function_names() -> None:
    i = issue("=_xludf.IFS(A1,1)", "F-FUNC-UNKNOWN")
    assert i.message.startswith("IFS is stored as a user-defined function (_xludf.IFS)")
    assert i.data["user_defined"] is True
    assert F.analyze("=_xludf.IFS(A1,1)").formula == "=_xludf.IFS(A1,1)"


def test_strip_xlfn_leaves_text_alone() -> None:
    assert F.strip_xlfn('=_xlfn.IFS(A1,"_xlfn.")') == '=IFS(A1,"_xlfn.")'
    assert F.strip_xlfn("=SUM(A1)") == "=SUM(A1)"


# ----------------------------------------------------------------------------- functions in formulas

def test_volatile_functions() -> None:
    assert rules("=TODAY()") == ["F-VOLATILE"]                   # flagged and volatile: reported once
    assert rules("=NOW()-A1") == ["F-VOLATILE"]
    assert rules("=RAND()") == ["F-VOLATILE"]
    assert rules("=TODAY()", allow_functions=("today",)) == []
    assert rules("=OFFSET(A1,B1,C1)") == ["F-FUNC-REFUSED"]     # refused, not also F-VOLATILE
    assert rules("=INDIRECT(B1)") == ["F-FUNC-REFUSED"]
    assert sorted(rules('=INFO("osversion")')) == ["F-FUNC-REFUSED", "F-VOLATILE"]
    assert rules("=CELL(\"filename\",A1)") == ["F-VOLATILE"]


def test_function_classes_in_formulas() -> None:
    assert rules("=SUMIFS(A:A,B:B,C1)") == []
    assert rules("=QUERY(A:C,\"select A\")") == ["F-FUNC-REFUSED"]
    assert rules("=VLOOKUP(A1,B:C,C1,FALSE)") == ["F-FUNC-FLAGGED"]
    assert rules("=VLOOKUP(A1,B:C,C1,FALSE)", allow_functions=("VLOOKUP",)) == []
    assert rules("=OFFSET(A1,B1,C1)", allow_functions=("OFFSET",)) == ["F-FUNC-REFUSED"]
    assert rules("=MYFUNC(A1)") == ["F-FUNC-UNKNOWN"]
    assert rules("=QUERY(A:C,B1)", product="sheets") == ["F-FUNC-FLAGGED"]
    assert rules("=SPARKLINE(A1:A9)", product="sheets") == []
    assert rules("=TRIMRANGE(A:A)", product="excel") == []
    assert rules("=TRIMRANGE(A:A)") == ["F-FUNC-FLAGGED"]
    i = issue("=OFFSET(A1,B1,C1)", "F-FUNC-REFUSED")
    assert i.fix.startswith("use INDEX over a whole column") and i.data["function"] == "OFFSET"
    with pytest.raises(ValueError):
        F.check_formula("=SUM(A1)", product="libreoffice")


def test_nested_if_rule() -> None:
    assert "F-NESTED-IF" in rules("=IF(A1,IF(B1,1,0),0)")
    assert "F-NESTED-IF" in rules("=IF(A1,SUM(IF(B1,C1,D1)),0)")        # IF anywhere inside another IF
    assert "F-NESTED-IF" not in rules("=IF(AND(A1,B1),1,0)")
    assert "F-NESTED-IF" not in rules("=IF(A1,1,0)+IF(B1,1,0)")
    assert "F-NESTED-IF" not in rules("=IFERROR(IF(A1,1,0),0)")
    i = issue("=IF(A1,IF(B1,IF(C1,1,0),0),0)", "F-NESTED-IF")
    assert i.data == {"count": 2, "if_depth": 3}


# ----------------------------------------------------------------------------- other formula rules

def test_self_sheet_rule() -> None:
    assert "F-SELF-SHEET" in rules("=calc!D7+D8", sheet="calc")
    assert "F-SELF-SHEET" in rules("='Calc'!D7", sheet="calc")
    assert "F-SELF-SHEET" not in rules("=inputs!D7", sheet="calc")
    assert "F-SELF-SHEET" not in rules("=calc!D7")                      # no sheet given: not checked
    assert issue("=calc!D7", "F-SELF-SHEET", sheet="calc").fix == "drop the sheet prefix: D7"


def test_ref_error_rule() -> None:
    assert "F-REF-ERROR" in rules("=#REF!+A1")
    assert "F-REF-ERROR" in rules("=SUM(Sheet1!#REF!)")
    assert "F-REF-ERROR" not in rules('=IF(A1="#REF!",0,1)')
    assert "F-REF-ERROR" not in rules("=IFERROR(A1,#N/A)")


def test_array_rule() -> None:
    assert rules("={1,2,3}") == ["F-ARRAY"]
    assert "F-ARRAY" in rules("=SUM(A1#)")
    assert "F-ARRAY" not in rules("=SUM((A1,B1))")                      # a union in parentheses is not an array


def test_name_rule() -> None:
    i = issue("=Amount*Rate", "F-NAME")
    assert i.severity == "warning"
    assert rules("=Amount*Rate").count("F-NAME") == 2
    assert "F-NAME" not in rules("=A1*B1")


def test_structured_reference_rule() -> None:
    assert "X-STRUCTREF" in rules("=Table1[@Amount]*2")
    assert "X-STRUCTREF" in rules("=Table1[[#This Row],[Amount]]")
    assert "X-STRUCTREF" not in rules("=SUM(Table1[Amount])")


def test_parse_failure_is_reported_not_raised() -> None:
    for f in ("=)", "=SUM(A1))", '="abc', "=A1\"x\"", "=SUM(A1:A3)#"):
        a = F.analyze(f)
        assert a.ok is False and a.error, f
        assert "F-PARSE" in rules(f), f
    a = F.analyze("=SUM(IF(A1,MAX(B1)))+)")
    assert a.depth == 3 and a.functions == ("SUM", "IF", "MAX")
    assert "F-DEPTH" in rules("=SUM(IF(A1,MAX(B1)))+)")
    assert F.relative_form("=)", "A1") == "=)"
    assert F.analyze("=SUM(A1").ok is False


def test_spill_reference_is_read() -> None:
    a = F.analyze("=SUM(Sheet1!$B$2#)")
    assert a.ok and a.refs[0].text == "Sheet1!$B$2#" and a.refs[0].kind == "range"


def test_issue_order_errors_first() -> None:
    found = F.check_formula("=IFERROR(INDEX(A:A,MATCH(B1,C:C,0)),0)*0.05+OFFSET(A1,1,1)")
    severities = [i.severity for i in found]
    assert severities == sorted(severities, key=lambda s: {"error": 0, "warning": 1}[s])
    assert found[0].rule == "F-DEPTH"


def test_check_formula_validates_settings() -> None:
    with pytest.raises(ValueError):
        F.check_formula("=A1", depth_limit=-1)
    with pytest.raises(ValueError):
        F.check_formula("=A1", length_limit=0)


def test_clean_helper_formulas_of_the_design_pass() -> None:
    clean = ["=ROW()", '=--(INDEX(raw_sales!$A:$A,A2)<>"")', "=INDEX(raw_sales!$C:$C,A2)",
             '=INDEX(raw_sales!$B:$B,A2)&""', "=EOMONTH(C2,0)", "=SUMIFS(h_sales!$G:$G,h_sales!$C:$C,$B7,h_sales!$F:$F,D$6)",
             "=--(ABS(G5)>H5)", "=--(OR(D5<E5,D5>F5))", "=--(D5<>0)", "=COUNTA(raw_sales!$A:$A)-1-COUNT(h_sales!$A:$A)",
             "=--(OR(SUM(I5:I18)>0,COUNT(I5:I18)<>COUNTA(B5:B18)))", "=checks!$C$2", "=EOMONTH(inputs!$D$6,0)"]
    for f in clean:
        assert F.check_formula(f, sheet="calc") == [], f


# ----------------------------------------------------------------------------- the interface (design 8.6)

def test_interface_signatures() -> None:
    sig = inspect.signature(F.check_formula)
    for name in ("depth_limit", "length_limit", "allowed_literals", "product", "allow_functions"):
        assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert list(inspect.signature(F.over_limit_parts).parameters) == ["formula", "limit"]
    assert list(inspect.signature(F.relative_form).parameters) == ["formula", "host"]
    for fn in (F.analyze, F.depth, F.references, F.to_file_formula, F.strip_xlfn):
        assert list(inspect.signature(fn).parameters) == ["formula"]
    fields = {f.name for f in dataclasses.fields(F.Analysis)}
    assert fields >= {"formula", "ok", "error", "depth", "chain", "length", "calls", "functions", "refs", "literals",
                      "has_line_break", "is_pure_link", "has_array"}
    assert [f.name for f in dataclasses.fields(F.Issue)] == ["rule", "severity", "message", "fix", "data"]
    assert {f.name for f in dataclasses.fields(F.Part)} == {"text", "start", "end", "chain", "level",
                                                             "remaining_depth"}


def test_every_rule_id_is_known_to_the_audit() -> None:
    from lib import audit
    known = set(audit.RULES)
    missing = [r for r in F.RULE_ORDER if r not in known and r != "F-PARSE"]
    assert missing == []


def test_analysis_to_dict_is_json() -> None:
    d = F.analyze("=IFERROR(INDEX(A:A,MATCH(B1,C:C,0)),0)").to_dict()
    assert json.loads(json.dumps(d))["depth"] == 3


def test_analyze_accepts_array_formula_objects() -> None:
    class Arr:
        text = "=SUM(A1:A3*B1:B3)"
    assert F.analyze(Arr()).depth == 1          # type: ignore[arg-type]
    with pytest.raises(TypeError):
        F.analyze(5)                            # type: ignore[arg-type]


def test_many_formulas_are_fast_enough() -> None:
    import time
    t0 = time.perf_counter()
    for r in range(2, 3002):
        F.check_formula(f"=SUMIFS(h_sales!$G:$G,h_sales!$C:$C,$B{r},h_sales!$F:$F,D$6)")
    assert time.perf_counter() - t0 < 60          # 3,000 distinct formulas; measured well under 5 s on the build PC


# ----------------------------------------------------------------------------- the `formula` subcommand

def test_cli_depth_finding_exit_1() -> None:
    proc = run_cli("=IFERROR(INDEX(A:A,MATCH(B1,C:C,0)),0)")
    assert proc.returncode == 1, proc.stderr
    assert "depth:      3 (limit 2): IFERROR > INDEX > MATCH" in proc.stdout
    assert "move:       MATCH(B1,C:C,0) (level 3; the helper has depth 1)" in proc.stdout
    assert "F-DEPTH" in proc.stdout and proc.stdout.rstrip().endswith("errors: 1  warnings: 0  info: 0")


def test_cli_clean_exit_0_and_limit_flag() -> None:
    assert run_cli("=SUM(A1:A3)").returncode == 0
    assert run_cli("=IFERROR(INDEX(A:A,MATCH(B1,C:C,0)),0)", "--limit", "3").returncode == 0


def test_cli_strict_and_allow() -> None:
    assert run_cli("=VLOOKUP(A1,B:C,C1,0)").returncode == 0
    assert run_cli("=VLOOKUP(A1,B:C,C1,0)", "--strict").returncode == 1
    assert run_cli("=VLOOKUP(A1,B:C,C1,0)", "--strict", "--allow", "VLOOKUP").returncode == 0
    assert run_cli("=A1/12", "--strict", "--allowed-literals", "0,1,12").returncode == 0


def test_cli_json_stdout_is_only_json() -> None:
    proc = run_cli("=XLOOKUP(A1,B:B,C:C)*0.05", "--json", "-")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["command"] == "formula" and data["summary"]["warnings"] == 1
    assert data["data"]["analysis"]["depth"] == 1
    assert data["data"]["file_form"] == "=_xlfn.XLOOKUP(A1,B:B,C:C)*0.05"
    assert data["data"]["functions"][0] == {"name": "XLOOKUP", "class": "allowed", "excel": "365", "sheets": "yes",
                                            "volatile": False}
    assert "depth:" in proc.stderr


def test_cli_refusals_exit_2() -> None:
    proc = run_cli("=SUM(A1)", "--limit", "-1")
    assert proc.returncode == 2 and "--limit" in proc.stderr
    assert run_cli("").returncode == 2
    assert run_cli("=A1", "--allowed-literals", "x").returncode == 2
    assert run_cli("=A1", "--product", "lotus").returncode == 2


def test_cli_parse_failure_is_a_report() -> None:
    # v4 (C-sol-3): unbalanced brackets make the formula incomplete: F-PARSE (error), exit 1 without --strict too
    proc = run_cli("=SUM(A1))")
    assert proc.returncode == 1 and "F-PARSE" in proc.stdout and "Traceback" not in proc.stderr
    assert "the formula is not complete: 1 opening and 2 closing brackets" in proc.stdout
    assert run_cli("=SUM(A1))", "--strict").returncode == 1
    # text the tokenizer cannot fully read that is not incomplete keeps the F-PARSE warning
    proc = run_cli("=SUM(A1:A3)#")
    assert proc.returncode == 0 and "F-PARSE" in proc.stdout and "Traceback" not in proc.stderr
    assert "not complete" not in proc.stdout
    assert run_cli("=SUM(A1:A3)#", "--strict").returncode == 1


# ----------------------------------------------------------------------------- Excel: the file prefixes work

EXCEL_CASES = [
    ("=XLOOKUP(2,A1:A3,B1:B3)", 20),
    ("=MAXIFS(B1:B3,A1:A3,\">1\")", 30),
    ("=IFNA(NA(),7)", 7),
    ("=LET(x,A2,x*3)", 6),
    ("=CONCAT(A1,A2)", "12"),
    ("=IFS(A1=1,5,TRUE,6)", 5),
    ("=XMATCH(3,A1:A3)", 3),
    ("=ROWS(FILTER(A1:A3,A1:A3>1))", 2),
    ("=INDEX(SORT(B1:B3,1,-1),1)", 30),
    ("=LAMBDA(x,x*2)(A2)", 4),
    ("=SUM(MAP(A1:A3,LAMBDA(v,v*2)))", 12),
]


@pytest.mark.excel
def test_file_prefixes_recalculate_in_excel(tmp_path: Path) -> None:
    """openpyxl writes to_file_formula() text; Excel recalculates; the values are right and no cell is #NAME?."""
    from openpyxl import Workbook, load_workbook
    from lib import excel
    wb = Workbook()
    ws = wb.active
    ws.title = "t"
    for r, (a, b) in enumerate([(1, 10), (2, 20), (3, 30)], start=1):
        ws.cell(r, 1, a)
        ws.cell(r, 2, b)
    for r, (display, _expected) in enumerate(EXCEL_CASES, start=1):
        ws.cell(r, 4, F.to_file_formula(display))
    ws.cell(1, 6, "=XLOOKUP(2,A1:A3,B1:B3)")          # no prefix: Excel is expected to show #NAME?
    path = tmp_path / "prefixes.xlsx"
    wb.save(path)
    try:
        excel.recalc(path, save=True)
    except NotImplementedError:
        pytest.skip("lib.excel.recalc is not written yet")
    values = load_workbook(path, data_only=True)["t"]
    for r, (display, expected) in enumerate(EXCEL_CASES, start=1):
        got = values.cell(r, 4).value
        if isinstance(expected, str):
            assert str(got) == expected, display
        else:
            assert got == pytest.approx(expected), display
    assert values.cell(1, 6).value == "#NAME?"
