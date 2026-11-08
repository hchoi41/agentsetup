"""trace_source.py: recompute deliverable measures from source tables joined across the steps (design spec v1 section 6.2; fix r1 A-H5 part 4).

Ratios recompute numerator and denominator from source (spec 6.2). When a measure needs rows of several source
tables, trace.py asks this module for a source-join plan per sampled unit and, per sampled grain value:

1. the card's definitions: bindings.columns gives the grain (e.g. order_month = DATE_TRUNC(DATE(qt_orders.created_at),
   MONTH)) and each measure (revenue = SUM(qt_order_items.sale_price)) over bindings.tables; a chain ratio
   (link name or headline column) is the ratio of its recomputed numerator and denominator;
2. the join path from plan.json: each read step names its source table and pushed filters; each join step's ON
   equalities are resolved through the step SQL (aliases, CTE projections) to source-table columns, giving the
   edges (table.column = table.column); a breadth-first walk from the grain's table reaches every table a measure
   names;
3. one warehouse lookup per needed source table (single table, no join, no aggregate, so warehouse_jobs.py can batch
   it): the grain's table restricted by the grain definition and its read step's filters, each next table by its
   read step's filters and `key IN (...)` with the keys collected from the previous table's result. Lookups run in
   stages (generate, runner, generate again);
4. a local DuckDB aggregation of the fetched rows with the card's definitions, joined along the same edges.

Not applied: filters written in non-read steps, and joins to tables no measure or grain definition names (an
inner join to such a table can drop rows in B; a difference then shows as a FAIL to explain). A measure whose
definition, tables or join path cannot be resolved is returned with its reason; trace.py lists it in
measures_not_recomputed.

Fix r3 (the G1b bindings shape, card_freeze.py A3-5): when the card's bindings carry `measures`, the definitions come
from there instead of bindings.columns, which stays only as the fallback for older run folders:
- `measures` {name: {expr, table[, filter]}}: the expression with its unqualified columns qualified by `table`; a
  `filter` applies inside each aggregate (SUM(x) -> SUM(IF(filter, x, NULL)); COUNT(*) -> COUNT(IF(filter, 1, NULL)));
- `derived` {name: expression over measure names}: evaluated locally on the recomputed measures (e.g.
  rev_per_customer = revenue / NULLIF(customers, 0)); a derived name wins over a chain ratio of the same name;
- `date` {table, column, truncation, timezone}: the definition of the date grain column (the first grain column whose
  contract type is DATE or TIMESTAMP, else the first grain column): DATE -> DATE_TRUNC(DATE(col), unit), TIMESTAMP ->
  TIMESTAMP_TRUNC(col, unit), STRING -> FORMAT_DATE of the truncated date ('%Y-%m' for MONTH, '%Y' YEAR, '%Y-Q%Q'
  QUARTER, '%G-W%V' WEEK and ISOWEEK, '%Y-%m-%d' DAY and NONE); DATE(col, timezone) unless the timezone is UTC. Any
  other grain column is read as the same-named column of the date table;
- `joins` [{left, right, on: [[lcol, rcol]]}]: the join edges (inner), used instead of plan.json's join steps;
  plan.json, when present, still gives the read steps' pushed filters. Tables are named as placeholder_table writes
  them ('{{ds}}.qt_orders' -> 'ds.qt_orders').

Fix r4 C4-5 (aera r4 H5): every declared key pair of a join is applied (AND), never only the first: a composite key
(tenant_id, order_id) is matched on both columns in the lookups (`(a = x AND b = y) OR ...`) and in the local
recompute (ON a AND b). The join type is the one the bindings declare (`type`: inner, the default, or left; from
plan.json the join's side). NULL keys never match: a parent row with a NULL in any key column contributes no key,
and the local ON compares with `=`, which is never true for NULL. A join that cannot be executed as declared (a type
other than inner or left, a key pair that is not [left column, right column], a column missing from the lock's
column snapshot, or in plan.json an ON clause that is not a conjunction of column equalities resolving to source
columns) is not approximated: every measure whose path needs it gets that reason, so the lookup is NOT_TRACED for it
and trace.py lists it as not recomputed from source (complete-coverage wording withheld). Bindings joins take their
pairs from `on_keys` (fix r4 LOW: `on` must be quoted in YAML, so the key is renamed) and still read `on`.
"""
from __future__ import annotations

