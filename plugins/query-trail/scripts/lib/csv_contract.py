"""CSV contract: writer, typed sidecar, sanctioned reader, row-count assertion.

Implements design spec v1 section 2.2 P6 ("assert rows_written == COUNT(*) whenever rows are
written"), section 5.5 (the only CSV reader is DuckDB read_csv with allow_quoted_nulls=false and
sidecar types) and synthesis v4 decision D-04:

- UTF-8 without BOM, LF line endings, one header row, RFC 4180 quoting.
- Writer is DuckDB COPY (Arrow tables go through DuckDB COPY too); never pandas to_csv.
- NULL is an empty unquoted field; the empty string is written as "".
- Types are pinned by a typed sidecar <stem>.schema.json written next to the CSV.
- The only reader is DuckDB read_csv(columns=<sidecar>, allow_quoted_nulls=false); pandas only
  ever receives frames from DuckDB and never parses CSV itself.
- A normaliser rejects a BOM, a CR outside a quoted field (CRLF or \\r\\r\\n) and non-UTF-8 bytes.
- Nested types (STRUCT, LIST, MAP, ARRAY, UNION) are refused: they must be serialised to text
  by rule before export.

Reused from research_r2/T02_prototype (proto_chain_v2.py, fidelity2.py; T02 step chaining
report v2): the sidecar idea, COPY as writer and the read_csv(columns=..., allow_quoted_nulls=false)
reader. The T02 sidecar was a bare [[name, type], ...] list; this sidecar is an object that also
records the row count, the CSV sha256 and the contract (stage 1 change, recorded in the README).
TIMESTAMP WITH TIME ZONE is written in UTC: the writer sets the DuckDB session TimeZone to UTC,
because DuckDB otherwise writes the machine's local offset (probe, 2026-11-07).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Sequence

import duckdb

from lib.exit_codes import CheckFailedError, RefusedError

SIDECAR_SUFFIX = ".schema.json"
SIDECAR_VERSION = "1"
WRITER_NAME = "duckdb COPY"
READER_FLAGS = "duckdb read_csv allow_quoted_nulls=false columns=<sidecar>"
_BOM = b"\xef\xbb\xbf"
_NESTED = re.compile(r"(STRUCT|MAP|UNION)\s*\(|\[\s*\d*\s*\]\s*$|^LIST\b|^ARRAY\b", re.IGNORECASE)


class CsvContractError(RefusedError):
    """A file or a type does not meet the CSV contract (exit 2: nothing is accepted)."""


class RowCountError(CheckFailedError):
    """rows read back from the CSV differ from the engine's COUNT(*) (exit 1, never accepted)."""


@dataclasses.dataclass(frozen=True)
class Column:
    """One column of a typed sidecar: name and DuckDB type text."""

    name: str
    type: str


@dataclasses.dataclass(frozen=True)
class Sidecar:
    """Typed sidecar describing one CSV (D-04)."""

    columns: tuple[Column, ...]
    rows: int
    csv_sha256: str
    csv_bytes: int
    writer: str = WRITER_NAME

    def to_json(self) -> dict[str, Any]:
        """Return the JSON object written to <stem>.schema.json."""
        return {
            "schema_version": SIDECAR_VERSION,
            "columns": [{"name": c.name, "type": c.type} for c in self.columns],
            "rows": self.rows,
            "csv_sha256": self.csv_sha256,
            "csv_bytes": self.csv_bytes,
            "contract": {
                "encoding": "utf-8",
                "bom": False,
                "line_ending": "LF",
                "quoting": "RFC 4180",
                "null_token": "<empty unquoted field>",
                "empty_string_token": "\"\" (quoted)",
                "writer": self.writer,
                "reader": READER_FLAGS,
            },
        }

    @staticmethod
    def from_json(obj: dict[str, Any]) -> "Sidecar":
        """Parse a sidecar object; raise CsvContractError when it is malformed."""
        try:
            cols = tuple(Column(str(c["name"]), str(c["type"])) for c in obj["columns"])
            return Sidecar(cols, int(obj["rows"]), str(obj["csv_sha256"]), int(obj["csv_bytes"]),
                           str(obj.get("contract", {}).get("writer", WRITER_NAME)))
        except (KeyError, TypeError, ValueError) as exc:
            raise CsvContractError(f"malformed sidecar: {exc}") from exc


