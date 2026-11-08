"""split.py: the D0 splitter on query B (design spec v1 sections 2.2 P5, 4.1, 5.5; decisions Q4).

Ported from research_r2/T04_tests/decomp_lib.py (T04 decomposition report v2: 45 queries, chain
equal to whole on 45 of 45 on DuckDB). Pipeline:

1. parse exactly one query statement; refuse scripting, DML/DDL and NOT IN (subquery) (G10);
2. qualify against the catalog schema (case-sensitive dialect: no name is lowercased);
3. refuse select-list correlated scalars and non-equality correlation (G10);
4. decorrelate equality-correlated subqueries (sqlglot unnest_subqueries); anything still
   correlated is refused (G10);
5. lift derived tables to CTEs (eliminate_subqueries);
6. read-step rule (spec 2.2 P5, **assumed**, outside the 45/45 evidence): every base-table read
   becomes its own read step: projection of the columns used, plus single-table WHERE conjuncts
   when the table is read once and is not on the null-supplying side of an outer join;
7. one step per CTE, split further to one operation per step (join ladder, aggregate, window,
   union branches; rules R-CTE ... R-UNION of T04 D-1), in a stable topological order.

Outputs: steps/NN_<verb>_<object>.sql (the step body, placeholders kept) and plan.json, whose
source_sha256 is B's frozen sha256. Exit 2 wording (fix r3 B3-4, spec v4 1.4): "refused; no warehouse job was run (with --fetch-schema the free schema reads may have been made); local effects: <files written, changed or removed, or none>" on stderr (lib/refusal.py).
Exit 0 PASS; 2 when B is not frozen or no longer matches its freeze
(lib.freeze_check), does not parse or qualify, or the state is elsewhere; 3 with gate_request.json (G10) on
a refused construct.

CLI: python -I -X utf8 -B scripts/split.py --run-dir RUN [--sql B.sql] [--schema catalog_schema.json]
     [--fetch-schema --env-file F] [--dialect bigquery] [--max-joins 1]
"""
from __future__ import annotations

import argparse
import dataclasses
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import sqlglot  # noqa: E402
from sqlglot import exp  # noqa: E402
from sqlglot.optimizer.eliminate_subqueries import eliminate_subqueries  # noqa: E402
from sqlglot.optimizer.qualify import qualify  # noqa: E402
from sqlglot.optimizer.scope import traverse_scope  # noqa: E402
from sqlglot.optimizer.simplify import simplify  # noqa: E402
from sqlglot.optimizer.unnest_subqueries import unnest_subqueries  # noqa: E402

from lib import freeze_check, runctx, sqltext  # noqa: E402
from lib import refusal  # noqa: E402  fix r3 B3-4: exit-2 local effects on stderr
from lib.csv_contract import write_text_lf  # noqa: E402
from lib.exit_codes import ExitCode, QueryTrailError, RefusedError  # noqa: E402

PLAN_FILE = "plan.json"
STEPS_DIR = "steps"
FINAL = "final"
NONDETERMINISTIC = ("RAND", "RANDOM", "GENERATE_UUID", "UUID", "CURRENT_", "NOW", "SESSION_USER")


class RefusedConstruct(QueryTrailError):
    """A construct the splitter does not split (G10, exit 3)."""

    exit_code = ExitCode.NEEDS_DECISION

    def __init__(self, kind: str, snippet: str) -> None:
        super().__init__(f"refused construct: {kind}: {snippet[:160]}")
        self.kind, self.snippet = kind, snippet


class QualifyError(RefusedError):
    """B does not qualify against the catalog schema (unknown table or column): exit 2."""


