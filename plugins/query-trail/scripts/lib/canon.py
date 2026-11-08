"""Canonical row serialisation and the streamed multiset hash (design spec v1 section 3.2).

Each row becomes canonical text by the D-04 type rules: NULL distinct from "", decimals at the
contract scale, microsecond timestamps, FLOAT rounded to the card tolerance for hashing only.
The step hash is the sum modulo 2^128 of each canonical row's SHA-256 (read as a big-endian
integer, so the sum keeps its low 128 bits), plus the row count. A sum keeps duplicates, which
XOR would cancel; it uses constant memory and ignores row order.

Canonical row text is the compact JSON array of canonical cell strings, with JSON null for SQL
NULL. Cells are typed by the declared column type (sidecar or DESCRIBE), never by guessing; a
Python value that does not fit its declared type raises CanonError. The value hash is
type-blind on purpose; the schema is compared separately through `schema_fingerprint`, so a
level-60 comparison is count + schema + hash (spec 3.1, 6.1).

FLOAT note (spec 3.2): two in-tolerance values can round to different grid points when they
straddle a boundary; that is a false mismatch, never a false pass. With no tolerance the FLOAT
cell is the shortest round-trip repr, so an in-tolerance change is visible (level 90 and 100).
Separator idea from research_r2/T06 (unit separator for composite keys) is replaced here by
JSON quoting, which has no ambiguous concatenations.
"""
from __future__ import annotations

import collections
import dataclasses
import datetime as dt
import decimal
import hashlib
import json
import math
import re
import uuid
from typing import Any, Iterable, Iterator, Sequence

import duckdb

from lib.csv_contract import Column, describe, read_csv, read_csv_sql, read_sidecar
from lib.exit_codes import ExitCode, RefusedError

MOD = 1 << 128
LOCAL_ROUTE = "local_sha256_sum128"

_INT = {"TINYINT", "SMALLINT", "INTEGER", "INT", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT",
        "UINTEGER", "UBIGINT", "UHUGEINT", "INT2", "INT4", "INT8", "INT64"}
_FLOAT = {"FLOAT", "DOUBLE", "REAL", "FLOAT4", "FLOAT8", "FLOAT64"}
_DEC = {"DECIMAL", "NUMERIC", "BIGNUMERIC", "BIGDECIMAL"}
_STR = {"VARCHAR", "STRING", "TEXT", "CHAR", "BPCHAR", "JSON"}
_TS_NAIVE = {"TIMESTAMP", "DATETIME", "TIMESTAMP_US", "TIMESTAMP_MS", "TIMESTAMP_S", "TIMESTAMP_NS"}
_TS_TZ = {"TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ"}
_DEFAULT_SCALE = {"NUMERIC": 9, "BIGNUMERIC": 38, "BIGDECIMAL": 38, "DECIMAL": 3}
_TYPE_RE = re.compile(r"^\s*([A-Z_ ]+?)\s*(?:\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\))?\s*$", re.IGNORECASE)


# Public type-class sets (NOTES_stage2b item 2): scripts use these, not the private names.
INT_TYPES = frozenset(_INT)
FLOAT_TYPES = frozenset(_FLOAT)
DECIMAL_TYPES = frozenset(_DEC)
STRING_TYPES = frozenset(_STR)
TIMESTAMP_NAIVE_TYPES = frozenset(_TS_NAIVE)
TIMESTAMP_TZ_TYPES = frozenset(_TS_TZ)


def type_class(type_text: str) -> str:
    """Coarse class of a column type: int, float, decimal, string, date, timestamp, timestamp_tz, bool, other."""
    base = str(type_text).split("(")[0].strip().upper()
    if base in _INT:
        return "int"
    if base in _FLOAT:
        return "float"
    if base in _DEC:
        return "decimal"
    if base in _STR:
        return "string"
    if base == "DATE":
        return "date"
    if base in _TS_TZ:
        return "timestamp_tz"
    if base in _TS_NAIVE:
        return "timestamp"
    if base in ("BOOLEAN", "BOOL"):
        return "bool"
    return "other"


class CanonError(RefusedError):
    """A value or type cannot be canonicalised under the D-04 rules (exit 2)."""


