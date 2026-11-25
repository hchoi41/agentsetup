"""Tests for lib/functions.py: every class of the catalogue, the profiles, the file prefixes, and
references/functions.md in step with the code (design section 8.5). Owner: CORE-F."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from lib import functions as FN

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent

# The lists of design section 8.5, word for word.
DESIGN_REFUSED = ("OFFSET", "INDIRECT", "LAMBDA", "QUERY", "ARRAYFORMULA", "IMPORTRANGE", "IMPORTDATA", "IMPORTHTML",
                  "IMPORTXML", "IMPORTFEED", "GOOGLEFINANCE", "SPARKLINE", "CALL", "INFO", "REGISTER.ID", "RTD",
                  "WEBSERVICE", "CUBEKPIMEMBER", "CUBEMEMBER", "CUBEMEMBERPROPERTY", "CUBERANKEDMEMBER", "CUBESET",
                  "CUBESETCOUNT", "CUBEVALUE")
DESIGN_FLAGGED = ("VLOOKUP", "HLOOKUP", "CHOOSE", "LET", "IFS", "SWITCH", "FILTER", "UNIQUE", "SORT", "SORTBY",
                  "SEQUENCE", "TODAY", "NOW", "RAND", "RANDBETWEEN", "CELL", "TAKE", "DROP", "TEXTSPLIT", "GROUPBY",
                  "PIVOTBY", "TRIMRANGE")
DESIGN_ALLOWED = ("SUM", "SUMIFS", "COUNTIFS", "AVERAGEIFS", "INDEX", "MATCH", "XLOOKUP", "XMATCH", "IF", "IFERROR",
                  "AND", "OR", "MIN", "MAX", "ABS", "EOMONTH", "EDATE", "YEAR", "MONTH", "DATE", "TEXT", "ROUND",
                  "COUNT", "COUNTA", "COUNTIF", "SUMIF", "SUMPRODUCT", "ROW", "ROWS", "COLUMN", "COLUMNS", "NOT",
                  "ISBLANK", "ISNUMBER", "ISTEXT", "ISERROR", "IFNA", "LEN", "LEFT", "RIGHT", "MID", "TRIM", "UPPER",
                  "LOWER", "VALUE", "N", "NA", "DAY", "AVERAGE", "MAXIFS", "MINIFS")


@pytest.mark.parametrize("name", DESIGN_REFUSED)
def test_design_refused(name: str) -> None:
    assert FN.classify(name) == "refused"


@pytest.mark.parametrize("name", DESIGN_FLAGGED)
def test_design_flagged(name: str) -> None:
    assert FN.classify(name) == "flagged"


@pytest.mark.parametrize("name", DESIGN_ALLOWED)
def test_design_allowed(name: str) -> None:
    assert FN.classify(name) == "allowed"
    for product in FN.PRODUCTS:
        assert FN.classify(name, product) == "allowed", product


def test_unknown_function() -> None:
    assert FN.classify("MYFUNC") == "unknown"
    assert FN.info("MYFUNC") is None
    assert FN.classify("textbefore", "excel") == "unknown"


def test_every_class_has_entries_and_the_design_lists_are_complete() -> None:
    for cls in FN.CLASSES:
        assert FN.names(cls), cls
    assert set(DESIGN_REFUSED) <= set(FN.names("refused"))
    assert set(DESIGN_FLAGGED) <= set(FN.names("flagged"))
    assert set(DESIGN_ALLOWED) <= set(FN.names("allowed"))


@pytest.mark.parametrize("name", sorted(FN.CATALOGUE))
def test_entry_is_well_formed(name: str) -> None:
    e = FN.CATALOGUE[name]
    assert e.name == name == name.upper() and not name.startswith("_XL")
    assert e.excel in FN.EXCEL_VALUES and e.sheets in FN.SHEETS_VALUES
    assert e.cls in FN.CLASSES and e.cls_excel in FN.CLASSES and e.cls_sheets in FN.CLASSES
    assert e.volatile == (name in FN.VOLATILE)
    assert e.xlfn == (name in FN.XLFN_NAMES)
    assert e.reason.strip() and e.availability.strip()
    # every reason names a source: a research id, a standard, the spec or design, or the builder's reading
    assert re.search(r"confirmed|FAST|design|spec |PwC|PFM|FMD", e.reason), e.reason
    assert "confirmed" in e.availability, e.availability
    if e.cls != "allowed":
        assert e.replacement, f"{name}: a refused or flagged function says what to use instead"


@pytest.mark.parametrize("name", sorted(FN.CATALOGUE))
def test_profile_rules(name: str) -> None:
    e = FN.CATALOGUE[name]
    if e.excel == "no":
        assert e.cls_excel == "refused"
    if e.sheets == "no":
        assert e.cls_sheets == "refused"
    if e.sheets == "not confirmed":
        assert e.cls != "allowed" and e.cls_sheets != "allowed", "Sheets availability not confirmed: never allowed"
    if e.excel != "no" and e.sheets == "yes" and e.cls == "allowed":
        assert e.cls_excel == e.cls_sheets == "allowed"


def test_profiles_move_one_product_functions() -> None:
    assert [FN.classify("QUERY", p) for p in FN.PRODUCTS] == ["refused", "refused", "flagged"]
    assert [FN.classify("SPARKLINE", p) for p in FN.PRODUCTS] == ["refused", "refused", "allowed"]
    assert [FN.classify("SPLIT", p) for p in FN.PRODUCTS] == ["refused", "refused", "allowed"]
    assert [FN.classify("CUBEVALUE", p) for p in FN.PRODUCTS] == ["refused", "flagged", "refused"]
    assert [FN.classify("CALL", p) for p in FN.PRODUCTS] == ["refused", "refused", "refused"]
    assert [FN.classify("TRIMRANGE", p) for p in FN.PRODUCTS] == ["flagged", "allowed", "flagged"]
    assert [FN.classify("FILTERXML", p) for p in FN.PRODUCTS] == ["flagged", "allowed", "flagged"]


def test_practice_classes_stay_in_every_profile() -> None:
    for name in ("OFFSET", "INDIRECT", "LAMBDA", "MAP"):
        assert {FN.classify(name, p) for p in FN.PRODUCTS} == {"refused"}, name
    for name in ("VLOOKUP", "LET", "FILTER", "TODAY", "TAKE", "NPV"):
        assert {FN.classify(name, p) for p in FN.PRODUCTS} == {"flagged"}, name


def test_bad_product_is_refused() -> None:
    with pytest.raises(ValueError):
        FN.classify("SUM", "lotus")
    with pytest.raises(ValueError):
        FN.names("allowed", "lotus")


def test_lookup_ignores_case_and_prefixes() -> None:
    assert FN.info("xlookup") is FN.CATALOGUE["XLOOKUP"]
    assert FN.info("_xlfn.XLOOKUP") is FN.CATALOGUE["XLOOKUP"]
    assert FN.info("_xlfn._xlws.FILTER") is FN.CATALOGUE["FILTER"]
    assert FN.classify("_xlfn.LET") == "flagged"
    assert FN.normalize_name(" sum( ") == "SUM"


def test_file_prefixes() -> None:
    assert FN.needs_xlfn("XLOOKUP") and FN.needs_xlfn("_xlfn.ifs")
    assert not FN.needs_xlfn("SUM") and not FN.needs_xlfn("SUMIFS") and not FN.needs_xlfn("EOMONTH")
    assert FN.needs_xlfn("STDEV.S")                    # not catalogued, still prefixed
    assert FN.file_prefix("FILTER") == FN.file_prefix("sort") == "_xlfn._xlws."
    assert FN.file_prefix("UNIQUE") == "_xlfn." and FN.file_prefix("SUM") == ""
    assert FN.XLWS_NAMES <= FN.XLFN_NAMES


def test_volatile_set() -> None:
    assert {n for n, e in FN.CATALOGUE.items() if e.volatile} == set(FN.VOLATILE)
    assert FN.VOLATILE_REPORTED_AS_REFUSED == {"OFFSET", "INDIRECT"}
    assert not FN.CATALOGUE["ROW"].volatile and not FN.CATALOGUE["COLUMN"].volatile


def test_replacement() -> None:
    assert "INDEX" in (FN.replacement("VLOOKUP") or "")
    assert FN.replacement("SUM") is None and FN.replacement("MYFUNC") is None


def test_functions_md_is_in_step_with_the_catalogue() -> None:
    path = SKILL / "references" / "functions.md"
    assert path.read_text(encoding="utf-8") == FN.render_markdown(), (
        "references/functions.md differs from the catalogue: run python -P -X utf8 -B scripts/lib/functions.py "
        "--write from the skill folder")


def test_functions_md_lists_every_entry_with_its_class() -> None:
    text = (SKILL / "references" / "functions.md").read_text(encoding="utf-8")
    sections = re.split(r"^## ", text, flags=re.M)
    by_class = {s.split(" ", 1)[0].lower(): s for s in sections if s.startswith(("Refused", "Flagged", "Allowed"))}
    for name, e in FN.CATALOGUE.items():
        assert f"\n| {name} | {e.excel} | {e.sheets} |" in by_class[e.cls], name
    assert "not confirmed" in text
    assert re.search(r"[A-Z0-9.#-]+, confirmed\)", text), "confirmed claims name their research/verify id"


def test_functions_module_is_standard_library_only() -> None:
    code = (f"import sys; sys.path.insert(0, {str(SCRIPTS)!r}); import lib.functions; "
            "bad = [m for m in sys.modules if m.split('.')[0] in ('openpyxl', 'pandas')]; sys.exit(1 if bad else 0)")
    # -B: no __pycache__ inside the plugin (compiled files hold absolute paths; O-1)
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def test_functions_md_marks_what_lift_reproduces() -> None:
    """v3, A-aera-13: the Lift column is generated from lib.lift.supported_functions(), the one list."""
    from lib import lift
    text = (SKILL / "references" / "functions.md").read_text(encoding="utf-8")
    header = next(line for line in text.splitlines() if line.startswith("| Function |"))
    k = [c.strip() for c in header.split("|")].index("Lift")
    rows = {line.split("|")[1].strip(): line.split("|")[k].strip() for line in text.splitlines()
            if line.startswith("| ") and not line.startswith(("| Function", "| Class", "| ---"))
            and line.split("|")[1].strip() in FN.CATALOGUE}
    assert set(rows) == set(FN.CATALOGUE)
    assert {n for n, v in rows.items() if v == "yes"} == set(FN.CATALOGUE) & lift.supported_functions()
    assert {rows[n] for n in ("PMT", "PV", "FV", "NPV", "SUMIFS", "ROW")} == {"yes"}
    assert {rows[n] for n in ("COLUMN", "LET", "MEDIAN", "OFFSET")} == {"no"}
    assert "LIFT-UNSUPPORTED" in text and "## The Lift column" in text


def test_write_markdown_to_another_path(tmp_path: Path) -> None:
    out = FN.write_markdown(tmp_path / "f.md")
    assert out.read_text(encoding="utf-8") == FN.render_markdown()
