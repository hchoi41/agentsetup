"""The one formula engine: nesting depth, length, literals, references, relative form (design section 8).

Owner: CORE-F. build, audit, split, compat, trace and diff use only this module for these measures,
so the counting rule is the same everywhere (F05).

Counting rule (design 8.1): depth = the largest number of function calls open at the same time,
counted on openpyxl Tokenizer tokens of type FUNC subtype OPEN (+1) and FUNC CLOSE (-1).
Array braces (type ARRAY), grouping parentheses (type PAREN), operators, references, literals,
LET variable names and the _xlfn. prefix do not count. Every function counts; there is no exempt list.
openpyxl is imported inside functions only.

The text that is measured (all offsets index it): `Analysis.formula`, the display form of the formula, which is
what the formula bar shows. It is the input with a leading "=" added when missing and the file prefixes removed
by strip_xlfn (`_xlfn.`, `_xlfn._xlws.`, `_xlws.`, `_xlpm.`; `_xlfn.ANCHORARRAY(A1)` shown as `A1#`,
`_xlfn.SINGLE(<argument>)` as `@<argument>`, for example `_xlfn.SINGLE(INDEX(A1:A3,MATCH(B1,A1:A3,0)))` as
`@INDEX(A1:A3,MATCH(B1,A1:A3,0))`, and as `@(<argument>)` when the argument is an expression). `@` is an
operator, so SINGLE is not counted in the depth (v3, A-aera-17). to_file_formula() turns a display form back
into the text Excel stores.

Tokenizer details measured on openpyxl 3.1.5 on the build PC (design 22): array braces are ARRAY tokens,
grouping parentheses PAREN tokens, `Table1[@Amount]` and `[1]Sheet1!B2` single RANGE operands, `--` two prefix
operators, `A1:INDEX(` one FUNC OPEN token. The tokenizer collapses a run of spaces into one token and does not
accept the spill operator `A1#`; this module aligns tokens to the text itself and masks `#`, tabs and line
breaks before tokenizing (same length, so the offsets stay exact). A formula the tokenizer cannot read at all
gets `ok=False` and a simpler character scan for its calls (rule F-PARSE).

Argument counts (v6, r5 verdict A-aera-6; rule F-ARGS, error): ARG_COUNTS holds the number of arguments each of 29
common functions takes, from Microsoft's function reference (support.microsoft.com, one page per function). The
count of a call is len(Call.args): the arguments between its parentheses, split only at its own top-level commas
(not inside a nested call, an array constant, a quoted text or a range union in parentheses); NAME() has 0 and an
empty argument counts (IF(A1,,1) has 3). A function outside ARG_COUNTS is not counted. A formula that is
incomplete or that the tokenizer cannot read is not counted (F-PARSE reports it).
"""
from __future__ import annotations

import argparse
import functools
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from . import functions as _fn

DEPTH_LIMIT_DEFAULT = 2          # must equal rules.depth_limit in references/style_dictionary.json (tested)
LENGTH_LIMIT_DEFAULT = 100       # characters including "="; the plugin's own choice (design D6)
ALLOWED_LITERALS_DEFAULT = (0, 1)
EXCEL_MAX_LENGTH = 8192          # Excel specifications and limits (platforms B5.1, verified)
EXCEL_MAX_DEPTH = 64
LARGEST_ARGUMENT_MIN = 20        # characters: a shorter largest argument gets the general F-LENGTH fix
MESSAGE_TEXT_MAX = 100           # characters of a formula part shown in a message (data keeps the full text)
INCOMPLETE_FIX = ("complete the expression: an operand after each operator, every argument the function needs, "
                  "closed brackets and quotes")   # F-PARSE (error), v4 C-sol-3

EXCEL_MAX_COL = 16384            # XFD
EXCEL_MAX_ROW = 1048576

# Rule order inside one result (errors first, then warnings, in design section 14 order).
RULE_ORDER = ("F-EXCEL-LIMIT", "F-DEPTH", "F-NESTED-IF", "F-FUNC-REFUSED", "F-EXTERNAL", "F-REF-ERROR",
              "F-PARSE", "F-ARGS", "F-LENGTH", "F-LINEBREAK", "F-LITERAL", "F-FUNC-FLAGGED", "F-FUNC-UNKNOWN", "F-VOLATILE",
              "F-SELF-SHEET", "F-ARRAY", "F-NAME", "X-STRUCTREF")
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

# openpyxl Token type and subtype values
OPERAND, FUNC, ARRAY, PAREN, SEP = "OPERAND", "FUNC", "ARRAY", "PAREN", "SEP"
OP_PRE, OP_IN, OP_POST, WSPACE = "OPERATOR-PREFIX", "OPERATOR-INFIX", "OPERATOR-POSTFIX", "WHITE-SPACE"
OPEN, CLOSE, ARG = "OPEN", "CLOSE", "ARG"
RANGE, NUMBER, TEXT, LOGICAL, ERROR = "RANGE", "NUMBER", "TEXT", "LOGICAL", "ERROR"


@dataclass(frozen=True)
class Call:
    name: str                    # upper case, without _xlfn.
    level: int                   # 1 = outermost
    start: int                   # offset of the name in the formula text
    end: int                     # offset just after the closing parenthesis
    parent: int | None           # index of the enclosing call in Analysis.calls
    args: tuple[tuple[int, int], ...] = ()   # (start, end) of each argument (addition by CORE-F)


@dataclass(frozen=True)
class Ref:
    text: str                    # as written, e.g. "raw_sales!$D:$D"
    sheet: str | None            # unquoted sheet name, None for the formula's own sheet
    workbook: str | None         # "[1]" style external prefix, None when internal
    kind: str                    # cell | range | column | row | name | structured | error
    a1: str                      # the part after "!", e.g. "$D:$D"
    start: int
    end: int


@dataclass(frozen=True)
class Literal:
    kind: str                    # number | text | logical | error
    text: str
    value: Any                   # float for numbers (sign and % applied), str, bool
    start: int
    end: int
    function: str | None         # the function whose argument it is, None at top level
    arg_index: int | None        # 1 = the first argument of that function


@dataclass(frozen=True)
class Part:
    """An over-limit subtree to move into a helper (design 8.1)."""
    text: str                    # e.g. "MATCH(B1,C:C,0)"
    start: int
    end: int
    chain: tuple[str, ...]       # the deepest chain it sits on, outer to inner
    level: int                   # its level on that chain (limit + 1)
    remaining_depth: int         # depth of the moved part (still over the limit when > limit)


@dataclass(frozen=True)
class Issue:
    rule: str                    # F-DEPTH, F-LENGTH, F-LITERAL, F-NESTED-IF, F-FUNC-REFUSED, ... (design 14)
    severity: str                # error | warning | info
    message: str
    fix: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Token:
    """One tokenizer token with its place in Analysis.formula (addition by CORE-F, for compat and split)."""
    text: str                    # as written
    type: str                    # openpyxl Token.type: OPERAND, FUNC, ARRAY, PAREN, SEP, OPERATOR-*, WHITE-SPACE
    subtype: str                 # RANGE, NUMBER, TEXT, LOGICAL, ERROR, OPEN, CLOSE, ARG, ROW or ""
    start: int
    end: int


@dataclass(frozen=True)
class Analysis:
    formula: str                 # with the leading "="
    ok: bool                     # tokenised without error
    error: str | None
    depth: int
    chain: tuple[str, ...]       # function names on the first deepest chain, outer to inner
    length: int                  # len() of the text without _xlfn. prefixes, including "="
    calls: tuple[Call, ...]
    functions: tuple[str, ...]   # distinct names, first-seen order
    refs: tuple[Ref, ...]
    literals: tuple[Literal, ...]
    has_line_break: bool
    is_pure_link: bool           # one reference and nothing else (a leading "+" allowed)
    has_array: bool              # array constant present
    # v4 (C-sol-3): the first place where the formula is not complete, as (reason, start, end) with the offsets of
    # F-DEPTH (in `formula`, so the first character after "=" is 1); None for a complete formula. Rule F-PARSE
    # (error). Only the forms Excel 16 refuses to open are recorded (tests/data/formula/incomplete_examples.json).
    incomplete: tuple[str, int, int] | None = None

    def call_text(self, index: int) -> str:
        c = self.calls[index]
        return self.formula[c.start:c.end]

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula": self.formula, "ok": self.ok, "error": self.error, "depth": self.depth,
            "chain": list(self.chain), "length": self.length,
            "calls": [{"name": c.name, "level": c.level, "start": c.start, "end": c.end, "parent": c.parent,
                       "text": self.formula[c.start:c.end]} for c in self.calls],
            "functions": list(self.functions),
            "refs": [{"text": r.text, "sheet": r.sheet, "workbook": r.workbook, "kind": r.kind, "a1": r.a1,
                      "start": r.start, "end": r.end} for r in self.refs],
            "literals": [{"kind": x.kind, "text": x.text, "value": x.value, "function": x.function,
                          "arg_index": x.arg_index, "start": x.start, "end": x.end} for x in self.literals],
            "has_line_break": self.has_line_break, "is_pure_link": self.is_pure_link, "has_array": self.has_array,
            "incomplete": (None if self.incomplete is None else
                           {"reason": self.incomplete[0], "start": self.incomplete[1], "end": self.incomplete[2]}),
        }