# ---------------------------------------------------------------- AST helpers (from decomp_lib)
def own_walk(node: exp.Expression):
    """Walk a node without descending into nested SELECT / subquery scopes."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        for ch in n.iter_expressions():
            if isinstance(ch, (exp.Subquery, exp.Select, exp.SetOperation)):
                continue
            stack.append(ch)


def select_parts(sel: exp.Select) -> list[exp.Expression]:
    parts: list[exp.Expression] = list(sel.expressions)
    for k in ("where", "group", "having", "qualify", "order"):
        v = sel.args.get(k)
        if v is not None:
            parts.append(v)
    return parts


def own_nodes(sel: exp.Select) -> list[exp.Expression]:
    return [n for p in select_parts(sel) for n in own_walk(p)]


def is_group_agg(n: exp.Expression) -> bool:
    """AggFunc evaluated at GROUP BY level (not the function of a window)."""
    return isinstance(n, exp.AggFunc) and not (isinstance(n.parent, exp.Window) and n.parent.this is n)


def source_names(sel: exp.Select) -> list[exp.Expression]:
    out = []
    fr = sel.args.get("from_")
    if fr is not None:
        out.append(fr.this)
    for j in sel.args.get("joins") or []:
        out.append(j.this)
    return out


def tables_in(node: exp.Expression) -> list[str]:
    return [t.name for t in node.find_all(exp.Table)]


def op_flags(sel: exp.Select) -> dict[str, Any]:
    """Which logical operations one SELECT contains (T04 D-1)."""
    nodes = own_nodes(sel)
    joins = sel.args.get("joins") or []
    f = {
        "n_joins": len(joins),
        "has_using": any(j.args.get("using") for j in joins),
        "where": sel.args.get("where") is not None,
        "group": sel.args.get("group") is not None,
        "agg": any(is_group_agg(n) for n in nodes),
        "window": any(isinstance(n, exp.Window) for n in nodes),
        "qualify": sel.args.get("qualify") is not None,
        "unnest": any(isinstance(n, (exp.Explode, exp.Unnest)) for n in nodes)
        or any(isinstance(s, exp.Unnest) for s in source_names(sel)),
        "distinct": sel.args.get("distinct") is not None,
        "order": sel.args.get("order") is not None,
        "limit": sel.args.get("limit") is not None,
    }
    f["is_agg_stage"] = bool(f["group"] or f["agg"])
    f["is_window_stage"] = bool(f["window"] or f["qualify"])
    return f


def op_count(f: Mapping[str, Any], max_joins: int = 1) -> int:
    joins_ops = 0 if f["n_joins"] == 0 else max(1, -(-f["n_joins"] // max_joins))
    return joins_ops + int(f["is_agg_stage"]) + int(f["is_window_stage"]) + int(f["unnest"])


def _col(name: str, table: str | None = None) -> exp.Column:
    return exp.column(name, table=table) if table else exp.column(name)


def _tidy(node: exp.Expression, drop_qualifiers: bool) -> exp.Expression:
    """Readable generated SQL: drop `x AS x`, table alias equal to its name, optionally qualifiers."""
    node = node.copy()
    for t in list(node.find_all(exp.Table)):
        if t.alias and t.alias == t.name:
            t.set("alias", None)
    for a in list(node.find_all(exp.Alias)):
        if isinstance(a.this, exp.Column) and a.this.name == a.alias:
            a.replace(a.this)
    for w in list(node.find_all(exp.Where)):
        cond = w.this
        if "TRUE" in cond.sql().upper():
            c2 = simplify(cond.copy())
            if isinstance(c2, exp.Boolean) and c2.this is True:
                w.parent.set("where", None)
            else:
                w.set("this", c2)
    if drop_qualifiers:
        for c in node.find_all(exp.Column):
            if c.table:
                c.set("table", None)
    return node


def _conjuncts(cond: exp.Expression | None) -> list[exp.Expression]:
    if cond is None:
        return []
    out, stack = [], [cond.this if isinstance(cond, exp.Where) else cond]
    while stack:
        n = stack.pop()
        if isinstance(n, exp.And):
            stack.append(n.expression)
            stack.append(n.this)
        elif isinstance(n, exp.Paren) and isinstance(n.this, exp.And):
            stack.append(n.this)
        else:
            out.append(n)
    return out


def _and_all(items: Sequence[exp.Expression]) -> exp.Expression:
    cond = items[0]
    for x in items[1:]:
        cond = exp.and_(cond, x)
    return cond


def _cols_of(node: exp.Expression, only_own: bool = True) -> list[exp.Column]:
    if only_own:
        return [n for n in own_walk(node) if isinstance(n, exp.Column)]
    return list(node.find_all(exp.Column))


# ---------------------------------------------------------------- steps
@dataclasses.dataclass
class Step:
    """One step of chain(B): a CTE-shaped query over base tables (read steps) or earlier steps."""

    name: str
    sql: str
    kind: str
    deps: list[str] = dataclasses.field(default_factory=list)
    note: str = ""
    columns: list[str] = dataclasses.field(default_factory=list)
    renames: list[str] = dataclasses.field(default_factory=list)
    grain: list[str] = dataclasses.field(default_factory=list)
    verb: str = ""
    is_read_step: bool = False
    source_tables: list[str] = dataclasses.field(default_factory=list)
    pushed_filters: list[str] = dataclasses.field(default_factory=list)


def _output_names(node: exp.Expression) -> list[str]:
    if isinstance(node, exp.SetOperation):
        return _output_names(node.this)
    if isinstance(node, exp.Subquery):
        return _output_names(node.this)
    if isinstance(node, exp.Select):
        return [e.alias_or_name for e in node.expressions]
    return []


def _verb(node: exp.Expression, kind: str) -> str:
    if kind == "read":
        return "read"
    if kind.endswith("recursive"):
        return "recursive"
    if isinstance(node, exp.SetOperation) or kind == "split:union":
        return "union"
    if not isinstance(node, exp.Select):
        return "project"
    f = op_flags(node)
    if f["n_joins"]:
        return "join"
    if f["is_agg_stage"]:
        return "aggregate"
    if f["is_window_stage"]:
        return "window"
    if f["unnest"]:
        return "unnest"
    if f["where"]:
        return "filter"
    if f["distinct"]:
        return "distinct"
    return "project"


class Splitter:
    """Join ladder, aggregate/window separation (T04 rules R-LADDER, R-AGG, R-AGGWIN, R-WINDOW)."""

    def __init__(self, dialect_name: str, max_joins: int = 1) -> None:
        self.dname = dialect_name
        self.max_joins = max_joins

    def text(self, node: exp.Expression) -> str:
        return sqltext.to_sql(node, self.dname, pretty=True)

    def split_select(self, sel: exp.Select, name: str, final_name: str | None = None) -> list[Step]:
        """`sel` must be qualified; the LAST step returned is named final_name or name."""
        final_name = final_name or name
        f = op_flags(sel)
        if op_count(f, self.max_joins) <= 1:
            node = _tidy(sel, drop_qualifiers=f["n_joins"] == 0)
            return [Step(final_name, self.text(node), "split:atomic", tables_in(sel))]
        steps: list[Step] = []
        other_ops = int(f["is_agg_stage"]) + int(f["is_window_stage"]) + int(f["unnest"])
        has_join = f["n_joins"] > 0
        mapping: dict[tuple[str, str], str] = {}
        src = None
        if has_join:
            ladder_final = other_ops == 0
            lsteps, mapping, src = self._ladder(sel, name, final_name, ladder_final)
            steps += lsteps
            if ladder_final:
                return steps
        rebased = self._rebase(sel, src, mapping) if has_join else sel.copy()
        rf = op_flags(rebased)
        if rf["is_agg_stage"] and rf["is_window_stage"]:
            g_step, rebased2 = self._agg_extract(rebased, f"{name}__agg", src if has_join else None)
            steps.append(g_step)
            steps.append(Step(final_name, self.text(_tidy(rebased2, drop_qualifiers=True)), "split:window",
                              [g_step.name]))
        else:
            kind = "aggregate" if rf["is_agg_stage"] else "window" if rf["is_window_stage"] else "unnest"
            steps.append(Step(final_name, self.text(_tidy(rebased, drop_qualifiers=True)), f"split:{kind}",
                              [src] if has_join else tables_in(sel)))
        return steps

    def _ladder(self, sel: exp.Select, name: str, final_name: str, ladder_final: bool):
        joins: list[exp.Join] = list(sel.args.get("joins") or [])
        from_tbl = sel.args["from_"].this
        base_alias = from_tbl.alias_or_name
        aliases = [base_alias] + [j.this.alias_or_name for j in joins]
        down_parts = list(sel.expressions) + [sel.args[k] for k in ("group", "having", "qualify", "order")
                                              if sel.args.get(k) is not None]
        down_cols = [c for p in down_parts for c in _cols_of(p, only_own=False) if c.table in aliases]
        conj = _conjuncts(sel.args.get("where"))
        chunks = [joins[i:i + self.max_joins] for i in range(0, len(joins), self.max_joins)]
        ends, n = [], 0
        for ch in chunks:
            n += len(ch)
            ends.append(n)

        def late_unsafe(idx: int) -> bool:
            return any((jj.side or "").upper() in ("RIGHT", "FULL") for jj in joins[idx:])

        placed: list[list[exp.Expression]] = [[] for _ in chunks]
        for cj in conj:
            cols = {c.table for c in _cols_of(cj, only_own=False) if c.table in aliases}
            has_sub = any(True for _ in cj.find_all(exp.Subquery, exp.Exists))
            target = len(chunks) - 1
            if not has_sub:
                for ci, e in enumerate(ends):
                    if cols <= set(aliases[: e + 1]):
                        target = ci if not late_unsafe(e) else len(chunks) - 1
                        break
            placed[target].append(cj)
        # R-COMMA: comma joins get their equality predicates moved from WHERE into ON
        for ci, chunk in enumerate(chunks):
            for j in chunk:
                if sqltext.is_comma_join(j) and not isinstance(j.this, exp.Unnest):
                    new_alias = j.this.alias_or_name
                    avail_before = set(aliases[: aliases.index(new_alias)])
                    moved = []
                    for cj in list(placed[ci]):
                        if isinstance(cj, exp.EQ):
                            ta = {c.table for c in _cols_of(cj, only_own=False)}
                            if new_alias in ta and ta - {new_alias} and (ta - {new_alias}) <= avail_before:
                                moved.append(cj)
                    if moved:
                        j.set("on", _and_all([m.copy() for m in moved]))
                        j.set("kind", None)  # a cross join plus an equality is an inner join
                        for m in moved:
                            placed[ci].remove(m)

        def needed_after(ci: int) -> list[tuple[str, str]]:
            keep = [(c.table, c.name) for c in down_cols]
            for later in range(ci + 1, len(chunks)):
                for j in chunks[later]:
                    on = j.args.get("on")
                    if on is not None:
                        keep += [(c.table, c.name) for c in _cols_of(on, only_own=False) if c.table in aliases]
                for cj in placed[later]:
                    keep += [(c.table, c.name) for c in _cols_of(cj, only_own=False) if c.table in aliases]
            avail = set(aliases[: ends[ci] + 1])
            seen, res = set(), []
            for k in keep:
                if k[0] in avail and k not in seen:
                    seen.add(k)
                    res.append(k)
            return res

        all_keep: list[tuple[str, str]] = []
        for ci in range(len(chunks)):
            for k in needed_after(ci):
                if k not in all_keep:
                    all_keep.append(k)
        by_col: dict[str, set[str]] = {}
        for a, c in all_keep:
            by_col.setdefault(c.lower(), set()).add(a)
        mapping: dict[tuple[str, str], str] = {}
        renames = []
        for a, c in all_keep:
            if len(by_col[c.lower()]) > 1:
                mapping[(a, c)] = f"{a}_{c}"
                renames.append(f"{a}.{c} -> {a}_{c}")
            else:
                mapping[(a, c)] = c
        steps: list[Step] = []
        prev_name: str | None = None
        prev_aliases: set[str] = set()
        for ci, chunk in enumerate(chunks):
            is_last = ci == len(chunks) - 1
            step_name = final_name if (is_last and ladder_final) else f"{name}__join_{ci + 1}"
            new_aliases = {j.this.alias_or_name for j in chunk}
            pa, pn = set(prev_aliases), prev_name

            def remap(node: exp.Expression, pa=pa, pn=pn) -> exp.Expression:
                def fn(x: exp.Expression) -> exp.Expression:
                    if isinstance(x, exp.Column) and x.table in pa:
                        return _col(mapping[(x.table, x.name)], pn)
                    return x
                return node.transform(fn)

            from_expr = exp.to_table(prev_name) if prev_name else from_tbl.copy()
            new_joins = []
            for j in chunk:
                j2 = j.copy()
                if j2.args.get("on") is not None and prev_name:
                    j2.set("on", remap(j2.args["on"]))
                new_joins.append(j2)
            q = exp.select()
            if is_last and ladder_final:
                q.set("expressions", [remap(e) for e in sel.expressions])
                for k in ("distinct", "order", "limit"):
                    if sel.args.get(k) is not None:
                        q.set(k, remap(sel.args[k]))
            else:
                items = []
                for (a, c) in needed_after(ci):
                    if a in prev_aliases:
                        items.append(_col(mapping[(a, c)], prev_name))
                    else:
                        col = _col(c, a)
                        items.append(col if mapping[(a, c)] == c else exp.alias_(col, mapping[(a, c)]))
                if not items:  # nothing needed downstream (COUNT(*) only): keep the row multiplicity
                    items = [exp.alias_(exp.Literal.number(1), "qt_one")]
                q.set("expressions", items)
            q.set("from_", exp.From(this=from_expr))
            q.set("joins", new_joins)
            wh = [remap(cj) if prev_name else cj.copy() for cj in placed[ci]]
            if wh:
                q.set("where", exp.Where(this=_and_all(wh)))
            for t in list(q.find_all(exp.Table)):
                if t.alias and t.alias == t.name:
                    t.set("alias", None)
            st = Step(step_name, self.text(q), "split:join", [])
            st.renames = renames if ci == 0 else []
            st.columns = [e.alias_or_name for e in q.expressions]
            steps.append(st)
            prev_name = step_name
            prev_aliases = ({base_alias} | new_aliases) if ci == 0 else (prev_aliases | new_aliases)
        return steps, mapping, prev_name

    def _rebase(self, sel: exp.Select, src: str, mapping: Mapping[tuple[str, str], str]) -> exp.Select:
        """Make `sel` read from the single table `src` (FROM/JOIN/WHERE consumed by the ladder)."""
        s = sel.copy()
        s.set("from_", exp.From(this=exp.to_table(src)))
        s.set("joins", None)
        s.set("where", None)

        def fn(n: exp.Expression) -> exp.Expression:
            if isinstance(n, exp.Column) and (n.table, n.name) in mapping:
                return _col(mapping[(n.table, n.name)])
            return n
        return s.transform(fn)

    def _agg_extract(self, sel: exp.Select, step_name: str, src: str | None):
        """Aggregate and window in one select: keys and aggregates first, windows over them next."""
        sel = sel.copy()
        items = list(sel.expressions)
        agg_nodes: dict[str, exp.Expression] = {}
        name_of: dict[str, str] = {}
        counter = 0
        parts = list(sel.expressions) + [sel.args[k] for k in ("having", "qualify", "order") if sel.args.get(k) is not None]
        for it in items:
            if isinstance(it, exp.Alias) and is_group_agg(it.this) and it.this.parent is it:
                agg_nodes[it.this.sql()] = it.this
                name_of[it.this.sql()] = it.alias
        for p in parts:
            for n in own_walk(p):
                if is_group_agg(n) and n.sql() not in agg_nodes:
                    counter += 1
                    agg_nodes[n.sql()] = n
                    name_of[n.sql()] = f"agg_{counter}"
        g = sel.args.get("group")
        keys: list[tuple[str, exp.Expression]] = []
        kc = 0
        if g is not None:
            for ke in g.expressions:
                nm = None
                if isinstance(ke, exp.Column):
                    nm = ke.name
                else:
                    for it in items:
                        if isinstance(it, exp.Alias) and it.this.sql() == ke.sql():
                            nm = it.alias
                    if nm is None:
                        kc += 1
                        nm = f"key_{kc}"
                keys.append((nm, ke))
        sel_items = [exp.alias_(ke.copy(), nm) if not (isinstance(ke, exp.Column) and ke.name == nm) else ke.copy()
                     for nm, ke in keys]
        sel_items += [exp.alias_(agg_nodes[k].copy(), name_of[k]) for k in agg_nodes]
        gq = exp.select()
        gq.set("expressions", sel_items)
        gq.set("from_", sel.args["from_"].copy())
        for k in ("where", "group", "having"):
            if sel.args.get(k) is not None:
                gq.set(k, sel.args[k].copy())
        repl = {k: name_of[k] for k in agg_nodes}
        for nm, ke in keys:
            repl[ke.sql()] = nm

        def rewrite(node: exp.Expression) -> exp.Expression:
            def fn(n: exp.Expression) -> exp.Expression:
                if n.sql() in repl and not isinstance(n, exp.Alias):
                    return _col(repl[n.sql()])
                return n
            return node.transform(fn)
        gq_t = _tidy(gq, drop_qualifiers=src is not None or len(source_names(gq)) == 1)
        step = Step(step_name, self.text(gq_t), "split:aggregate", tables_in(gq))
        step.columns = [e.alias_or_name for e in sel_items]
        step.grain = [nm for nm, _ in keys]
        step.renames = [f"{name_of[k]} (generated)" for k in agg_nodes if name_of[k].startswith("agg_")] + \
                       [f"{nm} (generated)" for nm, _ in keys if nm.startswith("key_")]
        s2 = exp.select()
        s2.set("expressions", [rewrite(e) for e in sel.expressions])
        s2.set("from_", exp.From(this=exp.to_table(step_name)))
        for k in ("distinct", "order", "limit", "qualify", "windows"):
            v = sel.args.get(k)
            if v is not None:
                s2.set(k, [rewrite(w) for w in v] if isinstance(v, list) else rewrite(v))
        return step, s2


def _split_union(sp: Splitter, node: exp.SetOperation, name: str) -> list[Step]:
    """Rule R-UNION: each branch with a real operation becomes a step; the union is one step."""
    branches: list[exp.Expression] = []
    ops: list[exp.SetOperation] = []

    def collect(n: exp.Expression) -> None:
        if isinstance(n, exp.SetOperation):
            collect(n.this)
            branches.append(n.expression)
            ops.append(n)
        else:
            branches.append(n)
    collect(node)
    steps: list[Step] = []
    refs: list[Any] = []
    first0 = branches[0].this if isinstance(branches[0], exp.Subquery) else branches[0]
    first_names = [e.alias_or_name for e in first0.expressions]
    for i, b in enumerate(branches, 1):
        if isinstance(b, exp.Subquery):
            b = b.this
        if i > 1:  # UNION is positional: later branches carry the first branch's column names
            b = b.copy()
            b.set("expressions", [exp.alias_(e.unalias() if isinstance(e, exp.Alias) else e, first_names[j])
                                  for j, e in enumerate(b.expressions)])
        f = op_flags(b)
        if f["is_agg_stage"] or f["is_window_stage"] or f["n_joins"] or f["unnest"]:
            sub = sp.split_select(b, f"{name}__branch_{i}")
            steps += sub
            refs.append(sub[-1].name)
        else:
            refs.append(b)
    cur = None
    for i, r in enumerate(refs):
        s = (exp.select(*[exp.column(c) for c in first_names]).from_(r) if isinstance(r, str)
             else _tidy(r.copy(), drop_qualifiers=len(source_names(r)) == 1))
        if cur is None:
            cur = s
        else:
            op = ops[i - 1]
            cur = type(op)(this=cur, expression=s, distinct=op.args.get("distinct"))
    if node.args.get("order") is not None:
        cur.set("order", node.args["order"].copy())
    steps.append(Step(name, sp.text(_tidy(cur, drop_qualifiers=False)), "split:union",
                      [r for r in refs if isinstance(r, str)], columns=first_names))
    return steps


# ---------------------------------------------------------------- refusals (G10)
def _refuse_statement(tree: exp.Expression) -> None:
    if sqltext.is_query(tree):
        return
    if isinstance(tree, (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter)):
        raise RefusedConstruct("DML", tree.sql()[:160])
    raise RefusedConstruct("scripting", tree.sql()[:160])


def _refuse_not_in(tree: exp.Expression) -> None:
    for n in tree.find_all(exp.Not):
        inner = n.this.this if isinstance(n.this, exp.Paren) else n.this
        if isinstance(inner, exp.In) and inner.args.get("query") is not None:
            raise RefusedConstruct("NOT IN (subquery)", n.sql())


def _in_select_list(sub_select: exp.Expression) -> bool:
    n = sub_select
    while n.parent is not None and not isinstance(n.parent, exp.Select):
        n = n.parent
    return n.parent is not None and n.arg_key == "expressions"


def _correlation_refusals(q: exp.Expression) -> None:
    for s in traverse_scope(q):
        if not (s.is_subquery and s.is_correlated_subquery):
            continue
        if _in_select_list(s.expression):
            raise RefusedConstruct("select-list correlated scalar", s.expression.sql())
        for c in s.external_columns:
            p = c.parent
            while p is not None and not isinstance(p, (exp.Predicate, exp.Select)):
                p = p.parent
            if p is None or not isinstance(p, exp.EQ):
                raise RefusedConstruct("non-equality correlation", (p or c).sql())
            anc = p.parent
            while anc is not None and anc is not s.expression:
                if isinstance(anc, exp.Or):
                    raise RefusedConstruct("non-equality correlation", anc.sql())
                anc = anc.parent


def _leftover_correlation(q: exp.Expression) -> None:
    for s in traverse_scope(q):
        if s.is_subquery and s.is_correlated_subquery:
            raise RefusedConstruct("non-equality correlation", s.expression.sql())


# ---------------------------------------------------------------- read-step lifting (spec 2.2 P5)
def _is_nondeterministic(node: exp.Expression) -> bool:
    for n in node.find_all(exp.Func):
        nm = (n.sql_name() if not isinstance(n, exp.Anonymous) else str(n.name)).upper()
        if any(nm.startswith(x) for x in NONDETERMINISTIC) or isinstance(n, (exp.Rand, exp.CurrentTimestamp,
                                                                             exp.CurrentDate, exp.CurrentTime)):
            return True
    return False


def _null_supplied(sel: exp.Select, t: exp.Table) -> bool:
    joins = list(sel.args.get("joins") or [])
    if sel.args.get("from_") is not None and sel.args["from_"].this is t:
        return any((j.side or "").upper() in ("RIGHT", "FULL") for j in joins)
    for i, j in enumerate(joins):
        if j.this is t:
            if (j.side or "").upper() in ("LEFT", "FULL"):
                return True
            return any((k.side or "").upper() in ("RIGHT", "FULL") for k in joins[i + 1:])
    return True


def _read_name(key: str, taken: set[str], by_name: dict[str, list[str]]) -> str:
    parts = key.split(".")
    base = parts[-1] if len(by_name[parts[-1]]) == 1 else "_".join(re.sub(r"[{}]", "", p) for p in parts[-2:])
    nm = "read_" + re.sub(r"[^A-Za-z0-9_]", "_", base)
    out, i = nm, 2
    while out in taken:
        out, i = f"{nm}_{i}", i + 1
    taken.add(out)
    return out


def lift_reads(q: exp.Expression, schema_flat: Mapping[str, Mapping[str, str]],
               dialect_name: str = "bigquery") -> tuple[exp.Expression, list[Step]]:
    """Replace every base-table reference by a read step (a leading CTE); return the reads."""
    occurrences: dict[str, list[exp.Table]] = {}
    for t in sqltext.base_tables(q):
        occurrences.setdefault(sqltext.table_key(t), []).append(t)
    if not occurrences:
        return q, []
    # 1. single-table WHERE conjuncts move into the read step (table read once, not null-supplied)
    pushed: dict[str, list[exp.Expression]] = {k: [] for k in occurrences}
    for key, occ in occurrences.items():
        if len(occ) != 1:
            continue
        t = occ[0]
        sel = t.parent.parent if isinstance(t.parent, (exp.From, exp.Join)) else None
        if not isinstance(sel, exp.Select) or _null_supplied(sel, t) or sel.args.get("where") is None:
            continue
        alias = t.alias_or_name
        others = [x.alias_or_name for x in source_names(sel) if x is not t]
        keep, move = [], []
        for cj in _conjuncts(sel.args["where"]):
            cols = _cols_of(cj, only_own=False)
            ok = (cols and all(c.table == alias for c in cols) and alias not in others
                  and not any(True for _ in cj.find_all(exp.Subquery, exp.Exists, exp.AggFunc, exp.Window))
                  and not _is_nondeterministic(cj))
            (move if ok else keep).append(cj)
        if move:
            pushed[key] = [m.copy() for m in move]
            sel.set("where", exp.Where(this=_and_all([k.copy() for k in keep])) if keep else None)
    # 2. columns each base table must provide (after the pushdown)
    needed: dict[str, set[str]] = {k: set() for k in occurrences}
    ids = {id(t): k for k, occ in occurrences.items() for t in occ}
    for scope in traverse_scope(q):
        for alias, src in scope.sources.items():
            if isinstance(src, exp.Table) and id(src) in ids:
                key = ids[id(src)]
                needed[key] |= {c.name for c in scope.columns if c.table == alias}
                sel = scope.expression
                if isinstance(sel, exp.Select):
                    for j in sel.args.get("joins") or []:
                        for u in j.args.get("using") or []:
                            needed[key].add(u.name)
    by_name: dict[str, list[str]] = {}
    for k in occurrences:
        by_name.setdefault(k.split(".")[-1], []).append(k)
    taken = sqltext.cte_names(q)
    reads: list[Step] = []
    read_ctes: list[exp.CTE] = []
    for key in sorted(occurrences):
        name = _read_name(key, taken, by_name)
        cols_schema = list((schema_flat.get(key) or {}).keys())
        cols = [c for c in cols_schema if c in needed[key]] + sorted(needed[key] - set(cols_schema))
        if not cols:
            cols = cols_schema[:1] or ["*"]
        src = occurrences[key][0].copy()
        src.set("alias", None)
        body = exp.select(*[exp.column(c) if c != "*" else exp.Star() for c in cols]).from_(src)
        if pushed[key]:
            conds = []
            for cj in pushed[key]:
                cj = cj.copy()
                for c in cj.find_all(exp.Column):
                    c.set("table", None)
                conds.append(cj)
            body.set("where", exp.Where(this=_and_all(conds)))
        read_ctes.append(exp.CTE(this=body, alias=exp.TableAlias(this=exp.to_identifier(name))))
        st = Step(name, "", "read", [], columns=cols, is_read_step=True, source_tables=[key],
                  pushed_filters=[sqltext.to_sql(c, dialect_name, pretty=False) for c in body.args["where"].this.flatten()]
                  if pushed[key] and isinstance(body.args["where"].this, exp.And) else
                  ([sqltext.to_sql(body.args["where"].this, dialect_name, pretty=False)] if pushed[key] else []))
        reads.append(st)
        for t in occurrences[key]:
            alias = t.alias_or_name
            t.replace(exp.Table(this=exp.to_identifier(name), alias=exp.TableAlias(this=exp.to_identifier(alias))))
    with_ = q.args.get("with_")
    existing = list(with_.expressions) if with_ else []
    if with_ is None:
        q.set("with_", exp.With(expressions=read_ctes + existing))
    else:
        with_.set("expressions", read_ctes + existing)
    return q, reads


# ---------------------------------------------------------------- planner
def _rename_generated(q: exp.Expression, orig_ctes: set[str]) -> exp.Expression:
    gen: dict[str, str] = {}
    for c in (q.args["with_"].expressions if q.args.get("with_") else []):
        if c.alias not in orig_ctes and (c.alias.startswith("_q") or c.alias.startswith("_u")):
            gen[c.alias] = ("sq_" if c.alias.startswith("_q") else "dec_") + re.sub(r"[^0-9]", "", c.alias)
    if not gen:
        return q

    def fn(n: exp.Expression) -> exp.Expression:
        if isinstance(n, exp.Table) and n.name in gen and not n.db:
            n.set("this", exp.to_identifier(gen[n.name]))
        if isinstance(n, exp.TableAlias) and isinstance(n.parent, exp.CTE) and n.name in gen:
            n.set("this", exp.to_identifier(gen[n.name]))
        return n
    return q.transform(fn)


def _tidy_decorrelated(q: exp.Expression) -> exp.Expression:
    """Give sqlglot's helper names (_u_N, _col_N) back their source names where derivable (T04)."""
    key_map: dict[str, str] = {}
    for cte in (q.args["with_"].expressions if q.args.get("with_") else []):
        if not cte.alias.startswith("dec_") or not isinstance(cte.this, exp.Select):
            continue
        for e in cte.this.expressions:
            if isinstance(e, exp.Alias) and re.fullmatch(r"_u_\d+", e.alias) and isinstance(e.this, exp.Column):
                key_map[e.alias] = e.this.name
    alias_to_cte = {t.alias: t.name for t in q.find_all(exp.Table)
                    if t.name.startswith("dec_") and t.alias and t.alias.startswith("_u_")}
    val_map = {(a, "_col_0"): f"{c}_value" for a, c in alias_to_cte.items()}
    for cte in (q.args["with_"].expressions if q.args.get("with_") else []):
        if cte.alias.startswith("dec_") and isinstance(cte.this, exp.Select):
            for e in cte.this.expressions:
                if isinstance(e, exp.Alias) and re.fullmatch(r"_col_\d+", e.alias):
                    e.set("alias", exp.to_identifier(f"{cte.alias}_value"))

    def fn(n: exp.Expression) -> exp.Expression:
        if isinstance(n, exp.Alias) and n.alias in key_map and isinstance(n.this, exp.Column):
            return exp.alias_(n.this, key_map[n.alias]) if n.this.name != key_map[n.alias] else n.this
        if isinstance(n, exp.Column):
            if n.name in key_map:
                return _col(key_map[n.name], n.table or None)
            if (n.table, n.name) in val_map:
                return _col(val_map[(n.table, n.name)], n.table)
        return n
    return q.transform(fn)


