"""`split`: rewrite one over-limit formula as helper cells; values unchanged after recalculation
(design sections 8.1, 11, 15; F19, F08a).

Owner: M7. The part to move is chosen by lib.formula.over_limit_parts, as the audit names it: on each deepest
chain, the call at level limit + 1. Moving every such call leaves the formula at depth `limit`; a moved part that
is still over the limit is split again, so every helper is within the limit too. Equal parts share one helper:
their texts are compared with text literals (in double quotes) exactly and the rest without case (v4, A-aera-5).

A part that sits inside IFERROR or IFNA (v4, A-aera-14) is never moved into a helper on its own, because the
helper could show an error that the guard hid:
- a guarded lookup, IFERROR(INDEX(<range>,MATCH(<key>,<keys>[,<type>])),<value>) (or IFNA), whose INDEX or MATCH
  call is the part to move, is rewritten with the pattern of references/helper-patterns.md H5, which keeps the
  guard on the MATCH part and on the INDEX part (v5, A-aera-5 and C-sol-9). <G> is the guard (IFERROR or IFNA), <T>
  its test (ISERROR or ISNA):
  helper 1 =<G>(MATCH(<key>,<keys>[,<type>]),0): the match position, or 0 when the key is missing;
  helper 2 =MAX(<helper 1>,1): a position INDEX can read (1 when helper 1 is 0);
  helper 3 =--<T>(INDEX(<range>,<helper 2>)): 1 when the guard would replace the result at that position (the
  return cell holds an error, or the position is outside the range), else 0;
  in place of the guard call IF(OR(<helper 1>=0,<helper 3>=1),<value>,INDEX(<range>,<helper 2>)): the guard's value
  when the key is missing or the result is an error the guard catches, else the result. The guard's value is
  written once, in that IF, as in the v4 pattern. Each helper and the IF have depth 2 or less (plus the depth of
  the range and of the value). When that IF is too deep where the guard sat, it goes to a helper of its own. With
  these, the host gives the original's result for every key and every return value, and no helper holds an
  error value that the guard hid (an IFNA lookup shows a key's own error, as the original does).
- a lookup inside IFNA that sits inside IFERROR is refused with SPLIT-GUARD: a helper that keeps IFNA's result would
  show the error values the IFERROR hid, and a helper with IFERROR would change the result for them.
- any other part under IFERROR or IFNA: split writes nothing and exits 1 with the error SPLIT-GUARD, which names
  the host cell, the guard and the part, whatever the current values are.

A part that lies in the second or third argument of an IF (v6, A-aera-10) is not moved into a helper that is
calculated for every input, because Excel calculates that argument only where the IF takes it:
- the helper carries the IF's condition: IF(<condition>,<part>,0) for a part from the second argument, and
  IF(<condition>,0,<part>) for a part from the third (empty text "" in place of 0 when the part's function always
  gives text, _TEXT_RESULT). Where the IF does not take the branch, the helper holds 0 (or ""), and the host does not
  read it. The condition is written as it is after the split: a part of the condition over the limit is in a helper
  of its own, which the condition reads. A helper over the depth limit is split again the same way; its helpers
  carry the same condition. The helpers' references get the host sheet's name, as the part's do;
- split refuses with SPLIT-GUARD (nothing written, exit 1) when the helper cannot carry the condition within the
  limits: the depth limit is 1 (IF and the part need depth 2), the part lies in a branch of two or more IFs, the
  part is the INDEX or MATCH call of a guarded lookup (the helpers of the pattern H5 take no condition), or a helper
  that carries the condition, or the split cell itself, is over the length limit. The finding names the IF, the
  argument and the part, and the fix names the pattern to apply by hand (references/helper-patterns.md, "A part in
  a branch of IF");
- a part in the first argument (the condition) is calculated whenever the IF is, and is moved as before.
Only IF is handled so. A part under another function that calculates an argument only in some cases (CHOOSE, IFS,
SWITCH) is moved as before; the check of the new helper cells (below) then finds an error value with the current
inputs only.

ROW() and COLUMN() without an argument (v7, A-aera-5) give the row or the column of the cell that holds them, so in
a helper they would give the helper's row or column. In every part that moves to a helper (a depth split and a
length split alike), each such call is written with the original cell as its argument: COLUMN() of Scenarios!D2
becomes COLUMN(Scenarios!D2) on split_helpers (the sheet name in quotes when it needs them, no sheet name when the
host is on split_helpers itself). The reference is relative, so the helpers of a copied-down block keep one form.
The host keeps its own ROW() and COLUMN(). A call that cannot be written so is refused with SPLIT-GUARD, which
names the call; nothing is written (SplitPosition).

Where the helpers go (any workbook): sheet `split_helpers`, created after the host sheet when it does not exist.
- The split cell and the cells around it that hold the same formula (same relative form: a copied-down column,
  a row copied across, or a block) are split together, so the column stays consistent (audit H-INCONSISTENT).
- Helper k of host cell (row r, column c) sits on split_helpers at row r (row r + 1 when the block starts in
  row 1) in its own column block; row 1 holds a header per helper column. Because every host cell and every
  helper keeps the same offset, the relative forms stay equal down the column and across the row.
- References in a moved part that name no sheet get the host sheet's name; a reference to split_helpers written
  on split_helpers names no sheet (FAST 3.04-02 to 3.04-07; the individual number not confirmed).

A formula within the depth limit but over the length limit (v2, A-aera-13): when it is a chain of + and - terms
at its top level (no &, no comparison and no enclosing parentheses at the top level), the terms are moved, in
order, into helper cells of a few terms each, every helper within the length limit; a helper whose first term
follows a minus holds its terms with the signs turned round and the host subtracts it, so every sign is kept.
The host cell adds the helpers (and, when that sum is itself over the limit, helpers that add helpers). Any
other over-long shape is not rewritten: exit 1 with the error finding SPLIT-SHAPE, which names the shape and the
pattern to apply by hand (references/helper-patterns.md, H10).

Proof (design 11): before and after are recalculated in Excel (the original as a copy) and compared by
lib.workbook.compare under the one preservation rule (v2, A-aera-16): formulas and typed values exactly,
calculated values within --calc-tolerance (relative), cells that hold a volatile function or depend on one left
out of the value comparison and counted; the report states it (workbook.preservation_statement). Every value
must be equal, the split cells included. A difference deletes the output (exit 1). Without Excel the output is
written only when the package has no part openpyxl drops, and the run exits 3 (values not verified).

The new helper cells are part of the comparison (v3, A-aera-9): a helper that holds an error value after the
recalculation while its host cell showed no error before the split (another function of the original formula,
such as ISERROR or CHOOSE, kept the error out of the result) is the error finding SPLIT-GUARD, which names the
helper cell and the guard; nothing is written and split exits 1, as for a difference. This needs the values, so
only a run with Excel checks it. A part under IFERROR or IFNA is refused before any value is read, and a part in a
branch of IF carries the condition or is refused before any value is read (above).

The cells split writes on split_helpers get the style dictionary's look (v3, A-aera-18; lib.styles through
compat.CellLook): the heading of each helper column the col_header type, the helper formulas the calc type, at
the size of the dictionary's sheet type "helpers" (8 in the plugin dictionary). Cells split did not write keep
their look.

With --spec (a built model): not available in this version (refused with what to do instead); see
split_cell. Build refuses an expression deeper than the limit and names the part to move.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import common
from . import formula as fx

HELPER_SHEET = "split_helpers"
PLACEHOLDER = "_rotm_h{}"
_PLACEHOLDER_RE = re.compile(r"_rotm_h(\d+)", re.I)
MAX_HELPERS = 60

# Arguments that take a reference or an array (1 = first argument; "odd"/"even" from that index on). A moved
# part in such a position would hand the function a value instead of a range.
_REF_ARGS: dict[str, Any] = {
    "SUMIF": {1, 3}, "COUNTIF": {1}, "AVERAGEIF": {1, 3}, "COUNTBLANK": {1},
    "SUMIFS": ("sumifs",), "AVERAGEIFS": ("sumifs",), "MAXIFS": ("sumifs",), "MINIFS": ("sumifs",),
    "COUNTIFS": ("odd",), "INDEX": {1}, "MATCH": {2}, "XMATCH": {2}, "XLOOKUP": {2, 3}, "VLOOKUP": {2},
    "HLOOKUP": {2}, "LOOKUP": {2, 3}, "ROW": {1}, "COLUMN": {1}, "ROWS": {1}, "COLUMNS": {1}, "OFFSET": {1},
    "CELL": {2}, "ISREF": {1}, "AREAS": {1}, "FORMULATEXT": {1}, "ISFORMULA": {1}, "SUMPRODUCT": ("all",),
    "SUBTOTAL": ("from2",), "AGGREGATE": ("from3",), "FILTER": ("all",), "SORT": {1}, "UNIQUE": {1},
}


@dataclass
class SplitPlan:
    original: str                                   # the formula before
    rewritten: str                                  # the formula after, referencing the helpers
    helpers: list[tuple[str, str]] = field(default_factory=list)   # (label, formula) in calculation order
    depth_after: int = 0
    # additions (M7): helper k is written as the name _rotm_h<k> in `rewritten` and in later helpers until
    # resolve() replaces each name with the helper's cell reference.
    names: list[str] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)  # the moved part of each helper, as in the original
    # additions (v2, A-aera-13): "depth" (moved nested calls) or "length" (an over-long + and - chain cut into
    # groups of terms); for "length", the number of terms in each first-level helper.
    kind: str = "depth"
    groups: list[int] = field(default_factory=list)
    # addition (v6, A-aera-10): helper name -> (the IF of the original formula, the argument 2 or 3, the part) for
    # each helper that carries the condition of an IF; split_cell checks their length.
    carried: dict[str, tuple[str, int, str]] = field(default_factory=dict)

    def resolve(self, refs: dict[str, str], *, host_refs: dict[str, str] | None = None) -> tuple[str, list[str]]:
        """Replace the helper names: `host_refs` (or `refs`) in the rewritten formula, `refs` in the helpers."""
        def sub(text: str, mapping: dict[str, str]) -> str:
            return "".join(seg if is_text else _PLACEHOLDER_RE.sub(lambda m: mapping[m.group(0).lower()], seg)
                           for is_text, seg in fx._segments(text))
        host = sub(self.rewritten, host_refs or refs)
        return host, [sub(h, refs) for _label, h in self.helpers]


def _refuse_names(a: Any) -> None:
    for name in ("LET", "LAMBDA"):
        if name in a.functions:
            raise common.Refusal(f"the formula uses {name}: a part that reads {name} names cannot be moved to "
                                 "another cell", fix="write each named step of the formula in its own helper cell")


def _check_movable(a: Any, part: Any) -> None:
    """Refuse a part whose value cannot stand in for it (a reference or array argument, a range operand)."""
    f = a.formula
    before = f[:part.start].rstrip()
    after = f[part.end:].lstrip()
    if before.endswith(":") or after.startswith(":"):
        raise common.Refusal(f"the part to move, {_short(part.text)}, is one end of a range (A1:{part.text[:5]}...); "
                             "a helper cell would hold its value, not a cell", fix="rewrite this formula by hand")
    idx = next((i for i, c in enumerate(a.calls) if c.start == part.start and c.end == part.end), None)
    if idx is None or a.calls[idx].parent is None:
        return
    parent = a.calls[a.calls[idx].parent]
    pos = next((k + 1 for k, (s, e) in enumerate(parent.args) if s <= part.start and part.end <= e), None)
    if pos is None:
        return
    arg_text = f[parent.args[pos - 1][0]:parent.args[pos - 1][1]].strip()
    if arg_text != part.text.strip():
        return                       # the part is inside an expression, which already yields a value
    rule = _REF_ARGS.get(parent.name)
    if rule is None:
        return
    hit = False
    if isinstance(rule, set):
        hit = pos in rule
    elif rule[0] == "sumifs":
        hit = pos == 1 or pos % 2 == 0
    elif rule[0] == "odd":
        hit = pos % 2 == 1
    elif rule[0] == "all":
        hit = True
    elif rule[0] == "from2":
        hit = pos >= 2
    elif rule[0] == "from3":
        hit = pos >= 3
    if hit:
        raise common.Refusal(f"the part to move, {_short(part.text)}, is argument {pos} of {parent.name}, which "
                             "takes a range or an array there; a helper cell would hold one value instead",
                             fix=f"rewrite it by hand: compute the inner step of {_short(part.text, 40)} in a helper "
                             "cell and keep the range in the formula")


def _short(text: str, n: int = 80) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[:n - 3] + "..."


def helper_key(text: str) -> str:
    """The text two parts must share to share one helper (v4, A-aera-5): upper case outside double quotes, text
    literals exactly as written, so that "x" and "X" stay apart (EXACT, FIND and SUBSTITUTE tell them apart)."""
    return "".join(seg if is_text else seg.upper() for is_text, seg in fx._segments(text))


# v7, A-aera-5: the functions whose call without an argument gives the position of the cell that holds it
POSITION_FUNCTIONS = ("ROW", "COLUMN")


def bind_position(text: str, host: str) -> str:
    """`text` (a formula that moves out of the cell `host`, an A1 cell without a sheet) with every ROW() and
    COLUMN() without an argument written as ROW(<host>) and COLUMN(<host>), so that it gives the host cell's row
    or column wherever it sits (v7, A-aera-5). Raises SplitPosition for such a call it cannot rewrite."""
    if not any(name + "(" in text.upper().replace(" ", "") for name in POSITION_FUNCTIONS):
        return text
    a = fx.analyze(text)
    f = a.formula
    edits: list[tuple[int, int]] = []
    for c in a.calls:
        if c.name not in POSITION_FUNCTIONS or c.args:
            continue
        call = f[c.start:c.end]
        open_at = c.start + call.find("(")
        if not a.ok or call.find("(") < 0 or not call.endswith(")") or f[open_at + 1:c.end - 1].strip():
            raise SplitPosition(call, host)
        edits.append((open_at + 1, c.end - 1))
    if not edits:
        return text
    out = f
    for s, e in sorted(edits, reverse=True):
        out = out[:s] + host + out[e:]
    return out


def plan_split(formula: str, *, limit: int, host: str = "A1") -> SplitPlan:
    """Split until the formula and every helper are within `limit`.

    A part inside IFERROR or IFNA (v4, A-aera-14): the INDEX or MATCH call of a guarded lookup is rewritten with
    the pattern H5 (see the module text); any other part raises SplitGuard. A part in the second or third argument
    of an IF (v6, A-aera-10): the helper carries the IF's condition, or SplitBranch is raised (see the module
    text). `host`: the cell that holds the formula (v7, A-aera-5): ROW() and COLUMN() without an argument in a
    helper are written as ROW(<host>) and COLUMN(<host>) (bind_position)."""
    if not isinstance(limit, int) or limit < 1:
        raise common.Refusal(f"limit {limit!r}: give a whole number 1 or more", key="--limit")
    f = fx.display_form(formula)
    a = fx.analyze(f)
    if not a.ok:
        raise common.Refusal(f"the formula could not be read ({a.error})", fix="correct the formula in Excel first")
    _refuse_names(a)
    helpers: list[list[str]] = []          # [name, text, part, label or ""]
    by_text: dict[str, str] = {}
    carried: dict[str, tuple[str, int, str]] = {}

    def helper_for(inner: str, part_text: str, label: str = "") -> str:
        key = helper_key(inner)
        name = by_text.get(key)
        if name is None:
            if len(helpers) >= MAX_HELPERS:
                raise common.Refusal(f"the formula needs more than {MAX_HELPERS} helper cells",
                                     fix="rewrite it by hand in smaller steps")
            name = PLACEHOLDER.format(len(helpers) + 1)
            helpers.append([name, inner, part_text, label])
            by_text[key] = name
        return name

    def lookup_edit(an: Any, lk: _Lookup, guards: list[int], p: Any) -> tuple[int, int, str]:
        """The pattern H5 for the guarded lookup `lk` (v5, A-aera-5 and C-sol-9): helpers 1 to 3, and the text that
        replaces the guard."""
        g = an.calls[lk.guard]
        if _mixed_guards(an, lk, guards):
            raise SplitGuard(g.name, p.text, reason=MIXED_REASON.format(lookup=p.text))
        probe = _pattern_texts(an, lk, *(PLACEHOLDER.format(0),) * 3)
        d_if = fx.depth("=" + probe.host)
        d_test = fx.depth(probe.test)
        inline = (g.level - 1) + d_if <= limit
        if limit < 2 or d_test > limit or not (inline or d_if <= limit):
            raise SplitGuard(g.name, p.text, reason=(
                f"the lookup {p.text} sits inside {g.name}; split rewrites such a lookup only with the pattern of "
                f"references/helper-patterns.md H5, which needs depth {max(2, d_if, d_test)} here, over the depth "
                f"limit {limit}"))
        position = helper_for(split(probe.position), lk.match)
        floor = helper_for(_pattern_texts(an, lk, position, "", "").floor, lk.match,
                           f"the larger of helper {_number(position)} and 1")
        test = helper_for(_pattern_texts(an, lk, position, floor, "").test, lk.index,
                          f"1 when {g.name} replaces INDEX({_short(lk.range, 30)},helper {_number(floor)}), else 0")
        host_part = _pattern_texts(an, lk, position, floor, test).host
        if inline:
            return g.start, g.end, host_part
        whole = an.formula[g.start:g.end]
        _check_movable(an, fx.Part(text=whole, start=g.start, end=g.end, chain=(), level=g.level, remaining_depth=0))
        return g.start, g.end, helper_for("=" + host_part, whole)

    def named_if(an: Any, branch: tuple[int, int], origin: tuple[str, int] | None) -> tuple[str, int]:
        """The IF to name for `branch` (call index, argument): in a helper text that carries a condition, its own
        IF stands for the IF of the original formula (`origin`)."""
        g = an.calls[branch[0]]
        if origin is not None and g.level == 1 and g.start == 1 and g.end == len(an.formula):
            return origin
        return an.formula[g.start:g.end], branch[1]

    def carry(an: Any, text: str, p: Any, branches: list[tuple[int, int]], edits: list[tuple[int, int, str]],
              origin: tuple[str, int] | None) -> str:
        """The helper for a part in the second or third argument of an IF (v6, A-aera-10): IF(<condition>,<part>,0)
        or IF(<condition>,0,<part>), split again when it is over the limit; SplitBranch when that cannot be done."""
        if_text, k = named_if(an, branches[-1], origin)
        if len(branches) > 1:
            raise SplitBranch(p.text, if_text, k, "the part lies in a branch of two or more IFs; split carries the "
                                                  "condition of one IF only")
        if limit < 2:
            raise SplitBranch(p.text, if_text, k, f"a helper that carries the condition holds IF and the part, so "
                                                  f"it has depth 2 or more, over the depth limit {limit}")
        if k not in (2, 3):
            raise SplitBranch(p.text, if_text, k, "IF takes 2 or 3 arguments")
        g = an.calls[branches[-1][0]]
        cond = _rewrite(text, edits, *g.args[0]).strip()
        fill = '""' if _gives_text(an, p) else "0"
        wrapped = f"=IF({cond},{p.text},{fill})" if k == 2 else f"=IF({cond},{fill},{p.text})"
        inner = split(wrapped, (if_text, k))
        shown = _PLACEHOLDER_RE.sub(lambda m: f"helper {m.group(1)}", cond)
        name = helper_for(inner, p.text, f"{_short(p.text, 60)} where IF({_short(shown, 40)}) gives its argument "
                                         f"{k}, else {fill}")
        carried.setdefault(name, (if_text, k, p.text))
        return name

    def split(text: str, origin: tuple[str, int] | None = None) -> str:
        """`origin`: for a helper text that carries a condition, the IF of the original formula that its own IF
        stands for, and the argument the part came from."""
        an = fx.analyze(text)
        if an.depth <= limit:
            return text
        parts = fx.over_limit_parts(text, limit)
        edits: list[tuple[int, int, str]] = []
        rewritten_guards: set[int] = set()
        in_branch: list[tuple[Any, list[tuple[int, int]]]] = []
        for p in parts:
            _check_movable(an, p)
            branches = _if_branches(an, p.start, p.end)
            guards = _error_guards(an, p.start, p.end)
            if guards:
                lk = _guarded_lookup(an, guards[0])
                if lk is None or (p.start, p.end) not in lk.spans:
                    raise SplitGuard(an.calls[guards[0]].name, p.text)
                if branches:                    # v6, A-aera-10: the helpers of H5 would take no condition
                    if_text, k = named_if(an, branches[-1], origin)
                    raise SplitBranch(p.text, if_text, k, "the part is the lookup of a guard (IFERROR or IFNA), "
                                                          "which split writes with the pattern H5, whose helpers "
                                                          "take no condition")
                if lk.guard not in rewritten_guards:
                    rewritten_guards.add(lk.guard)
                    edits.append(lookup_edit(an, lk, guards, p))
                continue
            if branches:
                in_branch.append((p, branches))
                continue
            inner = split("=" + p.text)
            edits.append((p.start, p.end, helper_for(inner, p.text)))
        # v6, A-aera-10: the parts in a branch of IF, those of an IF inside a condition first, so that the condition
        # a helper carries already reads the helpers of its own parts
        for p, branches in sorted(in_branch, key=lambda pb: _width(an.calls[pb[1][-1][0]])):
            edits.append((p.start, p.end, carry(an, text, p, branches, edits, origin)))
        out = text
        for s, e, t in sorted(edits, reverse=True):
            out = out[:s] + t + out[e:]
        if fx.depth(out) > limit:       # pragma: no cover - over_limit_parts covers every deep chain
            raise common.Refusal("the formula could not be brought within the limit", fix="rewrite it by hand")
        return out

    rewritten = split(f)
    for h in helpers:                  # v7, A-aera-5: a moved ROW() or COLUMN() keeps the host cell's position
        h[1] = bind_position(h[1], host)
    labels = [(f"helper {i + 1}: {h[3] or _short(h[2], 60)}", h[1]) for i, h in enumerate(helpers)]
    depth_after = max([fx.depth(rewritten)] + [fx.depth(h[1]) for h in helpers])
    return SplitPlan(original=f, rewritten=rewritten, helpers=labels, depth_after=depth_after,
                     names=[h[0] for h in helpers], parts=[h[2] for h in helpers], carried=carried)


# ----------------------------------------------------------------------------- a part in a branch of IF (v6)

# v6, A-aera-10: functions whose result is always text; a helper that carries a condition for such a part holds
# empty text "" where the IF does not take the branch (0 for any other part)
_TEXT_RESULT = frozenset({
    "TEXT", "CONCAT", "CONCATENATE", "TEXTJOIN", "LEFT", "LEFTB", "RIGHT", "RIGHTB", "MID", "MIDB", "UPPER",
    "LOWER", "PROPER", "TRIM", "CLEAN", "SUBSTITUTE", "REPLACE", "REPLACEB", "REPT", "CHAR", "UNICHAR", "FIXED",
    "DOLLAR", "TEXTBEFORE", "TEXTAFTER", "ADDRESS"})
# v6, A-aera-10: the SPLIT-GUARD texts for a part in a branch of IF (plan section 5.1)
IF_REASON = ("the part {part} lies in argument {k} of IF({inner}); in a helper it would be calculated for every "
             "input, also where the IF does not take that branch")
GUARD_IF_FIX = ("apply the pattern of references/helper-patterns.md for a part in a branch of IF by hand (the helper "
                "holds IF(<condition>,<part>,0) or IF(<condition>,0,<part>)), or split this formula by hand; nothing "
                "was written")
IF_SHOWN = 80                                 # characters of the IF's arguments shown in the SPLIT-GUARD message


def _if_branches(a: Any, start: int, end: int) -> list[tuple[int, int]]:
    """(index in Analysis.calls, argument number) of each IF of `a` whose second or later argument holds
    [start, end), outermost first. An IF whose first argument (the condition) holds it is left out."""
    found = []
    for i, c in enumerate(a.calls):
        if c.name != "IF" or not (c.start <= start and end <= c.end) or (c.start, c.end) == (start, end):
            continue
        k = next((n + 1 for n, (s, e) in enumerate(c.args) if s <= start and end <= e), None)
        if k is not None and k >= 2:
            found.append((i, k))
    return sorted(found, key=lambda t: a.calls[t[0]].level)


def _width(call: Any) -> int:
    return call.end - call.start


def _rewrite(text: str, edits: list[tuple[int, int, str]], start: int, end: int) -> str:
    """text[start:end] with the edits that lie inside it applied."""
    out = text[start:end]
    for s, e, t in sorted((x for x in edits if start <= x[0] and x[1] <= end), reverse=True):
        out = out[:s - start] + t + out[e - start:]
    return out


def _gives_text(a: Any, part: Any) -> bool:
    """True when the part is a call of a function of _TEXT_RESULT."""
    call = next((c for c in a.calls if (c.start, c.end) == (part.start, part.end)), None)
    return call is not None and call.name in _TEXT_RESULT


# ----------------------------------------------------------------------------- over-long + and - chains (v2)

SHAPE_RULE = "SPLIT-SHAPE"
SHAPE_FIX = ("rewrite it by hand: put each part of the formula in its own helper cell and let the cell add or "
             "combine the helpers (references/helper-patterns.md, H10)")
REF_LENGTH_GUESS = 8                        # characters of a helper reference on split_helpers, e.g. "BH1048576"
_LOOSE_OPERATORS = frozenset({"&", "=", "<>", "<", ">", "<=", ">="})   # bind looser than + and -
_NESTING = frozenset({"FUNC", "PAREN", "ARRAY"})


class SplitShape(Exception):
    """The formula is over the length limit and is not a shape split rewrites (reported as SPLIT-SHAPE)."""

    def __init__(self, shape: str) -> None:
        super().__init__(shape)
        self.shape = shape


def chain_terms(formula: str) -> list[tuple[str, str]] | None:
    """The terms of a chain of + and - at the formula's top level: [(sign, term text)], the first sign "+" (a
    leading unary sign stays in the first term's text). None when the formula has one term only, holds & or a
    comparison at its top level (they bind looser than + and -), or cannot be read."""
    f = fx.display_form(formula)
    try:
        tokens = fx.tokenize(f)
    except ValueError:
        return None
    depth = 0
    cuts = []
    for t in tokens:
        if t.type in _NESTING and t.subtype == "OPEN":
            depth += 1
        elif t.type in _NESTING and t.subtype == "CLOSE":
            depth -= 1
        elif depth == 0 and t.type == "OPERATOR-INFIX":
            if t.text in ("+", "-"):
                cuts.append(t)
            elif t.text in _LOOSE_OPERATORS:
                return None
    if not cuts:
        return None
    terms: list[tuple[str, str]] = []
    start, sign = 1, "+"
    for t in cuts:
        terms.append((sign, f[start:t.start].strip()))
        start, sign = t.end, t.text
    terms.append((sign, f[start:].strip()))
    if any(not text for _s, text in terms):
        return None
    return terms


def _shape_of(formula: str) -> str:
    """Words for the shape of an over-long formula split does not rewrite (for the SPLIT-SHAPE finding)."""
    f = fx.display_form(formula)
    a = fx.analyze(f)
    body = f[1:].strip()
    outer = next((c for c in a.calls if c.parent is None and f[c.start:c.end].strip() == body), None)
    if outer is not None:
        return f"one call of {outer.name} with {len(outer.args)} arguments"
    try:
        tokens = fx.tokenize(f)
    except ValueError:
        return "a formula the tokenizer cannot read"
    depth, ops = 0, []
    for t in tokens:
        if t.type in _NESTING and t.subtype == "OPEN":
            depth += 1
        elif t.type in _NESTING and t.subtype == "CLOSE":
            depth -= 1
        elif depth == 0 and t.type == "OPERATOR-INFIX" and t.text.strip():
            ops.append(t.text)
    loose = sorted({o for o in ops if o in _LOOSE_OPERATORS})
    if loose:
        return f"top-level operators {', '.join(loose)}, which bind looser than + and -"
    if ops:
        return f"a chain of the operators {', '.join(sorted(set(ops)))} at the top level, not of + and -"
    if body.startswith("("):
        return "an expression inside one pair of parentheses"
    return "a single term"


def _invert(sign: str) -> str:
    return "-" if sign == "+" else "+"


def _group_text(terms: list[tuple[str, str]]) -> str:
    """The helper formula of a group of terms. A group whose first sign is "-" holds its terms with the signs
    turned round (the host subtracts it): -a+b-c = -(a-b+c), without a unary minus (Excel binds a unary minus
    tighter than ^, so "-a^2" would not be the same as "0-a^2")."""
    flip = terms[0][0] == "-"
    out = "=" + terms[0][1]
    for sign, text in terms[1:]:
        out += (_invert(sign) if flip else sign) + text
    return out


def plan_length_split(formula: str, *, length_limit: int, sheet: str | None = None,
                      ref_length: int = REF_LENGTH_GUESS, host_ref_length: int | None = None,
                      groups: list[int] | None = None, host: str = "A1") -> SplitPlan:
    """Split an over-long + and - chain into helpers of a few terms each (v2, A-aera-13).

    `sheet`: the host sheet when the helpers go to another sheet (their references then name it, which counts
    in their length). `ref_length` and `host_ref_length`: the characters a helper reference takes on the helper
    sheet and in the host cell. `groups`: the number of terms in each first-level helper (to split the cells of
    a block the same way). `host`: the cell that holds the formula; ROW() and COLUMN() without an argument in a
    term are written as ROW(<host>) and COLUMN(<host>) before the terms are measured (v7, A-aera-5). Raises
    SplitShape when the formula is not such a chain or a term alone is over the limit."""
    if not isinstance(length_limit, int) or length_limit < 1:
        raise common.Refusal(f"length limit {length_limit!r}: give a whole number 1 or more")
    f = fx.display_form(formula)
    terms = chain_terms(f)
    if terms is None:
        raise SplitShape(_shape_of(f))
    terms = [(sign, bind_position("=" + text, host)[1:]) for sign, text in terms]
    host_ref_length = ref_length if host_ref_length is None else host_ref_length

    def measure(text: str) -> int:
        return fx.analyze(qualify(text, sheet) if sheet else text).length

    cut: list[list[tuple[str, str]]] = []
    if groups is not None:
        if sum(groups) != len(terms) or any(n < 1 for n in groups):
            raise common.Refusal("the cells of the block do not split the same way",
                                 fix="split one cell at a time with --cell-only")
        k = 0
        for n in groups:
            cut.append(terms[k:k + n])
            k += n
    else:
        current: list[tuple[str, str]] = []
        for term in terms:
            if current and measure(_group_text(current + [term])) > length_limit:
                cut.append(current)
                current = []
            current.append(term)
        cut.append(current)
    for g in cut:
        if measure(_group_text(g)) > length_limit:
            if len(g) == 1:
                raise SplitShape(f"a chain of + and - whose term {_short(g[0][1], 60)} is over the length limit "
                                 f"{length_limit} by itself")
            raise common.Refusal("the cells of the block do not split the same way within the length limit",
                                 fix="split one cell at a time with --cell-only")
    helpers: list[tuple[str, str]] = []
    names: list[str] = []
    parts: list[str] = []
    level: list[tuple[str, str]] = []                     # (sign, helper name) the host adds
    first = 1
    for g in cut:
        name = PLACEHOLDER.format(len(helpers) + 1)
        text = _group_text(g)
        helpers.append((f"helper {len(helpers) + 1}: terms {first} to {first + len(g) - 1}", text))
        names.append(name)
        parts.append(text[1:])
        level.append((g[0][0], name))
        first += len(g)

    def chain_length(items: list[tuple[str, str]], each: int) -> int:
        return 1 + len(items) * each + (len(items) - 1)

    while chain_length(level, host_ref_length) > length_limit:
        per = length_limit // (ref_length + 1)             # 1 + n * ref_length + (n - 1) <= length_limit
        if per < 2 or len(level) < 2:
            raise SplitShape(f"a chain of + and - whose helpers cannot be added within the length limit "
                             f"{length_limit}")
        upper: list[tuple[str, str]] = []
        for i in range(0, len(level), per):
            g = level[i:i + per]
            if len(g) == 1:                                # one helper left over: the next level adds it
                upper.append(g[0])
                continue
            name = PLACEHOLDER.format(len(helpers) + 1)
            text = _group_text(g)
            numbers = ", ".join(str(names.index(n) + 1) for _s, n in g)
            helpers.append((f"helper {len(helpers) + 1}: adds helpers {numbers}", text))
            names.append(name)
            parts.append(text[1:])
            upper.append((g[0][0], name))
        level = upper
    if len(helpers) > MAX_HELPERS:
        raise common.Refusal(f"the formula needs more than {MAX_HELPERS} helper cells",
                             fix="rewrite it by hand in smaller steps")
    rewritten = "=" + level[0][1] + "".join(sign + name for sign, name in level[1:])
    depth_after = max([fx.depth(rewritten)] + [fx.depth(h) for _l, h in helpers])
    return SplitPlan(original=f, rewritten=rewritten, helpers=helpers, depth_after=depth_after, names=names,
                     parts=parts, kind="length", groups=[len(g) for g in cut])


def qualify(formula: str, sheet: str) -> str:
    """Give every reference that names no sheet the sheet `sheet` (for a formula moved to another sheet)."""
    a = fx.analyze(formula)
    out = a.formula
    prefix = common.quote_sheet(sheet) + "!"
    for r in sorted(a.refs, key=lambda r: r.start, reverse=True):
        if r.sheet is None and r.workbook is None and r.kind in ("cell", "range", "column", "row"):
            text = r.text
            lead = "@" if text.startswith("@") else ""
            out = out[:r.start] + lead + prefix + text[len(lead):] + out[r.end:]
    return out


# ----------------------------------------------------------------------------- the host block

def _same_block(sheet: Any, col: int, row: int, form: str) -> tuple[int, int, int, int]:
    """The rectangle around (col, row) whose cells hold the same relative form: the column run first, then the
    columns beside it whose cells over the same rows all match."""
    def matches(r: int, c: int) -> bool:
        cell = sheet.at(r, c)
        if cell is None or cell.formula is None or cell.formula_kind not in (None, "normal"):
            return False
        try:
            return fx.relative_form(cell.formula, cell.coord) == form
        except ValueError:
            return False
    r1 = r2 = row
    while r1 > 1 and matches(r1 - 1, col):
        r1 -= 1
    while matches(r2 + 1, col):
        r2 += 1
    c1 = c2 = col
    while c1 > 1 and all(matches(r, c1 - 1) for r in range(r1, r2 + 1)):
        c1 -= 1
    while all(matches(r, c2 + 1) for r in range(r1, r2 + 1)):
        c2 += 1
    return c1, r1, c2, r2


def _limit_for(src: Path, cli_limit: int | None) -> tuple[int, str]:
    if cli_limit is not None:
        return cli_limit, "--limit"
    try:
        from . import manifest as man
        m = man.load_manifest(src)
    except common.RotmError:
        m = None
    if m and isinstance(m.get("settings"), dict) and isinstance(m["settings"].get("depth_limit"), int):
        return int(m["settings"]["depth_limit"]), "the model's manifest"
    return fx.DEPTH_LIMIT_DEFAULT, "the default"


def _length_limit_for(src: Path) -> tuple[int, str]:
    """The length limit: the model's manifest (settings.length_limit), else the default (100 characters)."""
    try:
        from . import manifest as man
        m = man.load_manifest(src)
    except common.RotmError:
        m = None
    if m and isinstance(m.get("settings"), dict) and isinstance(m["settings"].get("length_limit"), int):
        return int(m["settings"]["length_limit"]), "the model's manifest"
    return fx.LENGTH_LIMIT_DEFAULT, "the default"


