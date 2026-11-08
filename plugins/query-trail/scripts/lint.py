"""lint.py: rules N1 to N9 and the alias lint on B, A and every step (design spec v1 sections 2.1 G9, 2.2 P4 and P5).

Rules (sqlglot AST; research_r2/T03_work/nondet_lint.py and T04 lint_step as sources):

| Rule | Severity | Detects |
|---|---|---|
| N1 | ERROR | ROW_NUMBER, NTILE, LAG, LEAD, FIRST/LAST/NTH_VALUE without a total order (no ORDER BY, or an ORDER BY that covers no declared unique key; run_config lint.unique_keys) |
| N2 | ERROR over a join, else WARN | SELECT * (schema drift; collision under a join) |
| N3 | ERROR | implicit CROSS JOIN (a join without ON/USING and no linking equality); WARN for an explicit CROSS JOIN or a comma join linked in WHERE |
| N4 | ERROR | colliding output names (case-insensitive; BigQuery renames them id_1 silently, L BQ02) |
| N5 | ERROR | non-deterministic functions (RAND, GENERATE_UUID, CURRENT_*, SESSION_USER), LIMIT without ORDER BY, ARRAY_AGG/STRING_AGG without ORDER BY, ANY_VALUE, TABLESAMPLE (conductor decision: ERROR, waivable at G9) |
| N6 | WARN | division whose denominator is neither NULLIF(...) nor a non-zero literal (use NULLIF or SAFE_DIVIDE) |
| N7 | WARN | unqualified column in a SELECT that reads more than one source |
| N8 | WARN | SELECT DISTINCT over a join (may hide a fan-out) |
| N9 | WARN | UNION without ALL (silent de-duplication) |
| A1 | ERROR | computed select expression without an alias (engines auto-name differently: sum(x) vs _col_5, T04) |
| B1 | WARN | a table the metric card's `bindings.tables` declares that B does not read (fix r6, cold reviewer LOW; no number is wrong, but Max should see it) |
| B2 | WARN | a warehouse table B joins none of whose columns is used outside its own join condition (fix r11 L8, live run 06: a LEFT JOIN to qt_users read no column; fix r12 M1: the text depends on the join kind). LEFT JOIN: "joined but unused: <table>; drop it at G2, or say which column it supplies". INNER, plain JOIN, CROSS, RIGHT, FULL or a comma join: dropping it changes the rows, so never "drop it": "INNER JOIN to <table> keeps only rows with a match and no column of it is read; dropping it changes the result: confirm whether the filter is intended (bind it as a filter row at G3, or drop the table at G2 if not)" (CROSS: "repeats each row once per row of it"; RIGHT/FULL: "keeps every row ... matched or not"). Aliases are resolved; a column in WHERE, SELECT, GROUP BY, HAVING, ORDER BY or another join's ON counts as used, and so does a qualified `u.*` or a column written with the table's name or full path; for a comma join the WHERE equalities linking it are its join condition; a SELECT * or an unqualified column in that SELECT counts as possibly used (N7 warns about the latter), so no B2 there; a CTE joined is not a table |
| A2 | WARN | bare column renamed (col AS other): names are preserved until P10 (D-09), logged as a rename; INFO when the new name is one the output contract pre-assigns (an intended alias, DB-02; fix r1, spec v2 2.2 P5) |

Exit 2 wording (fix r3 B3-4, spec v4 1.4): "refused; no warehouse call was made; local effects: <files written, changed or removed, or none>" on stderr (lib/refusal.py).
Waivers come from run_config lint_waivers (rule, target, optional match, reason): G9. Exit 1 on any
unwaived ERROR, else 0; lint_report.csv is written unless the run is refused (D-04 writer). B and A are
read only after lib.freeze_check: at P5 they must match their freeze (exit 2 otherwise); at P4 (the
pre-freeze loop) an unfrozen draft is linted, a frozen side must still match.

CLI: python -I -X utf8 -B scripts/lint.py --run-dir RUN [--targets B,A,steps]
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlglot import exp  # noqa: E402

from lib import freeze_check, runctx, sqltext  # noqa: E402
from lib import refusal  # noqa: E402  fix r3 B3-4: exit-2 local effects on stderr
from lib.exit_codes import ExitCode  # noqa: E402

REPORT = "lint_report.csv"
COLUMNS = ["target", "rule", "severity", "snippet", "message", "waived", "waiver_reason", "decision_row"]
_ORDER_SENSITIVE_WINDOW = (exp.RowNumber, exp.Ntile, exp.Lag, exp.Lead, exp.FirstValue, exp.LastValue, exp.NthValue)
_NONDET_CLASSES = tuple(c for c in (getattr(exp, n, None) for n in (
    "Rand", "Uuid", "CurrentTimestamp", "CurrentDate", "CurrentDatetime", "CurrentTime", "CurrentUser",
    "SessionUser", "CurrentTimestampLTZ")) if c is not None)
_NONDET_NAMES = {"RAND", "RANDOM", "GENERATE_UUID", "UUID", "NOW", "SYSDATE", "GETDATE", "SESSION_USER",
                 "CURRENT_TIMESTAMP", "CURRENT_DATE", "CURRENT_DATETIME", "CURRENT_TIME", "CURRENT_USER"}


@dataclasses.dataclass
class Finding:
    """One lint hit."""

    target: str
    rule: str
    severity: str
    snippet: str
    message: str
    waived: bool = False
    waiver_reason: str | None = None
    decision_row: str | None = None


def _s(node: exp.Expression) -> str:
    return sqltext.unprotect(node.sql())[:120]


def _order_names(order: exp.Expression | None) -> set[str]:
    if order is None:
        return set()
    return {c.name for o in order.expressions for c in o.find_all(exp.Column)}


def _named_windows(tree: exp.Expression) -> dict[str, exp.Window]:
    out: dict[str, exp.Window] = {}
    for sel in tree.find_all(exp.Select):
        for w in sel.args.get("windows") or []:
            if w.alias:
                out[w.alias] = w
    return out


def lint_sql(target: str, sql: str, dialect_name: str = "bigquery",
             unique_keys: Mapping[str, Sequence[str]] | None = None,
             intended_aliases: Iterable[str] | None = None) -> list[Finding]:
    """Every N1 to N9 and A1/A2 finding of one SQL text (A2 is INFO for an intended alias)."""
    tree = sqltext.parse_one_query(sql, dialect_name)
    intended = {a.lower() for a in (intended_aliases or ())}
    keys = [set(v) for v in (unique_keys or {}).values()]
    out: list[Finding] = []

    def add(rule: str, sev: str, node: exp.Expression, msg: str) -> None:
        out.append(Finding(target, rule, sev, _s(node), msg))

    named = _named_windows(tree)
    # N1
    for w in tree.find_all(exp.Window):
        if not isinstance(w.this, _ORDER_SENSITIVE_WINDOW):
            continue
        order = w.args.get("order")
        if order is None and isinstance(w.args.get("alias"), exp.Identifier) and w.args["alias"].name in named:
            order = named[w.args["alias"].name].args.get("order")
        if order is None:
            add("N1", "ERROR", w, f"{w.this.key.upper()} without ORDER BY: rows are numbered in arbitrary order")
        elif not any(k and k <= _order_names(order) for k in keys):
            add("N1", "ERROR", w, f"{w.this.key.upper()} ORDER BY covers no declared unique key: ties give "
                                  "a different answer per run (L BQ03); add a unique tie-breaker or declare the key")
    for sel in tree.find_all(exp.Select):
        joins = sel.args.get("joins") or []
        # N2
        for e in sel.expressions:
            if isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star)):
                add("N2", "ERROR" if joins else "WARN", e,
                    "SELECT * over a join: column collision and drift" if joins else "SELECT *: schema drift")
        # N3
        where_conj = []
        if sel.args.get("where") is not None:
            stack = [sel.args["where"].this]
            while stack:
                n = stack.pop()
                if isinstance(n, exp.And):
                    stack += [n.this, n.expression]
                else:
                    where_conj.append(n)
        for j in joins:
            if j.args.get("on") is not None or j.args.get("using") or isinstance(j.this, exp.Unnest):
                continue
            if not sqltext.is_comma_join(j):
                add("N3", "WARN", j, "explicit CROSS JOIN: row count multiplies; confirm the right side's cardinality")
                continue
            alias = j.this.alias_or_name
            linked = any(isinstance(c, exp.EQ) and alias in {x.table for x in c.find_all(exp.Column)}
                         and len({x.table for x in c.find_all(exp.Column)}) > 1 for c in where_conj)
            if linked:
                add("N3", "WARN", j, "comma join linked in WHERE: write JOIN ... ON")
            else:
                add("N3", "ERROR", j, "implicit CROSS JOIN: no join condition")
        # N4, A1, A2
        names = [e.alias_or_name for e in sel.expressions if not isinstance(e, exp.Star)]
        lowered = [n.lower() for n in names if n]
        dups = sorted({n for n in names if n and lowered.count(n.lower()) > 1})
        if dups:
            add("N4", "ERROR", sel.expressions[0], f"colliding output names {dups}: BigQuery renames them silently")
        for e in sel.expressions:
            if isinstance(e, exp.Alias):
                if isinstance(e.this, exp.Column) and not isinstance(e.this.this, exp.Star) and e.this.name != e.alias:
                    if e.alias.lower() in intended:
                        add("A2", "INFO", e, f"column renamed {e.this.name} -> {e.alias}: the output contract's "
                                             "pre-assigned name (intended alias)")
                    else:
                        add("A2", "WARN", e, f"column renamed {e.this.name} -> {e.alias}: names are preserved until "
                                             "P10 (D-09); logged as a rename")
            elif not isinstance(e, (exp.Column, exp.Star)):
                add("A1", "ERROR", e, "computed expression without an alias: engines auto-name it differently")
        # N5 LIMIT without ORDER BY
        if sel.args.get("limit") is not None and sel.args.get("order") is None:
            add("N5", "ERROR", sel.args["limit"], "LIMIT without ORDER BY: an arbitrary subset")
        # N7
        sources = ([sel.args["from_"].this] if sel.args.get("from_") is not None else []) + [j.this for j in joins]
        if len(sources) > 1:
            outs = {e.alias for e in sel.expressions if isinstance(e, exp.Alias)}
            using = {u.name for j in joins for u in (j.args.get("using") or [])}
            for part in [*sel.expressions, sel.args.get("where"), sel.args.get("group"), sel.args.get("having")]:
                if part is None:
                    continue
                for c in part.find_all(exp.Column):
                    if not c.table and not isinstance(c.this, exp.Star) and c.name not in outs | using:
                        if c.find_ancestor(exp.Select) is sel:
                            add("N7", "WARN", c, f"unqualified column {c.name} in a select over {len(sources)} sources")
        # N8
        if sel.args.get("distinct") is not None and joins:
            add("N8", "WARN", sel, "SELECT DISTINCT over a join may hide a fan-out")
    # N5 functions
    for n in tree.find_all(exp.Func):
        nm = (n.name if isinstance(n, exp.Anonymous) else n.sql_name()).upper()
        if isinstance(n, _NONDET_CLASSES) or nm in _NONDET_NAMES:
            add("N5", "ERROR", n, f"non-deterministic function {nm}: breaks replay and whole-vs-chain (the pin "
                                  "supplies as_of_utc)")
        elif isinstance(n, (exp.ArrayAgg, exp.GroupConcat)) or nm in ("STRING_AGG", "ARRAY_AGG", "LISTAGG"):
            if not any(isinstance(x, exp.Order) for x in n.walk()):
                add("N5", "ERROR", n, f"{nm} without ORDER BY: element order is arbitrary")
        elif isinstance(n, exp.AnyValue) or nm == "ANY_VALUE":
            add("N5", "ERROR", n, "ANY_VALUE: arbitrary value per group")
    for n in tree.find_all(exp.TableSample):
        add("N5", "ERROR", n, "TABLESAMPLE: a different sample every run (L BQ03)")
    # N6
    for d in tree.find_all(exp.Div):
        den = d.expression
        while isinstance(den, exp.Paren):
            den = den.this
        if isinstance(den, exp.Nullif):
            continue
        if isinstance(den, exp.Literal) and den.is_number and float(den.this) != 0:
            continue
        if isinstance(den, exp.Cast) and isinstance(den.this, exp.Literal) and den.this.is_number and float(den.this.this) != 0:
            continue
        add("N6", "WARN", d, "division without NULLIF on the denominator (use NULLIF or SAFE_DIVIDE)")
    # N9
    for u in tree.find_all(exp.Union):
        if u.args.get("distinct"):
            add("N9", "WARN", u.this if u.this is not None else u, "UNION without ALL de-duplicates silently")
    return out


def apply_waivers(findings: Iterable[Finding], waivers: Sequence[Mapping[str, Any]]) -> list[Finding]:
    """Mark findings covered by a G9 waiver (rule, target or *, optional snippet match, reason)."""
    out = []
    for f in findings:
        for w in waivers:
            if w["rule"] != f.rule or not str(w.get("reason", "")).strip():
                continue
            if w.get("target", "*") not in ("*", f.target):
                continue
            if w.get("match") and w["match"] not in f.snippet:
                continue
            f.waived, f.waiver_reason, f.decision_row = True, w["reason"], w.get("decision_row")
            break
        out.append(f)
    return out


def _table_name(ref: Any) -> str:
    """The bare table name of a reference ({{project}}.{{ds}}.t, ds.t, t), lower-case."""
    return str(ref).strip().strip("`").split(".")[-1].lower()


def unread_binding_tables(run_dir: Path, b_sql: str, dialect_name: str = "bigquery") -> list[Finding]:
    """B1 (fix r6): a WARN for every bindings table B does not read, compared by bare table name; none when the
    card or its bindings are absent."""
    card = run_dir / "02_metric_card.md"
    try:
        b = (runctx.card_fields(card).get("bindings") or {}) if card.is_file() else {}
    except Exception:  # noqa: BLE001 - an unreadable card is card_freeze.py's business, not lint's
        return []
    declared = [t for t in (b.get("tables") or []) if isinstance(t, str) and t.strip()] if isinstance(b, Mapping) else []
    if not declared:
        return []
    read = {_table_name(k) for k in sqltext.base_table_keys(sqltext.parse_one_query(b_sql, dialect_name))}
    return [Finding("B", "B1", "WARN", t, f"table {t} is declared in the card's bindings.tables but B does not read it "
                    "(no wrong number follows from this; confirm with Max that it is not needed)")
            for t in declared if _table_name(t) not in read]


def _where_links(sel: exp.Select, alias: str) -> set[int]:
    """Columns of WHERE equalities that link `alias` to another source (a comma join's join condition)."""
    where = sel.args.get("where")
    stack, out = ([where.this] if where is not None else []), set()
    while stack:
        n = stack.pop()
        if isinstance(n, exp.And):
            stack += [n.this, n.expression]
        elif isinstance(n, exp.EQ):
            cols = list(n.find_all(exp.Column))
            tabs = {c.table.lower() for c in cols}
            if alias in tabs and len(tabs) > 1:
                out |= {id(c) for c in cols}
    return out


def b2_message(j: exp.Join, name: str, linked: bool = False) -> str:
    """Fix r12 M1: the B2 text by join kind. Only a LEFT join may be dropped without changing which rows B keeps."""
    side, kind = str(j.side or "").upper(), str(j.args.get("kind") or "").upper()
    if side == "LEFT":
        return (f"joined but unused: {name}; drop it at G2, or say which column it supplies (no column of it is read "
                "outside its own ON clause)")
    comma = sqltext.is_comma_join(j)
    word = "comma join" if comma else f"{side or kind or 'INNER'} JOIN"
    if side == "RIGHT":
        effect, what = "keeps every row of it, matched or not,", "join"
    elif side == "FULL":
        effect, what = "keeps every row of both sides, matched or not,", "join"
    elif kind == "CROSS" and not linked:
        effect, what = "repeats each row once per row of it", "join"
    else:  # INNER, a plain JOIN, a comma join linked in WHERE
        effect, what = "keeps only rows with a match", "filter"
    return (f"{word} to {name} {effect} and no column of it is read; dropping it changes the result: confirm whether "
            f"the {what} is intended (bind it as a filter row at G3, or drop the table at G2 if not)")


def joined_unused_tables(b_sql: str, dialect_name: str = "bigquery") -> list[Finding]:
    """B2 (fix r11 L8, r12 M1): a WARN for every warehouse table B joins none of whose columns is used outside its
    own join condition (ON, or for a comma join the WHERE equalities linking it); the text depends on the join kind."""
    tree = sqltext.parse_one_query(b_sql, dialect_name)
    base = {id(t) for t in sqltext.base_tables(tree)}
    out: list[Finding] = []
    for sel in tree.find_all(exp.Select):
        joins = sel.args.get("joins") or []
        if any(isinstance(e, exp.Star) for e in sel.expressions):
            continue
        for j in joins:
            t = j.this
            if not isinstance(t, exp.Table) or id(t) not in base or j.args.get("using"):
                continue
            alias = t.alias_or_name.lower()
            on = j.args.get("on")
            own = {id(c) for c in on.find_all(exp.Column)} if on is not None else set()
            links = _where_links(sel, alias) if sqltext.is_comma_join(j) else set()
            cols = [c for c in sel.find_all(exp.Column) if id(c) not in own | links]
            if any(not c.table for c in cols):
                continue  # an unqualified column may come from this table: possibly used
            if any(c.table.lower() in (alias, t.name.lower()) for c in cols):
                continue  # read through its alias, or through its table name or full path
            name = sqltext.unprotect(sqltext.table_key(t))
            out.append(Finding("B", "B2", "WARN", _s(j), b2_message(j, name, bool(links))))
    return out


def intended_aliases(run_dir: Path) -> list[str]:
    """Column names 03_output_contract.json pre-assigns (DB-02); [] when the file is absent or not a contract."""
    p = run_dir / "03_output_contract.json"
    try:
        cols = runctx.read_json(p).get("columns") or [] if p.is_file() else []
    except (OSError, ValueError, AttributeError):
        return []
    return [str(c.get("name")) for c in cols if isinstance(c, Mapping) and c.get("name")]


def lint_targets(run_dir: Path, targets: Sequence[str], dialect_name: str, frozen: bool = True) -> list[tuple[str, str]]:
    """(target name, SQL text) for B, A and every step in plan.json; B and A pass the freeze check first."""
    out = []
    for t in targets:
        if t in ("B", "A"):
            p = run_dir / f"{t}.sql"
            if p.is_file():
                if frozen:
                    freeze_check.require_frozen(run_dir, t)
                else:
                    freeze_check.require_frozen_if_recorded(run_dir, t)
                out.append((t, p.read_text(encoding="utf-8")))
        elif t == "steps" and (run_dir / "plan.json").is_file():
            for s in runctx.read_json(run_dir / "plan.json")["steps"]:
                out.append((s["name"], (run_dir / s["file"]).read_text(encoding="utf-8")))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lint.py", description="query-trail lint N1-N9 and alias lint (spec 2.2 P5)")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--targets", default="B,A,steps")
    a = ap.parse_args(argv)

    def body() -> int:
        st = runctx.enter(a.run_dir, ("P4", "P5"))
        cfg = runctx.load_run_config(a.run_dir)
        runctx.require_g0a(cfg)
        dialect_name = "bigquery"
        if (a.run_dir / "plan.json").is_file():
            dialect_name = runctx.read_json(a.run_dir / "plan.json").get("dialect", "bigquery")
        uk = (cfg.get("lint") or {}).get("unique_keys") or {}
        targets = lint_targets(a.run_dir, a.targets.split(","), dialect_name, frozen=st.phase != "P4")
        intended = intended_aliases(a.run_dir)  # read only after B (and A) passed the freeze check
        findings: list[Finding] = []
        for name, sql in targets:
            findings += lint_sql(name, sql, dialect_name, uk, intended)
            if name == "B":
                findings += unread_binding_tables(a.run_dir, sql, dialect_name)
                findings += joined_unused_tables(sql, dialect_name)
        findings = apply_waivers(findings, cfg.get("lint_waivers") or [])
        rows = [[f.target, f.rule, f.severity, f.snippet, f.message, "true" if f.waived else "false",
                 f.waiver_reason, f.decision_row] for f in findings]
        runctx.write_rows_csv(a.run_dir / REPORT, COLUMNS, rows)
        errors = [f for f in findings if f.severity == "ERROR" and not f.waived]
        print(f"lint.py: {len(findings)} findings, {len(errors)} unwaived ERROR -> {REPORT}")
        return int(ExitCode.FAIL if errors else ExitCode.PASS)

    return runctx.run_cli(lambda: refusal.guard(a.run_dir, body, warehouse=False), "lint.py")


if __name__ == "__main__":
    sys.exit(main())