import collections
import decimal
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import duckdb
import sqlglot
from sqlglot import exp

from lib import runctx as c2

PH = re.compile(r"\{\{(\w+)\}\}")
PH_PREFIX = "qtph_"
NUM = re.compile(r"^-?\d+(\.\d+)?$")


def _parse(sql: str) -> exp.Expression:
    return sqlglot.parse_one(PH.sub(lambda m: PH_PREFIX + m.group(1), sql), read="bigquery")


def placeholder_table(ref: str) -> str:
    """'{{project}}.{{ds}}.qt_orders' -> 'ds.qt_orders' (dataset alias and table, as bindings.tables writes it)."""
    parts = PH.sub(lambda m: m.group(1), ref).replace("`", "").split(".")
    return ".".join(parts[-2:])


def short(full: str) -> str:
    return full.split(".")[-1]


class PlanGraph:
    """Source tables, read-step filters and join edges of plan.json, resolved to source-table columns."""

    def __init__(self, run_dir: Path) -> None:
        plan = c2.read_input_json(Path(run_dir) / "plan.json", "plan.json")
        self.ctes: dict[str, exp.Expression] = {}
        self.filters: dict[str, list[str]] = {}
        for s in plan.get("steps", []):
            try:
                self.ctes[str(s["cte"])] = _parse((Path(run_dir) / s["file"]).read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - an unparsable step only removes edges; reasons say so
                continue
            if s.get("is_read_step"):
                for t in s.get("source_tables") or []:
                    self.filters[placeholder_table(str(t))] = [str(f) for f in s.get("pushed_filters") or []]
        self.edges: list[tuple[tuple[str, str], tuple[str, str], str]] = []
        self.join_errors: dict[frozenset[str], str] = {}  # fix r4 C4-5: table pair -> why it cannot run as declared
        for cte, tree in self.ctes.items():
            for j in tree.args.get("joins") or []:
                on = j.args.get("on")
                if on is None:
                    continue
                side = str(j.side or "").upper()
                kind = "left" if side == "LEFT" else "inner"
                found, problems = [], []
                if side in ("RIGHT", "FULL") or str(j.kind or "").upper() == "CROSS":
                    problems.append(f"{side or j.kind} JOIN is not supported in a source recompute")
                conds = list(on.flatten()) if isinstance(on, exp.And) else [on]
                for eq in conds:
                    if not isinstance(eq, exp.EQ) or not (isinstance(eq.this, exp.Column)
                                                          and isinstance(eq.expression, exp.Column)):
                        problems.append(f"ON condition {eq.sql(dialect='bigquery')[:80]} is not a column equality")
                        continue
                    a, b = eq.this, eq.expression
                    ra = self.resolve(cte, a.table or None, a.name)
                    rb = self.resolve(cte, b.table or None, b.name)
                    if ra and rb and ra[0] != rb[0]:
                        found.append((ra, rb))
                    else:
                        problems.append(f"ON {a.sql()} = {b.sql()} does not resolve to two source tables")
                for ra, rb in found:
                    if (ra, rb, kind) not in self.edges:
                        self.edges.append((ra, rb, kind))
                    if problems:
                        self.join_errors[frozenset((ra[0], rb[0]))] = "; ".join(problems)

    def _scope(self, tree: exp.Expression) -> dict[str, tuple[str, str]]:
        scope = {}
        for t in tree.find_all(exp.Table):
            if not t.db and t.name in self.ctes:
                scope[t.alias_or_name] = ("cte", t.name)
            else:
                db = t.db[len(PH_PREFIX):] if t.db.startswith(PH_PREFIX) else t.db
                scope[t.alias_or_name] = ("table", f"{db}.{t.name}" if db else t.name)
        return scope

    def resolve(self, cte: str, alias: str | None, col: str, depth: int = 0) -> tuple[str, str] | None:
        """(source table, column) behind `alias.col` in step `cte`, or None (an expression, not a column)."""
        tree = self.ctes.get(cte)
        if tree is None or depth > 32:
            return None
        scope = self._scope(tree)
        if alias is None:
            if len(scope) != 1:
                return None
            alias = next(iter(scope))
        target = scope.get(alias)
        if target is None:
            return None
        if target[0] == "table":
            return target[1], col
        return self.resolve_out(target[1], col, depth + 1)

    def resolve_out(self, cte: str, col: str, depth: int = 0) -> tuple[str, str] | None:
        tree = self.ctes.get(cte)
        if tree is None:
            return None
        star = False
        for e in getattr(tree, "selects", []):
            if isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star)):
                star = True
                continue
            if e.alias_or_name != col:
                continue
            inner = e.this if isinstance(e, exp.Alias) else e
            return self.resolve(cte, inner.table or None, inner.name, depth) if isinstance(inner, exp.Column) else None
        return self.resolve(cte, None, col, depth) if star else None