# ----------------------------------------------------------------------------- errors a guard hid (v3, A-aera-9)

GUARD_RULE = "SPLIT-GUARD"
GUARD_FIX = ("keep the guard around the moved part (references/helper-patterns.md H5) or split this formula by "
             "hand; nothing was written")
# v4, A-aera-14: the fix of SPLIT-GUARD for a part under IFERROR or IFNA that is not a lookup split moves
GUARD_PART_FIX = "apply the pattern of references/helper-patterns.md H5 by hand, or split this formula by hand"
GUARD_SHOWN = 50                              # SPLIT-GUARD findings listed; the rest are counted in a note
_ERROR_GUARDS = ("IFERROR", "IFNA")
# Other functions whose result can leave out an error value of a part: a branch not taken (IF, IFS, SWITCH,
# CHOOSE) or a test of the value (the IS functions, ERROR.TYPE), or AGGREGATE with its ignore-errors option.
_OTHER_GUARDS = ("IF", "IFS", "SWITCH", "CHOOSE", "ISERROR", "ISERR", "ISNA", "ISNUMBER", "ISTEXT", "ISNONTEXT",
                 "ISLOGICAL", "ISBLANK", "ERROR.TYPE", "AGGREGATE")


@dataclass
class HelperLink:
    """One helper cell split writes, with the host cell it serves and the part of the original it holds."""
    host_sheet: str
    host: str                                  # host cell, e.g. "B1"
    helper: str                                # cell on split_helpers, e.g. "B2"
    original: str                              # the host's formula before the split (display form)
    part: str                                  # the moved part, as in the original


