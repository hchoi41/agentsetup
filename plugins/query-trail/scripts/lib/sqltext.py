"""SQL text helpers shared by the P4 to P7 scripts (design spec v1 sections 1.6, 2.2 P5 and P6, 5.2; D-12).

- Dialects are case-sensitive variants of sqlglot's BigQuery and DuckDB dialects, so `qualify`
  never lowercases a column name (a lowercased header would break the name audit, D-09).
- Identifier placeholders `{{project}}.{{dataset}}.table` (research_r2/T16, D-12) are protected
  as plain identifiers (`qtph__project`) before parsing and restored after generation, so SQL
  files keep placeholders and only `render` (strict, ported from T16 dctx.render) produces a
  runnable query, in memory, just before a job is submitted.
- `base_tables` lists the warehouse reads of a statement: Table nodes that are not CTE names and
  not table functions.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect

from lib.exit_codes import RefusedError

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
SENTINEL = "qtph__"
_SENTINEL_RE = re.compile(r"\bqtph__([A-Za-z_][A-Za-z0-9_]*)\b")
_VALUE_RULES = {
    "bigquery": {"project": r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$", "dataset": r"^[A-Za-z0-9_]{1,1024}$"},
    "duckdb": {"project": r"^[A-Za-z_][A-Za-z0-9_]*$", "dataset": r"^[A-Za-z_][A-Za-z0-9_]*$"},
}
_DIALECTS: dict[str, Dialect] = {}


class SqlTextError(RefusedError):
    """SQL that cannot be parsed, has more than one statement, or has a bad placeholder (exit 2)."""


def dialect(name: str = "bigquery") -> Dialect:
    """Case-sensitive sqlglot dialect (no identifier lowercasing in qualify)."""
    if name not in _DIALECTS:
        _DIALECTS[name] = Dialect.get_or_raise(f"{name}, normalization_strategy = case_sensitive")
    return _DIALECTS[name]


def sha256_text(text: str) -> str:
    """SHA-256 of text as UTF-8 with LF line endings (how every SQL file is written)."""
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- placeholders
def protect(sql: str) -> str:
    """Replace `{{name}}` with the identifier `qtph__name` so sqlglot can parse it."""
    if SENTINEL in sql:
        raise SqlTextError(f"SQL already contains the reserved prefix {SENTINEL!r}")
    return PLACEHOLDER.sub(lambda m: SENTINEL + m.group(1), sql)


def unprotect(sql: str) -> str:
    """Restore `{{name}}` placeholders in generated SQL (backticks around a protected path are dropped)."""
    out = re.sub(r"`((?:qtph__\w+|\w+)(?:\.(?:qtph__\w+|[\w-]+))*)`",
                 lambda m: m.group(1) if SENTINEL in m.group(1) else m.group(0), sql)
    return _SENTINEL_RE.sub(lambda m: "{{" + m.group(1) + "}}", out)


def placeholders_in(sql: str) -> set[str]:
    """Placeholder names used in SQL text."""
    return {m.group(1) for m in PLACEHOLDER.finditer(sql)}


def _kind(name: str) -> str:
    return "project" if name == "project" else "dataset"


def render(sql: str, values: Mapping[str, Any], dialect_name: str = "bigquery") -> str:
    """Strict substitution (T16): unknown name, invalid value or leftover braces raise SqlTextError.

    On BigQuery a `{{project}}.{{ds}}.table` path becomes one backticked token.
    """
    rules = _VALUE_RULES[dialect_name]

    def val(name: str) -> str:
        if name not in values:
            raise SqlTextError(f"placeholder '{name}' has no value in the env file")
        v = str(values[name])
        if not re.fullmatch(rules[_kind(name)], v):
            raise SqlTextError(f"value for placeholder '{name}' fails the {dialect_name} identifier rule")
        return v

    if dialect_name == "bigquery":
        # an already backticked path (discover.py, trace.py write `{{project}}.{{ds}}.t`) keeps one pair
        triple = re.compile(r"`?\{\{\s*(project)\s*\}\}\.\{\{\s*(\w+)\s*\}\}\.([A-Za-z_][A-Za-z0-9_]*)`?")
        sql = triple.sub(lambda m: "`%s.%s.%s`" % (val(m.group(1)), val(m.group(2)), m.group(3)), sql)
    out = PLACEHOLDER.sub(lambda m: val(m.group(1)), sql)
    if "{{" in out or "}}" in out:
        raise SqlTextError("unresolved or malformed placeholder left in SQL")
    return out


# ---------------------------------------------------------------- parsing and generation
def parse_statements(sql: str, dialect_name: str = "bigquery") -> list[exp.Expression]:
    """Parse every statement (placeholders protected); a parse error is a refusal."""
    try:
        trees = sqlglot.parse(protect(sql), read=dialect(dialect_name))
    except sqlglot.errors.ParseError as exc:
        raise SqlTextError(f"SQL does not parse ({dialect_name}): {str(exc).splitlines()[0][:200]}") from exc
    return [t for t in trees if t is not None]


def parse_one_query(sql: str, dialect_name: str = "bigquery") -> exp.Expression:
    """Exactly one statement; raise SqlTextError otherwise."""
    trees = parse_statements(sql, dialect_name)
    if len(trees) != 1:
        raise SqlTextError(f"expected exactly one statement, found {len(trees)}")
    return trees[0]


def to_sql(node: exp.Expression, dialect_name: str = "bigquery", pretty: bool = True) -> str:
    """Generate SQL text with placeholders restored."""
    return unprotect(node.sql(dialect=dialect(dialect_name), pretty=pretty))


class BignumericError(SqlTextError):
    """BIGNUMERIC does not fit DuckDB DECIMAL(38): never replayed locally (D-03, spec 5.5)."""


def transpile_to_duckdb(sql_or_tree: str | exp.Expression, strip_time_travel: bool = False) -> str:
    """BigQuery SQL -> DuckDB SQL with explicit type rules instead of the transpiler's defaults.

    sqlglot 30.30 turns GoogleSQL NUMERIC into a bare DECIMAL (DuckDB DECIMAL(18,3): scale 9 silently
    truncated, the NUMERIC maximum fails to convert) and BIGNUMERIC into DECIMAL(38,5). Here NUMERIC without
    parameters becomes DECIMAL(38,9) (the GoogleSQL NUMERIC scale); NUMERIC(P,S) keeps its parameters;
    BIGNUMERIC raises BignumericError. sqlglot writes ORDER BY with explicit NULLS FIRST/LAST matching
    BigQuery (DuckDB's own default is NULLS LAST), so null ordering is stated, not inherited.
    """
    tree = parse_one_query(sql_or_tree, "bigquery") if isinstance(sql_or_tree, str) else sql_or_tree.copy()
    for d in list(tree.find_all(exp.DataType)):
        if d.this == exp.DataType.Type.BIGDECIMAL:
            raise BignumericError("BIGNUMERIC beyond DuckDB DECIMAL(38): not replayed locally (D-03)")
        if d.this == exp.DataType.Type.DECIMAL and not d.expressions:
            d.replace(exp.DataType.build("DECIMAL(38, 9)", dialect="duckdb"))
    if strip_time_travel:
        for t in tree.find_all(exp.Table):
            if t.args.get("version") is not None:
                t.set("version", None)
    return unprotect(tree.sql(dialect=dialect("duckdb")))


def is_query(node: exp.Expression) -> bool:
    """True for a SELECT, a set operation or a parenthesised query (WITH is carried on these)."""
    return isinstance(node, (exp.Select, exp.SetOperation, exp.Subquery))


# ---------------------------------------------------------------- tables
def cte_names(tree: exp.Expression) -> set[str]:
    """Every CTE alias in the tree."""
    return {c.alias for c in tree.find_all(exp.CTE)}


def table_parts(t: exp.Table) -> tuple[str, ...]:
    """(catalog, db, name) parts that are present, placeholders restored."""
    parts = [p for p in (t.catalog, t.db, t.name) if p]
    return tuple(unprotect(p) for p in parts)


def table_key(t: exp.Table) -> str:
    """Dotted reference of a table as written (placeholders restored)."""
    return ".".join(table_parts(t))


def is_comma_join(j: exp.Join) -> bool:
    """`FROM a, b`: no ON/USING, no side, and not an explicit CROSS JOIN.

    sqlglot 30.30 marks a BigQuery comma join kind=CROSS; only an explicit CROSS JOIN carries the
    `pivots` arg (parser detail; sqlglot is pinned, spec 5.5).
    """
    if j.args.get("on") is not None or j.args.get("using") or j.side:
        return False
    kind = str(j.args.get("kind") or "").upper()
    return not kind or (kind == "CROSS" and "pivots" not in j.args)


def is_table_function(t: exp.Table) -> bool:
    """A FROM item that is a function call (DuckDB range(), read_csv()), not a stored table."""
    return not isinstance(t.this, exp.Identifier)


def base_tables(tree: exp.Expression, extra_ctes: Iterable[str] = ()) -> list[exp.Table]:
    """Table nodes that read warehouse tables: not CTE references, not table functions."""
    ctes = cte_names(tree) | set(extra_ctes)
    out = []
    for t in tree.find_all(exp.Table):
        if is_table_function(t):
            continue
        if not t.db and not t.catalog and t.name in ctes:
            continue
        out.append(t)
    return out


def base_table_keys(tree: exp.Expression, extra_ctes: Iterable[str] = ()) -> list[str]:
    """Sorted distinct dotted references of the base tables a statement reads."""
    return sorted({table_key(t) for t in base_tables(tree, extra_ctes)})


# ---------------------------------------------------------------- catalog schema
def nested_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Turn {"ds.table": {"col": "TYPE"}} (or an already nested dict) into sqlglot's nested form.

    Keys may carry placeholders; they are protected the same way as the SQL. Every table must
    have the same depth (sqlglot's MappingSchema requirement).
    """
    flat: dict[tuple[str, ...], dict[str, str]] = {}

    def walk(prefix: tuple[str, ...], node: Mapping[str, Any]) -> None:
        for k, v in node.items():
            if isinstance(v, Mapping) and v and all(isinstance(x, str) for x in v.values()):
                flat[prefix + tuple(protect(k).split("."))] = {str(c): str(t) for c, t in v.items()}
            elif isinstance(v, Mapping):
                walk(prefix + tuple(protect(k).split(".")), v)
            else:
                raise SqlTextError(f"schema entry {k!r} is not a mapping")

    walk((), schema)
    depths = {len(k) for k in flat}
    if len(depths) > 1:
        raise SqlTextError(f"catalog schema mixes table reference depths {sorted(depths)}")
    out: dict[str, Any] = {}
    for parts, cols in flat.items():
        node = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = cols
    return out


def flat_schema(schema: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Inverse of nested_schema, placeholders restored: {"ds.table": {"col": "TYPE"}}."""
    out: dict[str, dict[str, str]] = {}

    def walk(prefix: tuple[str, ...], node: Mapping[str, Any]) -> None:
        for k, v in node.items():
            if isinstance(v, Mapping) and v and all(isinstance(x, str) for x in v.values()):
                out[unprotect(".".join(prefix + (k,)))] = dict(v)
            elif isinstance(v, Mapping):
                walk(prefix + (k,), v)

    walk((), schema)
    return out


# ---------------------------------------------------------------- card conditions (fix r5 addendum 7)
CONDITION_MEANING = ("a SQL condition over the source step's columns, e.g. `status = 'Complete'`, or `TRUE` for all "
                     "rows")


def condition_problem(expr: Any) -> str | None:
    """Why a card condition (MECE universe or part, pop_bands condition) is not one SQL boolean expression, else None.

    Parsed with sqlglot's BigQuery dialect as the WHERE clause of `SELECT 1 FROM t WHERE <expr>`; prose ("all orders
    created in the month"), a second clause (GROUP BY, ORDER BY, LIMIT ...) or a second statement is refused."""
    if not isinstance(expr, str) or not expr.strip():
        return "empty condition"
    try:
        stmts = [s for s in sqlglot.parse(f"SELECT 1 FROM t WHERE {expr}", read="bigquery") if s is not None]
    except Exception as e:  # noqa: BLE001 - every parser error is the same answer: not a condition
        return f"not a SQL condition ({str(e).splitlines()[0]})"
    if len(stmts) != 1 or not isinstance(stmts[0], exp.Select):
        return "not a single SQL condition"
    extra = sorted(k for k, v in stmts[0].args.items() if v and k not in ("expressions", "from", "from_", "where"))
    if extra or stmts[0].args.get("where") is None:
        return "not a single SQL condition (it adds " + (", ".join(extra) or "nothing to WHERE") + ")"
    return None


def condition_columns(expr: str) -> list[str]:
    """Fix r6 H1: the column names a card condition references (unqualified, sorted, unique); [] when it names none.

    The condition must already pass condition_problem(); a qualifier (`t.revenue`) is dropped."""
    tree = sqlglot.parse_one(f"SELECT 1 FROM t WHERE {expr}", read="bigquery")
    where = tree.args.get("where")
    return sorted({c.name for c in where.find_all(exp.Column)} if where is not None else set())