def column_refs(sql: str) -> set[tuple[str, str]]:
    """(table short name, column) of every qualified column in a card definition."""
    return {(c.table, c.name) for c in _parse(sql).find_all(exp.Column) if c.table}


def strip_table(sql: str, table: str) -> str:
    """The definition with `table.` qualifiers removed (for the single-table root lookup), in GoogleSQL."""
    tree = _parse(sql)
    for c in tree.find_all(exp.Column):
        if c.table == table:
            c.set("table", None)
    return tree.sql(dialect="bigquery")


_TRUNC_FMT = {"YEAR": "%Y", "QUARTER": "%Y-Q%Q", "MONTH": "%Y-%m", "WEEK": "%G-W%V", "ISOWEEK": "%G-W%V",
              "DAY": "%Y-%m-%d", "HOUR": "%Y-%m-%d", "NONE": "%Y-%m-%d"}


def qualify(sql: str, table_short: str, filt: str | None = None) -> str:
    """A measure expression with its unqualified columns qualified by `table_short`; `filt` applied in each aggregate."""
    tree = _parse(sql)
    for c in list(tree.find_all(exp.Column)):
        if not c.table:
            c.set("table", exp.to_identifier(table_short))
    if filt:
        cond = _parse(filt)
        for c in list(cond.find_all(exp.Column)):
            if not c.table:
                c.set("table", exp.to_identifier(table_short))
        for agg in list(tree.find_all(exp.AggFunc)):
            arg = agg.this
            if isinstance(arg, exp.Distinct):
                arg.set("expressions", [exp.If(this=cond.copy(), true=e, false=exp.Null()) for e in arg.expressions])
            elif isinstance(arg, exp.Star) or arg is None:
                agg.set("this", exp.If(this=cond.copy(), true=exp.Literal.number(1), false=exp.Null()))
            else:
                agg.set("this", exp.If(this=cond.copy(), true=arg.copy(), false=exp.Null()))
    return tree.sql(dialect="bigquery")


def _contract_types(run_dir: Path) -> dict[str, str]:
    p = Path(run_dir) / "03_output_contract.json"
    oc = c2.read_input_json(p, "03_output_contract.json") if p.is_file() else {}
    cols = oc.get("columns") if isinstance(oc, Mapping) else None
    return {str(c.get("name")): str(c.get("type") or "").upper() for c in cols or [] if isinstance(c, Mapping)}


def date_definition(d: Mapping[str, Any], out_type: str) -> str:
    """The grain definition of the card's `date` binding for an output column of `out_type`."""
    col = f"{short(placeholder_table(str(d['table'])))}.{d['column']}"
    unit = str(d.get("truncation") or "NONE").upper()
    tz = str(d.get("timezone") or "UTC")
    as_date = f"DATE({col})" if tz.upper() == "UTC" else f"DATE({col}, '{tz}')"
    if out_type == "TIMESTAMP":
        return col if unit == "NONE" else (f"TIMESTAMP_TRUNC({col}, {unit})" if tz.upper() == "UTC"
                                           else f"TIMESTAMP_TRUNC({col}, {unit}, '{tz}')")
    trunc = as_date if unit in ("NONE", "DAY", "HOUR") else f"DATE_TRUNC({as_date}, {unit})"
    if out_type == "DATE":
        return trunc
    return f"FORMAT_DATE('{_TRUNC_FMT.get(unit, '%Y-%m-%d')}', {trunc})"