@dataclass
class GuardProblem:
    host_sheet: str
    host: str
    helper: str
    error: str                                 # the error value, e.g. "#DIV/0!"
    guard: str | None                          # IFERROR, IFNA, another function of _OTHER_GUARDS, or None

    def message(self) -> str:
        hid = f"which the original formula's {self.guard} hid" if self.guard else \
            "which the original formula did not show"
        return (f"{HELPER_SHEET}!{self.helper} holds {self.error}, {hid}; split would put an error into the "
                "workbook")


def guard_name(original: str, part: str) -> str | None:
    """The function of `original` around the moved `part` that kept an error of the part out of the result: the
    innermost IFERROR or IFNA around it, else the innermost function of _OTHER_GUARDS around it, else None."""
    f = fx.display_form(original)
    a = fx.analyze(f)
    if not a.ok:
        return None
    text = (part or "").strip()
    starts = [m.start() for m in re.finditer(re.escape(text), f, re.I)] if text else []
    best: str | None = None
    for group in (_ERROR_GUARDS, _OTHER_GUARDS):
        found = []
        for s in starts:
            e = s + len(text)
            for c in a.calls:
                name = c.name.upper().replace("_XLFN.", "")
                if name in group and c.start <= s and e <= c.end and (c.start, c.end) != (s, e):
                    found.append((c.end - c.start, name))
        if found:
            best = min(found)[1]                 # the innermost: the shortest call around the part
            break
    return best