@dataclasses.dataclass(frozen=True)
class CellSpec:
    """How one column is canonicalised: base type, decimal scale and FLOAT tolerance."""

    name: str
    base: str
    scale: int | None = None
    float_tolerance: float | None = None


@dataclasses.dataclass(frozen=True)
class StepDigest:
    """A step's multiset hash: digest (32 hex chars), row count, route and schema fingerprint."""

    digest: str
    rows: int
    route: str
    schema_fingerprint: str
    float_tolerance: float | None = None

    def to_json(self) -> dict[str, Any]:
        """Return the manifest `multiset_hash` object."""
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class Comparison:
    """Outcome of comparing two digests: exit code plus the reasons for a non-PASS."""

    code: ExitCode
    reasons: tuple[str, ...]


# ---------------------------------------------------------------- types
def parse_type(type_text: str) -> tuple[str, int | None, int | None]:
    """Split 'DECIMAL(18,2)' into ('DECIMAL', 18, 2); 'BIGINT' into ('BIGINT', None, None)."""
    m = _TYPE_RE.match(type_text)
    if not m:
        raise CanonError(f"unsupported column type: {type_text!r}")
    base = " ".join(m.group(1).upper().split())
    prec = int(m.group(2)) if m.group(2) else None
    scale = int(m.group(3)) if m.group(3) else None
    return base, prec, scale


def cell_spec(column: Column, float_tolerance: float | None = None, scale_override: int | None = None) -> CellSpec:
    """Build the CellSpec for a sidecar column; `scale_override` is the output-contract scale."""
    base, prec, scale = parse_type(column.type)
    if base in _DEC:
        scale = scale_override if scale_override is not None else (scale if scale is not None else
                                                                   (0 if prec is not None else _DEFAULT_SCALE[base]))
    tol = float_tolerance if base in _FLOAT else None
    if tol is not None and not (math.isfinite(tol) and tol > 0):
        raise CanonError(f"float tolerance must be a positive finite number, got {tol!r}")
    return CellSpec(column.name, base, scale, tol)


def specs_for(columns: Sequence[Column], float_tolerance: float | None = None,
              scale_overrides: dict[str, int] | None = None) -> tuple[CellSpec, ...]:
    """CellSpecs for all columns; scale overrides come from the G1b output contract."""
    so = scale_overrides or {}
    return tuple(cell_spec(c, float_tolerance, so.get(c.name)) for c in columns)