def _stable_topo(names: list[str], deps: Mapping[str, set[str]]) -> list[str]:
    """Kahn's algorithm with ties broken by original position (deterministic)."""
    pos = {n: i for i, n in enumerate(names)}
    indeg = {n: len(deps[n] & set(names)) for n in names}
    ready = sorted([n for n in names if indeg[n] == 0], key=pos.get)
    out: list[str] = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for m in names:
            if n in deps[m]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    ready.append(m)
                    ready.sort(key=pos.get)
    if len(out) != len(names):
        raise RefusedError("CTE dependency cycle in B")
    return out


def _author_text_allowed(body: exp.Expression) -> bool:
    return not sqltext.base_tables(body, extra_ctes=sqltext.cte_names(body.root())) and \
        not any(True for _ in body.find_all(exp.Subquery, exp.Exists))


@dataclasses.dataclass
class SplitPlan:
    """The ordered steps of chain(B) plus warnings and generated names."""

    steps: list[Step]
    warnings: list[str]
    renames_pending: list[str]


def plan_query(sql: str, schema: Mapping[str, Any], dialect_name: str = "bigquery", max_joins: int = 1) -> SplitPlan:
    """Split one whole query into ordered single-operation steps; raise RefusedConstruct (G10)."""
    try:
        orig = sqltext.parse_one_query(sql, dialect_name)
    except sqltext.SqlTextError as exc:
        if "exactly one statement" in str(exc):
            raise RefusedConstruct("scripting", "more than one statement") from exc
        raise
    _refuse_statement(orig)
    _refuse_not_in(orig)
    d = sqltext.dialect(dialect_name)
    nested = sqltext.nested_schema(schema)
    flat = sqltext.flat_schema(nested)
    orig_with = orig.args.get("with_")
    orig_ctes = {c.alias: c for c in (orig_with.expressions if orig_with else [])}
    recursive = bool(orig_with and orig_with.args.get("recursive"))
    try:
        q = qualify(orig.copy(), schema=nested, dialect=d, quote_identifiers=False, identify=False)
    except (sqlglot.errors.OptimizeError, sqlglot.errors.SchemaError) as exc:
        raise QualifyError(f"B does not qualify against the catalog schema: {exc}") from exc
    _correlation_refusals(q)
    q = unnest_subqueries(q)
    _leftover_correlation(q)
    q = eliminate_subqueries(q)
    q = _rename_generated(q, set(orig_ctes))
    q = _tidy_decorrelated(q)
    q, reads = lift_reads(q, flat, dialect_name)
    read_names = {r.name: r for r in reads}
    ctes = list(q.args["with_"].expressions) if q.args.get("with_") else []
    final_body = q.copy()
    final_body.set("with_", None)
    names = [c.alias for c in ctes]
    deps = {c.alias: (set(tables_in(c.this)) & set(names)) - {c.alias} for c in ctes}
    order = _stable_topo(names, deps)
    by_name = {c.alias: c for c in ctes}
    sp = Splitter(dialect_name, max_joins)
    steps: list[Step] = []
    for nm in order:
        body = by_name[nm].this
        if nm in read_names:
            st = read_names[nm]
            st.sql = sp.text(body)
            steps.append(st)
            continue
        if recursive and nm in set(tables_in(body)) and isinstance(body, exp.SetOperation):
            cte = by_name[nm]
            cols = [str(cc) for cc in (cte.args["alias"].columns if cte.args.get("alias") else [])]
            col_list = ", ".join(cols) if cols else "*"
            ctecols = f"({col_list})" if cols else ""
            text = f"WITH RECURSIVE {nm}{ctecols} AS (\n{sp.text(body)}\n)\nSELECT {col_list} FROM {nm}"
            steps.append(Step(nm, text, "original:recursive", [], "recursive CTE kept whole (rule R-REC)",
                              columns=cols or _output_names(body)))
            continue
        if isinstance(body, exp.SetOperation):
            steps += _split_union(sp, body, nm)
            continue
        ss = sp.split_select(body, nm)
        if len(ss) == 1 and nm in orig_ctes and _author_text_allowed(orig_ctes[nm].this):
            ss[0].sql = sqltext.to_sql(orig_ctes[nm].this, dialect_name, pretty=True)
            ss[0].kind = "original"
        elif len(ss) == 1 and nm not in orig_ctes:
            ss[0].kind = "lifted"
        steps += ss
    if isinstance(final_body, exp.SetOperation):
        steps += _split_union(sp, final_body, FINAL)
    else:
        ss = sp.split_select(final_body, FINAL)
        ob = orig.copy()
        ob.set("with_", None)
        if len(ss) == 1 and _author_text_allowed(ob):
            ss[0].sql = sqltext.to_sql(ob, dialect_name, pretty=True)
            ss[0].kind = "original"
        steps += ss
    stepnames = {s.name for s in steps}
    nodes_by_step: dict[str, exp.Expression] = {}
    warnings: list[str] = []
    renames: list[str] = []
    for s in steps:
        node = sqltext.parse_one_query(s.sql, dialect_name)
        nodes_by_step[s.name] = node
        inner = set(sqltext.cte_names(node))
        s.deps = sorted((set(tables_in(node)) & stepnames) - {s.name} - inner)
        if not s.columns:
            s.columns = _output_names(node.this if isinstance(node, exp.Subquery) else node)
        s.verb = _verb(node, s.kind)
        if s.kind == "split:aggregate" or (isinstance(node, exp.Select) and node.args.get("group") is not None
                                           and not s.grain):
            g = node.args.get("group") if isinstance(node, exp.Select) else None
            if g is not None and not s.grain:
                outs = {e.this.sql() if isinstance(e, exp.Alias) else e.sql(): e.alias_or_name
                        for e in node.expressions}
                s.grain = [outs.get(k.sql(), k.sql()) for k in g.expressions if not isinstance(k, exp.Literal)]
        renames += [f"{s.name}: {r}" for r in s.renames]
    for s in steps:
        if any(c.startswith(("_col_", "dec_")) or re.fullmatch(r"_u_\d+", c) for c in s.columns):
            renames.append(f"{s.name}: generated column names {[c for c in s.columns if c.startswith('_')]}")
    return SplitPlan(steps, warnings, renames)