class SplitGuard(Exception):
    """A part to move sits inside IFERROR or IFNA and is not the INDEX or MATCH call of a guarded lookup (v4,
    A-aera-14); split_cell reports it as the error SPLIT-GUARD. `guard`: IFERROR or IFNA; `part`: the part as
    written; `reason`: other words after the host cell (the lookup whose pattern H5 is over the depth limit, or a
    lookup inside IFNA inside IFERROR, v5)."""

    fix = GUARD_PART_FIX

    def __init__(self, guard: str, part: str, reason: str | None = None) -> None:
        super().__init__(reason or f"the part {part} sits inside {guard}")
        self.guard = guard
        self.part = part
        self.reason = reason

    def message(self, host: str) -> str:
        if self.reason:
            return f"{host}: {self.reason}; nothing was written"
        return (f"{host}: the part {self.part} sits inside {self.guard}, and split moves a guarded part only for a "
                "lookup (INDEX with MATCH); nothing was written")

    def data(self) -> dict[str, Any]:
        """Fields of the finding beyond host, guard and part."""
        return {}


class SplitBranch(SplitGuard):
    """A part to move lies in argument `argument` (2 or 3) of the IF `if_text`, and a helper cannot carry that IF's
    condition within the limits (v6, A-aera-10); split_cell reports it as the error SPLIT-GUARD with the fix
    GUARD_IF_FIX. `why`: the reason the condition cannot be carried (in the finding's data and a note)."""

    fix = GUARD_IF_FIX

    def __init__(self, part: str, if_text: str, argument: int, why: str) -> None:
        inner = if_text[3:-1] if if_text.upper().startswith("IF(") and if_text.endswith(")") else if_text
        super().__init__("IF", part, reason=IF_REASON.format(part=_short(part, fx.MESSAGE_TEXT_MAX), k=argument,
                                                              inner=_short(inner, IF_SHOWN)))
        self.if_text = if_text
        self.argument = argument
        self.why = why

    def message(self, host: str) -> str:
        return f"{host}: {self.reason}"

    def data(self) -> dict[str, Any]:
        return {"if": self.if_text, "argument": self.argument, "why": self.why}