def schema_fingerprint(columns: Sequence[Column]) -> str:
    """SHA-256 over the ordered [name, type] list; names, order and types all count."""
    payload = json.dumps([[c.name, c.type] for c in columns], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- cells
def _fmt_decimal(value: decimal.Decimal) -> str:
    if value == 0:
        value = abs(value)
    return format(value, "f")


def _canon_decimal(v: Any, spec: CellSpec) -> str:
    if isinstance(v, bool) or not isinstance(v, (decimal.Decimal, int)):
        raise CanonError(f"{spec.name}: expected DECIMAL, got {type(v).__name__}")
    d = decimal.Decimal(v)
    if not d.is_finite():
        raise CanonError(f"{spec.name}: non-finite DECIMAL {d}")
    q = decimal.Decimal(1).scaleb(-(spec.scale or 0))
    with decimal.localcontext() as ctx:
        ctx.prec = 1000
        return _fmt_decimal(d.quantize(q, rounding=decimal.ROUND_HALF_EVEN))


def _canon_float(v: Any, spec: CellSpec) -> str:
    if isinstance(v, bool) or not isinstance(v, (float, int)):
        raise CanonError(f"{spec.name}: expected FLOAT, got {type(v).__name__}")
    f = float(v)
    if math.isnan(f):
        return "NaN"
    if math.isinf(f):
        return "Infinity" if f > 0 else "-Infinity"
    if spec.float_tolerance is None:
        return repr(0.0 if f == 0 else f)
    tol = decimal.Decimal(repr(spec.float_tolerance))
    with decimal.localcontext() as ctx:
        ctx.prec = 1000
        k = (decimal.Decimal(repr(f)) / tol).quantize(decimal.Decimal(1), rounding=decimal.ROUND_HALF_EVEN)
        return _fmt_decimal(k * tol)


def _canon_timestamp(v: Any, spec: CellSpec, tz: bool) -> str:
    if not isinstance(v, dt.datetime):
        raise CanonError(f"{spec.name}: expected TIMESTAMP, got {type(v).__name__}")
    if v.tzinfo is not None:
        v = v.astimezone(dt.timezone.utc).replace(tzinfo=None)
        tz = True
    return v.isoformat(timespec="microseconds") + ("Z" if tz else "")


def _canon_simple(v: Any, spec: CellSpec) -> str:
    b = spec.base
    if b in _INT:
        if isinstance(v, bool) or not isinstance(v, int):
            raise CanonError(f"{spec.name}: expected integer, got {type(v).__name__}")
        return str(v)
    if b in _STR:
        if not isinstance(v, str):
            raise CanonError(f"{spec.name}: expected text, got {type(v).__name__}")
        return v
    if b in {"BOOLEAN", "BOOL"}:
        if not isinstance(v, bool):
            raise CanonError(f"{spec.name}: expected BOOLEAN, got {type(v).__name__}")
        return "true" if v else "false"
    if b == "DATE":
        if isinstance(v, dt.datetime) or not isinstance(v, dt.date):
            raise CanonError(f"{spec.name}: expected DATE, got {type(v).__name__}")
        return v.isoformat()
    if b in {"TIME", "TIME WITH TIME ZONE"}:
        if not isinstance(v, dt.time):
            raise CanonError(f"{spec.name}: expected TIME, got {type(v).__name__}")
        return v.isoformat(timespec="microseconds")
    if b in {"BLOB", "BYTES", "BYTEA", "VARBINARY"}:
        if not isinstance(v, (bytes, bytearray, memoryview)):
            raise CanonError(f"{spec.name}: expected bytes, got {type(v).__name__}")
        return bytes(v).hex()
    if b == "UUID":
        if not isinstance(v, (uuid.UUID, str)):
            raise CanonError(f"{spec.name}: expected UUID, got {type(v).__name__}")
        return str(uuid.UUID(str(v)))
    raise CanonError(f"{spec.name}: type {b} has no canonical rule (nested or unsupported)")


def canon_cell(value: Any, spec: CellSpec) -> str | None:
    """Canonical text of one cell, or None for SQL NULL (kept distinct from '')."""
    if value is None:
        return None
    if spec.base in _DEC:
        return _canon_decimal(value, spec)
    if spec.base in _FLOAT:
        return _canon_float(value, spec)
    if spec.base in _TS_NAIVE:
        return _canon_timestamp(value, spec, tz=False)
    if spec.base in _TS_TZ:
        return _canon_timestamp(value, spec, tz=True)
    return _canon_simple(value, spec)


def canon_row(values: Sequence[Any], specs: Sequence[CellSpec]) -> bytes:
    """Canonical row bytes: compact JSON array of canonical cells, UTF-8."""
    if len(values) != len(specs):
        raise CanonError(f"row has {len(values)} values for {len(specs)} columns")
    cells = [canon_cell(v, s) for v, s in zip(values, specs)]
    return json.dumps(cells, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def row_digest(row_bytes: bytes) -> int:
    """SHA-256 of a canonical row as a big-endian integer."""
    return int.from_bytes(hashlib.sha256(row_bytes).digest(), "big")


# ---------------------------------------------------------------- multiset hash
class MultisetHash:
    """Streaming accumulator: sum mod 2^128 of row SHA-256 values, plus the row count."""

    def __init__(self) -> None:
        self.total = 0
        self.rows = 0

    def add(self, row_bytes: bytes) -> None:
        """Add one canonical row."""
        self.total = (self.total + row_digest(row_bytes)) % MOD
        self.rows += 1

    def merge(self, other: "MultisetHash") -> None:
        """Combine a partial hash (for partitioned files or parallel batches)."""
        self.total = (self.total + other.total) % MOD
        self.rows += other.rows

    def hexdigest(self) -> str:
        """32 hex characters (128 bits)."""
        return format(self.total, "032x")


def hash_rows(rows: Iterable[Sequence[Any]], specs: Sequence[CellSpec]) -> MultisetHash:
    """Hash an iterable of value tuples in constant memory."""
    acc = MultisetHash()
    for r in rows:
        acc.add(canon_row(r, specs))
    return acc


def _canon_projection(columns: Sequence[Column]) -> str:
    parts = []
    for c in columns:
        q = '"' + c.name.replace('"', '""') + '"'
        base = parse_type(c.type)[0]
        parts.append(f"timezone('UTC', {q}) AS {q}" if base in _TS_TZ else q)
    return ", ".join(parts)


def iter_query_rows(con: duckdb.DuckDBPyConnection, sql: str, columns: Sequence[Column],
                    batch_rows: int = 10_000) -> Iterator[tuple[Any, ...]]:
    """Stream a query's rows in batches; TIMESTAMP WITH TIME ZONE is fetched as UTC wall time."""
    cur = con.execute(f"SELECT {_canon_projection(columns)} FROM ({sql}) AS qt_src")
    while True:
        batch = cur.fetchmany(batch_rows)
        if not batch:
            return
        yield from batch


def digest_query(con: duckdb.DuckDBPyConnection, sql: str, float_tolerance: float | None = None,
                 scale_overrides: dict[str, int] | None = None, columns: Sequence[Column] | None = None,
                 batch_rows: int = 10_000) -> StepDigest:
    """Streamed level-60 digest of a DuckDB query (types from DESCRIBE unless given)."""
    cols = tuple(columns) if columns is not None else describe(con, sql)
    specs = specs_for(cols, float_tolerance, scale_overrides)
    acc = hash_rows(iter_query_rows(con, sql, cols, batch_rows), specs)
    return StepDigest(acc.hexdigest(), acc.rows, LOCAL_ROUTE, schema_fingerprint(cols), float_tolerance)


def digest_csv(con: duckdb.DuckDBPyConnection, csv_path: Any, float_tolerance: float | None = None,
               scale_overrides: dict[str, int] | None = None) -> StepDigest:
    """Digest of a contract CSV read through its sidecar (same canonical rules as the stream).

    The file is validated first (byte contract and sha256 against the sidecar), so a changed
    CSV is refused rather than hashed.
    """
    sc = read_sidecar(csv_path)
    read_csv(con, csv_path, validate=True)
    sql = "SELECT * FROM " + read_csv_sql(csv_path, sc.columns)
    return digest_query(con, sql, float_tolerance, scale_overrides, columns=sc.columns)


def compare_digests(a: StepDigest, b: StepDigest) -> Comparison:
    """Level-60 comparison: REFUSED when routes or tolerances differ, FAIL on count, schema or hash."""
    if a.route != b.route:
        return Comparison(ExitCode.REFUSED, (f"digest routes differ: {a.route} vs {b.route}",))
    if a.float_tolerance != b.float_tolerance:
        return Comparison(ExitCode.REFUSED, (f"float tolerances differ: {a.float_tolerance} vs {b.float_tolerance}",))
    reasons = []
    if a.rows != b.rows:
        reasons.append(f"row count {a.rows} vs {b.rows}")
    if a.schema_fingerprint != b.schema_fingerprint:
        reasons.append("schema differs (names, order or types)")
    if a.digest != b.digest:
        reasons.append("multiset hash differs")
    return Comparison(ExitCode.FAIL if reasons else ExitCode.PASS, tuple(reasons))


# ---------------------------------------------------------------- multiplicity (spec 6.1 step 3 helper)
def multiplicity(rows: Iterable[Sequence[Any]], specs: Sequence[CellSpec]) -> collections.Counter[bytes]:
    """Count canonical rows (duplicates kept). Holds every distinct row in memory: level 90+ only."""
    return collections.Counter(canon_row(r, specs) for r in rows)


def multiplicity_diff(a: collections.Counter[bytes], b: collections.Counter[bytes]) -> list[tuple[str, int, int]]:
    """Canonical rows whose counts differ: (row text, count in a, count in b), sorted by row text."""
    keys = sorted(set(a) | set(b))
    return [(k.decode("utf-8"), a.get(k, 0), b.get(k, 0)) for k in keys if a.get(k, 0) != b.get(k, 0)]