JOIN_TYPES = ("inner", "left")


def _lock_columns(run_dir: Path) -> dict[str, set[str]]:
    """table (as placeholder_table writes it, lower case) -> lower column names, from 14_discovery_lock.json."""
    p = Path(run_dir) / "14_discovery_lock.json"
    try:
        tabs = c2.read_input_json(p).get("tables") if p.is_file() else None
    except Exception:  # noqa: BLE001 - an unreadable lock only removes the column check
        return {}
    if not isinstance(tabs, Mapping):
        return {}
    return {placeholder_table(str(t)).lower(): {str(c[0]).lower() for c in (e or {}).get("columns") or [] if c}
            for t, e in tabs.items() if isinstance(e, Mapping) and e.get("columns")}


def new_shape(run_dir: Path, b: Mapping[str, Any], grain: Sequence[str], join_errors: dict | None = None
              ) -> tuple[dict[str, str], dict[str, str], list[str], list | None, str | None]:
    """(definitions incl. the grain, derived, tables, join edges or None, error) from the r3 bindings shape;
    `join_errors` (fix r4 C4-5) collects table pair -> why a declared join cannot be executed as declared."""
    join_errors = {} if join_errors is None else join_errors
    lock_cols = _lock_columns(run_dir)
    tables = [placeholder_table(str(t)) for t in b.get("tables") or []]
    defs: dict[str, str] = {}
    for name, m in (b.get("measures") or {}).items():
        if isinstance(m, Mapping) and m.get("expr") and m.get("table"):
            try:
                defs[str(name)] = qualify(str(m["expr"]), short(placeholder_table(str(m["table"]))),
                                          str(m["filter"]) if m.get("filter") else None)
            except Exception:  # noqa: BLE001 - an unparsable definition is reported per measure below
                continue
    d = b.get("date")
    if grain:
        if not isinstance(d, Mapping) or not d.get("table") or not d.get("column"):
            return defs, {}, tables, None, "the card's bindings have no date {table, column} for the grain"
        types = _contract_types(run_dir)
        dated = next((g for g in grain if types.get(g) in ("DATE", "TIMESTAMP")), grain[0])
        for g in grain:
            defs[g] = (date_definition(d, types.get(g, "STRING")) if g == dated
                       else f"{short(placeholder_table(str(d['table'])))}.{g}")
    derived = {str(k): str(v) for k, v in (b.get("derived") or {}).items() if isinstance(v, str) and v.strip()}
    edges = None
    if isinstance(b.get("joins"), list):
        edges = []
        for j in b["joins"]:
            if not isinstance(j, Mapping) or not j.get("left") or not j.get("right"):
                continue
            left, right = placeholder_table(str(j["left"])), placeholder_table(str(j["right"]))
            kind = str(j.get("type") or "inner").strip().lower()
            problems = [] if kind in JOIN_TYPES else [f"join type {kind!r} is not supported (inner or left)"]
            pairs = list(j.get("on_keys") or j.get("on") or j.get(True) or [])
            if not pairs:
                problems.append("the join declares no key pairs (on_keys)")
            good = []
            for pair in pairs:
                if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(str(x).strip() for x in pair):
                    good.append((str(pair[0]), str(pair[1])))
                else:
                    problems.append(f"key pair {pair!r} is not [left column, right column]")
            for (t, c) in [(left, lc) for lc, _ in good] + [(right, rc) for _, rc in good]:
                known = lock_cols.get(t.lower())
                if known is not None and c.lower() not in known:
                    problems.append(f"column {c} is not a column of {t} (14_discovery_lock.json)")
            for lc, rc in good:
                edges.append(((left, lc), (right, rc), kind if kind in JOIN_TYPES else "inner"))
            if problems:
                join_errors[frozenset((left, right))] = "; ".join(problems)
    return defs, derived, tables, edges, None


def card_tolerance(card: Mapping[str, Any]) -> float:
    t = card.get("tolerance", 0)
    if isinstance(t, Mapping):
        t = t.get("trace", t.get("default", 0))
    return float(t or 0)