# v7, A-aera-5: the reason and the fix of SPLIT-GUARD for a ROW() or COLUMN() call split cannot bind to the host
POSITION_REASON = ("the call {call} gives the position of the cell that holds it, and split could not write the "
                   "cell {host} as its argument in the helper; in a helper it would give the helper's position")
POSITION_FIX = "write the cell as the argument of the call by hand ({name}({host})), then split again"


class SplitPosition(SplitGuard):
    """A ROW() or COLUMN() call without an argument in a part that moves to a helper, which bind_position cannot
    write with the host cell as its argument (v7, A-aera-5); split_cell reports it as the error SPLIT-GUARD, which
    names the call. Nothing is written."""

    fix = POSITION_FIX

    def __init__(self, call: str, host: str) -> None:
        name = call.split("(")[0].strip().upper()
        super().__init__(name, call, reason=POSITION_REASON.format(call=_short(call, fx.MESSAGE_TEXT_MAX), host=host))
        self.call = call
        self.host = host
        self.fix = POSITION_FIX.format(name=name, host=host)

    def data(self) -> dict[str, Any]:
        return {"call": self.call}


@dataclass(frozen=True)
class _Lookup:
    """IFERROR(INDEX(<range>,MATCH(<key>,<keys>[,<type>])),<value>) or the same with IFNA, in one analysis."""
    guard: int                                 # index of the IFERROR or IFNA call in Analysis.calls
    spans: frozenset[tuple[int, int]]          # (start, end) of the INDEX call and of the MATCH call
    range: str                                 # INDEX's first argument, as written
    match: str                                 # the MATCH call, as written
    value: str                                 # the guard's second argument, as written
    index: str = ""                            # the INDEX call, as written (v5)


