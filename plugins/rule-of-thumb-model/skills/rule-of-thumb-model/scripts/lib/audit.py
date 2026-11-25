"""`audit`: audit any workbook against the rules (design section 14; spec S2; plan F06, F07).

Owner: M4. The audit never writes the workbook. It writes only reports/status.json and reports/audit.json when
the workbook lies in a model folder (the finish-check hook reads status.json); --no-record turns that off.

Two modes:
- manifest mode: the workbook has a sidecar manifest written by `rotm.py build` (design 6.8). Raw sheets, helper
  columns, blocks, checks and cell types come from the manifest, and the manifest-only rules run (R-CHANGED,
  R-CONTRACT, R-TYPE, CT-VERSION, H-GAP, H-COVERAGE, H-BEYOND, C-BLOCK, C-WIRING, W-SELF-CHECK, S-NUMFMT). Sheets the
  manifest does not name are audited as in inferred mode. A registered check result cell that holds no formula is
  C-MISSING (error), as the master check cell is.
- inferred mode (any other workbook): raw sheets, helper columns and check cells are inferred (design 14) and
  those findings say "inferred". Styles are compared against the dictionary as a whole (its font, its two sizes,
  its fills, its font colours), not against a guessed cell type, because a wrong guess would be a false finding.
  When the workbook has inferred check cells, C-BLOCK reports each calculation block no check cell reads
  (A-aera-11; _inferred_blocks says what a block is). A run of links (each cell holds only a reference to one
  cell, such as =Sheet!B2) is not a calculation block (J-3). An empty cell between two runs of one formula in a
  column is H-GAP (inferred), and H-COVERAGE counts the raw rows no helper row reads, reading every run of the
  column first (v5, A-aera-11; _inferred_coverage).
- the dictionary: --dictionary and --use NAME (named looks) load it as theme does (v5, C-sol-11).
- both modes: V-NO-VALUE reports formula cells that hold no value in a workbook that has cached values (a cycle of
  formulas leaves its cells without a value; workbook.formula_cells_without_value). The saved file does not say
  which cells are circular, so the audit's fix sends the user to `rotm.py check`, which recalculates in Excel and
  names a circular reference when there is one (fix N-AUDIT).

Every formula rule comes from lib.formula.check_formula (CORE-F), so build, audit and split count depth the same
way (F05). Cells that hold the same formula (same relative form) on one sheet are checked once and reported as
one finding with a cell count. A rule family that fails on an unusual workbook becomes finding A-RULE-FAILED and
the other families still run, so the audit always ends with a report and never with a traceback (F06).
"""
from __future__ import annotations

import argparse
import bisect
import datetime as _dt
import difflib
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import common


@dataclass(frozen=True)
class RuleInfo:
    id: str
    severity: str        # error | warning | info (H-INCONSISTENT, H-GAP, C-MISSING, C-FAIL: warning when inferred;
                         # F-PARSE: warning when the tokenizer cannot read the text)
    needs: str           # formula | raw | manifest | values | styles | workbook
    title: str
    source: str


# The rule list of design section 14, plus F-PARSE (CORE-F request R1) and A-RULE-FAILED (F06).
# "verified" and "not re-checked" refer to the claim checks in research/verify/.
RULES: dict[str, RuleInfo] = {r.id: r for r in (
    RuleInfo("F-DEPTH", "error", "formula", "nesting depth over the limit",
             "Max, spec 1.2 ('less than 3 nested functions at most'); Rees PFM p113 (books B-17, verified)"),
    RuleInfo("F-LENGTH", "warning", "formula", "formula longer than the length limit",
             "FAST 3.03-01 gives no number (platforms C1.4, verified); 100 characters is the plugin's own (design D6)"),
    RuleInfo("F-EXCEL-LIMIT", "error", "formula", "over Excel's 8,192 characters or 64 levels",
             "Excel specifications (platforms B5.1, verified)"),
    RuleInfo("F-LINEBREAK", "warning", "formula", "line break inside a formula",
             "FAST 3.03-03 (not re-checked); ICAEW Code principle 13 'no line breaks' (platforms C2.3, verified)"),
    RuleInfo("F-LITERAL", "warning", "formula", "numeric literal typed into a formula",
             "FAST 3.04-01 (not re-checked); PwC 1, 0, -1 (guides L-14, verified); IFM 0 and 1 (books B-11, verified)"),
    RuleInfo("F-NESTED-IF", "error", "formula", "IF inside IF",
             "FAST 3.03-07 'Never use nested IFs' (platforms C1.39, verified)"),
    RuleInfo("F-FUNC-REFUSED", "error", "formula", "refused function for the product profile",
             "function catalogue, references/functions.md (design 8.5)"),
    RuleInfo("F-FUNC-FLAGGED", "warning", "formula", "flagged function",
             "function catalogue, references/functions.md (design 8.5)"),
    RuleInfo("F-FUNC-UNKNOWN", "warning", "formula", "function not in the catalogue",
             "function catalogue, references/functions.md (design 8.5)"),
    RuleInfo("F-VOLATILE", "warning", "formula", "volatile function",
             "books B-56 (verified); guides L-96 (verified)"),
    RuleInfo("F-SELF-SHEET", "warning", "formula", "reference names its own sheet",
             "FAST 3.04-02 to 3.04-07 (platforms C1.44; the individual number not confirmed)"),
    RuleInfo("F-EXTERNAL", "error", "formula", "reference to another workbook",
             "books B-52 (not re-checked); guides L-98 external links (verified)"),
    RuleInfo("F-REF-ERROR", "error", "formula", "#REF! inside the formula text", "researcher 2 section 7"),
    RuleInfo("F-PARSE", "error", "formula", "formula that is not complete (error: Excel does not open the workbook), "
             "or formula text the tokenizer cannot read (warning)", "F06; design 8 (CORE-F request R1); v4 C-sol-3"),
    RuleInfo("F-ARGS", "error", "formula", "a listed function with too few or too many arguments (Excel does not "
             "open the workbook)",
             "r5 verdict A-aera-6; Microsoft's function reference (references/model-spec-format.md, Argument counts)"),
    RuleInfo("F-ARRAY", "warning", "formula", "array formula or array constant",
             "FAST 3.03-09 (not re-checked); ICAEW Code 'Don't use array formulas' (platforms C2.4)"),
    RuleInfo("F-NAME", "warning", "formula", "defined name used in a formula", "FAST 3.03-08 (platforms A2.6)"),
    RuleInfo("R-FORMULA", "error", "raw", "formula on a raw sheet",
             "Max, spec 1.2 (data loaded on sheets as raw files, then formulas); spec 2.2 P2"),
    RuleInfo("R-NOT-ALONE", "error", "raw", "raw table not alone from A1",
             "design D9; platforms B2.6 (cells beside a refreshed query table break, verified)"),
    RuleInfo("R-CHANGED", "error", "manifest", "raw sheet differs from the landed snapshot",
             "spec 2.1a raw-sheet guard; spec 2.2 P2"),
    RuleInfo("R-CONTRACT", "error", "manifest", "raw headers differ from the contract", "spec 2.2 P1"),
    RuleInfo("R-TYPE", "error", "manifest", "cell of a typed raw column stored as text, or of a text column "
             "stored as a number",
             "spec 2.2 P1 (the contract's column types); spec 2.2 P2 (a raw sheet holds landed values only)"),
    RuleInfo("CT-VERSION", "error", "manifest", "workbook built from another version of its contract",
             "spec 2.2 P1 (the agreed column contract governs the raw table)"),
    RuleInfo("R-MERGED", "warning", "raw", "merged cells on a raw sheet", "FAST 4.02-02 (not re-checked)"),
    RuleInfo("H-INCONSISTENT", "error", "formula", "helper column formula not the same on every row",
             "FAST 3.02-01 (not re-checked); spec 2.2 P3"),
    RuleInfo("H-GAP", "error", "manifest", "empty or typed cell inside a helper column",
             "FAST 3.02-01 (not re-checked); spec 2.2 P3"),
    RuleInfo("H-COVERAGE", "error", "manifest", "helper rows end before the last raw row",
             "design 7.2; platforms B2.5 and B2.6 (verified)"),
    RuleInfo("H-BEYOND", "error", "manifest", "helper formulas below the last raw row",
             "r5 verdict C-sol-3; check.py H-BEYOND (v2)"),
    RuleInfo("B-INCONSISTENT", "warning", "formula", "block row formulas differ", "FAST 3.02-01 (not re-checked)"),
    RuleInfo("C-MISSING", "error", "workbook", "no master check or no check cells",
             "Max, spec 1.2 (checker cells and columns); spec 2.2 P5; books B-34 (verified)"),
    RuleInfo("C-BLOCK", "warning", "workbook", "calculation block without a check",
             "spec 2.2 P5 (each calculation block against an independent total)"),
    RuleInfo("C-WIRING", "error", "manifest", "check result outside the master check",
             "design 9.4; books B-34 (verified); guides L-40 (verified)"),
    RuleInfo("C-RECOMPUTE", "error", "manifest",
             "check result its own operands do not give, or a check formula build did not write",
             "r4 verdict A-aera-3 and C-sol-5; design 9"),
    RuleInfo("W-SELF-CHECK", "warning", "manifest", "check whose actual and expected are the same",
             "spec 2.2 P5 (each calculation block against an independent total)"),
    RuleInfo("C-FAIL", "error", "values", "a check holds a value other than 0",
             "design 9; guides L-38 (0 when fine, 1 when there is a problem, verified)"),
    RuleInfo("C-ALERT", "info", "values", "an alert is raised: an alert holds a value other than 0",
             "design 6.7 (alerts are not part of the master check); FAST Appendix B alerts (platforms C1.54, "
             "not re-checked)"),
    RuleInfo("V-ERROR", "error", "values", "error value in a cell", "books B-60 (not re-checked); guides L-98 (verified)"),
    RuleInfo("V-NO-VALUE", "error", "values", "formula cell with no value after recalculation",
             "guides L-96 circular references (verified); spec 2.2 P7 (a model is verified by its calculated values)"),
    RuleInfo("V-NOT-RECALC", "info", "values", "formulas with no cached values",
             "spec 2.2 P7; openpyxl writes formulas without cached values"),
    RuleInfo("S-FONT", "warning", "styles", "font name differs from the dictionary",
             "Max, spec 1.2 (Arial); guides L-61 one font (verified)"),
    RuleInfo("S-SIZE", "warning", "styles", "font size differs from the dictionary",
             "Max, spec 1.2 (8 for a brick wall of data, 9 with white space)"),
    RuleInfo("S-FILL", "warning", "styles", "fill colour not the dictionary's",
             "Max, spec 1.2 (minimal colour; grey headers); references/style_dictionary.json"),
    RuleInfo("S-FONTCOLOR", "warning", "styles", "font colour not the dictionary's", "references/style_dictionary.json"),
    RuleInfo("S-BOLD", "warning", "styles", "bold differs for header, title, total, output or master check",
             "references/style_dictionary.json"),
    RuleInfo("S-NUMFMT", "warning", "manifest", "number format differs from the item's format",
             "references/style_dictionary.json number_formats"),
    RuleInfo("S-MERGED", "warning", "workbook", "merged cells outside raw sheets", "FAST 4.02-02 (not re-checked)"),
    RuleInfo("S-HIDDEN", "warning", "workbook", "hidden rows, columns or sheets",
             "FAST 2.01-08 (not re-checked); guides L-99 (verified); books B-55 (partly verified)"),
    RuleInfo("W-CALCMODE", "warning", "workbook", "calculation mode not automatic", "books B-59 (not re-checked)"),
    RuleInfo("W-ITERATE", "error", "workbook", "iterative calculation on",
             "FAST 1.01-11 (not re-checked); guides L-96 circular references (verified)"),
    RuleInfo("X-STRUCTREF", "warning", "formula", "structured reference with @ or #This Row",
             "platforms A2.4 (verified)"),
    RuleInfo("X-FEATURE", "warning", "workbook", "feature in one product only",
             "researcher 2 #20 (Google macros differ from VBA, verified); design 7.3 and 7.4 (Power Query is "
             "Excel's, Connected Sheets is Google's)"),
    RuleInfo("A-UNREADABLE", "error", "workbook", "workbook or part could not be read", "plan F06"),
    RuleInfo("A-RULE-FAILED", "warning", "workbook", "a rule family could not run on this workbook",
             "plan F06 (the audit always ends with a report)"),
)}

# ----------------------------------------------------------------------------- inference (design 14)

RAW_SHEET_RE = re.compile(r"^(raw|data|src|landed)[_ -]?|_raw$", re.I)
# Words that mark a check (design 14). Strong words also let C-FAIL read the cell's value.
_STRONG_WORDS = r"\bcheck\w*|\bchk\w*|검증|체크|검사"
_WEAK_WORDS = r"\bcontrols?\b|\berrors?\b|\bdiff\w*|차이|오류|\breconcil\w*|\btie[- ]?outs?\b"
STRONG_CHECK_RE = re.compile(_STRONG_WORDS, re.I)
CHECK_WORD_RE = re.compile(_STRONG_WORDS + "|" + _WEAK_WORDS, re.I)
CHECK_SHEET_RE = re.compile(r"\bchecks?\b|\bchk\b|^checks?|검증|체크", re.I)
MASTER_RE = re.compile(r"\bmaster\b|\boverall\b|\ball\b|\btotal\b|마스터|전체|종합", re.I)   # with a strong check word
FAIL_TEXTS = frozenset({"FAIL", "FAILED", "ERROR", "ERR", "NG", "오류", "실패", "불일치"})

MIN_RUN = 3                 # cells: a helper column or row of formulas has at least this many (design 14)
MIN_BLOCK = 2               # cells: an inferred calculation block for C-BLOCK has at least this many (A-aera-11)
DOMINANT_SHARE = 0.6        # the shared formula must hold at least this share of a run (inferred mode only)
TOTAL_FUNCTIONS = frozenset({"SUM", "SUBTOTAL", "AGGREGATE"})
_TOTAL_START = re.compile(r"^=\s*\+?\s*(?:_xlfn\.)?(?:SUM|SUBTOTAL|AGGREGATE)\(", re.I)
BOLD_TYPES = ("col_header", "title", "heading", "total", "output", "master_check")   # S-BOLD (design 14)
WHITE = "FFFFFF"
AUTO_TEXT = "000000"
XML_SCAN_LIMIT = 256 << 20  # bytes: worksheet parts larger than this are not scanned for hidden rows


# ----------------------------------------------------------------------------- context

