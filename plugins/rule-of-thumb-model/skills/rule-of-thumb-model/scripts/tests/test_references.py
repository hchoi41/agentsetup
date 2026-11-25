"""Tests for the six reference files owned by M9 (design section 20).

- Every file name and every internal link mentioned in the references exists (or, for a file another builder
  owns that is not written yet, the test skips and names it).
- Every `rotm.py <subcommand>` and every audit rule id mentioned is one the plugin has.
- Every formula in helper-patterns.md measures as stated with the plugin's own counter (lib.formula), and every
  "After" formula and every specification expression passes the plugin's formula rules.
- Every source label "<report> <id>, confirmed" names an item that the claim checks in research/verify/ marked
  confirmed.
- fast-standard-rules.md carries the CC BY 4.0 attribution, and every FAST rule row has an evidence label.
- The example contract in column-contract.md passes the contract schema.

The checks that need the project's own files (the design `build/rotm_design_v1.md`, the research folder
`research/` with its claim checks in `research/verify/`) run only when the environment variable ROTM_SOURCE_TREE
names the project's source tree: the folder that holds `research/` and `build/`. Without the variable they skip.
A folder found next to the plugin never switches them on (v2, J-2). When the variable names a folder without
`research/verify/`, those checks fail and say so.

Owner: M9 (ROTM_SOURCE_TREE: P8 of v2). Run from the skill folder:
python -P -m pytest scripts/tests/test_references.py -q -p no:cacheprovider
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

from lib import common, formula, schemas
from lib import spec as spec_mod

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
REFS = SKILL / "references"
SOURCE_TREE_ENV = "ROTM_SOURCE_TREE"


def _source_tree() -> Path | None:
    """The project's source tree named by ROTM_SOURCE_TREE, or None when the variable is not set."""
    value = os.environ.get(SOURCE_TREE_ENV, "").strip()
    return Path(value) if value else None


SOURCE_TREE = _source_tree()
RESEARCH = SOURCE_TREE / "research" if SOURCE_TREE else None
VERIFY = RESEARCH / "verify" if RESEARCH else None
BUILD = SOURCE_TREE / "build" if SOURCE_TREE else None
PROJECT = SOURCE_TREE.parent if SOURCE_TREE else None


def _require_source_tree(what: str) -> None:
    """Skip without ROTM_SOURCE_TREE; fail when it names a folder that does not hold research/verify/."""
    if SOURCE_TREE is None:
        pytest.skip(f"{what} needs the project's source tree: set {SOURCE_TREE_ENV} to the folder that holds "
                    "research/ and build/")
    assert VERIFY is not None and VERIFY.is_dir(), \
        f"{SOURCE_TREE_ENV}={SOURCE_TREE} does not hold research/verify/; {what} cannot run"

M9_FILES = ("google-sheets.md", "excel-power-query.md", "fast-standard-rules.md",
            "column-contract.md", "checks.md", "helper-patterns.md")

# Files the design (sections 1 and 20) gives to other owners. While they are not written yet, a mention of them
# skips instead of failing; after integration every one of them exists and nothing skips.
PLANNED = {
    "references/model-spec-format.md": "M3",
    "references/style-dictionary.md": "M2",
    "README.md": "INTEGRATOR",
}

# Names of files inside a model folder (design section 4): not files of the plugin, but their names must follow
# the design.
MODEL_FOLDER_PATTERNS = (
    r"rotm_model\.json", r"model\.spec\.json", r"model\.manifest\.json", r"model\.xlsx",
    r"style_overrides\.json", r"MODEL_MAP\.md", r"task\.md", r"reports/status\.json",
    r"contracts/[a-z][a-z0-9_]{0,19}\.contract\.(json|md)", r"queries/[a-z][a-z0-9_]{0,19}\.(sql|pq\.m)",
    r"raw/[a-z][a-z0-9_]{0,19}\.csv", r"reports/[\w./-]+", r"lift/[\w.-]+",
    # the model folder's own folders, named alone (v3: "raw/, the controls files and the manifest are not changed")
    r"(?:raw|queries|contracts|reports|removed)/",
    # files in the plugin's state folder outside model folders (design D14, 3.2)
    r"last_error\.txt", r"excel\.lock", r"excel_jobs\.jsonl",
)