@dataclass(frozen=True)
class _Pattern:
    """The texts of the pattern H5 (v5, A-aera-5 and C-sol-9), with the helpers written as given."""
    position: str                              # helper 1: =<G>(MATCH(...),0)
    floor: str                                 # helper 2: =MAX(<helper 1>,1)
    test: str                                  # helper 3: =--<T>(INDEX(<range>,<helper 2>))
    host: str                                  # in place of the guard call


# v5, A-aera-5 and C-sol-9: the reason of SPLIT-GUARD for a lookup inside IFNA that sits inside IFERROR
MIXED_REASON = ("the lookup {lookup} sits inside IFNA, which sits inside IFERROR; split cannot keep the error "
                "behaviour of both guards with helper cells (a helper that keeps IFNA's result would show the error "
                "values that IFERROR hid), so it does not rewrite this lookup")


def _error_guards(a: Any, start: int, end: int) -> list[int]:
    """Indices of the IFERROR and IFNA calls of `a` around [start, end) (the span itself left out), innermost
    first."""
    found = [i for i, c in enumerate(a.calls)
             if c.name in _ERROR_GUARDS and c.start <= start and end <= c.end and (c.start, c.end) != (start, end)]
    return sorted(found, key=lambda i: a.calls[i].end - a.calls[i].start)


def _guarded_lookup(a: Any, gi: int) -> _Lookup | None:
    """The lookup of the pattern H5 when call `gi` is IFERROR or IFNA whose first argument is exactly
    INDEX(<range>,MATCH(...)) with two INDEX arguments and two or three MATCH arguments; else None."""
    f = a.formula
    g = a.calls[gi]
    if g.name not in _ERROR_GUARDS or len(g.args) != 2:
        return None

    def child(parent: int, name: str, text: str) -> int | None:
        return next((i for i, c in enumerate(a.calls)
                     if c.parent == parent and c.name == name and f[c.start:c.end] == text), None)

    ii = child(gi, "INDEX", f[g.args[0][0]:g.args[0][1]].strip())
    if ii is None or len(a.calls[ii].args) != 2:
        return None
    ix = a.calls[ii]
    mi = child(ii, "MATCH", f[ix.args[1][0]:ix.args[1][1]].strip())
    if mi is None or len(a.calls[mi].args) not in (2, 3):
        return None
    m = a.calls[mi]
    return _Lookup(guard=gi, spans=frozenset({(ix.start, ix.end), (m.start, m.end)}),
                   range=f[ix.args[0][0]:ix.args[0][1]].strip(), match=f[m.start:m.end],
                   value=f[g.args[1][0]:g.args[1][1]].strip(), index=f[ix.start:ix.end])


def _mixed_guards(a: Any, lk: _Lookup, guards: list[int]) -> bool:
    """True when the lookup's guard is IFNA and an IFERROR encloses it (`guards`: the guards around the part,
    innermost first); such a lookup is refused (v5, A-aera-5 and C-sol-9)."""
    return a.calls[lk.guard].name == "IFNA" and any(a.calls[i].name == "IFERROR" for i in guards if i != lk.guard)


def _pattern_texts(a: Any, lk: _Lookup, h1: str, h2: str, h3: str) -> _Pattern:
    """The texts of the pattern H5 for `lk` (v5, A-aera-5 and C-sol-9), with helpers 1, 2 and 3 written as `h1`,
    `h2` and `h3`. Helper 1 and the test of helper 3 take the lookup's own guard: IFERROR and ISERROR, or IFNA and
    ISNA (a lookup inside IFNA inside IFERROR is refused before, see _mixed_guards)."""
    fn = a.calls[lk.guard].name
    test = "ISERROR" if fn == "IFERROR" else "ISNA"
    return _Pattern(position=f"={fn}({lk.match},0)", floor=f"=MAX({h1},1)",
                    test=f"=--{test}(INDEX({lk.range},{h2}))",
                    host=f"IF(OR({h1}=0,{h3}=1),{lk.value},INDEX({lk.range},{h2}))")


def _number(name: str) -> int:
    """The helper number of a helper name _rotm_h<k>."""
    return int(_PLACEHOLDER_RE.fullmatch(name).group(1))


def guarded_lookup_call(formula: str, part: Any) -> str | None:
    """For an over-limit part of `formula` (a lib.formula.Part): "INDEX" or "MATCH" when the part is that call of a
    guarded lookup, the innermost IFERROR or IFNA around it holding exactly INDEX(<range>,MATCH(...)) (the case
    split rewrites with the pattern H5); else None."""
    return (lookup_pattern(formula, part) or (None,))[0]


def lookup_pattern(formula: str, part: Any) -> tuple[str, int, str, str] | None:
    """For the INDEX or MATCH call of a guarded lookup (see guarded_lookup_call): ("INDEX" or "MATCH", the level of
    the guard call (1 = outermost), helper 1's formula, the IF text that takes the guard call's place with helpers
    1, 2 and 3 written as _rotm_h1, _rotm_h2 and _rotm_h3); None for any other part, and for a lookup inside IFNA
    that sits inside IFERROR, which split refuses (v5). Helper 3 has the depth of the IF's INDEX part, so the IF's
    depth is the deepest of the pattern after helper 1."""
    a = fx.analyze(formula)
    if not a.ok:
        return None
    guards = _error_guards(a, part.start, part.end)
    lk = _guarded_lookup(a, guards[0]) if guards else None
    if lk is None or (part.start, part.end) not in lk.spans or _mixed_guards(a, lk, guards):
        return None
    pat = _pattern_texts(a, lk, *(PLACEHOLDER.format(k) for k in (1, 2, 3)))
    call = "MATCH" if a.formula[part.start:part.end] == lk.match else "INDEX"
    return call, a.calls[lk.guard].level, pat.position, pat.host


def guard_problems(links: list[HelperLink], before_values: dict[tuple[str, str], Any],
                   after_values: dict[tuple[str, str], Any]) -> list[GuardProblem]:
    """For each host cell, the first of its helpers (in calculation order) that holds an error value after the
    recalculation while the host cell held no error before the split."""
    from . import workbook as wbk

    def is_error(v: Any) -> bool:
        return isinstance(v, str) and v in wbk.ERROR_VALUES

    out: list[GuardProblem] = []
    done: set[tuple[str, str]] = set()
    for link in links:
        key = (link.host_sheet, link.host)
        if key in done:
            continue
        value = after_values.get((HELPER_SHEET, link.helper))
        if not is_error(value) or is_error(before_values.get(key)):
            continue
        done.add(key)
        out.append(GuardProblem(link.host_sheet, link.host, link.helper, str(value),
                                guard_name(link.original, link.part)))
    return out