# ----------------------------------------------------------------------------- display form and file form

_STRING_RE = re.compile(r'"(?:[^"]|"")*"?')
_PREFIX_RE = re.compile(r"_xlfn\._xlws\.|_xlfn\.|_xlws\.|_xlpm\.", re.I)
_SIMPLE_REF = (r"(?:'(?:[^']|'')+'!|[A-Za-z0-9_.\[\]]+!)?\$?[A-Za-z]{1,3}\$?\d+"
               r"(?::\$?[A-Za-z]{1,3}\$?\d+)?")
# Whitespace and line breaks inside the parentheses stay in the text, outside the reference (seen in a real
# file: "_xlfn.ANCHORARRAY(\nmultidimensional!$C$6)").
_ANCHOR_RE = re.compile(r"_xlfn\.ANCHORARRAY\((\s*)(" + _SIMPLE_REF + r")(\s*)\)", re.I)
_SIMPLE_REF_FULL = re.compile(_SIMPLE_REF + r"$")
_SINGLE_OPEN = re.compile(r"(?<![A-Za-z0-9_.])_xlfn\.SINGLE\(", re.I)
_CALL_START = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*\(")


def _segments(formula: str) -> list[tuple[bool, str]]:
    """Split into (is_text_literal, text) pieces; text literals are the double-quoted strings."""
    out: list[tuple[bool, str]] = []
    pos = 0
    for m in _STRING_RE.finditer(formula):
        if m.start() > pos:
            out.append((False, formula[pos:m.start()]))
        out.append((True, m.group(0)))
        pos = m.end()
    if pos < len(formula):
        out.append((False, formula[pos:]))
    return out


def _skip_quoted(f: str, i: int) -> int:
    """Offset just after the text literal or quoted sheet name that starts at f[i] (a doubled quote stays inside)."""
    q, j, n = f[i], i + 1, len(f)
    while j < n:
        if f[j] == q:
            if j + 1 < n and f[j + 1] == q:
                j += 2
                continue
            return j + 1
        j += 1
    return n


def _closing(f: str, open_at: int) -> int | None:
    """Offset of the parenthesis that closes the one at f[open_at]; text literals, quoted sheet names and
    brackets are skipped. None when it is not closed."""
    level, square, i, n = 0, 0, open_at, len(f)
    while i < n:
        c = f[i]
        if c in "\"'" and not square:
            i = _skip_quoted(f, i)
            continue
        if c == "[":
            square += 1
        elif c == "]" and square:
            square -= 1
        elif not square and c in "({":
            level += 1
        elif not square and c in ")}":
            level -= 1
            if level == 0:
                return i
        i += 1
    return None


def _one_operand(core: str) -> bool:
    """True when `core` (no outer spaces) is one A1 reference (_SIMPLE_REF) or one function call."""
    if _SIMPLE_REF_FULL.match(core):
        return True
    m = _CALL_START.match(core)
    return bool(m) and _closing(core, m.end() - 1) == len(core) - 1


def _show_single(f: str) -> str:
    """`_xlfn.SINGLE(<argument>)` (the file form of the @ operator; v3, A-aera-17) as `@<argument>` when the
    argument is one reference or one call, else as `@(<argument>)`. Spaces and line breaks around a reference
    or a call stay outside it. Text literals are not changed; a SINGLE inside the argument is shown too."""
    out: list[str] = []
    i, n, square = 0, len(f), 0
    while i < n:
        c = f[i]
        if square:
            square += {"[": 1, "]": -1}.get(c, 0)
            out.append(c)
            i += 1
            continue
        if c == "[":
            square = 1
            out.append(c)
            i += 1
            continue
        if c in "\"'":
            j = _skip_quoted(f, i)
            out.append(f[i:j])
            i = j
            continue
        m = _SINGLE_OPEN.match(f, i) if c == "_" else None
        if m:
            close = _closing(f, m.end() - 1)
            if close is not None:
                arg = _show_single(f[m.end():close])
                core = arg.strip()
                if core and _one_operand(core):
                    lead = arg[:len(arg) - len(arg.lstrip())]
                    tail = arg[len(arg.rstrip()):]
                    out.append(lead + "@" + core + tail)
                else:
                    out.append("@(" + arg + ")")
                i = close + 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def strip_xlfn(formula: str) -> str:
    """Remove _xlfn. and _xlws. prefixes (what the formula bar shows).

    Also removes `_xlpm.` (LET and LAMBDA names) and shows `_xlfn.ANCHORARRAY(<cell>)` as `<cell>#` and
    `_xlfn.SINGLE(<argument>)` as `@<argument>` (`@(<argument>)` when the argument is more than one reference
    or one call: an expression, a name, a structured or an external reference). Text inside double quotes is
    not changed."""
    if not formula or "_xl" not in formula.lower():
        return formula
    if "single(" in formula.lower():
        formula = _show_single(formula)
    out = []
    for is_text, seg in _segments(formula):
        if not is_text:
            seg = _ANCHOR_RE.sub(lambda m: m.group(1) + m.group(2) + "#" + m.group(3), seg)
            seg = _PREFIX_RE.sub("", seg)
        out.append(seg)
    return "".join(out)


def display_form(formula: str) -> str:
    """The text analyze() measures: a leading '=' added when missing, file prefixes removed."""
    if hasattr(formula, "text") and not isinstance(formula, str):   # openpyxl ArrayFormula
        formula = formula.text  # type: ignore[union-attr]
    if not isinstance(formula, str):
        raise TypeError(f"a formula must be text, got {type(formula).__name__}")
    text = formula if formula.startswith("=") else "=" + formula
    return strip_xlfn(text)


# ----------------------------------------------------------------------------- tokenizing

_NUMBER_RE = re.compile(r"^(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_IDENT_RE = re.compile(r"^[A-Za-z_\\][A-Za-z0-9_.\\]*$")
_SPILL_PREV = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_$.")


def _mask(f: str) -> str:
    """Same-length copy the openpyxl tokenizer can read: tabs and line breaks as spaces; the spill operator '#'
    after a reference as '_' (outside text literals, quoted sheet names and brackets)."""
    chars = list(f)
    i, n = 1, len(f)
    bracket = 0
    while i < n:
        c = f[i]
        if c in "\t\r\n":
            chars[i] = " "
        elif bracket == 0 and c in "\"'":
            j = i + 1
            while j < n:
                if f[j] == c:
                    if j + 1 < n and f[j + 1] == c:
                        j += 2
                        continue
                    break
                if f[j] in "\t\r\n":
                    chars[j] = " "
                j += 1
            i = j + 1
            continue
        elif c == "[":
            bracket += 1
        elif c == "]" and bracket:
            bracket -= 1
        elif c == "#" and bracket == 0 and i > 1 and f[i - 1] in _SPILL_PREV:
            chars[i] = "_"
        i += 1
    return "".join(chars)


class _AlignError(Exception):
    pass


def _align(f: str, masked: str, items: Iterable[Any]) -> list[Token]:
    """Give each openpyxl token its offsets in the text (the tokenizer drops them and collapses spaces)."""
    out: list[Token] = []
    pos = 1
    n = len(masked)
    for t in items:
        v = t.value
        if t.type == WSPACE:
            if pos < n and masked[pos] == " ":
                end = pos
                while end < n and masked[end] == " ":
                    end += 1
                start = pos
            else:
                j = masked.find(" ", pos)
                if j < 0:
                    raise _AlignError(f"whitespace token not found after offset {pos}")
                start = end = j
                while end < n and masked[end] == " ":
                    end += 1
        else:
            if masked.startswith(v, pos):
                start = pos
            else:
                start = masked.find(v, pos)
                if start < 0:
                    raise _AlignError(f"token {v!r} not found after offset {pos}")
            end = start + len(v)
        out.append(Token(f[start:end], t.type, t.subtype or "", start, end))
        pos = end
    return out


@dataclass
class _Parsed:
    formula: str
    tokens: tuple[Token, ...] | None
    error: str | None
    calls: list[dict[str, Any]]
    var_tokens: frozenset[int]
    prefix_refs: list[tuple[str, int, int]]          # range prefixes of FUNC tokens: (text, start, end)
    at_calls: dict[int, int]                         # call index -> offset of its '@' prefix
    at_groups: list[tuple[int, int]] = field(default_factory=list)   # '@(' groups: (offset of '@', end of '(')


def _openpyxl_tokens(masked: str) -> list[Any]:
    try:
        from openpyxl.formula.tokenizer import Tokenizer
    except ImportError as exc:  # pragma: no cover - openpyxl is a requirement of the plugin
        from .common import EnvUnavailable
        raise EnvUnavailable("openpyxl is not installed, so formulas cannot be read",
                             fix="install openpyxl for this Python, or run with the Python that has it") from exc
    return Tokenizer(masked).items


def _innermost_call(stack: list[tuple[str, int]]) -> int | None:
    for kind, idx in reversed(stack):
        if kind == "f":
            return idx
    return None


def _parse_tokens(f: str, tokens: list[Token]) -> _Parsed:
    calls: list[dict[str, Any]] = []
    stack: list[tuple[str, int]] = []
    prefix_refs: list[tuple[str, int, int]] = []
    at_calls: dict[int, int] = {}
    at_groups: list[tuple[int, int]] = []
    unbalanced = 0
    for ti, tk in enumerate(tokens):
        if tk.type == FUNC and tk.subtype == OPEN:
            body = tk.text[:-1]
            k = max(body.rfind(":"), body.rfind("@"), body.rfind("!"))
            name_raw = body[k + 1:]
            pre = body[:k + 1]
            if not name_raw.strip():
                # '@(' (the tokenizer reads it as a call without a name): the @ operator before a grouping
                # parenthesis, the display form of _xlfn.SINGLE(<expression>) (v3, A-aera-17). Not a call.
                if pre.endswith("@"):
                    at_groups.append((tk.start + len(pre) - 1, tk.end))
                stack.append(("g", -1))
                continue
            if pre.endswith(":") and len(pre) > 1:
                ref_text = pre[:-1]
                lead = 1 if ref_text.startswith("@") else 0
                prefix_refs.append((ref_text[lead:], tk.start + lead, tk.start + len(ref_text)))
            idx = len(calls)
            if "@" in pre:
                at_calls[idx] = tk.start + pre.rfind("@")
            calls.append({"name": _fn.normalize_name(name_raw), "level": 1 + sum(1 for e in stack if e[0] == "f"),
                          "start": tk.start + k + 1, "end": None, "parent": _innermost_call(stack), "args": [],
                          "arg_start": tk.end, "tok": ti})
            stack.append(("f", idx))
        elif tk.type == FUNC and tk.subtype == CLOSE:
            if stack and stack[-1][0] == "f":
                c = calls[stack.pop()[1]]
                c["args"].append((c["arg_start"], tk.start))
                c["end"] = tk.end
            elif stack and stack[-1][0] == "g":
                stack.pop()
            else:
                unbalanced += 1
        elif tk.type in (PAREN, ARRAY):
            kind = "p" if tk.type == PAREN else "a"
            if tk.subtype == OPEN:
                stack.append((kind, -1))
            elif stack and stack[-1][0] == kind:
                stack.pop()
            else:
                unbalanced += 1
        elif tk.type == SEP and tk.text == "," and stack and stack[-1][0] == "f":
            c = calls[stack[-1][1]]
            c["args"].append((c["arg_start"], tk.start))
            c["arg_start"] = tk.end
    error = None
    open_left = len(stack)
    for kind, idx in stack:
        if kind == "f":
            c = calls[idx]
            c["args"].append((c["arg_start"], len(f)))
            c["end"] = len(f)
    if open_left or unbalanced:
        parts = []
        if open_left:
            parts.append(f"{open_left} opening bracket(s) not closed")
        if unbalanced:
            parts.append(f"{unbalanced} closing bracket(s) without an opening one")
        error = "; ".join(parts)
    for c in calls:
        if len(c["args"]) == 1:
            s, e = c["args"][0]
            if not f[s:e].strip():
                c["args"] = []
    return _Parsed(f, tuple(tokens), error, calls, _let_vars(f, tokens, calls), prefix_refs, at_calls, at_groups)


def _let_vars(f: str, tokens: list[Token], calls: list[dict[str, Any]]) -> frozenset[int]:
    """Indexes of RANGE tokens that are LET variable names or LAMBDA parameters (not references)."""
    out: set[int] = set()
    for c in calls:
        if c["name"] not in ("LET", "LAMBDA") or not c["args"]:
            continue
        args = c["args"]
        if c["name"] == "LET":
            positions = [i for i in range(0, len(args) - 1, 2)]
        else:
            positions = list(range(len(args) - 1))
        names: set[str] = set()
        for i in positions:
            s, e = args[i]
            inside = [t for t in tokens if t.start >= s and t.end <= e and t.type != WSPACE]
            if len(inside) == 1 and inside[0].type == OPERAND and _IDENT_RE.match(inside[0].text):
                names.add(inside[0].text.upper())
        if not names:
            continue
        for ti, t in enumerate(tokens):
            if (t.type == OPERAND and t.subtype in (RANGE, NUMBER) and c["start"] <= t.start < c["end"]
                    and t.text.upper() in names):
                out.add(ti)
    return frozenset(out)


def _scan_calls(f: str) -> list[dict[str, Any]]:
    """Fallback when the tokenizer fails: function calls from a character scan (text literals, quoted names and
    brackets skipped; an identifier directly before '(' is a call)."""
    calls: list[dict[str, Any]] = []
    stack: list[int] = []     # call index, -1 for a grouping parenthesis or a brace
    i, n = 1, len(f)
    while i < n:
        c = f[i]
        if c in "\"'":
            j = i + 1
            while j < n:
                if f[j] == c:
                    if j + 1 < n and f[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            i = j + 1
            continue
        if c == "[":
            depth, j = 1, i + 1
            while j < n and depth:
                depth += {"[": 1, "]": -1}.get(f[j], 0)
                j += 1
            i = j
            continue
        if c == "(":
            j = i
            while j > 1 and (f[j - 1].isalnum() or f[j - 1] in "_."):
                j -= 1
            name = f[j:i]
            if name and (name[0].isalpha() or name[0] == "_"):
                parent = next((k for k in reversed(stack) if k >= 0), None)
                calls.append({"name": _fn.normalize_name(name), "level": 1 + sum(1 for k in stack if k >= 0),
                              "start": j, "end": None, "parent": parent, "args": [], "arg_start": i + 1})
                stack.append(len(calls) - 1)
            else:
                stack.append(-1)
        elif c == "{":
            stack.append(-1)
        elif c in ")}" and stack:
            k = stack.pop()
            if k >= 0:
                calls[k]["end"] = i + 1
        i += 1
    for c in calls:
        if c["end"] is None:
            c["end"] = n
    return calls


# ----------------------------------------------------------------------------- completeness (v4, C-sol-3)
#
# Measured on Excel 16 on the build PC, 2026-11-25 (tests/data/formula/incomplete_examples.json): Excel does not
# open a workbook that holds a formula with an operator that has no operand after it, two operators in a row
# where the second is not a sign, an operand missing at the start or after an opening bracket, unbalanced
# brackets or a text in quotes that is not closed. Excel opens every empty function argument that was measured
# (=ROUND(A1,), =ROUND(,1), =MATCH(A1,,0), =SUM(,), =IF(,1,2), =INDEX(,1)), so an empty argument is not refused.

_OPERATOR_TYPES = (OP_PRE, OP_IN, OP_POST)


def _bracket_problem(f: str) -> tuple[str, int, int] | None:
    """Unbalanced round brackets or braces, counted outside text literals, quoted sheet names and square
    brackets: (reason, start, end) at the first closing bracket without an opening one, else at the first
    opening bracket that is not closed."""
    opens: list[int] = []
    n_open = n_close = 0
    stray: int | None = None
    i, n, square = 1, len(f), 0
    while i < n:
        c = f[i]
        if square:
            square += {"[": 1, "]": -1}.get(c, 0)
        elif c == "[":
            square = 1
        elif c in "\"'":
            i = _skip_quoted(f, i)
            continue
        elif c in "({":
            n_open += 1
            opens.append(i)
        elif c in ")}":
            n_close += 1
            if opens:
                opens.pop()
            elif stray is None:
                stray = i
        i += 1
    if stray is None and not opens:
        return None
    at = stray if stray is not None else opens[0]
    return f"{n_open} opening and {n_close} closing brackets", at, at + 1


def _quote_problem(f: str) -> tuple[str, int, int] | None:
    """A text in double quotes that is not closed (quoted sheet names and square brackets skipped)."""
    i, n, square = 1, len(f), 0
    while i < n:
        c = f[i]
        if square:
            square += {"[": 1, "]": -1}.get(c, 0)
        elif c == "[":
            square = 1
        elif c in "\"'":
            j = i + 1
            while j < n:
                if f[j] == c:
                    if j + 1 < n and f[j + 1] == c:      # a doubled quote stays inside
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:                                   # no closing quote
                return ("a text in quotes is not closed", i, n) if c == '"' else None
            i = j + 1
            continue
        i += 1
    return None


def _operator_problem(tokens: Sequence[Token]) -> tuple[str, int, int] | None:
    """The first operator without an operand after it, two operators in a row where the second is not a sign,
    or an operand missing before an operator (at the start, after an opening bracket or after a comma)."""
    expect = True                                 # an operand is expected next
    prev: Token | None = None                     # the previous token that is not white space
    for tk in tokens:
        if tk.type == WSPACE:
            continue
        t, sub = tk.type, tk.subtype
        if t == OPERAND:
            expect = False
        elif t in (FUNC, PAREN, ARRAY) and sub == OPEN:
            expect = True
        elif t in (FUNC, PAREN, ARRAY) and sub == CLOSE:
            if expect and prev is not None and prev.type in _OPERATOR_TYPES:
                return f"the operator '{prev.text}' has no operand after it", prev.start, prev.end
            if expect and prev is not None and prev.type == PAREN and prev.subtype == OPEN:
                return f"an operand is missing before '{tk.text}'", tk.start, tk.end
            expect = False
        elif t == SEP:
            if expect and prev is not None and prev.type in _OPERATOR_TYPES:
                return f"the operator '{prev.text}' has no operand after it", prev.start, prev.end
            expect = True                         # an empty argument is accepted (measured, see above)
        elif t == OP_PRE:
            expect = True
        elif t in (OP_IN, OP_POST):
            if expect:
                if prev is not None and prev.type in _OPERATOR_TYPES:
                    return f"two operators in a row ('{prev.text}{tk.text}')", prev.start, tk.end
                return f"an operand is missing before '{tk.text}'", tk.start, tk.end
            expect = t == OP_IN
        prev = tk
    if expect and prev is not None and prev.type in _OPERATOR_TYPES:
        return f"the operator '{prev.text}' has no operand after it", prev.start, prev.end
    return None


def _incomplete(f: str, tokens: Sequence[Token] | None) -> tuple[str, int, int] | None:
    """Where the formula is not complete, or None (section above). Looked for in this order: a text in quotes
    that is not closed (it holds the rest of the formula), unbalanced brackets, then the operators (only when
    the tokenizer read the formula)."""
    return (_quote_problem(f) or _bracket_problem(f)
            or (_operator_problem(tokens) if tokens is not None else None))


@functools.lru_cache(maxsize=4096)
def _parse(f: str) -> _Parsed:
    masked = _mask(f)
    try:
        tokens = _align(f, masked, _openpyxl_tokens(masked))
    except Exception as exc:  # noqa: BLE001 - TokenizerError, IndexError and alignment failures alike
        if type(exc).__name__ == "EnvUnavailable":
            raise
        msg = str(exc).replace(masked, f) if masked != f else str(exc)
        return _Parsed(f, None, f"the tokenizer stopped: {type(exc).__name__}: {msg}", _scan_calls(f),
                       frozenset(), [], {})
    return _parse_tokens(f, tokens)


def tokenize(formula: str) -> list[Token]:
    """Tokens of the display form with their offsets (addition by CORE-F). Raises ValueError when the tokenizer
    cannot read the formula."""
    p = _parse(display_form(formula))
    if p.tokens is None:
        raise ValueError(p.error or "the formula could not be tokenized")
    return list(p.tokens)


# ----------------------------------------------------------------------------- references

_CELL_PART = re.compile(r"^(\$?)([A-Za-z]{1,3})(\$?)(\d+)$")
_COL_PART = re.compile(r"^(\$?)([A-Za-z]{1,3})$")
_ROW_PART = re.compile(r"^(\$?)(\d+)$")


def _col_number(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def _cell_ok(m: re.Match[str]) -> bool:
    return _col_number(m.group(2)) <= EXCEL_MAX_COL and 1 <= int(m.group(4)) <= EXCEL_MAX_ROW


def _first_bang(text: str) -> int:
    """Index of the first '!' outside quotes and brackets, -1 when none."""
    quote = False
    bracket = 0
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                quote = False
        elif c == "'":
            quote = True
        elif c == "[":
            bracket += 1
        elif c == "]" and bracket:
            bracket -= 1
        elif c == "!" and bracket == 0:
            return i
        i += 1
    return -1


def _split_colons(text: str) -> list[str]:
    """Split a1 text on ':' outside quotes."""
    parts, cur, quote = [], [], False
    for c in text:
        if c == "'":
            quote = not quote
        if c == ":" and not quote:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur))
    return parts


def _unquote(prefix: str) -> str:
    if len(prefix) >= 2 and prefix.startswith("'") and prefix.endswith("'"):
        return prefix[1:-1].replace("''", "'")
    return prefix


def _split_prefix(prefix: str) -> tuple[str | None, str | None]:
    """'[1]Sheet1' -> ('Sheet1', '[1]'); 'C:/x/[Book.xlsx]Data' -> ('Data', 'C:/x/[Book.xlsx]'); 'raw' -> ('raw',
    None); '[0]' -> (None, None) (the same workbook)."""
    p = _unquote(prefix)
    workbook = None
    if "]" in p and "[" in p:
        cut = p.rfind("]") + 1
        workbook, p = p[:cut], p[cut:]
        if workbook == "[0]":
            workbook = None
    return (p or None), workbook


def _endpoint(part: str) -> str:
    """One end of a range without its own sheet prefix ('Sheet 1'!B2 -> B2)."""
    k = _first_bang(part)
    return part[k + 1:] if k >= 0 else part


def _classify_a1(a1: str) -> str:
    if "#REF!" in a1.upper():
        return "error"
    if "[" in a1:
        return "structured"
    ends = [_endpoint(p) for p in _split_colons(a1)]
    if len(ends) == 1:
        m = _CELL_PART.match(ends[0])
        return "cell" if m and _cell_ok(m) else "name"
    kinds = []
    for e in ends:
        m = _CELL_PART.match(e)
        if m and _cell_ok(m):
            kinds.append("cell")
        elif _COL_PART.match(e) and _col_number(_COL_PART.match(e).group(2)) <= EXCEL_MAX_COL:  # type: ignore[union-attr]
            kinds.append("col")
        elif _ROW_PART.match(e) and 1 <= int(_ROW_PART.match(e).group(2)) <= EXCEL_MAX_ROW:  # type: ignore[union-attr]
            kinds.append("row")
        else:
            return "name"
    if all(k == "col" for k in kinds):
        return "column"
    if all(k == "row" for k in kinds):
        return "row"
    return "range"


def _make_ref(text: str, start: int, end: int) -> Ref:
    core = text[1:] if text.startswith("@") else text
    if core.endswith("#"):
        core = core[:-1]
    k = _first_bang(core)
    sheet = workbook = None
    if k >= 0:
        sheet, workbook = _split_prefix(core[:k])
        a1 = core[k + 1:]
    else:
        a1 = core
    kind = _classify_a1(a1)
    if text.endswith("#") and kind == "cell":
        kind = "range"
    return Ref(text=text, sheet=sheet, workbook=workbook, kind=kind, a1=a1, start=start, end=end)


# ----------------------------------------------------------------------------- analysis

def _literal_number(tokens: Sequence[Token], ti: int) -> tuple[float, int, int]:
    """Value with the unary minus signs before it and the % signs after it; start and end of that text."""
    tk = tokens[ti]
    value = float(tk.text)
    start, end = tk.start, tk.end
    j = ti - 1
    minus = 0
    while j >= 0 and tokens[j].type in (OP_PRE, WSPACE):
        if tokens[j].type == OP_PRE:
            if tokens[j].text == "-":
                minus += 1
            start = tokens[j].start
        j -= 1
    j = ti + 1
    while j < len(tokens) and tokens[j].type == OP_POST and tokens[j].text == "%":
        value /= 100.0
        end = tokens[j].end
        j += 1
    if minus % 2:
        value = -value
    return value, start, end


def _unescape_text(text: str) -> str:
    body = text[1:-1] if len(text) >= 2 and text.endswith('"') else text[1:]
    return body.replace('""', '"')


def _build(f: str, p: _Parsed) -> Analysis:
    calls = tuple(Call(name=c["name"], level=c["level"], start=c["start"], end=c["end"], parent=c["parent"],
                       args=tuple(c["args"])) for c in p.calls)
    depth = max((c.level for c in calls), default=0)
    chain: tuple[str, ...] = ()
    if depth:
        idx: int | None = next(i for i, c in enumerate(calls) if c.level == depth)
        names = []
        while idx is not None:
            names.append(calls[idx].name)
            idx = calls[idx].parent
        chain = tuple(reversed(names))
    seen: dict[str, None] = {}
    for c in calls:
        seen.setdefault(c.name, None)
    refs: list[Ref] = []
    literals: list[Literal] = []
    is_pure_link = False
    has_array = False
    has_line_break = any(("\n" in seg or "\r" in seg) for is_text, seg in _segments(f) if not is_text)
    if p.tokens is not None:
        tokens = p.tokens
        for text, s, e in p.prefix_refs:
            refs.append(_make_ref(text, s, e))
        # function context of every token, from the call spans
        for ti, tk in enumerate(tokens):
            if tk.type == ARRAY:
                has_array = True
            if tk.type != OPERAND:
                continue
            ctx_fn, ctx_arg = _context(calls, tk.start)
            st = tk.subtype
            if st == NUMBER and not _NUMBER_RE.match(tk.text):
                st = RANGE          # float() accepted it (inf, nan, 1_000) but Excel would read a name
            if st == NUMBER:
                value, s, e = _literal_number(tokens, ti)
                literals.append(Literal("number", f[s:e], value, s, e, ctx_fn, ctx_arg))
            elif st == TEXT:
                literals.append(Literal("text", tk.text, _unescape_text(tk.text), tk.start, tk.end, ctx_fn, ctx_arg))
            elif st == LOGICAL:
                literals.append(Literal("logical", tk.text, tk.text.upper() == "TRUE", tk.start, tk.end, ctx_fn,
                                        ctx_arg))
            elif st == ERROR:
                literals.append(Literal("error", tk.text, tk.text.upper(), tk.start, tk.end, ctx_fn, ctx_arg))
            elif ti not in p.var_tokens:
                refs.append(_make_ref(tk.text, tk.start, tk.end))
        refs.sort(key=lambda r: r.start)
        solid = [t for t in tokens if t.type != WSPACE]
        if solid and solid[0].type == OP_PRE and solid[0].text == "+":
            solid = solid[1:]
        if (len(solid) == 1 and solid[0].type == OPERAND and refs and len(refs) == 1
                and refs[0].start == solid[0].start and refs[0].kind != "error"):
            is_pure_link = True
    else:
        has_array = "{" in "".join(seg for is_text, seg in _segments(f) if not is_text)
    error = p.error
    return Analysis(formula=f, ok=error is None, error=error, depth=depth, chain=chain, length=len(f), calls=calls,
                    functions=tuple(seen), refs=tuple(refs), literals=tuple(literals),
                    has_line_break=has_line_break, is_pure_link=is_pure_link, has_array=has_array,
                    incomplete=_incomplete(f, p.tokens))


def _context(calls: Sequence[Call], offset: int) -> tuple[str | None, int | None]:
    """The innermost call whose argument list holds the offset, and the argument number (1 = first)."""
    best: Call | None = None
    for c in calls:
        if c.args and c.args[0][0] <= offset < c.end and (best is None or c.level > best.level):
            best = c
    if best is None:
        return None, None
    for i, (s, e) in enumerate(best.args):
        if s <= offset <= e:
            return best.name, i + 1
    return best.name, None


@functools.lru_cache(maxsize=20000)
def _analyze_display(f: str) -> Analysis:
    return _build(f, _parse(f))


def analyze(formula: str) -> Analysis:
    """depth, chain, length, calls, functions, refs, literals, flags of one formula (display form; see module doc)."""
    return _analyze_display(display_form(formula))


def depth(formula: str) -> int:
    return analyze(formula).depth


def _subtree_max(calls: Sequence[Call]) -> list[int]:
    best = [c.level for c in calls]
    for i in range(len(calls) - 1, -1, -1):
        parent = calls[i].parent
        if parent is not None and best[i] > best[parent]:
            best[parent] = best[i]
    return best


def _part(a: Analysis, i: int, best: list[int], limit: int) -> Part:
    calls = a.calls
    up = []
    j: int | None = calls[i].parent
    while j is not None:
        up.append(calls[j].name)
        j = calls[j].parent
    down = [calls[i].name]
    k = i
    while True:
        child = next((m for m in range(k + 1, len(calls)) if calls[m].parent == k and best[m] == best[k]), None)
        if child is None:
            break
        down.append(calls[child].name)
        k = child
    c = calls[i]
    return Part(text=a.formula[c.start:c.end], start=c.start, end=c.end, chain=tuple(reversed(up)) + tuple(down),
                level=c.level, remaining_depth=best[i] - limit)


def over_limit_parts(formula: str, limit: int) -> list[Part]:
    """For each distinct over-limit subtree: the call at level limit + 1 on its deepest chain."""
    if not isinstance(limit, int) or limit < 0:
        raise ValueError(f"the depth limit must be a whole number 0 or more, got {limit!r}")
    a = analyze(formula)
    if a.depth <= limit:
        return []
    best = _subtree_max(a.calls)
    return [_part(a, i, best, limit) for i, c in enumerate(a.calls) if c.level == limit + 1]


def references(formula: str) -> list[Ref]:
    return list(analyze(formula).refs)


# ----------------------------------------------------------------------------- relative form

def _host_cell(host: str) -> tuple[int, int]:
    h = host.strip()
    k = _first_bang(h)
    if k >= 0:
        h = h[k + 1:]
    m = _CELL_PART.match(h)
    if not m or not _cell_ok(m):
        raise ValueError(f"host must be a cell address such as E8 or calc!E8, got {host!r}")
    return _col_number(m.group(2)), int(m.group(4))


def _r1c1_part(part: str, hcol: int, hrow: int) -> str:
    m = _CELL_PART.match(part)
    if m:
        col, row = _col_number(m.group(2)), int(m.group(4))
        r = f"R{row}" if m.group(3) else ("R" if row == hrow else f"R[{row - hrow}]")
        c = f"C{col}" if m.group(1) else ("C" if col == hcol else f"C[{col - hcol}]")
        return r + c
    m = _COL_PART.match(part)
    if m:
        col = _col_number(m.group(2))
        return f"C{col}" if m.group(1) else ("C" if col == hcol else f"C[{col - hcol}]")
    m = _ROW_PART.match(part)
    if m:
        row = int(m.group(2))
        return f"R{row}" if m.group(1) else ("R" if row == hrow else f"R[{row - hrow}]")
    return part.upper()


def _norm_prefix(prefix: str) -> str:
    """A sheet prefix in one spelling: quoted only when needed (common.quote_sheet rule)."""
    sheet, workbook = _split_prefix(prefix)
    whole = (workbook or "") + (sheet or "")
    if not workbook and sheet and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", sheet) and not _CELL_PART.match(sheet):
        return sheet + "!"
    if workbook and not sheet and re.fullmatch(r"\[\d+\]", workbook):
        return workbook + "!"
    if workbook and sheet and re.fullmatch(r"\[\d+\]", workbook) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", sheet):
        return workbook + sheet + "!"
    return "'" + whole.replace("'", "''") + "'!"


def _ref_r1c1(text: str, hcol: int, hrow: int) -> str:
    lead = "@" if text.startswith("@") else ""
    core = text[len(lead):]
    spill = "#" if core.endswith("#") else ""
    if spill:
        core = core[:-1]
    k = _first_bang(core)
    prefix, a1 = (_norm_prefix(core[:k]), core[k + 1:]) if k >= 0 else ("", core)
    kind = _classify_a1(a1)
    if kind in ("structured", "error"):
        return lead + prefix + a1 + spill
    if kind == "name":
        return lead + prefix + a1.upper() + spill
    ends = []
    for p in _split_colons(a1):
        j = _first_bang(p)
        own = _norm_prefix(p[:j]) if j >= 0 else ""
        ends.append(own + _r1c1_part(p[j + 1:] if j >= 0 else p, hcol, hrow))
    if len(ends) > 1 and len(set(ends)) == 1 and kind in ("column", "row"):
        ends = ends[:1]
    return lead + prefix + ":".join(ends) + spill


_A1_FALLBACK = re.compile(r"(?<![A-Za-z0-9_.$])(\$?[A-Za-z]{1,3}\$?\d+)(?![A-Za-z0-9_(])")


def relative_form(formula: str, host: str) -> str:
    """Rewrite A1 references relative to the host cell: D7 in E8 -> R[-1]C[-1]; $D$7 -> R7C4; $D:$D -> C4."""
    hcol, hrow = _host_cell(host)
    f = display_form(formula)
    p = _parse(f)
    if p.tokens is None:
        out = []
        for is_text, seg in _segments(f):
            out.append(seg if is_text else _A1_FALLBACK.sub(lambda m: _r1c1_part(m.group(1), hcol, hrow), seg))
        return "".join(out)
    pieces = []
    for ti, tk in enumerate(p.tokens):
        if tk.type == OPERAND and ti in p.var_tokens:
            pieces.append(tk.text.upper())
        elif tk.type == OPERAND and (tk.subtype == RANGE or (tk.subtype == NUMBER and not _NUMBER_RE.match(tk.text))):
            pieces.append(_ref_r1c1(tk.text, hcol, hrow))
        elif tk.type == FUNC and tk.subtype == OPEN:
            body = tk.text[:-1]
            k = max(body.rfind(":"), body.rfind("@"), body.rfind("!"))
            pre, name = body[:k + 1], body[k + 1:]
            if pre.endswith(":") and len(pre) > 1:
                ref = pre[:-1]
                pre = _ref_r1c1(ref, hcol, hrow) + ":"
            pieces.append(pre + _fn.normalize_name(name) + "(")
        else:
            pieces.append(tk.text)
    return "=" + "".join(pieces)


# ----------------------------------------------------------------------------- file form

def to_file_formula(formula: str) -> str:
    """Add the _xlfn. prefix to functions that need it in the file (functions.FunctionInfo.xlfn).

    Also: _xlfn._xlws. for FILTER and SORT, _xlpm. before LET and LAMBDA names, `A1#` as
    _xlfn.ANCHORARRAY(A1) and `@` as _xlfn.SINGLE(...). Idempotent. A formula given without '=' is returned
    without '='."""
    had_eq = isinstance(formula, str) and formula.startswith("=")
    f = display_form(formula)
    p = _parse(f)
    edits: list[tuple[int, int, str]] = []        # (start, end, replacement)
    if p.tokens is None:
        for m in re.finditer(r"(?<![A-Za-z0-9_.])([A-Za-z][A-Za-z0-9.]*)\(", f):
            if not any(s <= m.start() < s + len(seg) for s, seg in _text_spans(f)):
                prefix = _fn.file_prefix(m.group(1))
                if prefix:
                    edits.append((m.start(), m.start(), prefix))
    else:
        for idx, c in enumerate(p.calls):
            prefix = _fn.file_prefix(c["name"])
            if prefix:
                edits.append((c["start"], c["start"], prefix))
            if idx in p.at_calls:
                at = p.at_calls[idx]
                edits.append((at, at + 1, "_xlfn.SINGLE("))
                edits.append((c["end"], c["end"], ")"))
        for at, open_end in p.at_groups:                 # '@(<expression>)' -> _xlfn.SINGLE(<expression>)
            edits.append((at, open_end, "_xlfn.SINGLE("))
        for ti, tk in enumerate(p.tokens):
            if ti in p.var_tokens:
                edits.append((tk.start, tk.start, "_xlpm."))
            elif tk.type == OPERAND and tk.subtype == RANGE and "[" not in tk.text.split("!")[-1]:
                s, e = tk.start, tk.end
                if tk.text.startswith("@"):
                    edits.append((s, s + 1, "_xlfn.SINGLE("))
                    edits.append((e, e, ")"))
                    s += 1
                if tk.text.endswith("#"):
                    edits.append((s, s, "_xlfn.ANCHORARRAY("))
                    edits.append((e - 1, e, ")"))
    out = f
    for s, e, rep in sorted(edits, key=lambda x: (x[0], x[1]), reverse=True):
        out = out[:s] + rep + out[e:]
    return out if had_eq else out[1:]


def _text_spans(f: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0)) for m in _STRING_RE.finditer(f)]