# ---------------------------------------------------------------- files
def warn_card_step_sources(run_dir: Path, steps: Sequence[Mapping[str, Any]]) -> list[str]:
    """Fix r4 C4-15: warn (stderr, exit unchanged) about card `step:<ref>` sources that match no step by name, CTE
    name or number; logic_tests.py refuses them at P9. Return the unmatched sources."""
    from lib import stepref

    card = Path(run_dir) / "02_metric_card.md"
    try:
        refs = stepref.card_step_refs(runctx.card_fields(card)) if card.is_file() else []
    except Exception:  # noqa: BLE001 - an unreadable card is card_freeze.py's business, not split's
        return []
    bad = stepref.unmatched(steps, refs)
    if bad:
        sys.stderr.write("split.py: WARNING: the card names step sources that match no step (name, CTE name or "
                         f"number): {'; '.join(bad)}; steps: {stepref.describe(steps)}. Keep the CTE names the card "
                         "references, or P9 refuses these sources\n")
    return bad


def step_file_name(index: int, step: Step) -> str:
    """NN_<verb>_<object> (spec 1.4); object = the step name, lowercased and cleaned."""
    obj = step.name[5:] if step.is_read_step and step.name.startswith("read_") else step.name
    obj = re.sub(r"[^a-z0-9]+", "_", obj.lower()).strip("_")[:40] or "step"
    if index > 99:
        raise RefusedError("more than 99 steps: split B into smaller cards")
    return f"{index:02d}_{step.verb}_{obj}"