def _guard_check(links: list[HelperLink]) -> Any:
    """The new_cells_check of compat.write_edits for split: SPLIT-GUARD findings; returns their number."""
    def check(before: Any, after: Any, report: Any) -> int:
        problems = guard_problems(links, before.values, after.values)
        for p in problems[:GUARD_SHOWN]:
            report.error(GUARD_RULE, p.message(), GUARD_FIX, sheet=HELPER_SHEET, cell=p.helper,
                         data={"host": f"{p.host_sheet}!{p.host}", "error": p.error, "guard": p.guard})
        if len(problems) > GUARD_SHOWN:
            report.note(f"{len(problems) - GUARD_SHOWN} more {GUARD_RULE} findings are not listed")
        return len(problems)
    return check


def _helper_look(src: Path, areas: list[tuple[str, str, str]]) -> Any:
    """compat.CellLook for the cells split writes on split_helpers: the dictionary the theme command uses for
    this workbook (the model's override when it sits in a model folder), at the size of a helpers sheet."""
    from . import compat
    from . import theme
    d = theme._load_dictionary(src, None, ())
    return compat.CellLook(dictionary=d, size=d.sheet_size("helpers"), areas=areas)


def _guard_refusal(report: Any, exc: SplitGuard, sheet: str, cell: str) -> Any:
    """The error SPLIT-GUARD for a part split does not move (a SplitGuard or a SplitBranch); nothing is written."""
    host = f"{sheet}!{cell}"
    report.error(GUARD_RULE, exc.message(host), exc.fix, sheet=sheet, cell=cell,
                 data={"host": host, "guard": exc.guard, "part": exc.part, **exc.data()})
    if isinstance(exc, SplitBranch):
        report.note(f"why the helper cannot carry the condition: {exc.why}")
    report.note("nothing was written")
    report.finish(False)
    return report


def _shape_finding(report: Any, sheet: str, cell: str, length: int, length_limit: int, depth_limit: int,
                   shape: str) -> None:
    report.error(SHAPE_RULE, f"{sheet}!{cell} has length {length:,} characters, over the length limit "
                 f"{length_limit}, and is within the depth limit {depth_limit}; split rewrites such a formula only "
                 f"when it is a chain of + and - terms at its top level, and this one is {shape}; nothing was "
                 "written", SHAPE_FIX, sheet=sheet, cell=cell,
                 data={"shape": shape, "length": length, "length_limit": length_limit})


# ----------------------------------------------------------------------------- the command