@dataclass
class _Ctx:
    path: Path
    wb: Any                                  # workbook.WorkbookInfo
    report: common.Report
    settings: dict[str, Any]
    product: str
    dictionary: Any                          # dictionary.Dictionary
    manifest: dict[str, Any] | None
    model_root: Path | None
    raw_sheets: dict[str, str]               # sheet -> why it is raw ("manifest", "--raw-sheets", "name", ...)
    selected: set[str] | None
    spec_data: dict[str, Any] | None = None
    rel_cache: dict[tuple[str, str], str] = field(default_factory=dict)
    manifest_sheets: set[str] = field(default_factory=set)
    checks_cache: tuple[list[Any], list[Any], list[Any]] | None = None
    pending: dict[tuple[Any, ...], common.Finding] = field(default_factory=dict)

    def want(self, *rules: str) -> bool:
        return self.selected is None or any(r in self.selected for r in rules)

    def add(self, rule: str, severity: str, message: str, fix: str = "", *, sheet: str | None = None,
            cells: Sequence[str] | None = None, cell: str | None = None, count: int | None = None,
            data: dict[str, Any] | None = None) -> None:
        """Add a finding; one with the same rule, severity, sheet, message and fix is merged into it (its count
        grows, its first 20 cells are kept), as common.Report.add does, but in constant time."""
        if not self.want(rule) and rule not in ("A-UNREADABLE", "A-RULE-FAILED"):
            return
        coords = list(cells or ([cell] if cell else []))
        n = count if count is not None else max(1, len(coords))
        key = (rule, severity, sheet, message, fix)
        old = self.pending.get(key)
        if old is not None:
            old.count += n
            for c in coords:
                if len(old.cells) >= 20:
                    break
                if c not in old.cells:
                    old.cells.append(c)
            return
        self.pending[key] = common.Finding(rule=rule, severity=severity, message=message, fix=fix, sheet=sheet,
                                           cell=coords[0] if coords else None, count=n, cells=coords[:20],
                                           data=dict(data or {}))

    def flush(self) -> None:
        """Move the pending findings into the report, errors first, then warnings, then info (stable)."""
        rank = {"error": 0, "warning": 1, "info": 2}
        merged = self.report.findings + list(self.pending.values())
        self.pending = {}
        self.report.findings = sorted(merged, key=lambda f: rank.get(f.severity, 3))

    def rel(self, c: Any) -> str:
        """The relative form of a formula cell (CORE-F relative_form), cached."""
        key = (c.sheet, c.coord)
        v = self.rel_cache.get(key)
        if v is None:
            from . import formula
            try:
                v = formula.relative_form(c.formula, c.coord) if c.formula else f"\x00{c.value!r}"
            except Exception:  # noqa: BLE001 - an unreadable formula compares by its text
                v = "\x00" + str(c.formula)
            self.rel_cache[key] = v
        return v

    def worksheets(self) -> list[Any]:
        return [s for s in self.wb.sheets if s.kind == "worksheet"]

    def inferred_sheet(self, name: str) -> bool:
        """True when the sheet is not described by a manifest (inferred rules apply)."""
        return self.manifest is None or name not in self.manifest_sheets


# ----------------------------------------------------------------------------- small helpers

def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _num_text(v: Any) -> str:
    if _is_number(v):
        f = float(v)
        if f.is_integer() and abs(f) < 1e15:
            return f"{int(f):,}" if abs(f) >= 10000 else str(int(f))
        return format(f, ".6g")
    return repr(v) if isinstance(v, str) else str(v)


def _short(text: Any, n: int = 80) -> str:
    t = str(text).replace("\r", " ").replace("\n", " ")
    return t if len(t) <= n else t[:n - 3] + "..."


def _rows_text(rows: Sequence[int]) -> str:
    """[5, 6, 7, 9] -> '5 to 7, 9'."""
    out: list[str] = []
    rs = sorted(set(rows))
    i = 0
    while i < len(rs):
        j = i
        while j + 1 < len(rs) and rs[j + 1] == rs[j] + 1:
            j += 1
        out.append(f"{rs[i]:,}" if i == j else f"{rs[i]:,} to {rs[j]:,}")
        i = j + 1
    shown = ", ".join(out[:6])
    return shown + (f" and {len(out) - 6} more" if len(out) > 6 else "")


def _rotm_cmd(sub: str, *args: str) -> str:
    """The command line an agent would run, with the skill path."""
    script = (common.scripts_dir() / "rotm.py").as_posix()
    quoted = " ".join(f'"{a}"' if (" " in a or not a) else a for a in args)
    return f'python -P -X utf8 -B "{script}" {sub} {quoted}'.rstrip()


def _rows_label(rows: Sequence[int]) -> str:
    """'row 7' or 'rows 5 to 7, 9'."""
    return ("row " if len(set(rows)) == 1 else "rows ") + _rows_text(rows)


def _norm_font_color(v: str | None) -> str:
    return (v or AUTO_TEXT).upper()