# Project documents that stay in the project folder and are not shipped with the plugin.
PROJECT_DOC_PREFIXES = ("research/", "verify/", "01.PROJECT/", "20261124_create_skill_")
PROJECT_DOC_NAMES = ("rotm_design_v1.md",)

FILE_EXTENSIONS = ("md", "json", "py", "ps1", "sh", "ini", "txt", "csv", "xlsx", "xlsm", "xls", "sql", "m", "png",
                   "jsonl")
FILE_TOKEN = re.compile(r"^[^\s`]+\.(?:" + "|".join(FILE_EXTENSIONS) + r")$")
DIR_TOKEN = re.compile(r"^[\w.\-]+(?:/[\w.\-]+)*/$")
WORKBOOK_EXT = (".xlsx", ".xlsm", ".xls")

# Rule ids that are not audit rules: build warnings (design 3.2, 7.3, 9.2) and the check command's own rules.
EXTRA_RULE_IDS = {"W-NO-CHECK", "W-PQ-NOT-ATTACHED", "F-PARSE",
                  # land and check findings named in column-contract.md (lib/land.py, lib/check.py)
                  "L-KEY", "L-NULL", "L-KNOWN", "L-SQL-EXPR", "D-KEY", "D-NULL"}
DESIGN_AUDIT_RULES = {
    "F-DEPTH", "F-LENGTH", "F-EXCEL-LIMIT", "F-LINEBREAK", "F-LITERAL", "F-NESTED-IF", "F-FUNC-REFUSED",
    "F-FUNC-FLAGGED", "F-FUNC-UNKNOWN", "F-VOLATILE", "F-SELF-SHEET", "F-EXTERNAL", "F-REF-ERROR", "F-ARRAY",
    "F-NAME", "R-FORMULA", "R-NOT-ALONE", "R-CHANGED", "R-CONTRACT", "R-MERGED", "H-INCONSISTENT", "H-GAP",
    "H-COVERAGE", "B-INCONSISTENT", "C-MISSING", "C-BLOCK", "C-WIRING", "C-FAIL", "V-ERROR", "V-NOT-RECALC",
    "S-FONT", "S-SIZE", "S-FILL", "S-FONTCOLOR", "S-BOLD", "S-NUMFMT", "S-MERGED", "S-HIDDEN", "W-CALCMODE",
    "W-ITERATE", "X-STRUCTREF", "X-FEATURE", "A-UNREADABLE"}
CONTRACT_ACTIONS = {"propose", "agree", "show", "status", "remove"}   # design 3.1; remove: v3, J-4

# A sample A1 reference for each placeholder kind (design 6.6), to measure specification expressions.
PLACEHOLDER_SAMPLES = {
    "in": "inputs!$D$6", "raw": "raw_t!$F:$F", "h": "h_t!$G:$G", "this": "C2", "row": "$B7", "col": "D$6",
    "r": "D$12", "prev": "C$12", "m": "$E7", "g": "calc!D7", "grow": "calc!$D7:$I7", "gcol": "calc!D$7:D$10",
    "b": "calc!D$12", "blk": "calc!$D$7:$I$10", "cell": "summary!$E$11", "chk": "checks!$I$9",
}
EXPRESSION_KEYS = {"expr", "cell", "grid_cell", "actual", "expected", "first"}

# Report names used in source labels -> (claim check file, how an id is written in that file).
VERIFY_FILES = {
    "platforms": "platforms_standards_check_v1.json",
    "researcher 2": "researcher2_check_v1.json",
    "books": "books_check_v1.json",
    "local guides": "local_guides_check_v1.json",
    "style survey": "style_survey_check_v1.json",
}
LABEL_RE = re.compile(r"(platforms|researcher 2|books|local guides|style survey)\s+"
                      r"((?:(?:#\d+|[A-Z]{1,4}-\d+|[A-Z]\d+(?:\.\d+)?)(?:\s*(?:,|and|to)\s*)?)+)")