def split_cell(path: str | Path, ref: str, *, out: str | Path | None = None, in_place: bool = False,
               limit: int | None = None, spec: str | Path | None = None, cell_only: bool = False,
               calc_tolerance: float | None = None) -> Any:
    """With `spec`: refused in this version (what to do instead is in the message); else add helpers on sheet
    split_helpers. `calc_tolerance`: relative tolerance for calculated values in the comparison (default
    workbook.DEFAULT_CALC_TOLERANCE); typed values are compared exactly. Returns a common.Report."""
    from . import compat
    from . import workbook as wbk
    if spec is not None:
        raise common.Refusal(
            "split --spec is not available in this version",
            key="--spec",
            fix="a built model's formulas come from its specification, and rotm.py build refuses an expression "
                "deeper than the limit and names the part to move: move that part into a helper column (helpers "
                "sheet), a line item or a measure of the specification, then run rotm.py build again "
                "(references/model-spec-format.md)")
    src = Path(common.native_path(path)).absolute()
    info = wbk.open_workbook(src, with_values=False, with_styles=False)
    sheet_part, addr = common.parse_ref(ref)
    names = [s.name for s in info.sheets if s.kind == "worksheet"]
    if sheet_part is None:
        raise common.Refusal(f"'{ref}' names no sheet", key="ref",
                             fix=f"write the cell as sheet!cell, e.g. {common.quote_sheet(names[0]) if names else 'calc'}!D7")
    sheet_name = next((n for n in names if n == sheet_part), None) or next(
        (n for n in names if n.casefold() == sheet_part.casefold()), None)
    if sheet_name is None:
        raise common.Refusal(f"the workbook has no sheet '{sheet_part}'", key="ref",
                             fix=f"use one of: {', '.join(names[:12])}")
    sheet = info.sheet(sheet_name)
    try:
        bc1, br1, bc2, br2 = common.range_bounds(addr)
    except ValueError:
        raise common.Refusal(f"'{addr}' is not a cell or a range", key="ref", fix="give a cell such as calc!D7") from None
    first = sheet.at(br1, bc1)
    if first is None or first.formula is None:
        raise common.Refusal(f"{sheet_name}!{common.col_letter(bc1)}{br1} holds no formula", key="ref",
                             fix="give the cell whose formula is over the limit (the audit's F-DEPTH finding names it)")
    if first.formula_kind not in (None, "normal"):
        raise common.Refusal(f"{sheet_name}!{first.coord} holds an array formula or a data table, which split does not "
                             "rewrite", key="ref", fix="rewrite it by hand in helper cells")
    lim, lim_source = _limit_for(src, limit)
    length_lim, length_source = _length_limit_for(src)
    tolerance = compat.check_calc_tolerance(calc_tolerance)
    report = common.Report(command="split", target=src.as_posix())
    report.data.update(limit=lim, limit_source=lim_source, length_limit=length_lim, length_limit_source=length_source,
                       output=None, written=False)
    a = fx.analyze(first.formula)
    if a.depth <= lim and a.length <= length_lim:
        report.info("SPLIT-NONE", f"{sheet_name}!{first.coord} has nesting depth {a.depth}, within the limit {lim}, "
                    f"and length {a.length:,} characters, within the limit {length_lim}; nothing to split",
                    "nothing to change", sheet=sheet_name, cell=first.coord)
        report.note("nothing was written")
        report.finish(False)
        return report
    kind = "depth" if a.depth > lim else "length"
    report.data.update(kind=kind, length_before=a.length)
    if kind == "length" and chain_terms(first.formula) is None:
        _shape_finding(report, sheet_name, first.coord, a.length, length_lim, lim, _shape_of(first.formula))
        report.note("nothing was written")
        report.finish(False)
        return report
    form = fx.relative_form(first.formula, first.coord)
    if (bc1, br1) != (bc2, br2):
        box = (bc1, br1, bc2, br2)
        for r in range(br1, br2 + 1):
            for c in range(bc1, bc2 + 1):
                cell = sheet.at(r, c)
                if cell is None or cell.formula is None or fx.relative_form(cell.formula, cell.coord) != form:
                    raise common.Refusal(f"{sheet_name}!{common.col_letter(c)}{r} does not hold the same formula as "
                                         f"{first.coord}", key="ref",
                                         fix="give one cell (split finds the cells with the same formula) or a range "
                                             "whose cells all hold the same formula")
    elif cell_only:
        box = (bc1, br1, bc1, br1)
    else:
        box = _same_block(sheet, bc1, br1, form)
    c1, r1, c2, r2 = box
    width = c2 - c1 + 1
    # helper sheet layout
    existing = info.sheet(HELPER_SHEET)
    if existing is not None and existing.kind != "worksheet":
        raise common.Refusal(f"'{HELPER_SHEET}' is a chart sheet", fix="rename that sheet and run split again")
    base = (existing.max_col + 2) if existing is not None and existing.cells else 2
    off = 1 if r1 == 1 else 0
    on_helper_sheet = sheet_name == HELPER_SHEET
    plans: dict[tuple[int, int], SplitPlan] = {}
    if kind == "depth":
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                cell = sheet.at(r, c)
                try:
                    plans[(r, c)] = plan_split(cell.formula, limit=lim, host=cell.coord)
                except SplitGuard as exc:      # v4, A-aera-14: a part under IFERROR or IFNA, whatever the values;
                    # v6, A-aera-10: a part in a branch of IF whose condition a helper cannot carry (SplitBranch)
                    return _guard_refusal(report, exc, sheet_name, cell.coord)
    else:
        # the longest helper reference the layout can give (column of helper MAX_HELPERS, the block's last row)
        ref_len = len(common.col_letter(base + MAX_HELPERS * width)) + len(str(r2 + off))
        host_ref_len = ref_len + (0 if on_helper_sheet else len(HELPER_SHEET) + 1)
        qual = None if on_helper_sheet else sheet_name
        try:
            lead = plan_length_split(first.formula, length_limit=length_lim, sheet=qual, ref_length=ref_len,
                                     host_ref_length=host_ref_len, host=first.coord)
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    cell = sheet.at(r, c)
                    plans[(r, c)] = plan_length_split(cell.formula, length_limit=length_lim, sheet=qual,
                                                      ref_length=ref_len, host_ref_length=host_ref_len,
                                                      groups=lead.groups, host=cell.coord)
        except SplitPosition as exc:           # v7, A-aera-5: a ROW() or COLUMN() that cannot be bound
            return _guard_refusal(report, exc, sheet_name, first.coord)
        except SplitShape as exc:
            _shape_finding(report, sheet_name, first.coord, a.length, length_lim, lim, exc.shape)
            report.note("nothing was written")
            report.finish(False)
            return report
    k_count = len(plans[(r1, c1)].helpers)
    if any(len(p.helpers) != k_count for p in plans.values()):
        raise common.Refusal("the cells of the block do not split the same way", key="ref",
                             fix="split one cell at a time with --cell-only")
    edits: list[Any] = []
    host_cells: set[tuple[str, str]] = set()
    helper_rows = (r1 + off, r2 + off)
    first_plan = plans[(r1, c1)]
    block_text = _box(c1, r1, c2, r2)
    areas: list[tuple[str, str, str]] = []           # the dictionary's look for every cell written on split_helpers
    for k in range(k_count):
        col = base + k * width
        label = (f"{sheet_name}!{block_text} {first_plan.helpers[k][0]}")
        edits.append(compat.CellEdit(HELPER_SHEET, f"{common.col_letter(col)}1", value=label[:250]))
        areas.append((HELPER_SHEET, f"{common.col_letter(col)}1", "col_header"))
        areas.append((HELPER_SHEET, _box(col, helper_rows[0], col + width - 1, helper_rows[1]), "calc"))
    links: list[HelperLink] = []
    written: list[dict[str, Any]] = []
    too_long: SplitBranch | None = None              # v6, A-aera-10: a helper that carries a condition, too long
    too_long_at = ""
    for (r, c), p in sorted(plans.items()):
        refs_on_helper = {}
        refs_from_host = {}
        for k, name in enumerate(p.names):
            hc = common.a1(base + k * width + (c - c1), r + off)
            refs_on_helper[name.lower()] = hc
            refs_from_host[name.lower()] = hc if on_helper_sheet else f"{HELPER_SHEET}!{hc}"
        qualified = SplitPlan(p.original, p.rewritten,
                              [(lab, h if on_helper_sheet else qualify(h, sheet_name)) for lab, h in p.helpers],
                              p.depth_after, p.names, p.parts, p.kind, p.groups)
        host_text, helper_texts = qualified.resolve(refs_on_helper, host_refs=refs_from_host)
        host_coord = common.a1(c, r)
        host_length = fx.analyze(host_text).length
        if p.carried and host_length > length_lim and too_long is None:
            first_carried = p.carried[next(n for n in p.names if n in p.carried)]
            too_long_at = host_coord
            too_long = SplitBranch(first_carried[2], first_carried[0], first_carried[1],
                                   f"the cell {sheet_name}!{host_coord} would have length {host_length:,} characters "
                                   f"after the split, over the length limit {length_lim} ({length_source})")
        edits.append(compat.CellEdit(sheet_name, host_coord, formula=fx.to_file_formula(host_text)))
        host_cells.add((sheet_name, host_coord))
        for k, ht in enumerate(helper_texts):
            hc = refs_on_helper[p.names[k].lower()]
            edits.append(compat.CellEdit(HELPER_SHEET, hc, formula=fx.to_file_formula(ht)))
            links.append(HelperLink(sheet_name, host_coord, hc, p.original, p.parts[k] if k < len(p.parts) else ""))
            origin = p.carried.get(p.names[k])
            n = fx.analyze(ht).length if origin else 0
            if origin and n > length_lim and too_long is None:
                too_long_at = host_coord
                too_long = SplitBranch(origin[2], origin[0], origin[1],
                                       f"the helper that carries the condition, {HELPER_SHEET}!{hc}, would have "
                                       f"length {n:,} characters, over the length limit {length_lim} "
                                       f"({length_source})")
        if len(written) < 50:
            written.append({"cell": f"{sheet_name}!{host_coord}", "before": p.original, "after": host_text,
                            "helpers": [{"cell": f"{HELPER_SHEET}!{refs_on_helper[n.lower()]}", "formula": t}
                                        for n, t in zip(p.names, helper_texts)]})
    if too_long is not None:                         # v6, A-aera-10: refused before anything is written
        return _guard_refusal(report, too_long, sheet_name, too_long_at)
    # every written formula within the limit (by construction; checked by code)
    over = [e for e in edits if e.formula is not None and fx.depth(e.formula) > lim]
    if over:  # pragma: no cover
        raise common.RotmError(f"split produced a formula over the limit: {over[0].sheet}!{over[0].cell}")
    lengths = [fx.analyze(e.formula).length for e in edits if e.formula is not None]
    if kind == "length" and max(lengths) > length_lim:
        raise common.Refusal(f"split could not keep every written formula within the length limit {length_lim} "
                             f"(the longest has {max(lengths):,} characters); nothing was written", key="ref",
                             fix="split one cell at a time with --cell-only, or rewrite it by hand "
                                 "(references/helper-patterns.md, H10)")
    report.data.update(block=f"{sheet_name}!{block_text}", cells=(r2 - r1 + 1) * width, helpers_per_cell=k_count,
                       helper_sheet=HELPER_SHEET, depth_before=a.depth, depth_after=first_plan.depth_after,
                       length_after=max(lengths), parts=first_plan.parts, examples=written)
    if info.manifest:
        report.warning("SPLIT-BUILT", "this workbook was built from a model specification: the next rotm.py build "
                       "replaces it and drops split_helpers", "move the part into a helper column or line item of "
                       "the specification and build again (references/model-spec-format.md)")
    target = compat.plan_output(src, out, in_place, "split")
    intended = wbk.Intended(kind="cells", cells=host_cells)
    if existing is None:
        intended.new_sheets = {HELPER_SHEET}
    else:
        intended.new_rows = {HELPER_SHEET: (1, helper_rows[1])}
    compat.write_edits(src, target, edits, command="split", report=report, intended=intended,
                       new_sheets=[] if existing is not None else [HELPER_SHEET], after_sheet=sheet_name,
                       in_place=in_place, calc_tolerance=tolerance, look=_helper_look(src, areas),
                       new_cells_check=_guard_check(links))
    if report.data.get("written"):
        n = (r2 - r1 + 1) * width
        what = (f"depth at most {lim}" if kind == "depth" else
                f"length at most {length_lim} characters (a chain of + and - in {k_count} helpers)")
        report.info("SPLIT-DONE", f"{n} cell(s) of {sheet_name}!{block_text} now have {what}; "
                    f"{k_count} helper cell(s) each on {HELPER_SHEET}", "nothing to change", sheet=sheet_name,
                    cell=common.a1(c1, r1))
    report.finish(False)
    if report.data.get("written") and not report.verified.get("recalculated") and report.exit_code == common.EXIT_OK:
        report.exit_code, report.status = common.EXIT_NOT_AVAILABLE, "not_verified"
    return report


def _box(c1: int, r1: int, c2: int, r2: int) -> str:
    a = common.a1(c1, r1)
    return a if (c1, r1) == (c2, r2) else f"{a}:{common.a1(c2, r2)}"


def add_split_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("workbook")
    p.add_argument("ref", help="the cell whose formula is over the limit, e.g. calc!D7")
    p.add_argument("-o", "--out", help="output file (default <stem>_split.xlsx)")
    p.add_argument("--in-place", action="store_true", help="replace the workbook (a .bak_<time>.xlsx copy is kept)")
    p.add_argument("--limit", type=int, help="depth limit (default 2)")
    p.add_argument("--spec", help="the model specification of a built model (rewrite the spec, then rebuild)")
    p.add_argument("--cell-only", action="store_true",
                   help="split only this cell, not the cells around it that hold the same formula")
    from . import compat
    compat.add_calc_tolerance_argument(p)
    p.add_argument("--json", metavar="FILE", help="write the report as JSON ('-' = stdout)")
    p.add_argument("--quiet", action="store_true", help="print only the summary line")


def run_split(args: argparse.Namespace) -> int:
    report = split_cell(args.workbook, args.ref, out=args.out, in_place=args.in_place, limit=args.limit,
                        spec=args.spec, cell_only=args.cell_only,
                        calc_tolerance=getattr(args, "calc_tolerance", None))
    return report.emit(args.json, quiet=getattr(args, "quiet", False))