def write_plan(run_dir: Path, sql_path: Path, plan: SplitPlan, dialect_name: str, max_joins: int,
               source_sha256: str | None = None) -> dict[str, Any]:
    """Write steps/NN_*.sql and plan.json (source_sha256: B's frozen sha256); return the plan object."""
    steps_dir = run_dir / STEPS_DIR
    steps_dir.mkdir(parents=True, exist_ok=True)
    for old in steps_dir.glob("*.sql"):
        old.unlink()
    file_of: dict[str, str] = {}
    entries = []
    for i, s in enumerate(plan.steps, 1):
        nm = step_file_name(i, s)
        file_of[s.name] = nm
        text = s.sql.rstrip() + "\n"
        write_text_lf(steps_dir / f"{nm}.sql", text)
        entries.append({
            "step": i, "name": nm, "cte": s.name, "file": f"{STEPS_DIR}/{nm}.sql",
            "sql_sha256": sqltext.sha256_text(text), "kind": s.kind, "verb": s.verb,
            "is_read_step": s.is_read_step, "source_tables": s.source_tables,
            "inputs": ([{"kind": "source_table", "ref": t} for t in s.source_tables] if s.is_read_step else []),
            "deps": s.deps, "columns": s.columns, "grain": s.grain,
            "pushed_filters": s.pushed_filters, "note": s.note,
        })
    for e in entries:
        if not e["is_read_step"]:
            e["inputs"] = [{"kind": "prior_step", "ref": file_of[d]} for d in e["deps"]]
        e["deps"] = [file_of[d] for d in e["deps"]]
    if source_sha256 is None:
        source_sha256 = sqltext.sha256_text(sql_path.read_text(encoding="utf-8"))
    obj = {"schema_version": "1", "source_sql": sql_path.name, "source_sha256": source_sha256,
           "dialect": dialect_name, "sqlglot_version": sqlglot.__version__, "max_joins": max_joins,
           "rule_set": "T04 D-1 plus read-step lifting (spec 2.2 P5, assumed)",
           "final_step": len(entries), "steps": entries, "renames_pending": plan.renames_pending,
           "warnings": plan.warnings}
    runctx.write_json(run_dir / PLAN_FILE, obj)
    return obj