def build(run_dir: Path, card: Mapping[str, Any], grain: Sequence[str], measures: Sequence[str],
          ratios: Mapping[str, tuple[str, str]]) -> dict[str, Any]:
    """Source-join plan for one unit: tables in walk order, columns, filters, definitions, and reasons per measure."""
    b = card.get("bindings") if isinstance(card.get("bindings"), Mapping) else {}
    out: dict[str, Any] = {"tables": [], "measures": {}, "ratios": {}, "derived": {}, "reasons": {},
                           "tolerance": card_tolerance(card)}

    def fail_all(why: str) -> dict[str, Any]:
        out["reasons"] = {m: why for m in measures}
        return out

    edges = None
    derived: dict[str, str] = {}
    join_errors: dict[frozenset[str], str] = {}
    word = "bindings.columns"
    if isinstance(b.get("measures"), Mapping) and b["measures"]:  # fix r3: the G1b bindings shape
        word = "bindings.measures"
        defs, derived, tables, edges, err = new_shape(run_dir, b, list(grain), join_errors)
        if err:
            return fail_all(err)
    else:  # older run folders: bindings.columns
        defs = ({str(k): str(v) for k, v in (b.get("columns") or {}).items()}
                if isinstance(b.get("columns"), Mapping) else {})
        tables = [str(t) for t in b.get("tables") or []]
    by_short = {short(t): t for t in tables}
    if not defs or not tables:
        return fail_all("the card has no bindings.measures (or bindings.columns) or bindings.tables for a source-join "
                        "recompute")
    has_plan = (Path(run_dir) / "plan.json").is_file()
    if edges is None and not has_plan:
        return fail_all("no plan.json: join keys unknown")
    if not grain:
        return fail_all("the deliverable grain is unknown (output contract keys)")
    root_tables, grain_defs = set(), {}
    for g in grain:
        if g not in defs:
            return fail_all(f"grain column {g}: no definition in the card's {word}")
        refs = column_refs(defs[g])
        root_tables |= {t for t, _ in refs}
        grain_defs[g] = defs[g]
    if len(root_tables) != 1 or next(iter(root_tables)) not in by_short:
        return fail_all("the grain definition must name exactly one table of bindings.tables")
    root_short = next(iter(root_tables))
    root = by_short[root_short]
    graph = PlanGraph(run_dir) if has_plan else None
    filters = graph.filters if graph is not None else {}
    if edges is None:
        edges = graph.edges if graph is not None else []
        join_errors = dict(graph.join_errors) if graph is not None else {}
    grouped = group_edges(edges)
    need: dict[str, set[str]] = collections.defaultdict(set)
    need[root] |= {c for _, c in column_refs(" AND ".join(grain_defs.values()))}
    resolved: dict[str, str] = {}
    for m in measures:
        if m in defs:
            refs = column_refs(defs[m])
            bad = sorted({t for t, _ in refs if t not in by_short})
            if not refs or bad:
                out["reasons"][m] = (f"definition names tables outside bindings.tables: {', '.join(bad)}" if bad
                                     else "definition has no table-qualified column")
                continue
            resolved[m] = defs[m]
    # breadth-first join walk from the grain's table; fix r4 C4-5: one edge per table pair with ALL its key pairs
    parent: dict[str, tuple[str, list[str], list[str], str] | None] = {root: None}
    queue = collections.deque([root])
    while queue:
        t = queue.popleft()
        for (ta, tb), (pairs, kind) in grouped.items():
            for x, y, flip in ((ta, tb, False), (tb, ta, True)):
                if x == t and y not in parent:
                    ps = [(b2, a2) for a2, b2 in pairs] if flip else list(pairs)
                    parent[y] = (t, [p[0] for p in ps], [p[1] for p in ps], kind)
                    queue.append(y)
    used: list[str] = [root]
    for m, d in list(resolved.items()):
        miss = [by_short[t] for t, _ in column_refs(d) if by_short[t] not in parent]
        if miss:
            why = [f"join {' - '.join(sorted(k))} cannot be executed as declared: {v}" for k, v in join_errors.items()
                   if k & set(miss)]
            out["reasons"][m] = "; ".join([f"no join path from {root} to {', '.join(sorted(set(miss)))} in plan.json's "
                                           "join steps"] + why)
            del resolved[m]
            continue
        bad = []
        for t, _ in column_refs(d):
            full = by_short[t]
            while parent.get(full):
                why = join_errors.get(frozenset((full, parent[full][0])))
                if why:
                    bad.append(f"join {parent[full][0]} - {full} cannot be executed as declared: {why}")
                full = parent[full][0]
        if bad:
            out["reasons"][m] = "; ".join(dict.fromkeys(bad))
            del resolved[m]
            continue
        for t, c in column_refs(d):
            full = by_short[t]
            need[full].add(c)
            chain = []
            while full not in used and parent.get(full):
                chain.append(full)
                full = parent[full][0]
            used += [x for x in reversed(chain) if x not in used]
    order = [t for t in parent if t in used]  # BFS order keeps every parent before its child
    for t in order:  # fix r4: every key column first, so a parent's lookup carries its child's key columns too
        p = parent[t]
        if p:
            need[p[0]].update(p[1])
            need[t].update(p[2])
    for t in order:
        p = parent[t]
        out["tables"].append({"table": t, "parent": p[0] if p else None,
                              "parent_col": p[1][0] if p else None, "col": p[2][0] if p else None,
                              "parent_cols": list(p[1]) if p else [], "cols": list(p[2]) if p else [],
                              "join": p[3] if p else None,
                              "columns": sorted(need[t]), "filters": filters.get(t, [])})
    out["measures"] = resolved
    for m in measures:
        if m in resolved or m in out["reasons"]:
            continue
        if m in derived:  # fix r3: an expression over measure names, evaluated on the recomputed measures
            names = {w for w in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b(?!\s*\()", derived[m])
                     if w.upper() not in ("AND", "OR", "NOT", "NULL", "CASE", "WHEN", "THEN", "ELSE", "END", "IS")}
            miss = sorted(names - set(resolved))
            if miss:
                out["reasons"][m] = f"derived {m}: {', '.join(miss)} not recomputed from source"
            else:
                out["derived"][m] = derived[m]
            continue
        num, den = ratios.get(m, (None, None))
        if num and den and num in resolved and den in resolved:
            out["ratios"][m] = [num, den]
        elif num and den:
            out["reasons"][m] = f"chain ratio {num}/{den}: a part has no source definition"
        else:
            out["reasons"][m] = f"no definition in the card's {word} (and not a chain ratio of defined parts)"
    out["root"], out["grain_defs"] = root, grain_defs
    if not resolved:
        out["tables"] = []
    return out


def group_edges(edges: Sequence[tuple[tuple[str, str], tuple[str, str], str]]
                ) -> dict[tuple[str, str], tuple[list[tuple[str, str]], str]]:
    """Fix r4 C4-5: (table a, table b) -> ([(col a, col b), ...], join kind), every key pair of the pair kept, in
    declaration order; the orientation of the first edge seen."""
    out: dict[tuple[str, str], tuple[list[tuple[str, str]], str]] = {}
    for (ta, ca), (tb, cb), kind in edges:
        if (ta, tb) in out:
            pairs = out[(ta, tb)][0]
            if (ca, cb) not in pairs:
                pairs.append((ca, cb))
        elif (tb, ta) in out:
            pairs = out[(tb, ta)][0]
            if (cb, ca) not in pairs:
                pairs.append((cb, ca))
        else:
            out[(ta, tb)] = ([(ca, cb)], kind)
    return out


def _parent_keys(t: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    return (list(t.get("parent_cols") or ([t["parent_col"]] if t.get("parent_col") else [])),
            list(t.get("cols") or ([t["col"]] if t.get("col") else [])))


def lookup_sql(sj: Mapping[str, Any], i: int, row: Mapping[str, Any], types: Mapping[str, str],
               parent_rows: Sequence[Mapping[str, Any]] | None, literal: Callable[[Any, str], str],
               source_sql: Callable[[str], str]) -> str | None:
    """GoogleSQL for table i of the walk: the root by the grain, the others by the parent's keys; None = no keys."""
    t = sj["tables"][i]
    cols = ", ".join(f"`{c}`" for c in t["columns"])
    conds = [f"({f})" for f in t["filters"]]
    if i == 0:
        for g, d in sj["grain_defs"].items():
            v = row.get(g)
            e = strip_table(d, short(t["table"]))
            conds.append(f"({e}) IS NULL" if v is None else f"({e}) = {literal(v, types.get(g, ''))}")
    else:
        pcols, ccols = _parent_keys(t)
        # fix r4 C4-5: every key column; a parent row with a NULL in any key never matches, so it adds no key
        tuples = sorted({tuple(r.get(c) for c in pcols) for r in parent_rows or []
                         if all(r.get(c) is not None for c in pcols)}, key=lambda v: tuple((str(type(x)), str(x)) for x in v))
        if not tuples:
            return None
        if len(ccols) == 1:
            conds.append(f"`{ccols[0]}` IN ({', '.join(literal(k[0], '') for k in tuples)})")
        else:
            conds.append("(" + " OR ".join("(" + " AND ".join(f"`{c}` = {literal(v, '')}" for c, v in zip(ccols, k))
                                           + ")" for k in tuples) + ")")
    return f"SELECT {cols} FROM {source_sql(t['table'])} WHERE {' AND '.join(conds)}"


def _typed(values: Sequence[Any]) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return "VARCHAR"
    if all(isinstance(v, bool) for v in vals):
        return "BOOLEAN"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
        return "BIGINT"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "DOUBLE"
    if all(isinstance(v, str) and NUM.match(v.strip()) for v in vals):
        return "DECIMAL(38,9)"
    return "VARCHAR"


def compute(sj: Mapping[str, Any], rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    """The card's definitions over the fetched rows, joined along the walk; ratios from their recomputed parts."""
    con = duckdb.connect()
    try:
        con.execute("SET TimeZone = 'UTC'")
        for t in sj["tables"]:
            rows = list(rows_by_table.get(t["table"], []))
            cols = t["columns"]
            types = {c: _typed([r.get(c) for r in rows]) for c in cols}
            con.execute(f'CREATE TABLE "{short(t["table"])}" (' + ", ".join(f'"{c}" {types[c]}' for c in cols) + ")")
            if rows:
                conv = [tuple(None if r.get(c) is None else (decimal.Decimal(str(r[c])) if types[c].startswith("DECIMAL")
                                                            else (str(r[c]) if types[c] == "VARCHAR" else r[c]))
                              for c in cols) for r in rows]
                con.executemany(f'INSERT INTO "{short(t["table"])}" VALUES ({", ".join("?" for _ in cols)})', conv)
        frm = f'"{short(sj["tables"][0]["table"])}"'
        for t in sj["tables"][1:]:
            j = "LEFT JOIN" if t["join"] == "left" else "JOIN"
            pcols, ccols = _parent_keys(t)
            # fix r4 C4-5: all key pairs ANDed; `=` is never true for NULL, so NULL keys never match
            frm += f' {j} "{short(t["table"])}" ON ' + " AND ".join(
                f'CAST("{short(t["table"])}"."{c}" AS VARCHAR) = CAST("{short(t["parent"])}"."{p}" AS VARCHAR)'
                for p, c in zip(pcols, ccols))
        names = list(sj["measures"])
        sel = ", ".join(sqlglot.transpile(sj["measures"][m], read="bigquery", write="duckdb")[0] + f' AS "{m}"'
                        for m in names)
        got = dict(zip(names, con.execute(f"SELECT {sel} FROM {frm}").fetchone())) if names else {}
    finally:
        con.close()
    for m, (num, den) in sj["ratios"].items():
        n, d = got.get(num), got.get(den)
        got[m] = None if n is None or d in (None, 0) else float(n) / float(d)
    derived = sj.get("derived") or {}
    if derived:
        dcon = duckdb.connect()
        try:
            names = list(sj["measures"])
            dcon.execute("CREATE TABLE qt_m (" + ", ".join(f'"{m}" DOUBLE' for m in names) + ")")
            dcon.execute(f"INSERT INTO qt_m VALUES ({', '.join('?' for _ in names)})",
                         [None if got.get(m) is None else float(got[m]) for m in names])
            for m, e in derived.items():
                v = dcon.execute("SELECT " + sqlglot.transpile(e, read="bigquery", write="duckdb")[0]
                                 + " FROM qt_m").fetchone()[0]
                got[m] = None if v is None else float(v)
        finally:
            dcon.close()
    return got
