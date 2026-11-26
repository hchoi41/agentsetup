"""Tests of the v8 changes [A-aera-15] and [A-aera-13, C-sol-8] of package P6 (round 7 verdict, 2026-11-26).
Owner: P6.

[A-aera-15]: lib/functions.py and the generated references/functions.md. For MAP, BYROW, BYCOL, REDUCE, SCAN,
MAKEARRAY and ISOMITTED the reason the plugin flags the function is written apart from the statement on product
availability, and each availability statement carries the label the research gives that function. The claim check
of platforms A3.6 covers LET, LAMBDA and FILTER only, so no statement on the seven may call it confirmed. ISOMITTED
is described as testing whether an argument of a LAMBDA was left out.

[A-aera-13, C-sol-8] (documents only): references/google-sheets.md, "Cutover when the extract is on a new sheet":
step 2 says what it solves and when it does not help; a new step redirects the formulas with Find and replace; the
two proofs follow; the fallback is the export route and a model described as static. Nothing of it was run in
Google Sheets, and each new passage says so.

No test reads a file outside the plugin: the table of research items below was read by the P6 builder from
research/verify/platforms_standards_check_v1.json and the research reports (BUILD/v8_change_note_P6.md).

Run from the skill folder: python -P -X utf8 -B -m pytest scripts/tests/test_v8_references.py -q -p no:cacheprovider
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from lib import functions as FN

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
REFS = SKILL / "references"

SEVEN = ("MAP", "BYROW", "BYCOL", "REDUCE", "SCAN", "MAKEARRAY", "ISOMITTED")

# The research items that name each function, with the label the research gives them (P6 builder, 2026-11-26):
# - platforms A3.1 (the Google Sheets function list includes ... MAP, BYROW ...): stated in the platforms report;
#   the claim check holds no item A3.1, so not confirmed.
# - research synthesis (v1 and v2, the table of functions to avoid: "LAMBDA and its helpers MAP, BYROW, BYCOL,
#   REDUCE, SCAN, MAKEARRAY | Excel 365 and 2024 only", citing platforms A3.6 and D3): not confirmed.
# - platforms A3.6 (confirmed) names LET, LAMBDA and FILTER only; no item names ISOMITTED.
NAMED_BY = {
    "MAP": {"platforms A3.1": "not confirmed", "research synthesis": "not confirmed"},
    "BYROW": {"platforms A3.1": "not confirmed", "research synthesis": "not confirmed"},
    "BYCOL": {"research synthesis": "not confirmed"},
    "REDUCE": {"research synthesis": "not confirmed"},
    "SCAN": {"research synthesis": "not confirmed"},
    "MAKEARRAY": {"research synthesis": "not confirmed"},
    "ISOMITTED": {},
}
CONFIRMED_NAMING = {name: {item for item, label in items.items() if label == "confirmed"}
                    for name, items in NAMED_BY.items()}
ITEM_RE = re.compile(r"\b(platforms|researcher 2|books|local guides|style survey)\s+(#?[A-Z]?\d+(?:\.\d+)?)")
CUTOVER = "## Cutover when the extract is on a new sheet"


def _clauses(text: str) -> list[str]:
    return [c for c in re.split(r"[;()]", text) if c.strip()]


def _entry_text(name: str) -> str:
    e = FN.CATALOGUE[name]
    return " ; ".join((e.reason, e.availability, e.replacement or ""))


def _section(text: str, heading: str) -> str:
    assert heading in text, heading
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _step(section: str, n: int) -> str:
    """The text of the numbered step n of the section (from 'n. ' at the start of a line to the next step)."""
    parts = re.split(r"(?m)^(\d+)\. ", section)
    steps = {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}
    assert n in steps, (n, sorted(steps))
    return _flat(steps[n])


# ============================================================================= [A-aera-15]

@pytest.mark.parametrize("name", SEVEN)
def test_lambda_helpers_cite_confirmed_only_for_items_that_name_them(name: str) -> None:
    """[A-aera-15]: no clause of the entry attaches 'confirmed' to a research item that does not name the function
    (v7: 'takes a LAMBDA: Excel 365 or 2024 only like LAMBDA (platforms A3.6, confirmed)'); each item that names the
    function is cited with its label; the reason holds no availability statement and no research item."""
    text = _entry_text(name)
    wrong = []
    for clause in _clauses(text):
        if "confirmed" not in clause or "not confirmed" in clause:
            continue
        for m in ITEM_RE.finditer(clause):
            item = f"{m.group(1)} {m.group(2)}"
            if item not in CONFIRMED_NAMING[name]:
                wrong.append((item, clause.strip()))
    assert not wrong, (name, wrong)
    e = FN.CATALOGUE[name]
    for item, label in NAMED_BY[name].items():
        assert item in e.availability, (name, item, e.availability)
        after = e.availability.split(item, 1)[1][:150]          # the label follows the item it belongs to
        assert label in after, (name, item, label, e.availability)
    if name in ("MAP", "BYROW", "BYCOL", "REDUCE", "SCAN", "MAKEARRAY"):
        assert "the claim check of A3.6 covers LET, LAMBDA and FILTER only" in e.availability, e.availability
    else:
        assert "no research item names this function" in e.availability, e.availability
    assert not ITEM_RE.search(e.reason), e.reason
    assert not re.search(r"365|2024|2021|Excel|Google", e.reason), e.reason
    assert "spec P3" in e.reason and "one cell" in e.reason and "helper cell" in e.reason, e.reason
    assert e.excel_versions == FN.V_2024 and e.cls == e.cls_excel == e.cls_sheets == "refused"


def test_isomitted_is_described_as_testing_an_omitted_argument() -> None:
    """[A-aera-15]: ISOMITTED tests whether an argument of a LAMBDA was left out; it does not take a LAMBDA. The six
    others take a LAMBDA."""
    reason = FN.CATALOGUE["ISOMITTED"].reason
    assert reason.startswith("tests whether an argument of a LAMBDA was left out"), reason
    assert "takes a LAMBDA" not in reason
    for name in SEVEN[:-1]:
        assert FN.CATALOGUE[name].reason.startswith("takes a LAMBDA"), name


def test_functions_md_is_the_rendered_catalogue() -> None:
    """[A-aera-15]: references/functions.md is the text functions.render_markdown() writes, and the row of each of
    the seven holds the entry's reason and availability."""
    text = (REFS / "functions.md").read_text(encoding="utf-8")
    assert text == FN.render_markdown(), "run python -P -X utf8 -B scripts/lib/functions.py --write"
    for name in SEVEN:
        e = FN.CATALOGUE[name]
        row = next(line for line in text.splitlines() if line.startswith(f"| {name} |"))
        assert f"| {e.reason} |" in row and f"| {e.availability} |" in row, row
        assert "platforms A3.6, confirmed" not in row, row