def load_catalog_schema(path: Path) -> dict[str, Any]:
    """Catalog schema JSON: {"ds.table": {"col": "TYPE"}} or nested; a "tables" wrapper is accepted."""
    raw = runctx.read_json(path)
    return raw.get("tables", raw) if isinstance(raw, dict) else raw


def fetch_schema(engine: Any, sql: str, dialect_name: str, cfg: Any, render: Any) -> dict[str, dict[str, str]]:
    """Catalog schema from free dry runs of SELECT * per base table (spec 2.2 P5 'qualify against the catalog')."""
    tree = sqltext.parse_one_query(sql, dialect_name)
    out: dict[str, dict[str, str]] = {}
    for key in sqltext.base_table_keys(tree):
        res = engine.dry_run(render(f"SELECT * FROM {key}"), cfg)
        out[key] = {n: t for n, t in res.schema}
    return out


# ---------------------------------------------------------------- CLI
def main(argv: Sequence[str] | None = None, engine: Any = None) -> int:
    ap = argparse.ArgumentParser(prog="split.py", description="query-trail D0 splitter (spec 2.2 P5)")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--sql", default="B.sql", help="query to split: only the frozen B.sql is accepted")
    ap.add_argument("--schema", default="catalog_schema.json", help="catalog schema JSON, relative to the run folder")
    ap.add_argument("--fetch-schema", action="store_true", help="build the catalog schema from free dry runs")
    ap.add_argument("--env-file", default=None)
    ap.add_argument("--dialect", default="bigquery", choices=("bigquery", "duckdb"))
    ap.add_argument("--max-joins", type=int, default=1)
    a = ap.parse_args(argv)

    def body() -> int:
        run_dir = a.run_dir
        runctx.enter(run_dir, ("P5",))
        cfg = runctx.load_run_config(run_dir)
        runctx.require_g0a(cfg)
        sql_path = run_dir / a.sql
        if sql_path.resolve() != (run_dir / "B.sql").resolve():
            raise RefusedError(f"split.py splits the frozen B.sql only, not {a.sql}")
        frozen = freeze_check.require_frozen(run_dir, "B")
        sql = sql_path.read_text(encoding="utf-8")
        schema_path = run_dir / a.schema
        if a.fetch_schema:
            from lib import bq
            env = runctx.load_env(cfg, a.env_file)
            eng = engine or bq.BigQueryEngine(bq.Identity(env["project"], env["location"], env.get("service_account"),
                                                          cfg.get("owner_mode") == "research"))
            jc = bq.job_config(cfg["run_id"], "schema", cfg["caps"]["warehouse_bytes_per_job"], True, env["location"],
                               cfg.get("job_labels"))
            schema = fetch_schema(eng, sql, a.dialect, jc,
                                  lambda s: sqltext.render(s, env["placeholders"], a.dialect))
            runctx.write_json(schema_path, {"tables": schema})
        elif schema_path.is_file():
            schema = load_catalog_schema(schema_path)
        else:
            raise RefusedError(f"no catalog schema: {schema_path} (or pass --fetch-schema)")
        try:
            plan = plan_query(sql, schema, a.dialect, a.max_joins)
        except RefusedConstruct as exc:
            runctx.write_gate_request(run_dir, "G10", "P5", "split.py", f"refused construct: {exc.kind}",
                                      {"construct": exc.kind, "snippet": exc.snippet[:400],
                                       "options": ["hand split (labelled; B vs chain(B) mandatory)", "rewrite B"]})
            raise
        obj = write_plan(run_dir, sql_path, plan, a.dialect, a.max_joins, frozen)
        print(f"split.py: PASS: {len(obj['steps'])} steps "
              f"({sum(1 for s in obj['steps'] if s['is_read_step'])} read steps) -> {PLAN_FILE}")
        warn_card_step_sources(run_dir, obj["steps"])
        runctx.archive_own_request(run_dir, "split.py", ("G10",), "P5")  # fix r4 C4-10
        return int(ExitCode.PASS)

    return runctx.run_cli(lambda: refusal.guard(a.run_dir, body, warehouse=False), "split.py")


if __name__ == "__main__":
    sys.exit(main())