def _norm_fill(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.upper()
    return None if v == WHITE else v


def _text_cells(sheet: Any) -> tuple[dict[int, list[tuple[int, str]]], dict[int, list[tuple[int, str]]]]:
    """Text constants by row ({row: [(col, text)]}) and by column ({col: [(row, text)]}), sorted."""
    by_row: dict[int, list[tuple[int, str]]] = {}
    by_col: dict[int, list[tuple[int, str]]] = {}
    for c in sheet.iter_cells():
        if c.formula is None and c.formula_kind is None and isinstance(c.value, str) and c.value.strip():
            by_row.setdefault(c.row, []).append((c.col, c.value))
            by_col.setdefault(c.col, []).append((c.row, c.value))
    for d in (by_row, by_col):
        for lst in d.values():
            lst.sort()
    return by_row, by_col


def _nearest_before(lst: list[tuple[int, str]] | None, pos: int) -> str | None:
    """The text of the last entry with index < pos."""
    if not lst:
        return None
    i = bisect.bisect_left(lst, (pos, "")) - 1
    return lst[i][1] if i >= 0 else None


def _range_box(sheet_default: str, ref: Any) -> tuple[str, int, int, int, int] | None:
    """(sheet, min_col, min_row, max_col, max_row) of a cell, range or whole column reference."""
    sheet = ref.sheet or sheet_default
    a1 = ref.a1.replace("$", "")
    try:
        if ref.kind in ("cell", "range"):
            c1, r1, c2, r2 = common.range_bounds(a1)
            return sheet, c1, r1, c2, r2
        if ref.kind == "column":
            parts = a1.split(":")
            c1, c2 = common.col_index(parts[0]), common.col_index(parts[-1])
            return sheet, min(c1, c2), 1, max(c1, c2), 1_048_576
    except ValueError:
        return None
    return None


def _total_like(c: Any, direction: str) -> bool:
    """True for a display total: one SUM (or SUBTOTAL, AGGREGATE) over the cells above it (direction 'col')
    or to its left (direction 'row') in its own column or row."""
    from . import formula
    if not c.formula or not _TOTAL_START.match(c.formula):
        return False
    try:
        a = formula.analyze(c.formula)
    except Exception:  # noqa: BLE001
        return False
    if a.depth != 1 or not a.functions or not set(a.functions) <= TOTAL_FUNCTIONS:
        return False
    ranges = [r for r in a.refs if r.kind in ("range", "cell") and not r.sheet]
    if not ranges:
        return False
    for r in ranges:
        box = _range_box(c.sheet, r)
        if box is None:
            return False
        _s, c1, r1, c2, r2 = box
        if direction == "col" and not (c1 == c2 == c.col and r2 < c.row):
            return False
        if direction == "row" and not (r1 == r2 == c.row and c2 < c.col):
            return False
    return True


# ----------------------------------------------------------------------------- settings and inputs

def _resolve_target(path: str | Path) -> Path:
    from . import modelfolder
    p = Path(common.native_path(path))
    if p.is_dir():
        if not modelfolder.is_model_root(p):
            raise common.Refusal(f"{p.as_posix()} is a folder, not a workbook",
                                 fix="give the workbook file, or a model folder that holds rotm_model.json")
        p = p / modelfolder.read_marker(p).get("workbook", "model.xlsx")
        if not p.exists():
            raise common.Refusal(f"{p.as_posix()}: the model folder's workbook does not exist yet",
                                 fix="build it with rotm.py build, or give the workbook file")
    if not p.exists():
        raise common.Refusal(f"{p.as_posix()}: workbook not found", fix="check the path of the workbook")
    return p


def _load_dictionary(dictionary: Any, model_root: Path | None, spec_dict: str | None,
                     use: Sequence[str] = ()) -> Any:
    """The dictionary the audit compares styles with: a Dictionary object as given; else an override file
    (--dictionary), the model folder's override, or the plugin dictionary, each with the named looks of `use`
    (--use) applied as theme applies them (v5, C-sol-11: lib.dictionary.load_dictionary(override, use=...))."""
    from . import dictionary as dmod
    use = list(use or ())
    if dictionary is not None and hasattr(dictionary, "font_name"):
        if use:
            raise common.Refusal("give named overrides through the dictionary object or --use, not both",
                                 key="use")
        return dictionary
    if dictionary is not None:
        return dmod.load_dictionary(override=dictionary, use=use)
    if model_root is not None:
        return dmod.load_for_model(model_root, spec_override=spec_dict, use=use)
    return dmod.load_dictionary(use=use)


def _spec_json(spec: str | Path | None, wb_path: Path, manifest: dict[str, Any] | None,
               report: common.Report) -> dict[str, Any] | None:
    """The specification JSON: --spec (a bad file is refused) or the manifest's spec (a bad file is a note)."""
    if spec is not None:
        data = common.load_json_file(common.native_path(spec), "specification")
        if not isinstance(data, dict):
            raise common.Refusal(f"{Path(spec).name}: not a specification (a JSON object)",
                                 fix="give the model.spec.json file")
        return data
    if manifest and manifest.get("spec"):
        p = wb_path.parent / str(manifest["spec"])
        if p.is_file():
            try:
                data = common.read_json(p)
                return data if isinstance(data, dict) else None
            except (OSError, ValueError) as exc:
                report.note(f"specification {p.name} not read ({type(exc).__name__}); settings come from the manifest")
    return None


def _settings(ctx_args: dict[str, Any], spec_data: dict[str, Any] | None, manifest: dict[str, Any] | None,
              dictionary: Any) -> dict[str, Any]:
    from . import spec as spec_mod
    layer: dict[str, Any] = {}
    if manifest and isinstance(manifest.get("settings"), dict):
        layer.update({k: v for k, v in manifest["settings"].items() if k in spec_mod.SETTING_KEYS})
    if spec_data and isinstance(spec_data.get("settings"), dict):
        layer.update({k: v for k, v in spec_data["settings"].items()})
    cli = {k: v for k, v in ctx_args.items() if v is not None}
    return spec_mod.resolve_settings(layer, dictionary, cli)


def _product(product: str | None, spec_data: dict[str, Any] | None, manifest: dict[str, Any] | None,
             model_root: Path | None) -> str:
    from . import functions
    candidates = [product]
    if manifest and isinstance(manifest.get("settings"), dict):
        candidates.append(manifest["settings"].get("product"))
    if spec_data:
        candidates.append(spec_data.get("product"))
    if model_root is not None:
        try:
            from . import modelfolder
            candidates.append(modelfolder.read_marker(model_root).get("product"))
        except Exception:  # noqa: BLE001
            pass
    for c in candidates:
        if c:
            if c not in functions.PRODUCTS:
                raise common.Refusal(f"product '{c}' is not known", fix="use both, excel or sheets")
            return str(c)
    return "both"


def _raw_sheets(wb: Any, manifest: dict[str, Any] | None, given: Sequence[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    names = set(wb.sheet_names)
    if manifest:
        for s in manifest.get("sheets", []):
            if s.get("type") == "raw":
                out[s["name"]] = "manifest"
    for name in given or ():
        if name not in names:
            near = difflib.get_close_matches(name, list(names), n=1)
            raise common.Refusal(f"--raw-sheets: the workbook has no sheet '{name}'"
                                 + (f"; did you mean '{near[0]}'?" if near else ""),
                                 fix="give sheet names exactly as the sheet tabs show them, separated by commas")
        out.setdefault(name, "--raw-sheets")
    if manifest is None and not given:
        for s in wb.sheets:
            if s.kind != "worksheet":
                continue
            if RAW_SHEET_RE.search(s.name):
                out[s.name] = "inferred from its name"
            elif any(t.from_query for t in s.tables):
                out[s.name] = "inferred: it holds a table loaded by a query"
    return out


# ----------------------------------------------------------------------------- formula rules

def _formula_rules(ctx: _Ctx) -> None:
    from . import formula
    s = ctx.settings
    for sheet in ctx.worksheets():
        if sheet.name in ctx.raw_sheets:
            continue                                   # R-FORMULA reports formulas on raw sheets
        groups: dict[str, list[Any]] = {}
        for c in sheet.formulas():
            if c.formula is None:                      # a data table {=TABLE()} has no formula text
                continue
            groups.setdefault(ctx.rel(c), []).append(c)
        for cells in groups.values():
            rep = cells[0]
            coords = [c.coord for c in cells]
            issues = formula.check_formula(rep.formula, depth_limit=s["depth_limit"], length_limit=s["length_limit"],
                                           allowed_literals=s["allowed_literals"], product=ctx.product,
                                           allow_functions=s["allow_functions"], sheet=sheet.name)
            for iss in issues:
                data = dict(iss.data)
                data["formula"] = _short(rep.formula, 300)
                ctx.add(iss.rule, iss.severity, iss.message, iss.fix, sheet=sheet.name, cells=coords,
                        count=len(coords), data=data)
            if rep.formula_kind == "array":
                where = f" over {rep.array_ref}" if rep.array_ref and ":" in str(rep.array_ref) else ""
                ctx.add("F-ARRAY", "warning", f"array formula{where} (entered as an array, or a dynamic array)",
                        "write one ordinary formula per cell, or build the result in helper cells (FAST 3.03-09)",
                        sheet=sheet.name, cells=coords, count=len(coords),
                        data={"formula": _short(rep.formula, 300), "array_ref": rep.array_ref})


# ----------------------------------------------------------------------------- raw-sheet rules

def _raw_rectangle(sheet: Any) -> tuple[tuple[int, int, int, int] | None, list[str]]:
    """The raw table's rectangle (min_col, min_row, max_col, max_row) and problems with where it starts."""
    problems: list[str] = []
    tables = [t for t in sheet.tables]
    if len(tables) > 1:
        problems.append(f"{len(tables)} tables on one raw sheet ({', '.join(t.name for t in tables[:4])})")
    if tables:
        try:
            c1, r1, c2, r2 = common.range_bounds(tables[0].ref)
        except ValueError:
            return None, problems
        if (c1, r1) != (1, 1):
            problems.append(f"table {tables[0].name} starts at {common.a1(c1, r1)}, not A1")
        return (c1, r1, c2, r2), problems
    if sheet.at(1, 1) is None:
        first = next(iter(sheet.iter_cells()), None)
        problems.append("A1 is empty: the table does not start at A1"
                        + (f" (the first value is in {first.coord})" if first else ""))
        return None, problems
    width = 0
    while sheet.at(1, width + 1) is not None:
        width += 1
    last = 1
    r = 2
    while r <= sheet.max_row and any(sheet.at(r, c) is not None for c in range(1, width + 1)):
        last = r
        r += 1
    return (1, 1, width, last), problems


def _raw_rules(ctx: _Ctx) -> None:
    for name, why in ctx.raw_sheets.items():
        sheet = ctx.wb.sheet(name)
        if sheet is None or sheet.kind != "worksheet":
            continue
        tag = "" if why == "manifest" else f" ({why})" if why.startswith("inferred") else f" (named by {why})"
        if ctx.want("R-FORMULA"):
            fcells = [c for c in sheet.formulas()]
            if fcells:
                ctx.add("R-FORMULA", "error", f"formula on raw sheet {name}{tag}: a raw sheet holds landed values "
                        "only (P2)",
                        f"move the calculation to a helper sheet (h_<table>) that reads the raw columns, and land the "
                        f"raw data again with values only",
                        sheet=name, cells=[c.coord for c in fcells], count=len(fcells),
                        data={"formula": _short(fcells[0].formula or fcells[0].value, 200)})
        if ctx.want("R-NOT-ALONE"):
            rect, problems = _raw_rectangle(sheet)
            for p in problems:
                ctx.add("R-NOT-ALONE", "error", f"raw sheet {name}{tag}: {p}",
                        "put the raw table alone on its sheet with its header in row 1 from A1 (design D9); move "
                        "everything else to another sheet", sheet=name)
            if rect is not None:
                c1, r1, c2, r2 = rect
                outside = [c for c in sheet.iter_cells() if not (c1 <= c.col <= c2 and r1 <= c.row <= r2)]
                if outside:
                    ref = f"{common.a1(c1, r1)}:{common.a1(c2, r2)}"
                    ctx.add("R-NOT-ALONE", "error", f"raw sheet {name}{tag}: {len(outside):,} cell(s) lie outside "
                            f"the raw table {ref}; a refresh moves or overwrites them (platforms B2.6)",
                            "move that content to another sheet (helper columns go on h_<table>)", sheet=name,
                            cells=[c.coord for c in outside], count=len(outside), data={"table_range": ref})
        if ctx.want("R-MERGED") and sheet.merged:
            ctx.add("R-MERGED", "warning", f"raw sheet {name}{tag} has {len(sheet.merged)} merged range(s)",
                    "unmerge them: a raw table has one value per cell", sheet=name, cells=sheet.merged[:20],
                    count=len(sheet.merged))


def _manifest_raw_rules(ctx: _Ctx) -> None:
    from . import manifest as mmod
    from . import modelfolder
    m = ctx.manifest
    if not m:
        return
    for entry in m.get("sheets", []):
        if entry.get("type") != "raw":
            continue
        name, table = entry["name"], entry["table"]
        sheet = ctx.wb.sheet(name)
        if sheet is None:
            ctx.add("R-CHANGED", "error", f"raw sheet {name} (table '{table}') named in the manifest is not in the "
                    "workbook", f"rebuild the workbook: {_rotm_cmd('build', str(m.get('spec') or 'model.spec.json'))} "
                    "--force", sheet=name)
            continue
        header = []
        col = 1
        while sheet.at(1, col) is not None:
            header.append(str(sheet.at(1, col).value))
            col += 1
        expected = [str(x) for x in entry.get("columns") or []]
        sources: list[tuple[str, list[str]]] = []
        if expected:
            sources.append(("the manifest", expected))
        state: str | None = None
        contract: dict[str, Any] | None = None
        if ctx.model_root is not None:
            state, contract, _problems = modelfolder.read_contract(ctx.model_root, table)
            if contract and state != "invalid":
                sources.append((f"contracts/{table}.contract.json", [str(c["name"]) for c in contract["columns"]]))
        if ctx.want("CT-VERSION"):
            _contract_version_rule(ctx, entry, name, table, state, contract)
        if ctx.want("R-CONTRACT"):
            for where, cols in sources:
                if header != cols:
                    pos = next((i for i in range(max(len(header), len(cols)))
                                if i >= len(header) or i >= len(cols) or header[i] != cols[i]), 0)
                    found = header[pos] if pos < len(header) else "(nothing)"
                    want = cols[pos] if pos < len(cols) else "(nothing)"
                    ctx.add("R-CONTRACT", "error", f"raw sheet {name}: the headers in row 1 differ from {where}: "
                            f"column {common.col_letter(pos + 1)} is '{found}', the contract has '{want}' "
                            f"(contract columns: {', '.join(cols[:12])}{'...' if len(cols) > 12 else ''})",
                            f"land the data again with the contract's columns in the contract's order: "
                            f"{_rotm_cmd('land', '<model>', '--table', table, '--csv', '<file>')}",
                            sheet=name, cell=f"{common.col_letter(pos + 1)}1",
                            data={"header": header, "contract": cols, "source": where})
                    break
        types = mmod.raw_types(m, table, ctx.model_root) if ctx.want("R-TYPE", "R-CHANGED") else None
        if ctx.want("R-TYPE") and types is not None:
            _raw_type_rule(ctx, sheet, name, table, types, header or expected)
        if ctx.want("R-CHANGED") and entry.get("digest"):
            if types is None:
                ctx.report.note(f"R-CHANGED not checked on {name}: the column types of table '{table}' are in neither "
                                "the manifest nor a contract")
                continue
            digest, rows, _hdr = mmod.sheet_digest(ctx.wb.path, name, types)
            if digest != entry["digest"]:
                recorded = entry.get("rows")
                rows_txt = (f"{recorded:,} rows recorded, {rows:,} rows now" if isinstance(recorded, int)
                            else f"{rows:,} rows now")
                ctx.add("R-CHANGED", "error", f"raw sheet {name} differs from the landed snapshot recorded in the "
                        f"manifest ({rows_txt}): raw data changes only by landing",
                        f"rebuild from the snapshot ({_rotm_cmd('build', str(m.get('spec') or 'model.spec.json'))} "
                        f"--force), or after a refresh accept it with {_rotm_cmd('check', '<model>', '--accept-refresh')}",
                        sheet=name, data={"recorded": entry["digest"], "now": digest, "rows": rows})


# Raw column types whose cells must not be stored as text (R-TYPE; v3 plan 5.4, [A-aera-10]).
TEXT_REFUSED_TYPES = ("integer", "number", "date", "datetime", "boolean")


def _hash12(value: Any) -> str:
    """'sha256:<first 12 hex>...' of a hash written with or without the 'sha256:' prefix."""
    text = str(value)
    if text.lower().startswith("sha256:"):
        text = text.split(":", 1)[1]
    return f"sha256:{text[:12]}..."


def _contract_version_rule(ctx: _Ctx, entry: dict[str, Any], name: str, table: str, state: str | None,
                           contract: dict[str, Any] | None) -> None:
    """CT-VERSION (v3 plan 5.4): the raw entry's contract_sha256 (the agreed contract build read) differs from
    agreement.contract_sha256 of the contract agreed now. A contract that is not agreed is not compared; a raw
    entry without contract_sha256 (built before v3) gets a note."""
    if ctx.model_root is None or state != "agreed" or not contract:
        return
    agreed = (contract.get("agreement") or {}).get("contract_sha256")
    if not agreed:
        return
    built = entry.get("contract_sha256")
    if not built:
        ctx.report.note(f"{name}: the manifest records no contract hash (built before v3), so the contract version "
                        "was not compared")
        return
    if str(built) == str(agreed):
        return
    root = ctx.model_root.as_posix()
    ctx.add("CT-VERSION", "error", f"{name} was built from another version of its contract: the manifest records "
            f"{_hash12(built)}, contracts/{table}.contract.json is agreed as {_hash12(agreed)}",
            "the workbook was built from another version of the contract; land again if the rows change "
            f"({_rotm_cmd('land', root, '--table', table, '--csv', '<file>', '--force')}), and rebuild "
            f"({_rotm_cmd('build', root, '--force')})",
            sheet=name, data={"table": table, "manifest": str(built), "contract": str(agreed)})


def _stored_as_number(value: Any) -> bool:
    """True for a value the workbook stores as a number: a number, or a date or time (an Excel serial number)."""
    return _is_number(value) or isinstance(value, (_dt.datetime, _dt.date, _dt.time, _dt.timedelta))


def _stored_text(value: Any) -> str:
    """A number stored in a text column as the formula bar shows it (no thousands separator)."""
    if _is_number(value):
        f = float(value)
        return str(int(f)) if f.is_integer() and abs(f) < 1e15 else format(f, ".15g")
    return value.isoformat(sep=" ") if isinstance(value, _dt.datetime) else str(value)


def _raw_type_rule(ctx: _Ctx, sheet: Any, name: str, table: str, types: Sequence[str],
                   columns: Sequence[str]) -> None:
    """R-TYPE (v3 plan 5.4): a cell of an integer, number, date, datetime or boolean column (the manifest's types,
    else the contract's) that the workbook stores as text; and (v4, A-aera-9) a cell of a text column that the
    workbook stores as a number (for example the text key '901' turned into the number 901); and (v6, A-aera-5) a
    cell of a text column that the workbook stores as a logical value (the text TRUE turned into TRUE). One finding
    per column and kind, naming the first cell and the count. Formula cells (R-FORMULA) and error constants
    (V-ERROR) are left to their own rules."""
    typed = {i + 1: str(t) for i, t in enumerate(types) if t in TEXT_REFUSED_TYPES or t == "text"}
    if not typed:
        return
    found: dict[tuple[int, str], list[Any]] = {}
    for c in sheet.iter_cells():
        if c.row < 2 or c.col not in typed or c.formula is not None or c.formula_kind is not None:
            continue
        if typed[c.col] == "text":
            if c.data_type != "e" and not c.is_error and isinstance(c.value, bool):
                found.setdefault((c.col, "logical"), []).append(c)
            elif c.data_type != "e" and not c.is_error and _stored_as_number(c.value):
                found.setdefault((c.col, "number"), []).append(c)
        elif isinstance(c.value, str) and c.data_type != "e" and not c.is_error:
            found.setdefault((c.col, "text"), []).append(c)
    if not found:
        return
    root = ctx.model_root.as_posix() if ctx.model_root is not None else "<model>"
    fix = (f"land the data again ({_rotm_cmd('land', root, '--table', table, '--csv', '<file>', '--force')}) and "
           "rebuild; a raw sheet holds landed values only")
    for (col, kind), cells in sorted(found.items()):
        cells.sort(key=lambda c: c.row)
        first = cells[0]
        cname = str(columns[col - 1]) if col - 1 < len(columns) else common.col_letter(col)
        if kind == "logical":
            message = (f"{len(cells):,} cell(s) of text column {cname} in {name} are stored as logical values "
                       f"(first {first.coord}: {'TRUE' if first.value else 'FALSE'})")
        elif typed[col] == "text":
            message = (f"{len(cells):,} cell(s) of text column {cname} in {name} are stored as numbers (first "
                       f"{first.coord}: {_short(_stored_text(first.value), 40)})")
        else:
            message = (f"{len(cells):,} cell(s) of {typed[col]} column {cname} in {name} are stored as text (first "
                       f"{first.coord}: '{_short(first.value, 40)}')")
        ctx.add("R-TYPE", "error", message, fix, sheet=name, cells=[c.coord for c in cells], count=len(cells),
                data={"table": table, "column": cname, "type": typed[col], "first_cell": first.coord})


# ----------------------------------------------------------------------------- helper columns and blocks

def _raw_last_row(sheet: Any, ncols: int | None) -> int:
    """The last data row of a raw sheet: the last row that holds a value in any column of its table (row 1 when the
    sheet holds the header only). `ncols` is the table's column count (None: every column)."""
    last = 1
    for c in sheet.iter_cells():
        if c.row >= 2 and (ncols is None or c.col <= ncols) and c.value not in (None, "") and c.row > last:
            last = c.row
    return last


def _helper_beyond_rule(ctx: _Ctx, sheet: Any, entry: dict[str, Any], raw_name: str, boundary: int) -> None:
    """H-BEYOND (r5 verdict C-sol-3): helper cells below the last raw row. A row there that is empty in every helper
    column gives nothing (the repair of references/google-sheets.md clears these rows); formula cells (and typed
    values, which the fix clears too) give one finding per helper sheet, the rule check uses for such rows."""
    name, table = entry["name"], entry["table"]
    first = int(entry.get("first_row", 2))
    letters = [c["col"] for c in entry.get("columns", []) if c.get("col")]
    if not letters:
        return
    hcols = {common.col_index(x) for x in letters}
    below = max(boundary, first - 1) + 1
    formulas: list[Any] = []
    typed: list[Any] = []
    for c in sheet.iter_cells():
        if c.row >= below and c.col in hcols and c.value not in (None, ""):
            (formulas if c.is_formula else typed).append(c)
    if not formulas and not typed:
        return
    end = max(c.row for c in formulas + typed)
    last_col = common.col_letter(max(hcols))
    root = ctx.model_root.as_posix() if ctx.model_root is not None else "<model folder>"
    held: list[str] = []
    if formulas:
        held.append(f"{len(formulas):,} formula cell(s) (first {formulas[0].coord})")
    if typed:
        held.append(f"{len(typed):,} typed value(s) (first {typed[0].coord})")
    why = ("they read empty raw cells as 0" if not typed else
           "the formulas read empty raw cells as 0" if formulas else
           "the calculations that read the helper columns include them")
    if formulas and typed:
        why += " and the calculations that read the helper columns include the typed values"
    cells = formulas + typed
    ctx.add("H-BEYOND", "error",
            f"helper sheet {name}: rows {below:,} to {end:,} lie below the last raw row of {raw_name} (row "
            f"{boundary:,}) and hold {' and '.join(held)}; {why}",
            f"clear the contents of {name} rows {below:,} to {end:,} (column A to {last_col}; do not delete the "
            f"rows), or run {_rotm_cmd('extend')} \"{root}\" --table {table} (it clears them)",
            sheet=name, cells=[c.coord for c in cells], count=len(cells),
            data={"table": table, "raw_last_row": boundary, "first_beyond": below, "last_beyond": end,
                  "formula_cells": len(formulas), "typed_cells": len(typed)})


def _manifest_helper_rules(ctx: _Ctx) -> None:
    """H-GAP, H-INCONSISTENT, H-BEYOND and H-COVERAGE for the helper sheets a manifest names.

    v6 (r5 verdict C-sol-3): the rows a helper sheet must fill end at the last data row of its raw sheet in the
    workbook (_raw_last_row), not at the last helper row the manifest recorded, so a download whose helper rows below
    a shorter extract were cleared by hand (references/google-sheets.md) gives no H-GAP. Inside that boundary an
    empty or typed cell is H-GAP and a differing formula is H-INCONSISTENT; below it, helper formulas are H-BEYOND;
    raw rows that no helper row reads are H-COVERAGE (unchanged)."""
    from . import formula
    m = ctx.manifest
    if not m:
        return
    raw_entries = {s["table"]: s for s in m.get("sheets", []) if s.get("type") == "raw"}
    raw_by_table = {t: s["name"] for t, s in raw_entries.items()}
    for entry in m.get("sheets", []):
        if entry.get("type") != "helpers":
            continue
        name, table = entry["name"], entry["table"]
        sheet = ctx.wb.sheet(name)
        if sheet is None:
            ctx.add("H-COVERAGE", "error", f"helper sheet {name} (table '{table}') named in the manifest is not in "
                    "the workbook", "rebuild the workbook with rotm.py build --force", sheet=name)
            continue
        cols = [c for c in entry.get("columns", []) if c.get("col") and c.get("formula")]
        first = int(entry.get("first_row", 2))
        last = int(entry.get("last_row", first))
        if cols:
            ci0 = common.col_index(cols[0]["col"])
            frows = [c.row for c in sheet.iter_cells() if c.col == ci0 and c.formula]
            if frows:
                last = max(last, max(frows))
        raw_name = raw_by_table.get(table)
        raw = ctx.wb.sheet(raw_name) if raw_name else None
        if raw is not None:
            ncols = len(raw_entries[table].get("columns") or []) or None
            boundary = _raw_last_row(raw, ncols)
            last = min(last, boundary)                 # no H-GAP below the last raw row (v6, C-sol-3)
            if ctx.want("H-BEYOND"):
                _helper_beyond_rule(ctx, sheet, entry, raw_name, boundary)
        for col in cols:
            ci = common.col_index(col["col"])
            try:
                expected = formula.relative_form(col["formula"], f"{col['col']}{first}")
            except Exception:  # noqa: BLE001
                expected = None
            gaps: list[tuple[int, str]] = []
            odd: list[int] = []
            for r in range(first, last + 1):
                c = sheet.at(r, ci)
                if c is None:
                    gaps.append((r, "empty"))
                elif not c.is_formula:
                    gaps.append((r, f"typed value {_short(_num_text(c.value), 30)}"))
                elif expected is not None and ctx.rel(c) != expected:
                    odd.append(r)
            label = f"helper column {col['col']} '{col.get('id')}' of {name}"
            if gaps and ctx.want("H-GAP"):
                rows = [r for r, _w in gaps]
                kinds = sorted({w if w == "empty" else "typed value" for _r, w in gaps})
                ctx.add("H-GAP", "error", f"{label}: {len(gaps):,} {' or '.join(kinds)} cell(s) inside the formula "
                        f"rows {first:,} to {last:,} ({_rows_label(rows)})",
                        f"copy the formula of {col['col']}{first} into {col['col']}{rows[0]}"
                        + (" and the other listed cells" if len(rows) > 1 else "") + " (or rebuild)",
                        sheet=name, cells=[f"{col['col']}{r}" for r in rows], count=len(rows),
                        data={"column": col["col"], "formula": col["formula"], "first_row": first, "last_row": last})
            if odd and ctx.want("H-INCONSISTENT"):
                ctx.add("H-INCONSISTENT", "error", f"{label}: {len(odd):,} cell(s) hold a formula that differs from "
                        f"the column's formula {_short(col['formula'], 60)} ({_rows_label(odd)})",
                        f"copy the formula of {col['col']}{first} down the column over the listed cells",
                        sheet=name, cells=[f"{col['col']}{r}" for r in odd], count=len(odd),
                        data={"column": col["col"], "formula": col["formula"]})
        if ctx.want("H-COVERAGE") and cols:
            raw_name = raw_by_table.get(table)
            raw = ctx.wb.sheet(raw_name) if raw_name else None
            if raw is None:
                continue
            raw_last = max((c.row for c in raw.iter_cells() if c.col == 1), default=1)
            ci0 = common.col_index(cols[0]["col"])
            helper_last = max((c.row for c in sheet.iter_cells() if c.col == ci0 and c.formula), default=first - 1)
            if raw_last > helper_last:
                missing = raw_last - helper_last
                last_col = common.col_letter(max(common.col_index(c["col"]) for c in cols))
                root = ctx.model_root.as_posix() if ctx.model_root else "<model>"
                ctx.add("H-COVERAGE", "error",
                        f"{raw_name} has {raw_last - 1:,} data rows (rows 2 to {raw_last:,}); {name} covers rows "
                        f"{first:,} to {helper_last:,}; {missing:,} raw rows ({helper_last + 1:,} to {raw_last:,}) are "
                        "not covered",
                        f"{_rotm_cmd('extend', root, '--table', table)} (or copy {name} row {helper_last:,}, "
                        f"A:{last_col}, down to row {raw_last:,})",
                        sheet=name, cell=f"A{helper_last}", data={"raw_last_row": raw_last, "helper_last_row": helper_last,
                                                                  "uncovered": missing, "table": table})


def _odd_cells(cells: list[Any], ctx: _Ctx) -> tuple[str | None, list[Any]]:
    """The relative form most cells share and the cells (formula or not) that do not hold it."""
    forms = [ctx.rel(c) if (c is not None and c.formula) else None for c in cells]
    counts = Counter(f for f in forms if f is not None)
    if not counts:
        return None, []
    dominant, _k = counts.most_common(1)[0]
    return dominant, [c for c, f in zip(cells, forms) if f != dominant]


def _manifest_block_rules(ctx: _Ctx) -> None:
    m = ctx.manifest
    if not m or not ctx.want("B-INCONSISTENT"):
        return
    for entry in m.get("sheets", []):
        if entry.get("type") not in ("calc", "output"):
            continue
        sheet = ctx.wb.sheet(entry["name"])
        if sheet is None:
            continue
        for block in entry.get("blocks", []):
            try:
                c1, r1, c2, r2 = common.range_bounds(block["body"])
            except ValueError:
                continue
            shape = block.get("shape", "grid")
            lines: list[list[tuple[int, int]]]
            if shape == "measures":
                lines = [[(r, c) for r in range(r1, r2 + 1)] for c in range(c1, c2 + 1)]
            elif shape == "items":
                lines = [[(r, c) for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)]
            else:
                lines = [[(r, c) for r in range(r1, r2 + 1) for c in range(c1, c2 + 1)]]
            odd_all: list[Any] = []
            empty: list[str] = []
            for line in lines:
                cells = [sheet.at(r, c) for r, c in line]
                for (r, c), cell in zip(line, cells):
                    if cell is None:
                        empty.append(common.a1(c, r))
                present = [c for c in cells if c is not None]
                _dom, odd = _odd_cells(present, ctx)
                if shape == "items" and odd and odd == [present[0]]:
                    continue                           # the first column of an item may use its 'first' formula
                odd_all.extend(odd)
            where = f"block '{block['id']}' ({entry['name']}!{block['body']}, {shape})"
            if odd_all:
                ctx.add("B-INCONSISTENT", "warning", f"{where}: {len(odd_all)} cell(s) differ from the formula the "
                        "other cells of their " + ("column" if shape == "measures" else "row" if shape == "items"
                                                   else "block") + " share",
                        "rebuild from the specification (rotm.py build --force); a deliberate exception belongs in the "
                        "specification", sheet=entry["name"], cells=[c.coord for c in odd_all], count=len(odd_all),
                        data={"block": block["id"], "shape": shape})
            if empty:
                ctx.add("B-INCONSISTENT", "warning", f"{where}: {len(empty)} empty cell(s) inside the block body",
                        "rebuild from the specification (rotm.py build --force)", sheet=entry["name"], cells=empty,
                        count=len(empty), data={"block": block["id"], "shape": shape})


def _inferred_consistency(ctx: _Ctx) -> None:
    """Helper columns and formula rows of sheets without a manifest (design 14: runs of 3 or more cells)."""
    if not ctx.want("H-INCONSISTENT", "B-INCONSISTENT", "H-COVERAGE", "H-GAP"):
        return
    check_keys = {(c.sheet, c.coord) for c in _inferred_checks(ctx)[0]}
    raw_last: dict[str, int] = {}
    for name in ctx.raw_sheets:
        s = ctx.wb.sheet(name)
        if s is not None and s.kind == "worksheet":
            raw_last[name] = max((c.row for c in s.iter_cells()), default=1)
    for sheet in ctx.worksheets():
        if sheet.name in ctx.raw_sheets or not ctx.inferred_sheet(sheet.name) or CHECK_SHEET_RE.search(sheet.name):
            continue
        fcells = [c for c in sheet.formulas() if c.formula and (c.sheet, c.coord) not in check_keys]
        by_col: dict[int, list[Any]] = {}
        by_row: dict[int, list[Any]] = {}
        for c in fcells:
            by_col.setdefault(c.col, []).append(c)
            by_row.setdefault(c.row, []).append(c)
        flagged: set[str] = set()
        covered: set[str] = set()
        text_rows = {c.row for c in sheet.constants() if isinstance(c.value, str) and c.value.strip()}
        used_rows = {r for r, _c in sheet.cells}
        # columns: helper columns (H-INCONSISTENT, inferred); a display total below the column is left out
        for col, lst in sorted(by_col.items()):
            lst.sort(key=lambda c: c.row)
            letter = common.col_letter(col)
            for run in _runs(lst, key=lambda c: c.row):
                consider = [c for c in run if not _total_like(c, "col")]
                odd = [c for c in _dominant_odd(consider, ctx) if c is not consider[0]]   # a header formula on top
                if odd:
                    odd_keys = {c.coord for c in odd}
                    dom_cell = _model_cell(consider, odd, ctx)
                    ctx.add("H-INCONSISTENT", "warning", f"(inferred) column {letter} rows {run[0].row:,} to "
                            f"{run[-1].row:,}: {len(odd)} of {len(run)} formula cells differ from the formula the "
                            f"other rows share ({_short(dom_cell.formula, 60)} in {dom_cell.coord})",
                            f"copy the formula of {dom_cell.coord} over {', '.join(c.coord for c in odd[:5])}"
                            + (" and the other listed cells" if len(odd) > 5 else ""),
                            sheet=sheet.name, cells=[c.coord for c in odd], count=len(odd),
                            data={"inferred": True, "column": letter, "first_row": run[0].row,
                                  "last_row": run[-1].row})
                    flagged.update(odd_keys)
            # v5 (A-aera-11): every run of the column is read before coverage is reported
            _inferred_coverage(ctx, sheet, letter, lst, raw_last, covered, text_rows, used_rows)
        # rows: formula rows (B-INCONSISTENT, inferred); the first cell of a row may differ (an opening value)
        for row, lst in sorted(by_row.items()):
            lst.sort(key=lambda c: c.col)
            for run in _runs(lst, key=lambda c: c.col):
                consider = [c for c in run if not _total_like(c, "row")]
                odd = _dominant_odd(consider, ctx)
                if not odd:
                    continue
                first = consider[0].coord
                odd = [c for c in odd if c.coord != first and c.coord not in flagged]
                if not odd:
                    continue
                odd_keys = {c.coord for c in odd}
                dom_cell = _model_cell(consider, odd, ctx)
                ctx.add("B-INCONSISTENT", "warning", f"(inferred) row {row:,} {run[0].coord}:{run[-1].coord}: "
                        f"{len(odd)} of {len(run)} formula cells differ from the formula the other cells of the row "
                        f"share ({_short(dom_cell.formula, 60)} in {dom_cell.coord})",
                        f"copy the formula of {dom_cell.coord} across the row over "
                        f"{', '.join(c.coord for c in odd[:5])}",
                        sheet=sheet.name, cells=[c.coord for c in odd], count=len(odd),
                        data={"inferred": True, "row": row})
                flagged.update(odd_keys)
        # typed values between cells that hold one formula (a broken helper column or row). Without a manifest this
        # is H-INCONSISTENT (the column's formula is not the same on every row, spec S2); an empty cell between two
        # runs of one formula is H-GAP (inferred, v5 A-aera-11; _inferred_coverage).
        _typed_runs(ctx, sheet, check_keys)


def _typed_runs(ctx: _Ctx, sheet: Any, check_keys: set[tuple[str, str]]) -> None:
    """Typed values inside a formula column or row (inferred mode): a run of one or more typed values (numbers,
    dates, logical values; v6, r5 verdict A-aera-7: also two or more adjacent ones, as values pasted over several
    rows) between two cells that hold the same relative formula, with at least one more cell of that formula beyond
    either end, is one finding for the run, with its range and the formula to copy over it. Text cells are not
    counted and end a run, so a label row between two parts of a column gives no finding. A column run is
    H-INCONSISTENT; a row run is B-INCONSISTENT, and a cell already named by a column run is not named again."""
    def typed(c: Any) -> bool:
        return (c is not None and not c.is_formula and c.formula_kind is None and not isinstance(c.value, str)
                and (c.sheet, c.coord) not in check_keys)

    def same(x: Any, form: str) -> bool:
        return x is not None and bool(x.formula) and ctx.rel(x) == form

    reported: set[str] = set()
    for rule, (dr, dc), axis in (("H-INCONSISTENT", (1, 0), "column"), ("B-INCONSISTENT", (0, 1), "row")):
        for c in sheet.constants():
            if not typed(c):
                continue
            a = sheet.at(c.row - dr, c.col - dc)            # the cell before the run
            if a is None or not a.formula:
                continue
            run = [c]
            nxt = sheet.at(c.row + dr, c.col + dc)
            while typed(nxt):
                run.append(nxt)
                nxt = sheet.at(nxt.row + dr, nxt.col + dc)
            b = nxt                                         # the cell after the run
            form = ctx.rel(a)
            if not same(b, form):
                continue
            a2 = sheet.at(a.row - dr, a.col - dc)
            b2 = sheet.at(b.row + dr, b.col + dc)
            if not (same(a2, form) or same(b2, form)):
                continue
            coords = [x.coord for x in run]
            if any(k in reported for k in coords):
                continue
            reported.update(coords)
            if len(run) == 1:
                ctx.add(rule, "warning", f"(inferred) the typed value {_short(_num_text(c.value), 30)} in {c.coord} "
                        f"sits inside a {axis} whose cells on both sides hold one formula ({_short(a.formula, 50)} "
                        f"in {a.coord})",
                        f"copy the formula of {a.coord} into {c.coord}; if the value is an input, put it on the "
                        "inputs sheet and reference it", sheet=sheet.name, cell=c.coord, data={"inferred": True})
                continue
            span = f"{coords[0]}:{coords[-1]}"
            ctx.add(rule, "warning", f"(inferred) {len(run)} typed values in {span} sit inside a {axis} whose cells "
                    f"on both sides hold one formula ({_short(a.formula, 50)} in {a.coord}, the same formula in "
                    f"{b.coord})",
                    f"copy the formula of {a.coord} over {span}; if the values are inputs, put them on the inputs "
                    "sheet and reference them", sheet=sheet.name, cells=coords, count=len(run),
                    data={"inferred": True, "range": span, "formula": a.formula, "model_cell": a.coord})


_INDEX_RAW = re.compile(r"INDEX\((?P<sheet>'(?:[^']|'')+'|[^!(),]+)!C\d+(?::C\d+)?,RC")


UNCOVERED_ROWS_SHOWN = 200   # raw row numbers kept in a finding's data (all are counted)


def _raw_target(form: str, raw_last: dict[str, int]) -> str | None:
    """The raw sheet a relative form reads row by row: a same-row reference to the raw sheet (=raw!C2*2 in row 2,
    relative form raw!RC3*2) or an INDEX over a whole raw column with a same-row row number
    (=INDEX(raw!$C:$C,A2), design 7.1); None when it reads none."""
    for name in raw_last:
        if f"{common.quote_sheet(name)}!RC" in form or f"{name}!RC" in form:
            return name
    m = _INDEX_RAW.search(form)
    if m:
        s = m.group("sheet")
        s = s[1:-1].replace("''", "'") if s.startswith("'") else s
        return s if s in raw_last else None
    return None


def _column_gaps(ctx: _Ctx, sheet: Any, cells: list[Any], text_rows: set[int]) -> list[tuple[int, int, Any, Any]]:
    """Empty cells between two runs of one formula in a column (v5, A-aera-11): (first empty row, last empty row,
    the formula cell above, the formula cell below) for each gap. `cells`: the column's formula cells sorted by row.
    The cells above and below the gap hold the same relative form, the two runs hold MIN_RUN or more cells of it,
    and no cell of the gap rows holds text (a row with a label is a deliberate break between two blocks). The
    caller leaves out the gaps of empty spacer rows in a column that reads no raw sheet."""
    runs: list[list[Any]] = []
    for c in cells:
        if runs and c.row == runs[-1][-1].row + 1:
            runs[-1].append(c)
        else:
            runs.append([c])
    out: list[tuple[int, int, Any, Any]] = []
    for above, below in zip(runs, runs[1:]):
        a, b = above[-1], below[0]
        form = ctx.rel(a)
        if ctx.rel(b) != form:
            continue
        if sum(1 for c in above + below if ctx.rel(c) == form) < MIN_RUN:
            continue
        rows = range(a.row + 1, b.row)
        if any(sheet.at(r, a.col) is not None or r in text_rows for r in rows):
            continue                               # a typed value (H-INCONSISTENT, inferred) or a label row
        out.append((a.row + 1, b.row - 1, a, b))
    return out


def _inferred_coverage(ctx: _Ctx, sheet: Any, letter: str, cells: list[Any], raw_last: dict[str, int],
                       covered: set[str], text_rows: set[int] | None = None,
                       used_rows: set[int] | None = None) -> None:
    """H-GAP and H-COVERAGE (inferred) of one column; `cells` are all its formula cells, sorted by row (v5,
    A-aera-11: every run is read before coverage is reported).
    - H-GAP: an empty cell between two runs of one formula, at that cell; the fix copies the formula of the cell
      above into it. When the column reads a raw sheet row by row, the finding counts the raw rows of the gap
      (every gap counts: a raw row no helper row reads). In another column a gap counts only when every gap row
      holds another cell (`used_rows`); an empty spacer row between two blocks gets no finding.
    - H-COVERAGE: a helper column that reads a raw sheet row by row ends above the raw sheet's last data row. The
      count is of the raw rows, from the column's first formula row down, that no helper row reads (gaps inside
      included); the copy-down advice covers the rows missing at the end only."""
    if not cells or not ctx.want("H-COVERAGE", "H-GAP"):
        return
    target, form = None, None
    body = [c for c in cells if not _total_like(c, "col")]
    if raw_last and body:
        top, k = Counter(ctx.rel(c) for c in body).most_common(1)[0]
        if k >= MIN_RUN:
            target = _raw_target(top, raw_last)
            form = top if target else None
    gaps = _column_gaps(ctx, sheet, cells, text_rows or set()) if ctx.want("H-GAP") else []
    for g0, g1, above, below in gaps:
        reads_raw = target is not None and ctx.rel(above) == form
        if not reads_raw and used_rows is not None and not all(r in used_rows for r in range(g0, g1 + 1)):
            continue                               # an empty spacer row in a column that reads no raw sheet
        coords = [f"{letter}{r}" for r in range(g0, g1 + 1)]
        where = coords[0] if len(coords) == 1 else f"{coords[0]} to {coords[-1]}"
        verb = "is" if len(coords) == 1 else "are"
        data: dict[str, Any] = {"inferred": True, "column": letter, "first_row": g0, "last_row": g1,
                                "copy_from": above.coord, "formula": above.formula}
        message = (f"(inferred) column {letter}: {where} {verb} empty between two runs of one formula "
                   f"({_short(above.formula, 60)} in {above.coord}, the same in {below.coord})")
        if reads_raw:
            rows = [r for r in range(g0, g1 + 1) if r <= raw_last[target]]
            if rows:
                n = len(rows)
                message += (f"; {n:,} raw row{'' if n == 1 else 's'} ({_rows_text(rows)}) of {target} "
                            f"{'is' if n == 1 else 'are'} read by no helper row")
                data.update(raw_sheet=target, uncovered=n, uncovered_rows=rows[:UNCOVERED_ROWS_SHOWN])
        ctx.add("H-GAP", "warning", message,
                f"copy the formula of {above.coord} into {coords[0]}"
                + (f" and down to {coords[-1]}" if len(coords) > 1 else "")
                + "; if the empty rows are a deliberate break between two blocks, this inferred finding does not "
                "apply", sheet=sheet.name, cells=coords, count=len(coords), data=data)
    if target is None or not ctx.want("H-COVERAGE"):
        return
    read = sorted(c.row for c in body if ctx.rel(c) == form)
    first, last = read[0], read[-1]
    raw_end = raw_last[target]
    key = f"{target}|{letter}"
    if last >= raw_end or key in covered:
        return                                     # no rows missing at the end: a gap inside is H-GAP's
    covered.add(key)
    read_set = set(read)
    unread = [r for r in range(first, raw_end + 1) if r not in read_set]
    inside = [r for r in unread if r < last]
    fix = (f"copy row {last:,} of the helper columns down to row {raw_end:,} (in a model built by this plugin: "
           "rotm.py extend)")
    if inside:
        fix += (f"; the empty cells inside the column ({', '.join(f'{letter}{r}' for r in inside[:5])}"
                + (" and more" if len(inside) > 5 else "") + ") get the formula of the cell above them (H-GAP)")
    ctx.add("H-COVERAGE", "warning", f"(inferred) column {letter} reads {target} row by row but ends at row {last:,}; "
            f"{target} has data down to row {raw_end:,}, so {len(unread):,} raw row(s) ({_rows_text(unread)}) are "
            "not covered", fix, sheet=sheet.name, cell=f"{letter}{last}",
            data={"inferred": True, "raw_sheet": target, "helper_last_row": last, "raw_last_row": raw_end,
                  "uncovered": len(unread), "uncovered_rows": unread[:UNCOVERED_ROWS_SHOWN]})


def _runs(cells: list[Any], key: Callable[[Any], int]) -> Iterable[list[Any]]:
    """Maximal runs of consecutive positions (row or column numbers), MIN_RUN cells or more."""
    run: list[Any] = []
    for c in cells:
        if run and key(c) != key(run[-1]) + 1:
            if len(run) >= MIN_RUN:
                yield run
            run = []
        run.append(c)
    if len(run) >= MIN_RUN:
        yield run


def _model_cell(cells: list[Any], odd: list[Any], ctx: _Ctx) -> Any:
    """The cell to copy from: the nearest cell before the first odd cell that holds the shared formula."""
    odd_keys = {c.coord for c in odd}
    forms = Counter(ctx.rel(c) for c in cells if c.coord not in odd_keys)
    dominant = forms.most_common(1)[0][0]
    holders = [c for c in cells if c.coord not in odd_keys and ctx.rel(c) == dominant]
    before = [c for c in holders if (c.row, c.col) < (odd[0].row, odd[0].col)]
    return before[-1] if before else holders[0]


def _dominant_odd(cells: list[Any], ctx: _Ctx) -> list[Any]:
    """Inferred mode: the cells that differ from a formula held by at least MIN_RUN cells and DOMINANT_SHARE of
    the run; empty when no formula dominates (a list of different line items is not a helper column)."""
    if len(cells) < MIN_RUN:
        return []
    forms = [ctx.rel(c) for c in cells]
    dominant, k = Counter(forms).most_common(1)[0]
    if k < MIN_RUN or k / len(cells) < DOMINANT_SHARE or k == len(cells):
        return []
    return [c for c, f in zip(cells, forms) if f != dominant]


# ----------------------------------------------------------------------------- checks

def _inferred_checks(ctx: _Ctx) -> tuple[list[Any], list[Any], list[Any]]:
    """(check cells, result cells whose value C-FAIL reads, master check cells) of sheets without a manifest.

    A check cell is a formula labelled with a check word (the nearest text to its left in its row, or above it in
    its column) or any formula on a sheet named check or checks. Results (the cells whose value C-FAIL reads): cells
    under a column header with a strong check word, the rightmost formula of a row labelled with one (every cell of
    such a row when they share one formula), and the rightmost formula of each row on a check sheet. A master check
    is a check labelled with a master word and a strong check word ("Master check", "Overall check"), or any
    formula that reads a range holding two or more results."""
    if ctx.checks_cache is not None:
        return ctx.checks_cache
    from . import formula
    checks: list[Any] = []
    results: dict[tuple[str, str], Any] = {}
    masters: dict[tuple[str, str], Any] = {}
    for sheet in ctx.worksheets():
        if sheet.name in ctx.raw_sheets or not ctx.inferred_sheet(sheet.name):
            continue
        check_sheet = bool(CHECK_SHEET_RE.search(sheet.name))
        by_row, by_col = _text_cells(sheet)
        strong_rows: dict[int, list[Any]] = {}
        sheet_rows: dict[int, list[Any]] = {}
        for c in sheet.formulas():
            left = _nearest_before(by_row.get(c.row), c.col)
            above = _nearest_before(by_col.get(c.col), c.row)
            if not (check_sheet or CHECK_WORD_RE.search(f"{left or ''} | {above or ''}")):
                continue
            checks.append(c)
            if any(t and MASTER_RE.search(t) and STRONG_CHECK_RE.search(t) for t in (left, above)):
                masters[(c.sheet, c.coord)] = c       # "Master check", "Overall check", "전체 검증"
            elif STRONG_CHECK_RE.search(above or ""):
                results[(c.sheet, c.coord)] = c       # a column headed "Check"
            elif STRONG_CHECK_RE.search(left or ""):
                strong_rows.setdefault(c.row, []).append(c)
            elif check_sheet:
                sheet_rows.setdefault(c.row, []).append(c)
        for _row, lst in strong_rows.items():
            lst.sort(key=lambda c: c.col)
            if len(lst) >= 2 and len({ctx.rel(c) for c in lst}) == 1:
                for c in lst:                          # a row labelled check, one formula across periods
                    results[(c.sheet, c.coord)] = c
            else:
                results[(lst[-1].sheet, lst[-1].coord)] = lst[-1]   # the rightmost cell of a check row
        for _row, lst in sheet_rows.items():
            last = max(lst, key=lambda c: c.col)       # on a sheet named checks: the rightmost cell of each row
            results[(last.sheet, last.coord)] = last
    # a formula anywhere that reads a range holding two or more check results is a master check
    index: dict[str, dict[int, list[int]]] = {}
    for (s, _coord), c in results.items():
        index.setdefault(s, {}).setdefault(c.row, []).append(c.col)
    rows_sorted = {s: sorted(d) for s, d in index.items()}
    for d in index.values():
        for cols in d.values():
            cols.sort()
    if index:
        for sheet in ctx.worksheets():
            if sheet.name in ctx.raw_sheets:
                continue
            for c in sheet.formulas():
                key = (c.sheet, c.coord)
                if key in masters or not c.formula or ":" not in c.formula:
                    continue
                try:
                    refs = formula.references(c.formula)
                except Exception:  # noqa: BLE001
                    continue
                for r in refs:
                    box = _range_box(sheet.name, r)
                    if box is None or box[0] not in index:
                        continue
                    s, c1, r1, c2, r2 = box
                    rows = rows_sorted[s]
                    found = 0
                    for row in rows[bisect.bisect_left(rows, r1):bisect.bisect_right(rows, r2)]:
                        cols = index[s][row]
                        found += bisect.bisect_right(cols, c2) - bisect.bisect_left(cols, c1)
                        if s == c.sheet and row == c.row and c1 <= c.col <= c2 and key in results:
                            found -= 1
                        if found >= 2:
                            break
                    if found >= 2:
                        masters[key] = c
                        break
    out = (checks, [c for k, c in results.items() if k not in masters], list(masters.values()))
    ctx.checks_cache = out
    return out


def _check_rules(ctx: _Ctx) -> None:
    m = ctx.manifest
    any_formula = any(True for s in ctx.worksheets() for _c in s.formulas())
    if m is not None:
        _manifest_check_rules(ctx)
        if ctx.want("C-RECOMPUTE") and ctx.wb.has_cached_values:
            from . import recompute
            for f in recompute.check_findings(ctx.path, m):
                ctx.add(f.rule, f.severity, f.message, f.fix, sheet=f.sheet, cells=f.cells, data=f.data)
    if m is None:
        if not any_formula:
            ctx.report.note("no formulas: the check rules (C-MISSING, C-FAIL) do not apply to a workbook of values")
            return
        checks, results, masters = _inferred_checks(ctx)
        if ctx.want("C-MISSING"):
            if not checks:
                ctx.add("C-MISSING", "warning", "(inferred) no check cells: no formula is labelled check, chk, control, "
                        "error, diff, 검증, 체크, 차이 or 오류, and no sheet is named checks",
                        "add checks that tie each calculation block to an independent total, and one master check "
                        "that reads them all (references/checks.md)", data={"inferred": True})
            elif not masters:
                ctx.add("C-MISSING", "warning", f"(inferred) {len(checks)} check cell(s) but no master check: no check "
                        "is labelled master or overall, and no formula reads two or more check cells",
                        "add one master check that reads every check result and shows it in the same place on every "
                        "sheet (references/checks.md)", data={"inferred": True})
        if ctx.want("C-BLOCK") and checks:
            _inferred_block_rules(ctx, checks)
        if ctx.want("C-FAIL") and ctx.wb.has_cached_values:
            tol = float(ctx.settings.get("check_tolerance") or 0)
            master_keys = {(c.sheet, c.coord) for c in masters}
            failing: dict[tuple[str, str], list[tuple[Any, str]]] = {}
            for c in masters + results:
                bad = _failing_value(c.cached, tol)
                if bad is None:
                    continue
                kind = "master check" if (c.sheet, c.coord) in master_keys else "check"
                failing.setdefault((c.sheet, kind), []).append((c, bad))
            for (sheet, kind), items in failing.items():
                shown = ", ".join(f"{c.coord} = {bad}" for c, bad in items[:5]) + (" ..." if len(items) > 5 else "")
                ctx.add("C-FAIL", "warning", f"(inferred) {len(items):,} {kind} cell(s) hold a value other than 0 "
                        f"(tolerance {_num_text(tol)}): {shown}",
                        "find each failing check and correct the model or its data; if this workbook counts 1 or TRUE "
                        "as a pass, this inferred finding does not apply", sheet=sheet,
                        cells=[c.coord for c, _b in items], count=len(items),
                        data={"inferred": True, "values": {c.coord: (c.cached if not isinstance(c.cached, str)
                                                                     else str(c.cached)) for c, _b in items[:20]}})


@dataclass
class _Block:
    """An inferred calculation block (C-BLOCK without a manifest)."""
    sheet: str
    cells: list[Any]                             # sorted by row, then column
    box: tuple[int, int, int, int]               # min_col, min_row, max_col, max_row

    @property
    def first(self) -> str:
        return self.cells[0].coord

    @property
    def range(self) -> str:
        c1, r1, c2, r2 = self.box
        return f"{common.a1(c1, r1)}:{common.a1(c2, r2)}"

    def shape(self) -> str:
        c1, r1, c2, r2 = self.box
        if c1 == c2:
            return f"one formula down column {common.col_letter(c1)}"
        if r1 == r2:
            return f"one formula along row {r1:,}"
        return "one formula down and across"


def _is_one_cell_link(text: str | None) -> bool:
    """True for a formula that is only a reference to one cell (=B2, =+B2, =Sheet!$B$2): a link, not a calculation
    (J-3). A range, a defined name or anything with an operator or a function is not a link."""
    if not text:
        return False
    from . import formula
    try:
        a = formula.analyze(text)
    except Exception:  # noqa: BLE001 - an unreadable formula is not known to be a link
        return False
    return bool(a.is_pure_link and len(a.refs) == 1 and a.refs[0].kind == "cell")


def _inferred_blocks(ctx: _Ctx, check_keys: set[tuple[str, str]]) -> tuple[list[_Block], int]:
    """Calculation blocks of the sheets without a manifest (A-aera-11): formula cells that hold one relative
    formula and touch each other down a column or along a row, MIN_BLOCK cells or more, on a sheet that is not
    raw and not a checks sheet. Check cells are no part of a block. Runs that share a cell are one block, so a
    grid with one formula copied down and across is reported once. A run whose formula is only a reference to one
    cell (a link such as =Sheet!B2 or =B2) is not a calculation block (J-3); the second value counts those runs."""
    order = {s.name: i for i, s in enumerate(ctx.wb.sheets)}
    out: list[_Block] = []
    link_runs = 0
    for sheet in ctx.worksheets():
        if sheet.name in ctx.raw_sheets or not ctx.inferred_sheet(sheet.name) or CHECK_SHEET_RE.search(sheet.name):
            continue
        by_form: dict[str, dict[tuple[int, int], Any]] = {}
        for c in sheet.formulas():
            if c.formula is None or (c.sheet, c.coord) in check_keys:
                continue
            by_form.setdefault(ctx.rel(c), {})[(c.row, c.col)] = c
        for cells in by_form.values():
            if len(cells) < MIN_BLOCK:
                continue
            is_link = _is_one_cell_link(next(iter(cells.values())).formula)   # one relative form: one answer
            seen: set[tuple[int, int]] = set()
            for start in sorted(cells):
                if start in seen:
                    continue
                seen.add(start)
                stack, comp = [start], []
                while stack:
                    r, col = stack.pop()
                    comp.append(cells[(r, col)])
                    for nb in ((r + 1, col), (r - 1, col), (r, col + 1), (r, col - 1)):
                        if nb in cells and nb not in seen:
                            seen.add(nb)
                            stack.append(nb)
                if len(comp) < MIN_BLOCK:
                    continue
                if is_link:
                    link_runs += 1
                    continue
                comp.sort(key=lambda x: (x.row, x.col))
                box = (min(x.col for x in comp), comp[0].row, max(x.col for x in comp), comp[-1].row)
                out.append(_Block(sheet.name, comp, box))
    out.sort(key=lambda b: (order.get(b.sheet, 0), b.box[1], b.box[0]))
    return out, link_runs


def _ref_boxes(ctx: _Ctx, sheet_name: str, formula_text: str, depth: int = 0) -> list[tuple[str, int, int, int, int]]:
    """(sheet in lower case, min_col, min_row, max_col, max_row) of every reference of a formula: cells, ranges,
    whole columns and rows, and a defined name that names one of those. References to other workbooks and
    structured references are left out."""
    from . import formula
    try:
        refs = formula.references(formula_text)
    except Exception:  # noqa: BLE001 - an unreadable formula reads nothing that can be shown
        return []
    out: list[tuple[str, int, int, int, int]] = []
    for r in refs:
        if r.workbook:
            continue
        if r.kind == "name":
            if depth < 3:
                for value in _name_values(ctx, r.a1, r.sheet or sheet_name):
                    out.extend(_ref_boxes(ctx, sheet_name, "=" + value, depth + 1))
            continue
        if r.kind == "row":
            try:
                rows = [int(p) for p in r.a1.replace("$", "").split(":")]
            except ValueError:
                continue
            out.append(((r.sheet or sheet_name).casefold(), 1, min(rows), 16_384, max(rows)))
            continue
        box = _range_box(sheet_name, r)
        if box is not None:
            out.append((box[0].casefold(), *box[1:]))
    return out


def _name_values(ctx: _Ctx, name: str, sheet_name: str) -> list[str]:
    """The definitions of a defined name: the one scoped to the sheet, else the workbook's."""
    found = [(v, scope) for n, v, scope in ctx.wb.defined_names or [] if n.casefold() == name.casefold()]
    local = [v for v, scope in found if scope and scope.casefold() == sheet_name.casefold()]
    return local or [v for v, scope in found if scope is None]


def _inferred_block_rules(ctx: _Ctx, checks: list[Any]) -> None:
    """C-BLOCK (inferred, A-aera-11): each calculation block that no inferred check cell reads. A block is read
    when a check cell's formula refers to at least one of its cells (a range that overlaps the block counts).
    Runs of links are no blocks (J-3); a note gives their count."""
    check_keys = {(c.sheet, c.coord) for c in checks}
    boxes: dict[str, list[tuple[int, int, int, int]]] = {}
    for c in checks:
        if not c.formula:
            continue
        for s, c1, r1, c2, r2 in _ref_boxes(ctx, c.sheet, c.formula):
            boxes.setdefault(s, []).append((c1, r1, c2, r2))
    blocks, link_runs = _inferred_blocks(ctx, check_keys)
    unread = 0
    for block in blocks:
        reads = boxes.get(block.sheet.casefold(), [])
        b1, b2, b3, b4 = block.box
        near = [x for x in reads if x[0] <= b3 and x[2] >= b1 and x[1] <= b4 and x[3] >= b2]
        if any(x[0] <= cell.col <= x[2] and x[1] <= cell.row <= x[3] for x in near for cell in block.cells):
            continue
        unread += 1
        rep = block.cells[0]
        where = f"{common.quote_sheet(block.sheet)}!{block.range}"
        ctx.add("C-BLOCK", "warning", f"(inferred) calculation block {where} ({len(block.cells):,} formula cells, "
                f"{block.shape()}, first cell {block.first}: {_short(rep.formula, 60)}) is read by no check cell",
                "add a check that ties this block to an independent total (references/checks.md)",
                sheet=block.sheet, cell=block.first,
                data={"inferred": True, "range": block.range, "first_cell": block.first, "cells": len(block.cells),
                      "formula": _short(rep.formula, 300)})
    if blocks:
        ctx.report.note(f"inferred calculation blocks: {len(blocks):,}; {len(blocks) - unread:,} read by a check cell, "
                        f"{unread:,} read by none (C-BLOCK)")
    if link_runs:
        ctx.report.note(f"inferred calculation blocks: {link_runs:,} run(s) of links left out (each cell holds only a "
                        "reference to one cell, such as =Sheet!B2 or =B2; a link is not a calculation, so C-BLOCK "
                        "does not apply to it)")


def _failing_value(v: Any, tolerance: float = 0.0) -> str | None:
    """The shown value when a check result is not a pass (0, or within `tolerance` of 0 for an inferred check);
    None for a pass or for a value that is not read (a boolean, an empty cell, ordinary text)."""
    if v is None or isinstance(v, bool):
        return None
    if _is_number(v):
        return None if abs(float(v)) <= tolerance else _num_text(v)
    if isinstance(v, str):
        t = v.strip().upper()
        if t in FAIL_TEXTS or v.strip() in FAIL_TEXTS:
            return f"'{v.strip()}'"
        if t.startswith("#"):
            return t
    return None


def _cell_of(ctx: _Ctx, ref: str) -> tuple[str | None, str, Any]:
    sheet_name, coord = common.parse_ref(ref)
    coord = coord.replace("$", "")
    sheet = ctx.wb.sheet(sheet_name) if sheet_name else None
    return sheet_name, coord, sheet.cell(coord) if sheet is not None else None


def _manifest_check_rules(ctx: _Ctx) -> None:
    from . import formula
    m = ctx.manifest or {}
    checks = list(m.get("checks") or [])
    master_ref = m.get("master_check")
    msheet, mcoord, mcell = _cell_of(ctx, master_ref) if master_ref else (None, "", None)
    if ctx.want("C-MISSING"):
        if not master_ref:
            ctx.add("C-MISSING", "error", "the manifest names no master check", "rebuild the workbook with rotm.py build")
        elif mcell is None or not mcell.is_formula:
            ctx.add("C-MISSING", "error", f"the master check {master_ref} is empty or holds no formula",
                    "rebuild the workbook with rotm.py build", sheet=msheet, cell=mcoord)
        if not checks:
            ctx.add("C-MISSING", "error", "the manifest lists no checks", "add checks to the specification and rebuild")
        for chk in checks:
            # a check result that holds a typed value (for example 0) reads as passing whatever the model holds
            ref = chk.get("result")
            if not ref:
                continue
            s, coord, cell = _cell_of(ctx, ref)
            if cell is not None and cell.is_formula:
                continue
            held = ("is empty" if cell is None or cell.value is None
                    else f"holds the typed value {_short(_num_text(cell.value), 30)} and no formula")
            ctx.add("C-MISSING", "error", f"check '{chk.get('id')}' result {s}!{coord} {held}: a check result must "
                    "be a formula; a typed value shows the same result whatever the model holds",
                    "rebuild the workbook with rotm.py build --force (build writes the check's formula again)",
                    sheet=s, cell=coord, data={"check": chk.get("id")})
    if ctx.want("C-BLOCK"):
        for entry in m.get("sheets", []):
            for block in entry.get("blocks", []) or []:
                if not block.get("checks"):
                    ctx.add("C-BLOCK", "warning", f"block '{block['id']}' ({entry['name']}!{block['body']}) is named "
                            "by no check", "add a check whose actual or expected names the block ({blk:" + block["id"]
                            + "} or {b:" + block["id"] + ".<item>}) and rebuild", sheet=entry["name"],
                            cell=str(block["body"]).split(":")[0], data={"block": block["id"]})
    if ctx.want("C-WIRING") and mcell is not None and mcell.formula:
        boxes = [b for b in (_range_box(msheet or "", r) for r in formula.references(mcell.formula)) if b]
        for chk in checks:
            if chk.get("severity") == "alert":
                continue
            s, coord, _cell = _cell_of(ctx, chk["result"])
            try:
                col, row = common.split_cell(coord)
            except ValueError:
                continue
            if not any(b[0] == s and b[1] <= col <= b[3] and b[2] <= row <= b[4] for b in boxes):
                ctx.add("C-WIRING", "error", f"check '{chk['id']}' result {chk['result']} lies outside the ranges the "
                        f"master check {master_ref} reads", "rebuild the workbook with rotm.py build", sheet=s, cell=coord,
                        data={"check": chk["id"]})
        for link in m.get("master_links") or []:
            s, coord, cell = _cell_of(ctx, link)
            ok = False
            if cell is not None and cell.formula:
                a = formula.analyze(cell.formula)
                if a.is_pure_link and a.refs:
                    r = a.refs[0]
                    ok = ((r.sheet or s) == msheet and r.a1.replace("$", "") == mcoord)
            if not ok:
                try:
                    mc, mr = common.split_cell(mcoord)
                    wanted = f"={common.quote_sheet(msheet or 'checks')}!{common.a1(mc, mr, abs_col=True, abs_row=True)}"
                except ValueError:
                    wanted = f"a link to {master_ref}"
                ctx.add("C-WIRING", "error", f"{link} does not show the master check (it should hold {wanted})",
                        "rebuild the workbook with rotm.py build", sheet=s, cell=coord)
    if ctx.want("W-SELF-CHECK"):
        _self_check_rule(ctx, checks)
    if ctx.want("C-FAIL", "C-ALERT"):
        if not ctx.wb.has_cached_values:
            return
        targets = [(chk, chk["result"]) for chk in checks]
        if master_ref:
            targets.append(({"id": "master check", "label": "master check"}, master_ref))
        for chk, ref in targets:
            s, coord, cell = _cell_of(ctx, ref)
            value = cell.cached if cell is not None else None
            if _is_number(value) and float(value) == 0:
                continue
            shown = "nothing" if value is None else _num_text(value)
            label = chk.get("label") or chk["id"]
            if chk.get("severity") == "alert":
                # v4 (J-1): a raised alert is C-ALERT (information), as in check; its Result cell shows ALERT
                ctx.add("C-ALERT", "info", f"alert '{chk['id']}' ({_short(label, 60)}) is raised: {ref} holds {shown}",
                        "look at the alert's cells; alerts are not part of the master check", sheet=s, cell=coord,
                        data={"check": chk["id"], "value": value if not isinstance(value, str) else str(value)})
            else:
                ctx.add("C-FAIL", "error", f"check '{chk['id']}' ({_short(label, 60)}) fails: {ref} holds {shown}, "
                        "a passing check holds 0",
                        f"compare the actual and the expected value in row {coord.lstrip('ABCDEFGHIJKLMNOPQRSTUVWXYZ$')} "
                        f"of {s} and correct the model or its data", sheet=s, cell=coord,
                        data={"check": chk["id"], "value": value if not isinstance(value, str) else str(value)})


CHECK_ACTUAL_COL, CHECK_EXPECTED_COL = 4, 5     # checks sheet columns D (Actual) and E (Expected or minimum)
SELF_CHECK_KINDS = frozenset({"equal", "at_most"})   # the kinds W-SELF-CHECK compares (as build.py does)


def _link_target(sheet_name: str, text: str) -> tuple[str, str] | None:
    """(sheet in lower case, cell without $) when the formula is only a reference to one cell, else None."""
    if not _is_one_cell_link(text):
        return None
    from . import formula
    r = formula.analyze(text).refs[0]
    if r.workbook:
        return None
    return (r.sheet or sheet_name).casefold(), r.a1.replace("$", "").upper()


def _self_check_rule(ctx: _Ctx, checks: Sequence[dict[str, Any]]) -> None:
    """W-SELF-CHECK (v3 plan 5.4, A-aera-19): a registered check of the specification whose actual and expected
    cells (checks sheet columns D and E on the result's row) hold the same formula (after removing $ and spaces)
    or links to the same cell. Such a check compares a value with itself and can never fail. The checks build
    adds itself (manifest "auto": true) are left out, as build's own W-SELF-CHECK leaves them out. Only the kinds
    build compares, equal and at_most (v3 integrator, request of P2): a range check whose actual equals its
    minimum (column E) can still exceed its maximum."""
    def bare(text: str) -> str:
        return text.replace("$", "").replace(" ", "")

    for chk in checks:
        ref = chk.get("result")
        if not ref or chk.get("auto") is True or chk.get("kind") not in SELF_CHECK_KINDS:
            continue
        s, coord, _cell = _cell_of(ctx, ref)
        sheet = ctx.wb.sheet(s) if s else None
        if sheet is None:
            continue
        try:
            _col, row = common.split_cell(coord)
        except ValueError:
            continue
        actual, expected = sheet.at(row, CHECK_ACTUAL_COL), sheet.at(row, CHECK_EXPECTED_COL)
        if actual is None or expected is None or not actual.formula or not expected.formula:
            continue
        same = bare(actual.formula) == bare(expected.formula)
        if not same:
            target = _link_target(sheet.name, actual.formula)
            same = target is not None and target == _link_target(sheet.name, expected.formula)
        if not same:
            continue
        ctx.add("W-SELF-CHECK", "warning", f"check {chk.get('id')}: actual ({actual.formula.lstrip('=')}) and expected "
                f"({expected.formula.lstrip('=')}) are the same, so the check can never fail",
                "point one side at an independent total: a raw column total, a control total or another block's total",
                sheet=sheet.name, cells=[actual.coord, expected.coord], count=1,
                data={"check": chk.get("id"), "actual": actual.formula, "expected": expected.formula})


# ----------------------------------------------------------------------------- values

def _value_rules(ctx: _Ctx) -> None:
    n_formulas = 0
    for sheet in ctx.worksheets():
        errors: dict[str, list[str]] = {}
        for c in sheet.iter_cells():
            if c.is_formula:
                n_formulas += 1
            if c.is_error:
                text = str(c.cached if c.cached is not None else c.value)
                errors.setdefault(text, []).append(c.coord)
        if ctx.want("V-ERROR"):
            for text, coords in errors.items():
                ctx.add("V-ERROR", "error", f"{len(coords):,} cell(s) hold the error value {text}",
                        "find the cause in the formula or its inputs and correct it; do not hide it with IFERROR "
                        "(test for the specific error, guides L-98)", sheet=sheet.name, cells=coords, count=len(coords),
                        data={"error": text})
    if n_formulas and not ctx.wb.has_cached_values:
        ctx.report.note("values not verified: the workbook holds no cached values (not recalculated)")
        ctx.add("V-NOT-RECALC", "info", f"{n_formulas:,} formula cells have no cached values: the workbook was not "
                "recalculated after it was written, so error values and check results could not be read",
                f"recalculate it in Excel: {_rotm_cmd('check', '<model>')} (or rotm.py recalc <workbook>)",
                data={"formulas": n_formulas})


def _no_value_cells(found: Iterable[Any]) -> dict[str, list[str]]:
    """{sheet: [coord, ...]} from what workbook.formula_cells_without_value returns: (sheet, coord) pairs,
    'sheet!A1' texts, mappings with sheet and cell (or coord), or objects with .sheet and .coord."""
    out: dict[str, list[str]] = {}
    for item in found:
        sheet = coord = None
        if isinstance(item, str):
            sheet, coord = common.parse_ref(item)
        elif isinstance(item, dict):
            sheet, coord = item.get("sheet"), item.get("cell") or item.get("coord")
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            sheet, coord = item[0], item[1]
        else:
            sheet, coord = getattr(item, "sheet", None), getattr(item, "coord", None)
        if sheet and coord:
            out.setdefault(str(sheet), []).append(str(coord).replace("$", ""))
    return out


def _no_value_rules(ctx: _Ctx) -> None:
    """V-NO-VALUE (A-aera-5): in a workbook that has cached values, formula cells whose saved XML holds no value.
    Excel leaves the cells of a cycle of formulas (a formula that refers to itself through other cells) without
    a value. A workbook without cached values (a fresh build) is V-NOT-RECALC instead; a formula that returns
    empty text has a value and is not listed (workbook.formula_cells_without_value). The saved XML does not say
    which cells are circular, so every V-NO-VALUE of the audit gets fix N-AUDIT (v3 plan 5.4): `rotm.py check`
    recalculates in Excel and names a circular reference when there is one."""
    if not ctx.wb.has_cached_values:
        return
    from . import workbook as wbmod
    finder = getattr(wbmod, "formula_cells_without_value", None)
    if finder is None:
        ctx.report.note("V-NO-VALUE not checked: lib.workbook has no formula_cells_without_value")
        return
    def position(coord: str) -> tuple[int, int]:
        try:
            col, row = common.split_cell(coord)
        except ValueError:
            return (0, 0)
        return (row, col)

    root = ctx.model_root.as_posix() if ctx.model_root is not None else "<model>"
    for sheet, coords in _no_value_cells(finder(ctx.path)).items():
        coords = sorted(set(coords), key=position)
        ref = f"{common.quote_sheet(sheet)}!{coords[0]}"
        ctx.add("V-NO-VALUE", "error", f"{len(coords):,} formula cell(s) hold no value after the last "
                f"recalculation ({', '.join(coords[:5])}{' ...' if len(coords) > 5 else ''})",
                f"the saved file holds no value for this formula: {_rotm_cmd('check', root)} recalculates it in "
                "Excel and names a circular reference when there is one; otherwise look at the formula and its "
                f"inputs ({_rotm_cmd('trace', ctx.path.as_posix(), ref)})",
                sheet=sheet, cells=coords, count=len(coords), data={"cells": coords[:50]})


# ----------------------------------------------------------------------------- styles

def _style_sets(d: Any) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Fills and font colours the dictionary defines: hex -> the cell types that use it."""
    fills: dict[str, list[str]] = {}
    colors: dict[str, list[str]] = {AUTO_TEXT: []}
    for t in d.cell_types():
        st = d.style(t)
        if st.fill:
            fills.setdefault(st.fill.upper(), []).append(t)
        colors.setdefault(_norm_font_color(st.font_color), []).append(t)
    try:
        colors.setdefault(d.color("fail_text"), []).append("failing check (number format)")
    except common.RotmError:
        pass
    return fills, colors


def _sheet_size_of(ctx: _Ctx, name: str) -> int | None:
    if not ctx.manifest:
        return None
    from . import manifest as mmod
    entry = mmod.sheet_entry(ctx.manifest, name)
    if entry is None:
        return None
    if entry.get("font_size") in (8, 9):
        return int(entry["font_size"])
    try:
        return ctx.dictionary.sheet_size(entry.get("type", "calc"))
    except common.RotmError:
        return None


def _style_rules(ctx: _Ctx) -> None:
    from . import manifest as mmod
    d = ctx.dictionary
    font = d.font_name
    sizes = sorted(set(d.sizes.values()))
    fills, colors = _style_sets(d)
    fill_txt = ", ".join(f"{h} ({'/'.join(ts)})" for h, ts in sorted(fills.items())) or "none"
    color_txt = ", ".join(sorted(colors))
    fix = "apply the style dictionary with rotm.py theme <workbook> (a built model: rebuild it)"
    for sheet in ctx.worksheets():
        ranges = mmod.cell_type_ranges(ctx.manifest, sheet.name) if ctx.manifest else []
        sheet_size = _sheet_size_of(ctx, sheet.name)
        for c in sheet.iter_cells():
            if c.value is None or (isinstance(c.value, str) and not c.value.strip()):
                continue
            ct = mmod.cell_type_at(ranges, c.row, c.col) if ranges else None
            st = None
            if ct is not None and sheet_size is not None:
                try:
                    st = d.style(ct)
                except common.RotmError:
                    st = None
            # S-FONT: one font for every cell (type does not matter)
            if (c.font_name or "").strip().lower() != font.lower():
                ctx.add("S-FONT", "warning", f"font {c.font_name or '(not set)'}; the dictionary's font is {font}", fix,
                        sheet=sheet.name, cell=c.coord, data={"font": c.font_name})
            size = c.font_size
            if st is not None:
                expected = d.size_for(ct, sheet_size)
                if size is None or abs(float(size) - expected) > 1e-9:
                    ctx.add("S-SIZE", "warning", f"size {_size_txt(size)} on a {ct} cell; the dictionary gives "
                            f"{expected} on this sheet", fix, sheet=sheet.name, cell=c.coord,
                            data={"size": size, "expected": expected, "cell_type": ct})
                exp_fill = _norm_fill(st.fill)
                if _norm_fill(c.fill) != exp_fill:
                    ctx.add("S-FILL", "warning", f"fill {c.fill or 'none'} on a {ct} cell; the dictionary gives "
                            f"{exp_fill or 'no fill'}", fix, sheet=sheet.name, cell=c.coord,
                            data={"fill": c.fill, "expected": exp_fill, "cell_type": ct})
                exp_color = _norm_font_color(st.font_color)
                if _norm_font_color(c.font_color) != exp_color:
                    ctx.add("S-FONTCOLOR", "warning", f"font colour {_norm_font_color(c.font_color)} on a {ct} cell; "
                            f"the dictionary gives {exp_color}", fix, sheet=sheet.name, cell=c.coord,
                            data={"font_color": c.font_color, "expected": exp_color, "cell_type": ct})
                if ct in BOLD_TYPES and bool(c.bold) != bool(st.bold):
                    ctx.add("S-BOLD", "warning", f"{ct} cell is {'bold' if c.bold else 'not bold'}; the dictionary "
                            f"gives {'bold' if st.bold else 'not bold'}", fix, sheet=sheet.name, cell=c.coord,
                            data={"cell_type": ct})
                continue
            # without a cell type: the dictionary as a whole (inferred mode)
            if size is None or float(size) not in [float(s) for s in sizes]:
                ctx.add("S-SIZE", "warning", f"size {_size_txt(size)}; the dictionary uses {d.sizes.get('dense', 8)} "
                        f"(dense sheets) or {d.sizes.get('spaced', 9)} (sheets with white space)", fix,
                        sheet=sheet.name, cell=c.coord, data={"size": size})
            f = _norm_fill(c.fill)
            if f is not None and f not in fills:
                ctx.add("S-FILL", "warning", f"fill {f} is not a fill of the style dictionary (fills: {fill_txt})", fix,
                        sheet=sheet.name, cell=c.coord, data={"fill": f})
            fc = _norm_font_color(c.font_color)
            if fc not in colors:
                ctx.add("S-FONTCOLOR", "warning", f"font colour {fc} is not a font colour of the style dictionary "
                        f"(colours: {color_txt})", fix, sheet=sheet.name, cell=c.coord, data={"font_color": fc})
    if ctx.manifest and ctx.want("S-NUMFMT"):
        _numfmt_rules(ctx)


def _size_txt(size: Any) -> str:
    if size is None:
        return "(not set)"
    f = float(size)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def _numfmt_rules(ctx: _Ctx) -> None:
    r"""S-NUMFMT (manifest): the cells whose cell type fixes a format, the raw columns (the format key build recorded,
    else the column type's default), the scalar inputs (their format key) and the header cells that show an item of
    an input list. Block cells and helper cells are not compared: the manifest records no format key for a block
    cell, and a helper cell has the cell type calc, whose format is the item's.

    v6 (r5 verdict A-aera-8, A-aera-13): formats compare with styles.number_formats_equal, the comparison theme
    uses. Excel rewrites formats when it saves (measured on this PC: #,##0_);(#,##0);"-"_) is stored as
    #,##0_);\(#,##0\);"-"_), and yyyy-mm-dd as yyyy\-mm\-dd), which stays equal; a percent sign in quotes or after
    a backslash shows the sign without multiplying by 100, so 0.0"%" differs from 0.0%."""
    from . import manifest as mmod
    from . import styles
    d = ctx.dictionary
    m = ctx.manifest or {}
    expected: dict[tuple[str, str], tuple[str, str]] = {}      # (sheet, coord) -> (format, why)
    for sheet in ctx.worksheets():
        ranges = mmod.cell_type_ranges(m, sheet.name)
        if not ranges:
            continue
        for c in sheet.iter_cells():
            ct = mmod.cell_type_at(ranges, c.row, c.col)
            if ct is None:
                continue
            try:
                st = d.style(ct)
            except common.RotmError:
                continue
            if st.number_format != "item":
                expected[(sheet.name, c.coord)] = (st.number_format, f"{ct} cells ({st.number_format_key})")
    for entry in m.get("sheets", []):
        if entry.get("type") != "raw" or not entry.get("types"):
            continue
        sheet = ctx.wb.sheet(entry["name"])
        if sheet is None:
            continue
        by_col: dict[int, tuple[str, str]] = {}
        built = entry.get("formats") if isinstance(entry.get("formats"), list) else []
        for i, t in enumerate(entry["types"]):
            try:
                if i < len(built) and isinstance(built[i], str) and built[i]:
                    # the format build gave the column (the contract's "format", or two decimals for a number
                    # column whose landed values hold a fraction): recorded in the manifest (live test 1)
                    key = built[i]
                    by_col[i + 1] = (d.number_format(key), f"a {t} column ({key}, as built)")
                else:
                    key = d.format_for_column_type(t)
                    by_col[i + 1] = (d.number_format(key), f"a {t} column ({key})")
            except common.RotmError:
                continue
        for c in sheet.iter_cells():
            if c.row >= 2 and c.col in by_col:
                expected[(sheet.name, c.coord)] = by_col[c.col]
    spec = ctx.spec_data or {}
    for s in spec.get("sheets", []) if isinstance(spec.get("sheets"), list) else []:
        if not isinstance(s, dict) or s.get("type") != "inputs":
            continue
        for item in s.get("items", []) or []:
            if not isinstance(item, dict) or item.get("kind") not in (None, "scalar") or not item.get("format"):
                continue
            ref = (m.get("inputs") or {}).get(item.get("id"))
            if not ref:
                continue
            sname, coord = common.parse_ref(ref)
            try:
                expected[(sname or "inputs", coord.replace("$", ""))] = (d.number_format(item["format"]),
                                                                          f"input '{item['id']}' ({item['format']})")
            except common.RotmError:
                continue
    headers, also, unread = _header_formats(ctx)
    expected.update(headers)                     # these replace the row header's fixed General (HDR-FORMAT)
    for key in unread:
        expected.pop(key, None)
    if unread:
        ctx.report.note(f"S-NUMFMT: {len(unread):,} header cell(s) that show an input-list item were not compared: "
                        "the specification that names the list was not read")
    for (sname, coord), (fmt, why) in expected.items():
        sheet = ctx.wb.sheet(sname)
        cell = sheet.cell(coord) if sheet is not None else None
        if cell is None:
            continue
        have = cell.number_format or "General"
        if (sname, coord) in also and styles.number_formats_equal(have, also[(sname, coord)]):
            continue
        if not styles.number_formats_equal(have, fmt):
            ctx.add("S-NUMFMT", "warning", f"number format {cell.number_format!r}; the dictionary gives {fmt!r} for "
                    f"{why}", "rebuild the workbook with rotm.py build (or run rotm.py theme --number-formats)",
                    sheet=sname, cell=coord, data={"number_format": cell.number_format, "expected": fmt})


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")      # the pattern of build.ISO_DATE_RE


def _header_formats(ctx: _Ctx) -> tuple[dict[tuple[str, str], tuple[str, str]], dict[tuple[str, str], str],
                                        list[tuple[str, str]]]:
    """Rule HDR-FORMAT (v3 plan 5.5, J-2): the number format of each header cell that shows an item of an input
    list. Rows (`"rows": {"in": <list>}`, column B of each body row): the block rows' format, else the list item's
    format, else date when the item's value is an ISO date, else general. Value columns (`"cols": {"in": <list>}`,
    the header row over the body): the same with month for an ISO date. These are the keys build.py _dim_labels
    and _dim_headers compute for kind 'in'.

    Returns (expected, also, unread): expected maps (sheet, coord) to (format, why); also maps a cell whose key is
    text to General, which shows a text item the same way, so a header of a text list may hold either format;
    unread lists the header cells of such blocks when the specification (or the list) could not be read."""
    m = ctx.manifest or {}
    d = ctx.dictionary
    lists: dict[str, dict[str, Any]] = {}
    spec_blocks: dict[str, dict[str, Any]] = {}
    spec = ctx.spec_data if isinstance(ctx.spec_data, dict) else None
    for s in (spec or {}).get("sheets") or []:
        if not isinstance(s, dict):
            continue
        if s.get("type") == "inputs":
            for it in s.get("items") or []:
                if isinstance(it, dict) and it.get("kind") == "list" and it.get("id"):
                    lists[str(it["id"])] = it
        for b in s.get("blocks") or []:
            if isinstance(b, dict) and b.get("id"):
                spec_blocks[str(b["id"])] = b
    expected: dict[tuple[str, str], tuple[str, str]] = {}
    also: dict[tuple[str, str], str] = {}
    unread: list[tuple[str, str]] = []
    for entry in m.get("sheets", []):
        if entry.get("type") not in ("calc", "output"):
            continue
        for mb in entry.get("blocks") or []:
            places: list[tuple[str, int, str]] = []          # (coord, item index, "rows" or "cols")
            try:
                if mb.get("rows_kind") == "in" and mb.get("shape") != "items" and mb.get("labels"):
                    c1, r1, _c2, r2 = common.range_bounds(mb["labels"])
                    places += [(common.a1(c1, r), r - r1, "rows") for r in range(r1, r2 + 1)]
                if mb.get("cols_kind") == "in" and mb.get("shape") != "measures" and mb.get("header_row"):
                    b1, _r1, b2, _r2 = common.range_bounds(mb["body"])
                    places += [(common.a1(c, int(mb["header_row"])), c - b1, "cols") for c in range(b1, b2 + 1)]
            except (ValueError, TypeError):
                continue
            block = spec_blocks.get(str(mb.get("id")))
            for coord, k, axis in places:
                key = (entry["name"], coord)
                node = (block or {}).get(axis)
                item = lists.get(str(node.get("in"))) if isinstance(node, dict) else None
                values = item.get("values") if item else None
                if not isinstance(values, list) or k >= len(values):
                    unread.append(key)
                    continue
                iso = isinstance(values[k], str) and bool(_ISO_DATE_RE.match(values[k]))
                fkey = node.get("format") or item.get("format") or (
                    ("date" if axis == "rows" else "month") if iso else "general")
                try:
                    fmt = d.number_format(str(fkey))
                except common.RotmError:
                    unread.append(key)
                    continue
                expected[key] = (fmt, f"{'row' if axis == 'rows' else 'column'} headers that show an item of input "
                                      f"list '{node['in']}' ({fkey})")
                if fkey == "text":
                    also[key] = d.number_format("general")
    return expected, also, unread


# ----------------------------------------------------------------------------- workbook rules

_ROW_RE = re.compile(rb"<(?:\w+:)?row\b([^>]*)>")
_COL_RE = re.compile(rb"<(?:\w+:)?col\b([^>]*?)/?>")
_CF_RE = re.compile(rb"<(?:\w+:)?cfRule\b[^>]*\btype=\"(dataBar|iconSet)\"")
_HIDDEN_RE = re.compile(rb"\bhidden=\"(?:1|true)\"")
_OUTLINE_RE = re.compile(rb"\boutlineLevel=\"([1-9])\"")
_R_RE = re.compile(rb"\br=\"(\d+)\"")
_MIN_RE = re.compile(rb"\bmin=\"(\d+)\"")
_MAX_RE = re.compile(rb"\bmax=\"(\d+)\"")


def _sheet_part_names(z: zipfile.ZipFile) -> dict[str, str]:
    """Sheet name -> worksheet part name, from xl/workbook.xml and its relationships."""
    import xml.etree.ElementTree as ET
    names = z.namelist()
    wb_part = "xl/workbook.xml" if "xl/workbook.xml" in names else next((n for n in names if n.endswith("workbook.xml")), None)
    if wb_part is None:
        return {}
    base = wb_part.rsplit("/", 1)[0]
    rels_part = f"{base}/_rels/{wb_part.rsplit('/', 1)[1]}.rels"
    try:
        root = ET.fromstring(z.read(wb_part))
        rels = ET.fromstring(z.read(rels_part))
    except (KeyError, ET.ParseError):
        return {}
    targets = {r.get("Id"): r.get("Target") or "" for r in rels}
    out: dict[str, str] = {}
    for el in root.iter():
        if not el.tag.endswith("}sheet"):
            continue
        rid = next((v for k, v in el.attrib.items() if k.endswith("}id")), None)
        target = targets.get(rid or "")
        if not target:
            continue
        part = target.lstrip("/") if target.startswith("/") else f"{base}/{target}"
        parts: list[str] = []
        for seg in part.split("/"):
            if seg == "..":
                if parts:
                    parts.pop()
            elif seg and seg != ".":
                parts.append(seg)
        out[el.get("name") or ""] = "/".join(parts)
    return out


def _scan_sheet_xml(path: Path, notes: list[str]) -> dict[str, dict[str, Any]]:
    """Per sheet: hidden rows and columns (not grouped), and data-bar or icon-set rules."""
    out: dict[str, dict[str, Any]] = {}
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        return out
    with z:
        for name, part in _sheet_part_names(z).items():
            try:
                info = z.getinfo(part)
            except KeyError:
                continue
            if info.file_size > XML_SCAN_LIMIT:
                notes.append(f"sheet {name}: its XML ({info.file_size / 1e6:,.0f} MB) is larger than "
                             f"{XML_SCAN_LIMIT >> 20} MB, so hidden rows and columns were not looked for")
                continue
            try:
                data = z.read(part)
            except (OSError, zipfile.BadZipFile, KeyError):
                continue
            rows: list[int] = []
            for mt in _ROW_RE.finditer(data):
                attrs = mt.group(1)
                if _HIDDEN_RE.search(attrs) and not _OUTLINE_RE.search(attrs):
                    r = _R_RE.search(attrs)
                    rows.append(int(r.group(1)) if r else 0)
            cols: list[tuple[int, int]] = []
            sheet_data = data.find(b"<sheetData")
            head = data[:sheet_data] if sheet_data > 0 else data[:1 << 20]
            for mt in _COL_RE.finditer(head):
                attrs = mt.group(1)
                if _HIDDEN_RE.search(attrs) and not _OUTLINE_RE.search(attrs):
                    lo, hi = _MIN_RE.search(attrs), _MAX_RE.search(attrs)
                    if lo and hi:
                        cols.append((int(lo.group(1)), int(hi.group(1))))
            cf = sorted({m.group(1).decode() for m in _CF_RE.finditer(data)})
            out[name] = {"hidden_rows": rows, "hidden_cols": cols, "cf_kinds": cf}
    return out


def _workbook_rules(ctx: _Ctx) -> None:
    wb = ctx.wb
    if ctx.want("S-MERGED"):
        for s in ctx.worksheets():
            if s.name in ctx.raw_sheets or not s.merged:
                continue
            ctx.add("S-MERGED", "warning", f"{len(s.merged)} merged range(s) ({', '.join(s.merged[:4])}"
                    f"{'...' if len(s.merged) > 4 else ''})",
                    "unmerge them and use Center Across Selection for headings (FAST 4.02-02)", sheet=s.name,
                    cells=s.merged[:20], count=len(s.merged))
    scan: dict[str, dict[str, Any]] = {}
    if ctx.want("S-HIDDEN", "X-FEATURE"):
        scan = _scan_sheet_xml(wb.path, ctx.report.notes)
    if ctx.want("S-HIDDEN"):
        for s in wb.sheets:
            if s.state in ("hidden", "veryHidden"):
                ctx.add("S-HIDDEN", "warning", f"the sheet is {'very hidden' if s.state == 'veryHidden' else 'hidden'}",
                        "unhide it, or move what it holds to a visible sheet (FAST 2.01-08)", sheet=s.name)
            info = scan.get(s.name)
            if not info:
                continue
            rows, cols = info["hidden_rows"], info["hidden_cols"]
            if rows or cols:
                parts = []
                if rows:
                    parts.append(f"{len(rows):,} hidden row(s) ({_rows_text(rows)})")
                if cols:
                    ncols = sum(hi - lo + 1 for lo, hi in cols)
                    shown = ", ".join(f"{common.col_letter(lo)}:{common.col_letter(min(hi, 16384))}" if hi > lo
                                      else common.col_letter(lo) for lo, hi in cols[:4])
                    parts.append(f"{ncols:,} hidden column(s) ({shown})")
                ctx.add("S-HIDDEN", "warning", " and ".join(parts),
                        "unhide them, or group them (Data > Group) so a reader can see that something is folded "
                        "(guides L-99)", sheet=s.name, data={"rows": rows[:50], "columns": cols[:20]})
    if ctx.want("W-CALCMODE") and wb.calc_mode != "auto":
        ctx.add("W-CALCMODE", "warning", f"calculation mode is {wb.calc_mode}, not automatic",
                "set Formulas > Calculation Options > Automatic, and save")
    if ctx.want("W-ITERATE") and wb.iterate:
        ctx.add("W-ITERATE", "error", "iterative calculation is on, so circular references calculate without warning",
                "remove the circularity (for example compute interest on the opening balance) and turn iterative "
                "calculation off (File > Options > Formulas)")
    if ctx.want("X-FEATURE") and ctx.product != "excel":
        pkg = wb.package
        if pkg.vba:
            ctx.add("X-FEATURE", "warning", "the workbook holds VBA macros (xl/vbaProject.bin); Google Sheets does not "
                    "run them (its macros are Apps Script, researcher 2 #20)",
                    "do the work with formulas, or keep this model in Excel only (product excel)")
        nq = pkg.queries or (1 if pkg.has_power_query else 0)
        if nq:
            documented = bool(ctx.manifest) and any(s.get("landing") == "power_query"
                                                    for s in (ctx.manifest or {}).get("sheets", []))
            names = ", ".join(pkg.query_names[:5]) if pkg.query_names else "names not read"
            text = (f"{nq} Power Query quer{'y' if nq == 1 else 'ies'} ({names}): in Google Sheets the loaded tables "
                    "are static values; Connected Sheets lands the data there instead (design 7.4)")
            if documented and ctx.product == "both":
                ctx.add("X-FEATURE", "info", text, "for Google Sheets, follow references/google-sheets.md to land each "
                        "raw table with Connected Sheets", data={"queries": pkg.query_names})
            else:
                ctx.add("X-FEATURE", "warning", text, "land the raw data per the column contract (rotm.py land) and "
                        "follow references/google-sheets.md for Google Sheets, or set the product to excel",
                        data={"queries": pkg.query_names})
        for name, info in scan.items():
            if info.get("cf_kinds"):
                kinds = " and ".join("data bars" if k == "dataBar" else "icon sets" for k in info["cf_kinds"])
                ctx.add("X-FEATURE", "info", f"conditional formats with {kinds}: whether Google Sheets shows them is "
                        "not confirmed", "check the sheet after converting it to Google Sheets, or use a plain "
                        "number format", sheet=name)


# ----------------------------------------------------------------------------- the audit

FAMILIES: tuple[tuple[str, Callable[[_Ctx], None], frozenset[str]], ...] = (
    ("formula", _formula_rules, frozenset({
        "F-DEPTH", "F-LENGTH", "F-EXCEL-LIMIT", "F-LINEBREAK", "F-LITERAL", "F-NESTED-IF", "F-FUNC-REFUSED",
        "F-FUNC-FLAGGED", "F-FUNC-UNKNOWN", "F-VOLATILE", "F-SELF-SHEET", "F-EXTERNAL", "F-REF-ERROR", "F-PARSE",
        "F-ARGS", "F-ARRAY", "F-NAME", "X-STRUCTREF"})),
    ("raw-sheet", _raw_rules, frozenset({"R-FORMULA", "R-NOT-ALONE", "R-MERGED"})),
    ("manifest raw-sheet", _manifest_raw_rules, frozenset({"R-CHANGED", "R-CONTRACT", "R-TYPE", "CT-VERSION"})),
    ("helper-column", _manifest_helper_rules, frozenset({"H-INCONSISTENT", "H-GAP", "H-COVERAGE", "H-BEYOND"})),
    ("block", _manifest_block_rules, frozenset({"B-INCONSISTENT"})),
    ("inferred consistency", _inferred_consistency, frozenset({"H-INCONSISTENT", "B-INCONSISTENT",
                                                                 "H-COVERAGE", "H-GAP"})),
    ("check", _check_rules, frozenset({"C-MISSING", "C-BLOCK", "C-WIRING", "C-FAIL", "C-ALERT", "W-SELF-CHECK",
                                       "C-RECOMPUTE"})),
    ("value", _value_rules, frozenset({"V-ERROR", "V-NOT-RECALC"})),
    ("no-value", _no_value_rules, frozenset({"V-NO-VALUE"})),
    ("style", _style_rules, frozenset({"S-FONT", "S-SIZE", "S-FILL", "S-FONTCOLOR", "S-BOLD", "S-NUMFMT"})),
    ("workbook", _workbook_rules, frozenset({"S-MERGED", "S-HIDDEN", "W-CALCMODE", "W-ITERATE", "X-FEATURE"})),
)


def _parse_rules(rules: Sequence[str] | str | None) -> set[str] | None:
    if rules is None:
        return None
    items = [r.strip().upper() for r in (rules.split(",") if isinstance(rules, str) else rules) if r and r.strip()]
    if not items:
        return None
    for r in items:
        if r not in RULES:
            near = difflib.get_close_matches(r, list(RULES), n=1)
            raise common.Refusal(f"--rules: unknown rule '{r}'" + (f"; did you mean '{near[0]}'?" if near else ""),
                                 fix="use rule ids from the audit's rule list (SKILL.md, references/checks.md), "
                                     "separated by commas")
    return set(items)


def audit_workbook(path: str | Path, *, strict: bool = False, depth_limit: int | None = None,
                   length_limit: int | None = None, dictionary: Any = None, spec: str | Path | None = None,
                   rules: Sequence[str] | None = None, raw_sheets: Sequence[str] | None = None,
                   product: str | None = None, record: bool = True, use: Sequence[str] = ()) -> Any:
    """Every rule in RULES (or only `rules`) on one workbook. Returns a finished common.Report (exit code set);
    records the step in reports/status.json when the workbook sits in a model folder and `record` is true.
    `use`: named looks of the dictionary (--use), applied as theme applies them.
    Raises common.Refusal for bad input (no such file, unknown rule, bad --spec, --dictionary or --use)."""
    from . import modelfolder
    from . import workbook as wbmod
    target = _resolve_target(path)
    selected = _parse_rules(rules)
    report = common.Report(command="audit", target=common.norm_path(target), strict=strict)
    model_root = modelfolder.find_model_root(target)
    try:
        wb = wbmod.open_workbook(target)
    except common.Refusal as exc:
        report.error("A-UNREADABLE", exc.message, exc.fix or "open it in Excel and save it as .xlsx",
                     data={"reason": exc.line()})
        report.note("the workbook could not be read, so no other rule ran")
        report.data = {"mode": "unreadable", "rules_run": []}
        report.finish(strict)
        _record(report, model_root, target, None, record)
        return report
    for w in wb.warnings:
        report.note(f"reader: {w}")
    if wb.truncated:
        report.warning("A-UNREADABLE", f"only the first {wb.cell_count:,} non-empty cells were read; later cells "
                       "and sheets were not audited", "split the workbook, or audit a copy with fewer sheets")
    manifest = wb.manifest
    spec_data = _spec_json(spec, wb.path, manifest, report)
    dct = _load_dictionary(dictionary, model_root, (spec_data or {}).get("dictionary") if spec_data else None, use)
    settings = _settings({"depth_limit": depth_limit, "length_limit": length_limit}, spec_data, manifest, dct)
    prod = _product(product, spec_data, manifest, model_root)
    raw = _raw_sheets(wb, manifest, raw_sheets)
    ctx = _Ctx(path=wb.path, wb=wb, report=report, settings=settings, product=prod, dictionary=dct,
               manifest=manifest, model_root=model_root, raw_sheets=raw, selected=selected, spec_data=spec_data)
    if manifest:
        ctx.manifest_sheets = {s["name"] for s in manifest.get("sheets", [])}
    else:
        report.note("inferred: the workbook has no manifest, so raw sheets, helper columns and checks were inferred "
                    "and styles were compared with the dictionary as a whole")
    run: list[str] = []
    for name, fn, ids in FAMILIES:
        if selected is not None and not (ids & selected):
            continue
        try:
            fn(ctx)
            run.append(name)
        except Exception as exc:  # noqa: BLE001 - one family failing must not stop the audit (F06)
            saved = common.save_traceback(exc)
            where = f"; details in {saved.as_posix()}" if saved else ""
            report.warning("A-RULE-FAILED", f"the {name} rules could not run on this workbook ({type(exc).__name__}: "
                           f"{_short(exc, 160)}){where}; the other rules ran",
                           "check those rules by hand on this workbook, or send last_error.txt to whoever maintains "
                           "the plugin", data={"family": name, "rules": sorted(ids)})
    ctx.flush()
    report.data = {
        "mode": "manifest" if manifest else "inferred",
        "settings": {k: settings[k] for k in ("depth_limit", "length_limit", "allowed_literals", "allow_functions")},
        "product": prod,
        "dictionary": {"font": dct.font_name, "sizes": dct.sizes, "applied": list(dct.applied)},
        "raw_sheets": raw,
        "sheets": len(wb.sheets),
        "cells": wb.cell_count,
        "formula_cells": sum(1 for s in wb.sheets for c in s.cells.values() if c.is_formula),
        "has_cached_values": wb.has_cached_values,
        "rules_run": run,
        "selected_rules": sorted(selected) if selected else None,
        "sha256": wb.sha256,
    }
    report.finish(strict)
    _record(report, model_root, target, wb.sha256, record)
    return report


def _record(report: common.Report, model_root: Path | None, target: Path, sha: str | None, record: bool) -> None:
    """Record the audit in reports/status.json and reports/audit.json of the model folder (design 4.2).
    An audit of some rules only (--rules) is not recorded: the finish-check hook reads the record as a full audit."""
    if not record or model_root is None:
        return
    if report.data.get("selected_rules"):
        report.note("an audit of selected rules (--rules) is not recorded in reports/status.json")
        return
    from . import modelfolder
    c = report.counts()
    entry = {"at": report.at, "exit_code": report.exit_code, "strict": report.strict, "sha256": sha,
             "errors": c["errors"], "warnings": c["warnings"], "info": c["info"]}
    try:
        modelfolder.record_step(model_root, str(target), "audit", entry)
        common.write_json(Path(model_root) / "reports" / "audit.json", report.to_dict())
    except Exception as exc:  # noqa: BLE001 - the audit result stands even when the record cannot be written
        report.note(f"the audit was not recorded in {model_root.as_posix()}/reports ({type(exc).__name__}: "
                    f"{_short(exc, 120)})")


def add_audit_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("workbook", help="the .xlsx or .xlsm file (or a model folder: its workbook is audited)")
    p.add_argument("--strict", action="store_true", help="exit 1 on any warning (S3)")
    p.add_argument("--json", metavar="FILE", help="write the report as JSON ('-' = stdout; console lines go to stderr)")
    p.add_argument("--limit", type=int, help="nesting depth limit (default 2: the dictionary's rules.depth_limit)")
    p.add_argument("--max-length", type=int, help="formula length limit in characters (default 100)")
    p.add_argument("--dictionary", help="project override file (style_overrides.json format)")
    p.add_argument("--use", action="append", default=[], metavar="NAME",
                   help="named look of the dictionary, as theme takes it (e.g. input_blue_font); may be repeated")
    p.add_argument("--spec", help="the model specification (otherwise found through the manifest)")
    p.add_argument("--rules", help="comma-separated rule ids to run (default all)")
    p.add_argument("--raw-sheets", help="comma-separated raw sheet names (workbooks without a manifest)")
    p.add_argument("--product", choices=("both", "excel", "sheets"), help="function profile (default: manifest or both)")
    p.add_argument("--quiet", action="store_true", help="print only the summary line")
    p.add_argument("--no-record", action="store_true",
                   help="do not write reports/status.json and reports/audit.json in the model folder")


def run_audit(args: argparse.Namespace) -> int:
    raw = [s.strip() for s in args.raw_sheets.split(",") if s.strip()] if getattr(args, "raw_sheets", None) else None
    report = audit_workbook(args.workbook, strict=bool(args.strict), depth_limit=args.limit,
                            length_limit=args.max_length, dictionary=args.dictionary, spec=args.spec,
                            rules=args.rules, raw_sheets=raw, product=args.product,
                            record=not getattr(args, "no_record", False), use=getattr(args, "use", None) or ())
    return report.emit(args.json, quiet=bool(args.quiet))