# ----------------------------------------------------------------------------- the rules for one formula

def _short(text: str, limit: int = MESSAGE_TEXT_MAX) -> str:
    text = text.replace("\r", " ").replace("\n", " ")
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _num(v: float) -> str:
    if float(v).is_integer():
        return f"{int(v):,}" if abs(v) >= 10000 else str(int(v))
    return format(v, ".15g")


def _join(items: Sequence[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _largest_argument(a: Analysis) -> str | None:
    best = ""
    body = a.formula[1:].strip()
    for c in a.calls:
        for s, e in c.args:
            t = a.formula[s:e].strip()
            if len(t) > len(best) and t != body:
                best = t
    return best or None


# ----------------------------------------------------------------------------- argument counts (v6, A-aera-6)

@dataclass(frozen=True)
class ArgCount:
    """The numbers of arguments a function takes: min to max, in steps of `step` (2 for the pair rules: a criteria
    range with each criterion). `text` is the <allowed> text of the F-ARGS message and fix."""
    min: int
    max: int
    text: str
    step: int = 1

    def allows(self, count: int) -> bool:
        return self.min <= count <= self.max and (count - self.min) % self.step == 0

    @property
    def pairs(self) -> bool:
        return self.step == 2


def _arg_counts() -> dict[str, ArgCount]:
    exactly = {n: ArgCount(n, n, f"exactly {n}") for n in (1, 2, 3)}
    two_or_three = ArgCount(2, 3, "2 or 3")
    table: dict[str, ArgCount] = {"IF": two_or_three, "SUMIF": two_or_three, "AVERAGEIF": two_or_three,
                                  "MATCH": two_or_three,
                                  "AND": ArgCount(1, 255, "1 to 255"), "OR": ArgCount(1, 255, "1 to 255"),
                                  "INDEX": ArgCount(2, 4, "2 to 4"), "XLOOKUP": ArgCount(3, 6, "3 to 6"),
                                  "DATE": exactly[3]}
    for name in ("IFERROR", "IFNA", "ROUND", "ROUNDUP", "ROUNDDOWN", "MOD", "COUNTIF", "EOMONTH", "EDATE"):
        table[name] = exactly[2]
    for name in ("NOT", "ABS", "INT", "YEAR", "MONTH", "DAY"):
        table[name] = exactly[1]
    for name, top in (("SUMIFS", 255), ("AVERAGEIFS", 255), ("MAXIFS", 253), ("MINIFS", 253)):
        table[name] = ArgCount(3, top, "a range and then pairs of a criteria range and its criterion "
                               f"(3, 5, 7, ... up to {top})", step=2)
    table["COUNTIFS"] = ArgCount(2, 254, "pairs of a criteria range and its criterion (2, 4, 6, ... up to 254)",
                                 step=2)
    return table


# Source: Microsoft's function reference (support.microsoft.com, one page per function): the syntax line of each
# page, and for the pair rules "up to 127 range/criteria pairs" (SUMIFS, COUNTIFS, AVERAGEIFS) and "up to 126
# range/criteria pairs" (MAXIFS, MINIFS). Listed in references/model-spec-format.md, "Argument counts".
ARG_COUNTS: dict[str, ArgCount] = _arg_counts()
ARGS_FIX_REFERENCE = 'references/model-spec-format.md, "Argument counts"'


def _args_fix(name: str, rule: ArgCount) -> str:
    if rule.pairs:   # "give SUMIFS a range and then pairs ... (3, 5, 7, ... up to 255 arguments)"
        return f"give {name} {rule.text[:-1]} arguments) ({ARGS_FIX_REFERENCE})"
    word = "argument" if rule.max == 1 else "arguments"
    return f"give {name} {rule.text} {word} ({ARGS_FIX_REFERENCE})"


def check_formula(formula: str, *, depth_limit: int = DEPTH_LIMIT_DEFAULT,
                  length_limit: int = LENGTH_LIMIT_DEFAULT,
                  allowed_literals: Sequence[float] = ALLOWED_LITERALS_DEFAULT,
                  product: str = "both", allow_functions: Sequence[str] = (),
                  sheet: str | None = None) -> list[Issue]:
    """Every formula-level rule of design section 14 (F-*) for one formula.

    `sheet` (addition by CORE-F): the sheet that holds the formula; when given, F-SELF-SHEET is checked.
    The result is ordered errors first, then warnings, in RULE_ORDER."""
    if product not in _fn.PRODUCTS:
        raise ValueError(f"product must be one of {', '.join(_fn.PRODUCTS)}; got {product!r}")
    if not isinstance(depth_limit, int) or depth_limit < 0:
        raise ValueError(f"depth_limit must be a whole number 0 or more, got {depth_limit!r}")
    if not isinstance(length_limit, int) or length_limit < 1:
        raise ValueError(f"length_limit must be a whole number 1 or more, got {length_limit!r}")
    a = analyze(formula)
    allowed_abs = {abs(float(x)) for x in allowed_literals}
    allow = {_fn.normalize_name(n) for n in allow_functions}
    issues: list[Issue] = []

    def add(rule: str, severity: str, message: str, fix: str, **data: Any) -> None:
        issues.append(Issue(rule, severity, message, fix, data))

    # Excel's own limits (platforms B5.1, verified)
    if a.length > EXCEL_MAX_LENGTH:
        add("F-EXCEL-LIMIT", "error", f"formula length {a.length:,} characters is over Excel's limit of "
            f"{EXCEL_MAX_LENGTH:,} characters", "split the formula into helper cells", length=a.length,
            limit=EXCEL_MAX_LENGTH)
    if a.depth > EXCEL_MAX_DEPTH:
        add("F-EXCEL-LIMIT", "error", f"nesting depth {a.depth} is over Excel's limit of {EXCEL_MAX_DEPTH} levels",
            "split the formula into helper cells", depth=a.depth, limit=EXCEL_MAX_DEPTH)
    if a.incomplete is not None:
        # v4 (C-sol-3): Excel does not open a workbook that holds this formula; one F-PARSE per formula
        reason, start, end = a.incomplete
        add("F-PARSE", "error", f"the formula is not complete: {reason} at character {start} ({_short(a.formula)})",
            INCOMPLETE_FIX, start=start, end=end, reason=reason)
    elif not a.ok:
        add("F-PARSE", "warning", f"the formula text could not be fully read ({_short(a.error or '', 160)}); depth "
            "and functions come from a simpler scan, and references and typed numbers were not checked",
            "open the cell in Excel, correct the formula or type it again", error=a.error)

    # F-ARGS (v6, A-aera-6): a listed function with too few or too many arguments; Excel does not open the
    # workbook. One issue per call. Not counted when F-PARSE reports the formula (the calls may be cut short).
    if a.ok and a.incomplete is None:
        for c in a.calls:
            rule = ARG_COUNTS.get(c.name)
            count = len(c.args)
            if rule is None or rule.allows(count):
                continue
            add("F-ARGS", "error", f"{c.name} has {count} argument(s); it takes {rule.text} "
                f"({_short(a.formula[c.start:c.end])})", _args_fix(c.name, rule), function=c.name, count=count,
                allowed=rule.text, min=rule.min, max=rule.max, start=c.start, end=c.end)

    # F-DEPTH (design 8.1)
    if a.depth > depth_limit:
        for part in over_limit_parts(a.formula, depth_limit):
            fix = f"move {_short(part.text)} into a helper cell and reference that cell"
            if part.remaining_depth > depth_limit:
                fix += (f"; that helper still has depth {part.remaining_depth}, over the limit {depth_limit}: "
                        "split it again")
            add("F-DEPTH", "error", f"nesting depth {len(part.chain)} is over the limit {depth_limit} "
                f"({' > '.join(part.chain)})", fix, depth=len(part.chain), formula_depth=a.depth,
                limit=depth_limit, chain=list(part.chain), part=part.text, start=part.start, end=part.end,
                level=part.level, remaining_depth=part.remaining_depth)

    # F-NESTED-IF (FAST 3.03-07 'Never use nested IFs', platforms C1.39, verified)
    if_depth: dict[int, int] = {}
    for i, c in enumerate(a.calls):
        if c.name == "IF":
            outer = c.parent
            while outer is not None and a.calls[outer].name != "IF":
                outer = a.calls[outer].parent
            if_depth[i] = 1 + (if_depth[outer] if outer is not None else 0)
    nested = [i for i, d in if_depth.items() if d > 1]
    if nested:
        deepest = max(if_depth.values())
        add("F-NESTED-IF", "error", f"IF inside IF: {len(nested)} IF call(s) sit inside another IF (up to "
            f"{deepest} IF calls deep: {' > '.join(['IF'] * deepest)})",
            "replace the inner IF with a lookup table read with INDEX and MATCH, MIN or MAX, or 1/0 flags in "
            "helper cells (helper-patterns.md)", count=len(nested), if_depth=deepest)

    # functions (design 8.5)
    for name in a.functions:
        cls = _fn.classify(name, product)
        entry = _fn.info(name)
        volatile = name in _fn.VOLATILE and name not in _fn.VOLATILE_REPORTED_AS_REFUSED
        if cls == "unknown" and name.startswith("_XLUDF."):
            base = name[len("_XLUDF."):]
            add("F-FUNC-UNKNOWN", "warning", f"{base} is stored as a user-defined function (_xludf.{base}): the "
                "program that saved the file did not know it, and Excel may show #NAME?",
                f"type the formula again in Excel so that it is stored as {base}, or use a catalogue function",
                function=name, product=product, user_defined=True)
        elif cls == "unknown":
            add("F-FUNC-UNKNOWN", "warning", f"{name} is not in the function catalogue (references/functions.md)",
                f"check that {name} exists in both Excel and Google Sheets, or use a catalogue function that does "
                "the same", function=name, product=product)
        elif cls == "refused":
            add("F-FUNC-REFUSED", "error", f"{name} is refused in profile {product}: {entry.reason}",  # type: ignore[union-attr]
                f"use {entry.replacement}" if entry and entry.replacement else "replace it (references/functions.md)",
                function=name, product=product, **{"class": cls})
        elif cls == "flagged" and not volatile and name not in allow:
            add("F-FUNC-FLAGGED", "warning", f"{name} is flagged in profile {product}: {entry.reason}",  # type: ignore[union-attr]
                (f"use {entry.replacement}" if entry and entry.replacement else "replace it")
                + f", or accept it for this model with settings.allow_functions [\"{name}\"]",
                function=name, product=product, **{"class": cls})
        if volatile and name not in allow:
            add("F-VOLATILE", "warning", f"{name} is volatile: it recalculates on every change, so results and "
                "checks can differ between recalculations (books B-56)",
                (f"use {entry.replacement}" if entry and entry.replacement else "use one dated input cell"),
                function=name)

    # references
    externals: dict[str, Ref] = {}
    for r in a.refs:
        if r.workbook:
            externals.setdefault(r.workbook, r)
    for wb, r in externals.items():
        add("F-EXTERNAL", "error", f"reference to another workbook: {_short(r.text)}",
            "land the data from that workbook as a raw table (column contract, then Power Query or Connected "
            "Sheets) and reference the raw sheet", workbook=wb, ref=r.text)
    ref_errors = [r for r in a.refs if r.kind == "error"] + [x for x in a.literals
                                                              if x.kind == "error" and x.value == "#REF!"]
    if ref_errors or (not a.ok and "#REF!" in "".join(s for t, s in _segments(a.formula) if not t).upper()):
        add("F-REF-ERROR", "error", "the formula contains #REF! (a reference to cells that were deleted)",
            "rebuild the reference to the intended cell or range", count=max(1, len(ref_errors)))
    if sheet:
        for r in a.refs:
            if r.sheet and not r.workbook and r.sheet.casefold() == sheet.casefold():
                add("F-SELF-SHEET", "warning", f"the reference {_short(r.text)} names its own sheet {sheet}",
                    f"drop the sheet prefix: {r.a1}", ref=r.text)
    spills = [r for r in a.refs if r.text.endswith("#")]
    for r in spills:
        add("F-ARRAY", "warning", f"spill reference {r.text} reads a dynamic array",
            "reference a fixed range, or build the list in helper cells (FAST 3.03-09)", ref=r.text)
    if a.has_array:
        arrays = _array_texts(a)
        add("F-ARRAY", "warning", f"array constant {_short(arrays[0]) if arrays else '{...}'} in the formula",
            "put the values in input cells and reference them, or use helper cells (FAST 3.03-09)",
            arrays=arrays)
    seen_names: set[str] = set()
    for r in a.refs:
        if r.kind == "name" and not r.workbook and r.a1.upper() not in seen_names:
            seen_names.add(r.a1.upper())
            add("F-NAME", "warning", f"the defined name {r.a1} is used in the formula",
                "reference the cell or range with an A1 reference (FAST 3.03-08)", name=r.a1)
    for r in a.refs:
        if r.kind == "structured" and ("[@" in r.a1 or "#THIS ROW" in r.a1.upper()):
            add("X-STRUCTREF", "warning", f"structured reference {_short(r.text)} uses @ or #This Row, which "
                "Google Sheets does not support (platforms A2.4)",
                "run rotm.py compat to rewrite it as a same-row A1 reference, or write that A1 reference", ref=r.text)

    # F-LITERAL (design 8.3); numbers inside an array constant are reported once, by F-ARRAY (design 8.1 table)
    by_value: dict[float, list[Literal]] = {}
    spans = _array_spans(a)
    for x in a.literals:
        if x.kind == "number" and abs(x.value) not in allowed_abs and not any(s <= x.start < e for s, e in spans):
            by_value.setdefault(x.value, []).append(x)
    allowed_txt = _join([_num(v) for v in sorted(allowed_abs)]) or "none"
    for value, xs in by_value.items():
        shown = xs[0].text if xs[0].text == _num(value) else f"{xs[0].text} ({_num(value)})"
        where = f" (argument {xs[0].arg_index} of {xs[0].function})" if xs[0].function and xs[0].arg_index else ""
        add("F-LITERAL", "warning", f"the number {shown} is typed into the formula{where}; allowed typed numbers: "
            f"{allowed_txt}",
            f"put {_num(value)} in an input cell on the inputs sheet and reference that cell (in a model "
            "specification: {in:<id>})", value=value, text=xs[0].text, count=len(xs), function=xs[0].function,
            arg_index=xs[0].arg_index)

    # F-LENGTH (design 8.2) and F-LINEBREAK (FAST 3.03-03)
    if a.length > length_limit:
        largest = _largest_argument(a)
        if largest and len(largest) >= LARGEST_ARGUMENT_MIN:
            fix = f"move {_short(largest)} into a helper cell and reference that cell"
        else:
            fix = f"split it into helper cells of at most {length_limit} characters each"
        add("F-LENGTH", "warning", f"formula length {a.length:,} characters is over the limit {length_limit} "
            "characters", fix, length=a.length, limit=length_limit, largest_argument=largest)
    if a.has_line_break:
        add("F-LINEBREAK", "warning", "the formula has a line break",
            "write it on one line, or split it into helper cells (FAST 3.03-03)")

    order = {r: i for i, r in enumerate(RULE_ORDER)}
    issues.sort(key=lambda i: (_SEVERITY_ORDER.get(i.severity, 9), order.get(i.rule, len(order))))
    return issues


def _array_spans(a: Analysis) -> list[tuple[int, int]]:
    """(start, end) of every array constant {...} in the formula."""
    p = _parse(a.formula)
    out: list[tuple[int, int]] = []
    if p.tokens is None:
        return out
    depth_ = 0
    start = 0
    for tk in p.tokens:
        if tk.type == ARRAY and tk.subtype == OPEN:
            if depth_ == 0:
                start = tk.start
            depth_ += 1
        elif tk.type == ARRAY and tk.subtype == CLOSE and depth_:
            depth_ -= 1
            if depth_ == 0:
                out.append((start, tk.end))
    return out


def _array_texts(a: Analysis) -> list[str]:
    return [a.formula[s:e] for s, e in _array_spans(a)]


# ----------------------------------------------------------------------------- subcommand `formula`

def add_formula_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("formula", help='the formula, e.g. "=IFERROR(INDEX(A:A,MATCH(B1,C:C,0)),0)"')
    p.add_argument("--limit", type=int, help="depth limit (default: the dictionary's depth_limit, 2)")
    p.add_argument("--max-length", type=int, help="length limit in characters (default 100)")
    p.add_argument("--product", choices=("both", "excel", "sheets"), default="both")
    p.add_argument("--json", metavar="FILE", help="write the analysis as JSON ('-' = stdout)")
    p.add_argument("--strict", action="store_true", help="exit 1 on any warning as well")
    p.add_argument("--allow", metavar="NAMES", help="comma-separated flagged functions to accept, e.g. LET,IRR")
    p.add_argument("--allowed-literals", metavar="NUMBERS",
                   help="comma-separated numbers that may be typed into formulas (default 0,1)")
    p.add_argument("--sheet", help="the sheet that holds the formula (checks F-SELF-SHEET)")


def _parse_numbers(text: str) -> tuple[float, ...]:
    out = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.append(float(item))
        except ValueError:
            from .common import Refusal
            raise Refusal(f"'{item}' is not a number", key="--allowed-literals",
                          fix="give numbers separated by commas, e.g. --allowed-literals 0,1,12") from None
    return tuple(out)


def run_formula(args: argparse.Namespace) -> int:
    from . import common
    text = args.formula if isinstance(args.formula, str) else ""
    if not text.strip() or text.strip() == "=":
        raise common.Refusal("the formula is empty", key="formula",
                             fix='give the formula text in quotes, e.g. rotm.py formula "=SUM(A1:A3)"')
    limit = DEPTH_LIMIT_DEFAULT if args.limit is None else args.limit
    if limit < 0:
        raise common.Refusal(f"the depth limit {limit} is below 0", key="--limit", fix="give 0 or more, e.g. --limit 2")
    max_length = LENGTH_LIMIT_DEFAULT if args.max_length is None else args.max_length
    if max_length < 1:
        raise common.Refusal(f"the length limit {max_length} is below 1", key="--max-length",
                             fix="give 1 or more characters, e.g. --max-length 100")
    literals = ALLOWED_LITERALS_DEFAULT if args.allowed_literals is None else _parse_numbers(args.allowed_literals)
    allow = tuple(n.strip() for n in (args.allow or "").split(",") if n.strip())
    a = analyze(text)
    issues = check_formula(text, depth_limit=limit, length_limit=max_length, allowed_literals=literals,
                           product=args.product, allow_functions=allow, sheet=args.sheet)
    parts = over_limit_parts(text, limit)
    report = common.Report(command="formula", target=a.formula, strict=bool(args.strict))
    for i in issues:
        report.add(common.Finding(i.rule, i.severity, i.message, i.fix, data=dict(i.data)))
    report.data = {
        "analysis": a.to_dict(),
        "settings": {"depth_limit": limit, "length_limit": max_length, "allowed_literals": list(literals),
                     "product": args.product, "allow_functions": list(allow)},
        "parts": [{"text": p.text, "start": p.start, "end": p.end, "chain": list(p.chain), "level": p.level,
                   "remaining_depth": p.remaining_depth} for p in parts],
        "functions": [{"name": n, "class": _fn.classify(n, args.product),
                       "excel": getattr(_fn.info(n), "excel", None), "sheets": getattr(_fn.info(n), "sheets", None),
                       "volatile": n in _fn.VOLATILE} for n in a.functions],
        "file_form": to_file_formula(a.formula),
    }
    report.verified = {"recalculated": False, "rendered": False}
    report.finish(bool(args.strict))
    stream = sys.stderr if args.json is not None and str(args.json) == "-" else sys.stdout
    for line in _describe(a, limit, max_length, literals, args.product, parts):
        stream.write(line + "\n")
    return report.emit(args.json)


def _describe(a: Analysis, limit: int, max_length: int, literals: Sequence[float], product: str,
              parts: Sequence[Part]) -> list[str]:
    allowed_abs = {abs(float(x)) for x in literals}
    lines = [f"formula:    {a.formula}"]
    if not a.ok:
        lines.append(f"read:       not fully ({a.error})")
    chain = f": {' > '.join(a.chain)}" if a.chain else ""
    lines.append(f"depth:      {a.depth} (limit {limit}){chain}")
    lines.append(f"length:     {a.length} characters (limit {max_length})")
    if a.functions:
        lines.append("functions:  " + "; ".join(f"{n} {_fn.classify(n, product)}" for n in a.functions))
    if a.refs:
        lines.append("references: " + "; ".join(f"{r.text} {r.kind}" for r in a.refs[:20])
                     + (f"; and {len(a.refs) - 20} more" if len(a.refs) > 20 else ""))
    nums = [x for x in a.literals if x.kind == "number"]
    if nums:
        lines.append("numbers:    " + "; ".join(
            f"{x.text} ({'allowed' if abs(x.value) in allowed_abs else 'not allowed'})" for x in nums))
    for p in parts:
        lines.append(f"move:       {_short(p.text)} (level {p.level}; the helper has depth {p.remaining_depth})")
    if a.is_pure_link:
        lines.append("pure link:  yes")
    return lines