# ---------------------------------------------------------------- text and JSON files (UTF-8, no BOM, LF)
def write_text_lf(path: Path, text: str) -> None:
    """Write text as UTF-8 without BOM and with LF line endings, atomically."""
    data = text.replace("\r\n", "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_json_lf(path: Path, obj: Any) -> None:
    """Write JSON (indent 2, non-ASCII kept) under the same file contract."""
    write_text_lf(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 of a file's bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- normaliser
def contract_problems(data: bytes) -> list[str]:
    """List D-04 violations in CSV bytes: BOM, non-UTF-8, CR outside a quoted field, no final LF."""
    problems: list[str] = []
    if data.startswith(_BOM):
        problems.append("file starts with a UTF-8 BOM")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        problems.append(f"not valid UTF-8 at byte {exc.start}")
    # RFC 4180: splitting on the quote byte leaves unquoted text at even indexes ("" toggles twice).
    if any(b"\r" in seg for seg in data.split(b'"')[0::2]):
        problems.append("CR outside a quoted field (CRLF or \\r\\r\\n line ending)")
    if data.count(b'"') % 2:
        problems.append("unbalanced quote")
    if data and not data.endswith(b"\n"):
        problems.append("last line does not end with LF")
    return problems


def assert_contract(path: Path) -> None:
    """Raise CsvContractError when the file violates the byte-level contract."""
    problems = contract_problems(Path(path).read_bytes())
    if problems:
        raise CsvContractError(f"{path}: " + "; ".join(problems))


# ---------------------------------------------------------------- SQL helpers
def run_sql(con: duckdb.DuckDBPyConnection, sql: str) -> list[tuple[Any, ...]]:
    """Execute and drain a statement (DuckDB 2.0 keeps an undrained result open on the connection)."""
    return con.execute(sql).fetchall()


def fetch_dicts(con: duckdb.DuckDBPyConnection, query: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """Column names and rows (dicts) of a SQL text or relation, fetched through Arrow.

    DuckDB's Python fetch of TIMESTAMP WITH TIME ZONE needs pytz, which the pinned env does not carry; the
    Arrow route gives tz-aware datetimes (zoneinfo UTC) without it (found in the thin slice, 2026-11-08).
    """
    rel = con.sql(query) if isinstance(query, str) else query
    table = rel.to_arrow_table()
    return list(table.column_names), table.to_pylist()


def fetch_tuples(con: duckdb.DuckDBPyConnection, sql: str) -> list[tuple[Any, ...]]:
    """Rows as tuples through Arrow (see fetch_dicts)."""
    names, rows = fetch_dicts(con, sql)
    return [tuple(r[n] for n in names) for r in rows]


def scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    """First column of the only row of a drained query."""
    return run_sql(con, sql)[0][0]


def _sql_literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def quote_ident(name: str) -> str:
    """Double-quoted SQL identifier (DuckDB), inner quotes doubled."""
    return '"' + name.replace('"', '""') + '"'





def is_nested_type(type_text: str) -> bool:
    """True for STRUCT, MAP, UNION, LIST/ARRAY types, which the contract refuses."""
    return bool(_NESTED.search(type_text.strip()))


def describe(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[Column, ...]:
    """Return the column names and DuckDB types of a query without materialising it."""
    rows = run_sql(con, "DESCRIBE " + sql)
    return tuple(Column(str(r[0]), str(r[1])) for r in rows)


def read_csv_sql(csv_path: Path, columns: Sequence[Column]) -> str:
    """Build the sanctioned reader expression: read_csv with sidecar types and allow_quoted_nulls=false."""
    cols = ", ".join(f"{_sql_literal(c.name)}: {_sql_literal(c.type)}" for c in columns)
    return (
        f"read_csv({_sql_literal(Path(csv_path).resolve().as_posix())}, header = true, "
        f"columns = {{{cols}}}, allow_quoted_nulls = false, auto_detect = false, "
        "delim = ',', quote = '\"', escape = '\"', new_line = '\\n', strict_mode = true)"
    )


def sidecar_path(csv_path: Path) -> Path:
    """Return <dir>/<stem>.schema.json for a CSV path."""
    p = Path(csv_path)
    return p.with_name(p.stem + SIDECAR_SUFFIX)


def read_sidecar(csv_path: Path) -> Sidecar:
    """Load the sidecar of a CSV; a missing sidecar is a contract refusal."""
    sp = sidecar_path(csv_path)
    if not sp.exists():
        raise CsvContractError(f"sidecar missing: {sp}")
    return Sidecar.from_json(json.loads(sp.read_text(encoding="utf-8-sig")))


# ---------------------------------------------------------------- reader
def read_csv(con: duckdb.DuckDBPyConnection, csv_path: Path, validate: bool = True) -> duckdb.DuckDBPyRelation:
    """Open a contract CSV through its sidecar; the only sanctioned CSV reader (D-04)."""
    sc = read_sidecar(csv_path)
    if validate:
        assert_contract(csv_path)
        if sha256_file(csv_path) != sc.csv_sha256:
            raise CsvContractError(f"{csv_path}: sha256 differs from its sidecar")
    return con.sql("SELECT * FROM " + read_csv_sql(csv_path, sc.columns))


def count_csv_rows(con: duckdb.DuckDBPyConnection, csv_path: Path, columns: Sequence[Column]) -> int:
    """Count data rows by reading the CSV back through the sanctioned reader."""
    return int(scalar(con, "SELECT COUNT(*) FROM " + read_csv_sql(csv_path, columns)))


def assert_row_count(con: duckdb.DuckDBPyConnection, csv_path: Path, columns: Sequence[Column], expected: int) -> int:
    """Raise RowCountError unless the CSV holds exactly `expected` rows; return the count."""
    got = count_csv_rows(con, csv_path, columns)
    if got != expected:
        raise RowCountError(f"{csv_path}: {got} rows in CSV, engine COUNT(*) = {expected}")
    return got


# ---------------------------------------------------------------- writer
def _ensure_utc(con: duckdb.DuckDBPyConnection) -> None:
    run_sql(con, "SET TimeZone = 'UTC'")


def _refuse_nested(columns: Sequence[Column]) -> None:
    bad = [f"{c.name} {c.type}" for c in columns if is_nested_type(c.type)]
    if bad:
        raise CsvContractError("nested types are not allowed in a step CSV; serialise to text first: " + ", ".join(bad))


def write_query_csv(con: duckdb.DuckDBPyConnection, sql: str, csv_path: Path,
                    expected_rows: int | None = None) -> Sidecar:
    """Write the result of `sql` to `csv_path` under the contract and write its sidecar.

    `expected_rows` is the engine's COUNT(*) (for BigQuery: the job's row count); when omitted it
    is computed here with SELECT COUNT(*). The row-count assertion always runs.
    """
    csv_path = Path(csv_path)
    _ensure_utc(con)
    columns = describe(con, sql)
    _refuse_nested(columns)
    if expected_rows is None:
        expected_rows = int(scalar(con, f"SELECT COUNT(*) FROM ({sql})"))
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    target = _sql_literal(csv_path.resolve().as_posix())
    run_sql(con, f"COPY ({sql}) TO {target} (FORMAT csv, HEADER true, DELIMITER ',', "
                 "QUOTE '\"', ESCAPE '\"', NEW_LINE '\\n')")
    assert_contract(csv_path)
    rows = assert_row_count(con, csv_path, columns, expected_rows)
    sc = Sidecar(columns, rows, sha256_file(csv_path), csv_path.stat().st_size)
    write_json_lf(sidecar_path(csv_path), sc.to_json())
    return sc


def write_arrow_csv(table: Any, csv_path: Path, expected_rows: int | None = None) -> Sidecar:
    """Write a pyarrow Table (for example a streamed BigQuery result) through DuckDB COPY."""
    con = duckdb.connect()
    try:
        con.register("qt_arrow_in", table)
        n = table.num_rows if expected_rows is None else expected_rows
        return write_query_csv(con, "SELECT * FROM qt_arrow_in", csv_path, expected_rows=n)
    finally:
        con.close()


def projection_sql(columns: Sequence[Column]) -> str:
    """Comma list of quoted column names, for SELECT lists built from a sidecar."""
    return ", ".join(quote_ident(c.name) for c in columns)