ID_RE = re.compile(r"#\d+|[A-Z]{1,4}-\d+|[A-Z]\d+(?:\.\d+)?")


# ----------------------------------------------------------------------------- helpers

def _text(name: str) -> str:
    return (REFS / name).read_text(encoding="utf-8")


def _without_code_blocks(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


def _without_label_legend(text: str) -> str:
    """The text without its 'Evidence labels' section, which explains the labels instead of using them."""
    return re.sub(r"## Evidence labels\n.*?(?=\n## )", "", text, flags=re.S)


def _code_spans(text: str) -> list[str]:
    return re.findall(r"`([^`\n]+)`", _without_code_blocks(text))


def _links(text: str) -> list[str]:
    return re.findall(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)", _without_code_blocks(text))


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation removed, spaces as hyphens."""
    h = heading.strip().lower()
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    text = _without_code_blocks(path.read_text(encoding="utf-8"))
    return {_slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+?)\s*$", text, flags=re.M)}


def _planned(rel: str) -> str | None:
    rel = rel.replace("\\", "/")
    for path, owner in PLANNED.items():
        if rel == path or rel.endswith("/" + path) or rel == Path(path).name:
            return owner
    return None


def _resolve_plugin_file(token: str, base: Path) -> Path | None:
    roots = (base, SKILL, SKILL / "references", SCRIPTS, SCRIPTS / "lib", SCRIPTS / "tests", PLUGIN)
    for root in roots:
        candidate = (root / token)
        if candidate.exists():
            return candidate
    return None


def _is_model_folder_name(token: str) -> bool:
    return any(re.fullmatch(p, token) for p in MODEL_FOLDER_PATTERNS)


def _is_project_doc(token: str) -> bool:
    return token.startswith(PROJECT_DOC_PREFIXES) or token in PROJECT_DOC_NAMES or token.endswith("_check_v1.json")


def _resolve_project_doc(token: str) -> Path | None:
    if SOURCE_TREE is None:
        return None
    for root in (RESEARCH, VERIFY, BUILD, SOURCE_TREE, PROJECT, PROJECT.parent, PROJECT.parent.parent):
        candidate = root / token.rstrip("/")
        if candidate.exists():
            return candidate
    return None


def _table_rows(text: str, header_first: str) -> list[list[str]]:
    """Rows of every Markdown table whose header's first cell is `header_first`."""
    rows: list[list[str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and [c.strip() for c in line.strip("|").split("|")][0] == header_first:
            i += 2                                          # skip the header and the --- line
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            continue
        i += 1
    return rows


def _code(cell: str) -> str:
    m = re.search(r"`([^`]+)`", cell)
    assert m, f"no formula in backticks in table cell: {cell!r}"
    return m.group(1)


def _host_sheet(cell: str) -> str | None:
    m = re.match(r"`?'?([^'!`]+)'?!", cell)
    return m.group(1) if m else None


def _json_blocks(text: str) -> list[str]:
    return re.findall(r"```json\n(.*?)```", text, flags=re.S)


def _expressions(obj) -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in EXPRESSION_KEYS and isinstance(v, str):
                found.append(v)
            else:
                found.extend(_expressions(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(_expressions(v))
    return found


def _substitute(expr: str) -> str:
    def repl(m: re.Match) -> str:
        kind = m.group(1)
        assert kind in PLACEHOLDER_SAMPLES, f"unknown placeholder kind {{{kind}:...}} in {expr!r}"
        assert kind in spec_mod.PLACEHOLDER_KINDS, f"placeholder kind {kind!r} is not in lib.spec.PLACEHOLDER_KINDS"
        return PLACEHOLDER_SAMPLES[kind]
    return "=" + re.sub(r"\{([a-z]+)(?::[^{}]*)?\}", repl, expr)


def _known_rule_ids() -> set[str]:
    ids = set(DESIGN_AUDIT_RULES) | EXTRA_RULE_IDS
    try:                                                    # the audit module is written by M4 in parallel
        from lib import audit
        ids |= set(audit.RULES)
    except Exception:  # noqa: BLE001
        pass
    try:                                                    # check and extend rule ids (lib/check.py RULES)
        from lib import check
        ids |= set(check.RULES)
    except Exception:  # noqa: BLE001
        pass
    # rule ids that land, build, check and audit write into their findings (a quoted id in the module's source;
    # v3: check.py and audit.py too, for warnings such as D-KEY-SEPARATOR that are reported but not in RULES;
    # v4: criteria.py for D-CRITERIA, excel.py for X-OPEN of recalc, contract.py for N-HELPER-NAME;
    # v5: compat.py for X-FUNC, which google-sheets.md names for the formulas of validations and rules)
    for module in ("land.py", "build.py", "check.py", "audit.py", "criteria.py", "excel.py", "contract.py",
                   "compat.py"):
        path = SCRIPTS / "lib" / module
        if path.is_file():
            ids |= set(re.findall(r"\"([A-Z]{1,2}-[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*)\"", path.read_text(encoding="utf-8")))
    return ids


def _rotm_commands() -> set[str]:
    spec = importlib.util.spec_from_file_location("rotm_for_refs", SCRIPTS / "rotm.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return set(module.COMMANDS)


# ----------------------------------------------------------------------------- structure

@pytest.mark.parametrize("name", M9_FILES)
def test_reference_file_exists_with_title_and_evidence_labels(name: str) -> None:
    path = REFS / name
    assert path.is_file(), f"{path} is missing"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# "), f"{name}: the first line must be the title ('# ...')"
    assert "## Evidence labels" in text, f"{name}: add an 'Evidence labels' section"
    assert "\t" not in text, f"{name}: holds a tab character"


# ----------------------------------------------------------------------------- file names and links

@pytest.mark.parametrize("name", M9_FILES)
def test_every_file_name_mentioned_exists(name: str) -> None:
    missing: list[str] = []
    pending: list[str] = []
    for token in _code_spans(_text(name)):
        token = token.strip()
        if " " in token or not (FILE_TOKEN.match(token) or DIR_TOKEN.match(token)):
            continue
        if any(ch in token for ch in "<>{}*"):
            continue                                        # a pattern such as contracts/<table>.contract.json
        if _is_model_folder_name(token):
            continue
        if _is_project_doc(token):
            if SOURCE_TREE is None:
                continue                                    # project documents are checked only with ROTM_SOURCE_TREE
            if _resolve_project_doc(token) is None:
                missing.append(f"{token} (project document)")
            continue
        if token.lower().endswith(WORKBOOK_EXT):
            continue                                        # surveyed workbooks: test_surveyed_workbook_names
        if _resolve_plugin_file(token, REFS) is not None:
            continue
        owner = _planned(token)
        if owner:
            pending.append(f"{token} ({owner})")
            continue
        missing.append(token)
    assert not missing, f"{name} mentions files that do not exist: {missing}"
    if pending:
        pytest.skip(f"{name}: planned files of other owners are not written yet: {pending}")


@pytest.mark.parametrize("name", M9_FILES)
def test_every_internal_link_resolves(name: str) -> None:
    broken: list[str] = []
    pending: list[str] = []
    for target in _links(_text(name)):
        if re.match(r"^(https?|mailto):", target):
            continue
        path_part, _, anchor = target.partition("#")
        path = (REFS / path_part).resolve() if path_part else (REFS / name)
        if not path.exists():
            rel = path_part.replace("../", "")
            owner = _planned("references/" + rel if "/" not in rel else rel) or _planned(rel)
            if owner:
                pending.append(f"{target} ({owner})")
            else:
                broken.append(target)
            continue
        if anchor and path.suffix == ".md" and anchor not in _anchors(path):
            broken.append(f"{target} (no heading with anchor '{anchor}')")
    assert not broken, f"{name} has links that do not resolve: {broken}"
    if pending:
        pytest.skip(f"{name}: links to planned files of other owners: {pending}")


OTHER_REFERENCE_FILES = sorted(p.name for p in REFS.glob("*.md") if p.name not in M9_FILES) if REFS.is_dir() else []


@pytest.mark.parametrize("name", OTHER_REFERENCE_FILES)
def test_other_reference_files_name_existing_files_and_links(name: str) -> None:
    """The same two checks on the reference files of other owners (functions.md, style-dictionary.md,
    model-spec-format.md, ...): design section 20 gives this test every file name and link in references/."""
    test_every_file_name_mentioned_exists(name)
    test_every_internal_link_resolves(name)


def test_surveyed_workbook_names_are_in_the_research_catalogue() -> None:
    _require_source_tree("the check of surveyed workbook names against the research catalogue")
    catalogue = RESEARCH / "20261124_create_skill_ruleofthumb_financial_modeling_local_catalog_v1.csv"
    shortlist = RESEARCH / "20261124_create_skill_ruleofthumb_financial_modeling_template_shortlist_v1.txt"
    assert catalogue.is_file(), f"{SOURCE_TREE_ENV}: the research catalogue {catalogue.name} is missing"
    known = catalogue.read_text(encoding="utf-8", errors="replace")
    if shortlist.is_file():
        known += shortlist.read_text(encoding="utf-8", errors="replace")
    missing = []
    for name in M9_FILES:
        for token in _code_spans(_text(name)):
            token = token.strip()
            if token.lower().endswith(WORKBOOK_EXT) and "/" not in token and "!" not in token \
                    and not token.startswith(("=", "rotm.py", "python")) and not _is_model_folder_name(token) \
                    and not any(ch in token for ch in "<>{}*\""):
                if token not in known:
                    missing.append(f"{name}: {token}")
    assert not missing, f"workbook names not found in the research catalogue: {missing}"


def test_rotm_subcommands_mentioned_exist() -> None:
    commands = _rotm_commands()
    bad = []
    for name in M9_FILES:
        text = _text(name)
        for m in re.finditer(r"rotm\.py\"?\s+([a-z][a-z-]*)(?:\s+(?:\"[^\"]*\"\s+)?([a-z]+))?", text):
            sub = m.group(1)
            if sub not in commands:
                bad.append(f"{name}: rotm.py {sub}")
            elif sub == "contract" and m.group(2) and m.group(2) not in CONTRACT_ACTIONS:
                bad.append(f"{name}: rotm.py contract {m.group(2)}")
    assert not bad, f"unknown subcommands: {bad}"


def test_rule_ids_mentioned_exist() -> None:
    known = _known_rule_ids()
    bad = []
    for name in M9_FILES:
        for rid in set(re.findall(r"\b([A-Z]-[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*)\b", _text(name))):
            if rid not in known:
                bad.append(f"{name}: {rid}")
    assert not bad, f"rule ids that neither the audit nor the build has: {bad}"


# ----------------------------------------------------------------------------- formulas

def _formula_rows() -> list[tuple[str, str, str, int]]:
    text = _text("helper-patterns.md")
    rows = []
    for kind in ("Before", "After"):
        for cells in _table_rows(text, kind):
            assert len(cells) == 4, f"a {kind} row needs 4 cells (cell, holds, formula, depth): {cells}"
            rows.append((kind, cells[0], _code(cells[2]), int(cells[3])))
    return rows


def test_helper_patterns_hold_before_and_after_tables() -> None:
    rows = _formula_rows()
    assert sum(1 for r in rows if r[0] == "Before") >= 10
    assert sum(1 for r in rows if r[0] == "After") >= 20


@pytest.mark.parametrize("kind,cell,text,stated", _formula_rows() if (REFS / "helper-patterns.md").is_file() else [])
def test_helper_pattern_depth_is_as_stated(kind: str, cell: str, text: str, stated: int) -> None:
    measured = formula.depth(text)
    assert measured == stated, f"{kind} {cell}: {text} has depth {measured}, the table says {stated}"
    if kind == "After":
        assert measured <= 2
        issues = formula.check_formula(text, depth_limit=2, length_limit=100, sheet=_host_sheet(cell))
        assert issues == [], f"After {cell}: {text}: {[(i.rule, i.message) for i in issues]}"
    else:
        assert measured > 2 or formula.check_formula(text, depth_limit=2, length_limit=100), \
            f"Before {cell}: {text} breaks no rule, so it needs no rewrite"


def test_counting_rule_table_matches_the_counter() -> None:
    rows = _table_rows(_text("helper-patterns.md"), "Formula")
    assert len(rows) >= 10
    for cells in rows:
        text, stated, verdict = _code(cells[0]), int(cells[1]), cells[2]
        assert formula.depth(text) == stated, f"{text}: measured {formula.depth(text)}, table says {stated}"
        assert verdict.startswith("pass") == (stated <= 2), f"{text}: verdict '{verdict}' does not fit depth {stated}"


def test_specification_expressions_pass_the_formula_rules() -> None:
    blocks = _json_blocks(_text("helper-patterns.md")) + _json_blocks(_text("checks.md"))
    assert len(blocks) >= 10
    exprs: list[str] = []
    for block in blocks:
        exprs.extend(_expressions(json.loads(block)))
    assert len(exprs) >= 25
    for expr in exprs:
        f = _substitute(expr)
        issues = formula.check_formula(f, depth_limit=2, length_limit=100)
        assert issues == [], f"{expr} -> {f}: {[(i.rule, i.message) for i in issues]}"


@pytest.mark.parametrize("name", ("checks.md", "helper-patterns.md"))
def test_formulas_in_text_have_no_pipe(name: str) -> None:
    for span in _code_spans(_text(name)):
        if span.startswith("="):
            assert "|" not in span, f"{name}: a '|' inside a formula breaks a Markdown table: {span}"


def test_check_kinds_in_examples_are_the_designed_kinds() -> None:
    for block in _json_blocks(_text("checks.md")) + _json_blocks(_text("helper-patterns.md")):
        obj = json.loads(block)
        stack = [obj]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if "kind" in o and "label" in o:
                    assert o["kind"] in ("equal", "range", "zero", "at_most"), o
                    assert o.get("severity", "error") in ("error", "alert"), o
                stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)


def test_check_result_formulas_in_checks_md_match_their_depth_limit() -> None:
    rows = _table_rows(_text("checks.md"), "Kind")
    assert {r[0].strip("`") for r in rows} == {"equal", "range", "zero", "at_most"}
    for cells in rows:
        f = _code(cells[2]).replace(" r", "5")
        assert formula.depth(f) <= 2, f


# ----------------------------------------------------------------------------- sources

def _clauses(text: str) -> list[str]:
    return [c for c in re.split(r"[;()|\n]", text) if c.strip()]


@pytest.mark.parametrize("name", M9_FILES)
def test_confirmed_labels_match_the_claim_checks(name: str) -> None:
    _require_source_tree("the check of 'confirmed' labels against the claim checks")
    status: dict[str, dict[str, str]] = {}
    for report, file in VERIFY_FILES.items():
        data = json.loads((VERIFY / file).read_text(encoding="utf-8"))
        status[report] = {str(it["id"]): it["status"] for it in data["items"]}
    wrong = []
    for clause in _clauses(_without_label_legend(_text(name))):
        if "confirmed" not in clause or "not confirmed" in clause:
            continue
        for m in LABEL_RE.finditer(clause):
            report = m.group(1)
            for raw_id in ID_RE.findall(m.group(2)):
                item = raw_id.lstrip("#")
                st = status[report].get(item)
                if st is None:
                    wrong.append(f"{report} {raw_id}: not an item of {VERIFY_FILES[report]} ('{clause.strip()}')")
                elif st == "partly" and "for this part" not in clause:
                    wrong.append(f"{report} {raw_id} is only partly confirmed; write 'confirmed for this part' "
                                 f"('{clause.strip()}')")
                elif st not in ("confirmed", "partly"):
                    wrong.append(f"{report} {raw_id} has status {st} ('{clause.strip()}')")
    assert not wrong, f"{name}: {wrong}"


@pytest.mark.parametrize("name", ("google-sheets.md", "excel-power-query.md", "fast-standard-rules.md",
                                  "checks.md", "column-contract.md", "helper-patterns.md"))
def test_every_research_citation_carries_a_label(name: str) -> None:
    unlabeled = []
    for group in re.findall(r"\(([^()]*)\)", _without_label_legend(_without_code_blocks(_text(name)))):
        matches = list(LABEL_RE.finditer(group))
        if not matches or re.search(r"confirmed|not checkable|a proposal|inference", group):
            continue
        ids = [i for m in matches for i in ID_RE.findall(m.group(2))]
        if all(m.group(1) == "platforms" for m in matches) and all(i.startswith("E") for i in ids):
            continue                    # section E of the platforms report is its list of claims not confirmed
        unlabeled.append(group)
    assert not unlabeled, f"{name}: research citations without 'confirmed' or 'not confirmed': {unlabeled}"


def test_fast_file_has_the_attribution_and_a_label_on_every_rule_row() -> None:
    text = _text("fast-standard-rules.md")
    for needed in ("Creative Commons Attribution 4.0 International License",
                   "https://creativecommons.org/licenses/by/4.0/", "FAST Standard Organisation",
                   "FAST Standard 02c, July 2019"):
        assert needed in text, f"fast-standard-rules.md must say: {needed}"
    rows = _table_rows(text, "Rule")
    assert len(rows) >= 40
    for cells in rows:
        assert re.match(r"^\d\.\d\d-\d\d", cells[0]), f"not a FAST rule number: {cells[0]}"
        assert "confirmed" in cells[2], f"rule {cells[0]}: the evidence cell needs 'confirmed' or 'not confirmed'"
    for number in ("3.03-01", "3.03-07", "1.01-06", "3.04-01", "2.03-05", "4.01-03"):
        assert any(r[0].startswith(number) for r in rows), f"FAST {number} is missing"


def test_unconfirmed_topics_are_listed_as_not_confirmed() -> None:
    text = _text("fast-standard-rules.md")
    section = text.split("## Not confirmed", 1)[1]
    for topic in ("typed over", "row count", "converted to Google Sheets", "BPM"):
        assert topic in section, f"the 'Not confirmed' section must name: {topic}"
    gs = _text("google-sheets.md")
    for eid in ("platforms E1", "platforms E2", "platforms E5"):
        assert eid in gs, f"google-sheets.md must list {eid} as not confirmed"


def test_smart_method_is_stated_as_a_different_practice() -> None:
    text = _text("excel-power-query.md")
    section = text.split("## A different practice: transform before landing", 1)[1].split("\n## ", 1)[0]
    assert "books B-51, confirmed" in section
    assert "Max" in section and "spec 1.2" in section
    assert "wrong" in section                               # "Neither practice is wrong."


# ----------------------------------------------------------------------------- the example contract

def test_example_contract_passes_the_schema() -> None:
    blocks = _json_blocks(_text("column-contract.md"))
    contracts = [json.loads(b) for b in blocks if '"rotm_contract"' in b]
    assert len(contracts) == 1
    obj = contracts[0]
    body = {k: v for k, v in obj.items() if k != "agreement"}
    obj["agreement"]["contract_sha256"] = common.canonical_hash(body)
    errors = schemas.validate(obj, "contract")
    assert errors == [], [str(e) for e in errors]


def test_contract_field_table_names_every_schema_field() -> None:
    text = _text("column-contract.md")
    for field in ("table", "raw_sheet", "excel_table", "purpose", "source", "grain", "key", "columns", "expect.rows",
                  "expect.totals", "refresh", "agreement", "shown_sha256", "name", "type", "unit", "null_rule",
                  "allowed", "source_expression", "description"):
        assert f"| `{field}` |" in text, f"column-contract.md: the field table must list `{field}`"
    for t in schemas.COLUMN_TYPES:
        assert f"`{t}`" in text
    for c in schemas.CHANNELS:
        assert f"`{c}`" in text