# ============================================================================= [A-aera-13, C-sol-8]

def test_google_sheets_cutover_names_the_redirect_step_the_two_proofs_and_the_static_fallback() -> None:
    """[A-aera-13, C-sol-8]: the cutover section has five steps: step 2 says what it solves and that it does not
    help when the formulas follow a renamed sheet; step 3 redirects the formulas with Find and replace over all
    sheets within formulas, from the static sheet's name followed by ! to the extract sheet's name followed by !,
    and names what it covers; step 4 gives the two proofs; step 5 gives the export route and a model described as
    static. Each new passage says it was not run in Google Sheets, and the search within formulas is labelled not
    confirmed (no research item covers it)."""
    text = (REFS / "google-sheets.md").read_text(encoding="utf-8")
    cut = _section(text, CUTOVER)
    numbers = [int(n) for n in re.findall(r"(?m)^(\d+)\. ", cut)]
    assert numbers == [1, 2, 3, 4, 5], numbers
    step2, step3, step4, step5 = (_step(cut, n) for n in (2, 3, 4, 5))
    for words in ("What this step solves: the sheet that holds the extract cannot be given the name `raw_<t>`",
                  "What it does not solve: the extract can sit only on a new sheet, and the formulas follow a "
                  "renamed sheet", "For that case use step 3", "was run in Google Sheets"):
        assert words in step2, words
    assert step3.startswith("Redirect the formulas to the extract's sheet")
    for words in ("This step was not run in Google Sheets", "Edit > Find and replace", "all sheets",
                  "Also search within formulas", "is not confirmed: no research item covers it",
                  "the static sheet's present name followed by `!`", "`raw_<t>_static!`",
                  "the extract sheet's name followed by `!`", "`raw_<t>!`", "single quotes", "replace all",
                  "the fetch cells of `h_<t>`", "the generated checks", "`<t>_rows`",
                  "every calculation or check that reads a raw column directly", "{raw:<t>.<column>}",
                  "Keep the static sheet until both proofs of step 4 pass"):
        assert words in step3, words
    for words in ("The two proofs below were not run in Google Sheets",
                  "The first proof is which sheet the formulas name", "first fetch cell", "`<t>_rows`",
                  "The second proof is one changed source row seen in an output", "row-level comparison",
                  "it reads the static snapshot", "before you report any result"):
        assert words in step4, words
    for words in ("If the redirect of step 3 cannot be done or shown", "the model still reads the static snapshot",
                  "export route of [Models kept in Google Sheets](#models-kept-in-google-sheets)", "rotm.py land",
                  "The report describes the model as static", "This route was not run in Google Sheets"):
        assert words in step5, words
    assert "#models-kept-in-google-sheets" in step5 and "\n## Models kept in Google Sheets\n" in text
