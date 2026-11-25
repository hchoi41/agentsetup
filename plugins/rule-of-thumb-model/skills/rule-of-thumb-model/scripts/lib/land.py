"""`land`: raw data per the agreed contract; SQL and Power Query M texts; Power Query attach (design sections 7, 15; P2).

Owner: M3. Refuses (exit 2) unless the table's contract is agreed.

- `land DIR --table T` (no CSV): writes queries/T.sql (GoogleSQL for Connected Sheets, shaped to the contract) and
  queries/T.pq.m (Power Query M over raw/T.csv), so the SQL exists before any data does (P1 then P2).
- `land DIR --table T --csv FILE`: validates FILE against the contract (column names and order, types, nulls,
  allowed values, key uniqueness, text length, row count, control totals). Any failure is an error finding and
  nothing is written. Otherwise writes raw/T.csv normalised (UTF-8 without BOM, comma, header row, ISO dates, "."
  decimal, empty field = null; the same text the raw digest is computed over), the query texts, and the new digest
  into the manifest when the workbook was built.
- `land DIR --table T --csv FILE --force --control T.COLUMN=VALUE` (v5, A-aera-6 and C-sol-3; the syntax of check
  --control): the source's control total of a refreshed extract. FILE is validated as above, with the sum of
  COLUMN compared with VALUE (the contract's tolerance of that total) in place of the contract's or the accepted
  total. When FILE fits, the totals are recorded for the new snapshot's digest in raw/T.controls.json with the
  texts given (lib.controls, the record check --accept-refresh --control writes); check and build take them for
  that snapshot. A sum that does not meet VALUE is L-TOTAL and nothing is written. --control needs --csv.
- Query stamps (A-aera-2, C-sol-2): the first line of each generated query file is a comment
  `rotm-stamp: contract sha256:<hash>; body sha256:<hash>` (common.stamp_query): the agreed contract the file was
  written from and the hash of the text below the line. Every land call checks both files before it writes
  anything: absent: written; stamp names the agreed contract: kept (an edit made after this agreement stays);
  stamp names another contract and the text is unchanged: written again (a note says so); edited text with
  another contract, or no stamp: refused (exit 2), with the fixes --rewrite-queries (written again, the old file
  kept as <file>.bak_<time>) and --keep-queries (the file, reconciled by hand, is stamped with the agreed contract).
  --pq-source naming the other kind than queries/T.pq.m holds (csv or database): written again when not edited
  since land wrote it (note), else refused (F-PQ-SWITCH, exit 2) with --rewrite-queries (A-aera-12, C-sol-9).
- Datetimes are cut to the second, never rounded up; a Z or UTC offset (any offset, v5) is dropped and the time
  is landed as written; a number keeps 15 significant digits. A value landed otherwise than its text gets warning
  L-PRECISION, one per column and reason (A-aera-6). read_datetime_text is the one reader of a date and time;
  contract propose uses it too, so every column it proposes as datetime lands (v5, A-aera-16). A value that cannot
  be cast is L-TYPE only, not also L-NULL.
- queries/T.pq.m names the snapshot as `<model folder>\\raw\\T.csv`; the attach puts the model folder's path in
  the text it sends to Excel, so the file holds no path of this computer (O-1).
- `--power-query`: attaches a Power Query table that reads raw/T.csv to the built workbook's raw sheet, through
  Excel (lib.excel.pq_attach), and records landing power_query, the query's contract hash and (v5) the body hash
  of the attached text (query_body_sha256) in the manifest (and workbook_digest when the loaded rows equal the
  landed snapshot). A queries/T.pq.m without a stamp, or
  stamped for another contract, is refused before Excel starts.
- v6 (r5 verdict A-aera-2): after the attach, the formula of q_T is read back from the saved workbook through Excel
  (lib.excel.inventory) and its hash is recorded as query_formula_sha256 (query_formula_sha256(): line ends and
  trailing white space made uniform); check --refresh reads the formula again before the refresh and compares
  (X-QUERY-CHANGED). When the formula cannot be read back, no hash is recorded and a note says so.
- v6 (r5 verdict C-sol-6): after rows are landed into a model whose workbook has a manifest, a number column whose
  values hold more decimals than the column's number format (the manifest's formats) shows gets the information
  finding I-HIDDEN-DECIMALS (check.hidden_decimal_findings); no format is changed.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import io
import math
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import common

PQ_TYPES = {"text": "type text", "integer": "Int64.Type", "number": "type number",
            "date": "type date", "datetime": "type datetime", "boolean": "type logical"}

MODEL_FOLDER = "<model folder>"   # stands for the model folder's path in queries/<t>.pq.m (O-1)

# Power Query connectors for Value.NativeQuery. EnableFolding is written only for the systems Microsoft lists
# for it (platforms B1.4, verified with a narrower condition for Dataverse). The connector function names are
# the plugin's choice from Microsoft's connector documentation; they are not confirmed by the research files.
# The last field is how the SQL inside the query quotes a name for that system (C-sol-6): square brackets for
# SQL Server, double quotes for PostgreSQL, Snowflake, Redshift, SAP HANA and Oracle, backticks for BigQuery and
# MySQL. An unknown system gets Sql.Database and square brackets. queries/<t>.sql stays GoogleSQL.
_PQ_CONNECTORS = (
    (("bigquery",), 'GoogleBigQuery.Database(){[Name = "<billing project>"]}[Data]', True, "bigquery"),
    (("sql server", "sqlserver", "mssql", "azure sql", "synapse"), 'Sql.Database("<server>", "<database>")', True,
     "bracket"),
    (("postgres",), 'PostgreSQL.Database("<server>", "<database>")', True, "double"),
    (("snowflake",), 'Snowflake.Databases("<account>.snowflakecomputing.com", "<warehouse>")'
                     '{[Name = "<database>"]}[Data]', True, "double"),
    (("redshift",), 'AmazonRedshift.Database("<server>", "<database>")', True, "double"),
    (("sap hana", "hana"), 'SapHana.Database("<server>")', True, "double"),
    (("oracle",), 'Oracle.Database("<server>")', False, "double"),
    (("mysql",), 'MySQL.Database("<server>", "<database>")', False, "mysql"),
)
_QUOTE_STYLES = {"bigquery": "`name`", "mysql": "`name`", "bracket": "[name]", "double": '"name"'}
# The reserved keywords of GoogleSQL. Source: GoogleSQL lexical structure, reserved keywords; not re-checked here.
# A name that is one of them, or is not a plain identifier, is written in backticks (_sql_ident; contract propose
# writes a source expression taken from a header this way too, v4 A-aera-11).
GOOGLESQL_RESERVED = frozenset("""ALL AND ANY ARRAY AS ASC ASSERT_ROWS_MODIFIED AT BETWEEN BY CASE CAST COLLATE CONTAINS CREATE
CROSS CUBE CURRENT DEFAULT DEFINE DESC DISTINCT ELSE END ENUM ESCAPE EXCEPT EXCLUDE EXISTS EXTRACT FALSE FETCH
FOLLOWING FOR FROM FULL GROUP GROUPING GROUPS HASH HAVING IF IGNORE IN INNER INTERSECT INTERVAL INTO IS JOIN
LATERAL LEFT LIKE LIMIT LOOKUP MERGE NATURAL NEW NO NOT NULL NULLS OF ON OR ORDER OUTER OVER PARTITION PRECEDING
PROTO QUALIFY RANGE RECURSIVE RESPECT RIGHT ROLLUP ROWS SELECT SET SOME STRUCT TABLESAMPLE THEN TO TREAT TRUE
UNBOUNDED UNION UNNEST USING WHEN WHERE WINDOW WITH WITHIN""".split())
_PLAIN_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BACKTICK_IDENT_RE = re.compile(r"`((?:[^`\\]|\\.)+)`")      # exactly one backtick-quoted GoogleSQL identifier
_INT_RE = re.compile(r"^[+-]?\d+(?:\.0*)?$")
_NUM_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_DATE_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T]00:00(?::00(?:\.0+)?)?)?$")
# A date and time (read_datetime_text): the time with a T or a space, seconds and a fraction optional, then
# optionally Z, ' UTC' or a UTC offset written right after the time (+09:00, +0900, -05:30). v5 (A-aera-16): any
# offset, not only +00:00, is dropped and the time is landed as written (L-PRECISION 'zone').
_DT_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?"
                    r"(Z| UTC|[+-](\d{2}):?(\d{2}))?$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")      # characters an .xlsx cell cannot hold
MAX_EXAMPLES = 5
EXCEL_DIGITS = 15            # Excel keeps 15 significant digits (platforms B5.1 limits)
MAX_TEXT_CHARS = 32767       # the most characters an Excel cell holds (counted in UTF-16 code units, as Excel does)


@dataclass
class RawTable:
    table: str
    columns: list[str]
    types: list[str]
    rows: list[tuple[Any, ...]] = field(default_factory=list)   # typed per contract; None = null
    digest: str = ""                                            # manifest.raw_digest


# ----------------------------------------------------------------------------- values

def read_datetime_text(text: str, *, dates: bool = True) -> tuple[_dt.datetime | None, list[str]]:
    """The one reader of a date and time that land and contract propose use (round 4 verdict, A-aera-16):
    (datetime to the second, precision changes) or (None, []) for a text that is no date and time.
    Forms: YYYY-MM-DD, a T or a space, HH:MM, optional :SS and a fraction of a second, optional Z, ' UTC' or a UTC
    offset (+09:00, +0900, -05:30); with `dates`, also a date alone (YYYY-MM-DD, or with / or . and one-digit month
    or day, optionally followed by T00:00 or 00:00:00), read as midnight. The fraction is cut, never rounded up
    (change 'fraction' when it is not all zeros); a Z, ' UTC' or an offset is dropped and the time is kept as
    written (change 'zone'). Both are the keys of PRECISION_REASONS (warning L-PRECISION)."""
    t = (text or "").strip()
    m = _DT_RE.match(t)
    if m:
        if m.group(9) is not None and (int(m.group(9)) > 23 or int(m.group(10)) > 59):
            return None, []
        try:
            # the fraction m.group(7) is cut, never rounded up: 23:59:59.900 stays on its day (A-aera-6)
            d = _dt.datetime(*(int(m.group(i)) for i in range(1, 6)), int(m.group(6) or 0))
        except ValueError:
            return None, []
        changes = []
        if m.group(7) and m.group(7).strip("0"):
            changes.append("fraction")
        if m.group(8):
            changes.append("zone")
        return d, changes
    if dates:
        m = _DATE_RE.match(t)
        if m:
            try:
                return _dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))), []
            except ValueError:
                return None, []
    return None, []


def parse_value(text: str, col_type: str) -> tuple[Any, str | None]:
    """(typed value, problem) for one CSV field; '' is null. Dates become datetime.date, datetimes
    datetime.datetime to the second (a fraction of a second is cut, never rounded up; a Z or UTC offset is dropped
    and the time is kept as written: manifest.cut_to_second, A-aera-6), booleans bool. precision_changes() says
    which of these changes a value had."""
    if text is None or text == "":
        return None, None
    t = text.strip()
    if t == "":
        return (None, None) if col_type != "text" else (text, None)
    if col_type == "text":
        bad = _CONTROL_RE.search(text)
        if bad:
            return None, (f"'{text[:40]}' holds the control character U+{ord(bad.group(0)):04X}, which a workbook "
                          "cannot store")
        return text, None
    if col_type == "integer":
        if _INT_RE.match(t):
            return int(float(t)) if "." in t else int(t), None
        if _NUM_RE.match(t.replace(",", "")) and "," in t:
            return None, f"'{text}' has a thousands separator"
        return None, f"'{text}' is not a whole number"
    if col_type == "number":
        if _NUM_RE.match(t):
            x = float(t)
            if math.isinf(x) or math.isnan(x):
                return None, f"'{text}' is not a finite number"
            return x, None
        if _NUM_RE.match(t.replace(",", "")):
            return None, f"'{text}' has a thousands separator (the snapshot uses no separator and '.' decimals)"
        return None, f"'{text}' is not a number"
    if col_type == "date":
        m = _DATE_RE.match(t)
        if m:
            try:
                return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))), None
            except ValueError:
                return None, f"'{text}' is not a real date"
        return None, f"'{text}' is not a date in the form YYYY-MM-DD"
    if col_type == "datetime":
        d, _changes = read_datetime_text(t)          # the reader contract propose uses too (v5, A-aera-16)
        if d is not None:
            return d, None
        if _DT_RE.match(t):
            return None, f"'{text}' is not a real date and time"
        if _DATE_RE.match(t):
            return None, f"'{text}' is not a real date"
        return None, f"'{text}' is not a date and time in the form YYYY-MM-DD HH:MM:SS (local time)"
    if col_type == "boolean":
        u = t.upper()
        if u in ("TRUE", "1"):
            return True, None
        if u in ("FALSE", "0"):
            return False, None
        return None, f"'{text}' is not TRUE or FALSE"
    return text, None


def snapshot_text(value: Any, col_type: str) -> str:
    """The snapshot CSV text of a typed value (the normalisation of the raw digest, design 6.8). A datetime is cut
    to the second (manifest.cut_to_second, the rule of both digests; never rounded up)."""
    from . import manifest
    if col_type == "datetime" and isinstance(value, _dt.datetime):
        return manifest.cut_to_second(value).isoformat()
    return manifest.normalise_value(value, col_type)


PRECISION_REASONS = {
    "digits": f"more than {EXCEL_DIGITS} significant digits; Excel and the snapshot keep {EXCEL_DIGITS}",
    "fraction": "a fraction of a second was cut (never rounded up)",
    "zone": "a Z or UTC offset was dropped; the time is landed as written",
}
PRECISION_FIX = ("decide with the user: round the values in the source, or land the column as text (change its "
                 "type in the contract and get the contract agreed again)")


def precision_changes(text: str, col_type: str) -> list[str]:
    """The keys of PRECISION_REASONS that apply to one field parse_value accepted (A-aera-6): 'digits' for a number
    whose snapshot text (15 significant digits) is another value than the text; 'fraction' for a datetime whose
    fraction of a second is not all zeros; 'zone' for a datetime written with Z, ' UTC' or a UTC offset
    (read_datetime_text)."""
    t = (text or "").strip()
    if not t:
        return []
    if col_type == "number":
        from decimal import Decimal, InvalidOperation
        try:
            landed = snapshot_text(float(t), "number")
            return [] if Decimal(t) == Decimal(landed) else ["digits"]
        except (InvalidOperation, ValueError, OverflowError):
            return []
    if col_type == "datetime":
        return read_datetime_text(t)[1]
    return []


def _contract(model_root: Path, table: str, *, require_agreed: bool = True) -> dict[str, Any]:
    from . import modelfolder, spec as specmod
    state, data, why = modelfolder.read_contract(model_root, table)
    cfile = f"contracts/{table}.contract.json"
    model = model_root.as_posix()
    agree = (f"show the user contracts/{table}.contract.md, then record their reply with rotm.py contract agree "
             f"\"{model}\" --table {table} --reply \"<their words>\" --by <name>")
    if state == "missing":
        raise common.Refusal(f"raw table '{table}' has no contract ({cfile})",
                             fix=f"propose one with rotm.py contract propose \"{model}\" --table {table} --csv "
                                 "<sample>, show it to the user and record their reply")
    if state == "invalid":
        raise common.Refusal(f"{cfile} is not valid: " + "; ".join(why[:3]),
                             fix=f"propose it again with rotm.py contract propose \"{model}\" --table {table} --force")
    if not require_agreed:
        return data  # type: ignore[return-value]
    if state == "proposed":
        raise common.Refusal(f"the contract of raw table '{table}' is proposed, not agreed; nothing was landed",
                             fix=agree)
    if state == "stale":
        ag = data.get("agreement") or {}
        raise common.Refusal(f"the contract of raw table '{table}' was changed after {ag.get('by')} agreed it on "
                             f"{ag.get('at')}; nothing was landed", fix=agree)
    ag = data.get("agreement") or {}
    if ag.get("channel") == "example" and not specmod.example_channel_allowed(model_root):
        raise common.Refusal(f"the contract of raw table '{table}' carries the 'example' agreement of a shipped "
                             "example or template; outside the plugin folder it counts as not agreed", fix=agree)
    return data  # type: ignore[return-value]


def _root(model_root: str | Path) -> Path:
    from . import modelfolder
    r = Path(os.path.abspath(common.native_path(model_root)))
    if not r.is_dir():
        raise common.Refusal(f"{r.as_posix()} is not a folder", fix="give the model folder (the one holding "
                             "rotm_model.json)")
    if not modelfolder.is_model_root(r):
        found = modelfolder.find_model_root(r)
        if found is not None and found != r:
            raise common.Refusal(f"{r.as_posix()} lies inside the model folder {found.as_posix()}",
                                 fix=f"give {found.as_posix()}")
        raise common.Refusal(f"{r.as_posix()} is not a model folder (no rotm_model.json)",
                             fix=f"run rotm.py init \"{r.as_posix()}\" first")
    return r


# ----------------------------------------------------------------------------- reading and validating a CSV

def _read_csv(path: Path, encoding: str, delimiter: str) -> tuple[list[str], list[list[str]]]:
    if not path.is_file():
        raise common.Refusal(f"{path.as_posix()}: CSV file not found", fix="check the path given to --csv")
    try:
        text = path.read_text(encoding=encoding)
    except UnicodeDecodeError as exc:
        raise common.Refusal(f"{path.name} is not {encoding} text (byte {exc.start})",
                             fix="save it as CSV UTF-8, or pass --encoding cp949 for a file saved by Korean Excel") \
            from None
    except LookupError:
        raise common.Refusal(f"encoding '{encoding}' is not known", key="--encoding",
                             fix="use utf-8-sig (default), utf-8 or cp949") from None
    except OSError as exc:
        raise common.Refusal(f"{path.name} cannot be read ({exc.strerror or exc})",
                             fix="close the program that holds it") from None
    if text.startswith("﻿"):
        text = text[1:]
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        raise common.Refusal(f"{path.name} is empty", fix="land a CSV with a header row")
    header = [h.strip() for h in rows[0]]
    body = [r for r in rows[1:] if any(c != "" for c in r)]
    return header, body


def key_tuple(parts: Any) -> tuple[Any, ...]:
    """A key as land, check, lift and the workbook's dup helper compare it (common.key_tuple): exact text,
    case-sensitive, no wildcard, text never read as a number; numbers, dates and booleans by value."""
    return common.key_tuple(tuple(parts))


def expect_of(contract: dict[str, Any]) -> dict[str, Any]:
    return contract.get("expect") or {}


def _utf16_len(text: str) -> int:
    """The length Excel counts: UTF-16 code units (a character outside the Basic Multilingual Plane counts 2)."""
    return len(text.encode("utf-16-le")) // 2


def _accepted_totals(model_root: Path, table: str, names: list[str], types: list[str],
                     typed: list[tuple[Any, ...]]) -> tuple[list[dict[str, Any]] | None, str]:
    """(totals, digest): the control totals accepted for exactly these rows in raw/<table>.controls.json
    (lib.controls, written by check --accept-refresh --control), or None."""
    from . import controls, manifest
    if not controls.path(model_root, table).is_file():
        return None, ""
    digest = manifest.raw_digest(names, [[snapshot_text(v, types[i]) for i, v in enumerate(r)] for r in typed], types)
    found = controls.totals_for(model_root, table, digest)
    if isinstance(found, dict):                 # tolerate a record in place of its totals list
        found = found.get("totals")
    return (list(found) if found is not None else None), digest


def validate_rows(contract: dict[str, Any], header: list[str], body: list[list[str]], report: Any,
                  *, source: str, model_root: str | Path | None = None, controls: dict[str, float] | None = None,
                  totals_used: list[dict[str, Any]] | None = None) -> list[tuple[Any, ...]] | None:
    """Check the rows against the contract; add error findings to `report`. Returns the typed rows, or None
    when the header itself is wrong (nothing else can be checked). With `model_root`, the control totals accepted
    for exactly these rows (raw/<table>.controls.json) are checked in place of the contract's (A-aera-7).
    `controls` ({column: source total}, land --control, v5 A-aera-6 and C-sol-3) take the place of the contract's
    or the accepted total of their columns, with the contract's tolerance of that total (0 when the contract has
    none); a sum that does not meet one is L-TOTAL. When `totals_used` is a list, the totals the rows were
    compared with ({"column", "value", "tolerance"}) are appended to it."""
    cols = contract["columns"]
    names = [c["name"] for c in cols]
    table = contract["table"]
    if header != names:
        if sorted(header) == sorted(names):
            report.error("L-COLUMNS", f"{source}: the columns are the contract's but in another order: "
                         f"{', '.join(header)}", f"write them in the contract's order: {', '.join(names)} (change the "
                         "SELECT list)", data={"expected": names, "found": header})
        else:
            missing = [n for n in names if n not in header]
            extra = [h for h in header if h not in names]
            case = [h for h in extra if h.lower() in {n.lower() for n in names}]
            msg = f"{source}: the header does not match contract '{table}'"
            parts = []
            if missing:
                parts.append("missing " + ", ".join(missing))
            if extra:
                parts.append("not in the contract " + ", ".join(extra))
            if case:
                parts.append("names differ only in upper and lower case: " + ", ".join(case))
            report.error("L-COLUMNS", msg + " (" + "; ".join(parts) + ")",
                         f"land a CSV whose header is exactly: {', '.join(names)}; or propose the contract again",
                         data={"expected": names, "found": header})
        return None
    width = len(names)
    typed: list[tuple[Any, ...]] = []
    bad_width: list[int] = []
    type_bad: dict[int, list[str]] = {}
    nulls: dict[int, list[int]] = {}
    not_allowed: dict[int, list[str]] = {}
    digits: dict[int, list[str]] = {}
    too_long: dict[int, list[str]] = {}
    precision: dict[tuple[int, str], list[Any]] = {}     # (column, reason) -> [count, first line, text, landed]
    for ri, row in enumerate(body):
        line = ri + 2
        if len(row) != width:
            bad_width.append(line)
            continue
        vals = []
        for ci, c in enumerate(cols):
            v, prob = parse_value(row[ci], c["type"])
            if prob:
                type_bad.setdefault(ci, []).append(f"line {line}: {prob}")
            elif v is not None and c["type"] in ("number", "datetime"):
                for why in precision_changes(row[ci], c["type"]):
                    seen_p = precision.get((ci, why))
                    if seen_p is None:
                        precision[(ci, why)] = [1, line, row[ci], snapshot_text(v, c["type"])]
                    else:
                        seen_p[0] += 1
            if isinstance(v, str) and len(v) > MAX_TEXT_CHARS // 2 and _utf16_len(v) > MAX_TEXT_CHARS:
                too_long.setdefault(ci, []).append(f"line {line} ({_utf16_len(v):,} characters)")
            if v is None and not prob and c["null_rule"] == "never":
                # v5 (A-aera-16): a value that cannot be cast is L-TYPE only; it is not an empty field
                nulls.setdefault(ci, []).append(line)
            allowed = c.get("allowed")
            if allowed is not None and v is not None and not prob:
                ok = any(v == a or str(v) == str(a) for a in allowed)
                if not ok:
                    not_allowed.setdefault(ci, []).append(f"line {line}: {row[ci]!r}")
            if c["type"] == "integer" and isinstance(v, int) and abs(v) >= 10 ** EXCEL_DIGITS:
                digits.setdefault(ci, []).append(f"line {line}: {row[ci]}")
            vals.append(v)
        typed.append(tuple(vals))
    if bad_width:
        report.error("L-FIELDS", f"{source}: {len(bad_width)} row(s) do not have {width} fields (lines "
                     f"{', '.join(str(x) for x in bad_width[:MAX_EXAMPLES])})",
                     "quote fields that hold the delimiter, and give every row one value per contract column",
                     count=len(bad_width))
    for ci, probs in type_bad.items():
        c = cols[ci]
        report.error("L-TYPE", f"{source}: column {c['name']} (type {c['type']}): {len(probs)} value(s) do not "
                     f"fit: {'; '.join(probs[:MAX_EXAMPLES])}",
                     "cast the column in the SQL to the contract type, or propose the contract again with the "
                     "right type", count=len(probs), data={"column": c["name"]})
    for ci, lines in nulls.items():
        c = cols[ci]
        known = (c.get("known_nulls") or {}).get("max", 0)
        where_lines = f"lines {', '.join(str(x) for x in lines[:MAX_EXAMPLES])}"
        if known and len(lines) <= known:
            report.info("L-KNOWN", f"{source}: column {c['name']} is empty on {len(lines)} row(s) ({where_lines}); "
                        f"the contract agrees at most {known} as known (columns.{c['name']}.known_nulls), so the "
                        "rows are landed and the workbook's null check counts them",
                        data={"column": c["name"], "nulls": len(lines), "known": known})
            continue
        report.error("L-NULL", f"{source}: column {c['name']} is never null by contract but is empty on "
                     f"{len(lines)} row(s) ({where_lines})"
                     + (f"; the contract agrees only {known} as known" if known else ""),
                     "fill the value in the source query (COALESCE only if the user agreed a default); or, to land "
                     "the rows as delivered and keep the null check, agree the empty cells as known "
                     f"(contract propose --force ... --known-nulls {c['name']}={len(lines)}); or agree null_rule "
                     "'allowed' (no null check)", count=len(lines), data={"column": c["name"]})
    for ci, vals in not_allowed.items():
        c = cols[ci]
        report.error("L-ALLOWED", f"{source}: column {c['name']} holds {len(vals)} value(s) outside the allowed "
                     f"list {c['allowed']}: {'; '.join(vals[:MAX_EXAMPLES])}",
                     "filter or map them in the source query as agreed, or agree a longer allowed list",
                     count=len(vals), data={"column": c["name"]})
    for ci, vals in digits.items():
        c = cols[ci]
        report.warning("L-DIGITS", f"{source}: column {c['name']} holds whole numbers of {EXCEL_DIGITS + 1} or more "
                       f"digits ({'; '.join(vals[:MAX_EXAMPLES])}); Excel keeps 15 significant digits",
                       "make the column type text in the contract (identifiers are text)", data={"column": c["name"]})
    # A-aera-6: a value that the snapshot holds otherwise than the text: one warning per column and reason; the rows
    # are landed as the message says, and the user decides on rounding or on landing the column as text
    for (ci, why), (count, first, text, landed) in sorted(precision.items()):
        c = cols[ci]
        report.warning("L-PRECISION", f"column {c['name']}: {count} value(s) landed differently from the text "
                       f"(first line {first}: '{text}' landed as '{landed}'): {PRECISION_REASONS[why]}",
                       PRECISION_FIX, count=count,
                       data={"column": c["name"], "reason": why, "first_line": first, "text": text,
                             "landed": landed})
    for ci, vals in too_long.items():
        c = cols[ci]
        report.error("L-LENGTH", f"{source}: column {c['name']} holds {len(vals)} value(s) longer than "
                     f"{MAX_TEXT_CHARS:,} characters, the most an Excel cell holds: {'; '.join(vals[:MAX_EXAMPLES])}",
                     f"shorten the text of {c['name']} in the source query if the user agrees (for example "
                     f"LEFT(<expression>, {MAX_TEXT_CHARS})), or leave the column out of the contract",
                     count=len(vals), data={"column": c["name"]})
    if bad_width:
        return typed
    # key uniqueness: exact, case-sensitive equality (common.key_tuple), the rule of check's D-KEY, lift and the
    # workbook's dup helper (a later row whose key equals an earlier row's key counts once)
    key_idx = [names.index(k) for k in contract["key"]]
    seen: dict[tuple[Any, ...], int] = {}
    dups: list[str] = []
    pairs: list[str] = []
    for ri, row in enumerate(typed):
        k = key_tuple(row[i] for i in key_idx)
        if k in seen:
            dups.append(f"lines {seen[k]} and {ri + 2}: {', '.join(str(row[i]) for i in key_idx)}")
            pairs.append(f"{seen[k]} and {ri + 2}")
        else:
            seen[k] = ri + 2
    if dups:
        known = (expect_of(contract).get("key_repeats") or {}).get("max", 0)
        if known and len(dups) <= known:
            report.info("L-KNOWN", f"{source}: {len(dups)} row(s) repeat an earlier key ({', '.join(contract['key'])}): "
                        f"{'; '.join(dups[:MAX_EXAMPLES])}; the contract agrees at most {known} as known "
                        "(expect.key_repeats), so the rows are landed and the workbook's key check counts them",
                        data={"repeats": len(dups), "known": known})
        else:
            shown = "; ".join(pairs[:MAX_EXAMPLES]) + ("; ..." if len(pairs) > MAX_EXAMPLES else "")
            report.error("L-KEY", f"{source}: {len(dups)} row(s) repeat an earlier key ({', '.join(contract['key'])}): "
                         f"{'; '.join(dups[:MAX_EXAMPLES])}"
                         + (f"; the contract agrees only {known} as known" if known else ""),
                         f"show the user the rows on lines {shown} of {source} and look at them together: if the "
                         "user accepts them as they are, agree them as known exceptions (contract propose --force "
                         f"... --key-repeats {len(dups)}, then show the contract and record the user's reply); "
                         "otherwise have the source corrected. Do not remove or merge rows to make the key unique",
                         count=len(dups))
    # rows
    expect = contract.get("expect", {})
    n = len(typed)
    rows = expect.get("rows")
    if rows:
        if "exact" in rows and n != rows["exact"]:
            report.error("L-ROWS", f"{source}: {n:,} rows; the contract expects exactly {rows['exact']:,}",
                         "check the source query's filter, or agree the new row count", data={"rows": n})
        elif "min" in rows and not (rows["min"] <= n <= rows["max"]):
            report.error("L-ROWS", f"{source}: {n:,} rows; the contract expects {rows['min']:,} to {rows['max']:,}",
                         "check the source query's filter, or agree a new range", data={"rows": n})
    if n == 0 and not (contract.get("refresh") or {}).get("allow_empty", False):
        report.error("L-EMPTY", f"{source}: no data rows", "land a non-empty extract, or agree refresh.allow_empty "
                     "true in the contract")
    # control totals (from the source side, design 5): the exact sum of the landed text against the total with its
    # tolerance and nothing else (common.control_total_ok, A-aera-4). The totals accepted for exactly these rows by
    # check --accept-refresh --control (raw/<table>.controls.json) take the place of the contract's (A-aera-7).
    totals = list(expect.get("totals", []))
    given = dict(controls or {})
    whose = "the contract's"
    if model_root is not None:
        accepted, digest = _accepted_totals(Path(model_root), table, names, [c["type"] for c in cols], typed)
        if accepted is not None:
            totals = accepted
            whose = f"the accepted (raw/{table}.controls.json)"
            if any(t["column"] not in given for t in accepted):
                report.note(f"control totals: the ones accepted for these rows ({digest[:19]}...) in "
                            f"raw/{table}.controls.json, in place of the contract's")
    if given:
        # v5 (A-aera-6, C-sol-3): the source totals given with land --control, with the contract's tolerance
        tol_of = {x["column"]: x.get("tolerance", 0) or 0 for x in expect.get("totals", [])}
        rest = [t for t in totals if t["column"] not in given]
        totals = [{"column": c, "value": v, "tolerance": tol_of.get(c, 0), "given": True} for c, v in given.items()]
        totals += rest
    for t in totals:
        ci = names.index(t["column"])
        ctype = cols[ci]["type"]
        texts = [snapshot_text(r[ci], ctype) for r in typed
                 if isinstance(r[ci], (int, float)) and not isinstance(r[ci], bool)]
        tol = float(t.get("tolerance", 0) or 0)
        if totals_used is not None:
            totals_used.append({"column": t["column"], "value": t["value"], "tolerance": tol})
        ok, total, diff = common.control_total_ok(texts, t["value"], tol)
        if ok:
            continue
        if t.get("given"):
            report.error("L-TOTAL", f"{source}: the sum of {t['column']} is {_fmt(total)}; the source total given "
                         f"with --control is {_fmt(t['value'])} (tolerance {_fmt(tol)}); nothing was written",
                         "the export and the total must come from the same refresh: compare the extract with its "
                         "source and land it again",
                         data={"column": t["column"], "sum": float(total), "expected": t["value"], "tolerance": tol,
                               "difference": float(diff), "given": True})
            continue
        report.error("L-TOTAL", f"{source}: the sum of {t['column']} is {_fmt(total)}; {whose} control "
                     f"total is {_fmt(t['value'])} (tolerance {_fmt(tol)}; difference {_fmt(diff)})",
                     "find the missing or extra rows in the source query, or agree the new control total",
                     data={"column": t["column"], "sum": float(total), "expected": t["value"], "tolerance": tol,
                           "difference": float(diff)})
    return typed


def _fmt(x: Any) -> str:
    """1234567 -> '1,234,567'; 0.25 -> '0.25' (no exponent); a Decimal is shown as a float."""
    x = float(x)
    if x.is_integer():
        return f"{x:,.0f}"
    return f"{x:,.6f}".rstrip("0").rstrip(".")


# ----------------------------------------------------------------------------- snapshot

def write_snapshot(path: Path, columns: list[str], types: list[str], rows: list[tuple[Any, ...]]) -> None:
    """raw/<table>.csv: UTF-8 without BOM, comma, header row, normalised values, '\\n' line ends; atomic."""
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([snapshot_text(v, types[i]) for i, v in enumerate(r)])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8", newline="")
    os.replace(tmp, path)


def read_snapshot(model_root: str | Path, table: str) -> RawTable:
    """raw/<table>.csv typed per its contract (agreed or not: build checks agreement through lib.spec)."""
    from . import manifest
    root = Path(os.path.abspath(common.native_path(model_root)))
    contract = _contract(root, table, require_agreed=False)
    path = root / "raw" / f"{table}.csv"
    names = [c["name"] for c in contract["columns"]]
    types = [c["type"] for c in contract["columns"]]
    if not path.is_file():
        raise common.Refusal(f"raw/{table}.csv not found: raw table '{table}' has not been landed",
                             fix=f"land it with rotm.py land \"{root.as_posix()}\" --table {table} --csv <file>")
    header, body = _read_csv(path, "utf-8-sig", ",")
    probe = common.Report("land", path.as_posix())
    known = {k: v for k, v in expect_of(contract).items() if k == "key_repeats"}
    typed = validate_rows({**contract, "expect": known, "refresh": {"allow_empty": True}}, header, body, probe,
                          source=f"raw/{table}.csv")
    if typed is None or probe.counts()["errors"]:
        first = next((f for f in probe.findings if f.severity == "error"), None)
        raise common.Refusal(f"raw/{table}.csv does not fit its contract: {first.message if first else 'header'}",
                             fix=f"land it again with rotm.py land \"{root.as_posix()}\" --table {table} --csv "
                                 "<file> --force")
    digest, _n, _h = manifest.csv_digest(path, types)
    return RawTable(table=table, columns=names, types=types, rows=typed, digest=digest)


# ----------------------------------------------------------------------------- query texts

def _sql_ident(name: str, quote: str | None = None) -> str:
    """A column name in SQL. Without `quote` (GoogleSQL, queries/<t>.sql): bare when it is a plain identifier and
    not a reserved word, else in backticks. With `quote` (the SQL inside a database Power Query, C-sol-6): always
    quoted for that system, so the name and its case reach Power Query as the contract writes them:
    "bigquery" `name`, "mysql" `name` (a backtick doubled), "bracket" [name] (SQL Server), "double" "name"."""
    if quote is None:
        if _PLAIN_IDENT_RE.fullmatch(name) and name.upper() not in GOOGLESQL_RESERVED:
            return name
        quote = "bigquery"
    if quote == "bigquery":
        return "`" + name.replace("`", "\\`") + "`"
    if quote == "mysql":
        return "`" + name.replace("`", "``") + "`"
    if quote == "bracket":
        return "[" + name.replace("]", "]]") + "]"
    if quote == "double":
        return '"' + name.replace('"', '""') + '"'
    raise ValueError(f"unknown quote style {quote!r}")


def googlesql_identifier(name: str) -> str:
    """A name as a GoogleSQL identifier: bare when it matches ^[A-Za-z_][A-Za-z0-9_]*$ and is not one of
    GOOGLESQL_RESERVED, else in backticks (`order`, `Order Date`). contract propose writes a source expression
    taken from a header with it (v4, A-aera-11)."""
    return _sql_ident(name)


_SQL_TYPE_WORDS = {"DATE", "DATETIME", "TIME", "TIMESTAMP", "NUMERIC", "BIGNUMERIC", "JSON", "STRING", "BYTES",
                   "INT64", "FLOAT64", "BOOL", "DECIMAL", "INTEGER", "INT", "VARCHAR", "CHAR", "FLOAT", "REAL",
                   "DOUBLE", "PRECISION", "BOOLEAN", "SAFE", "CURRENT_DATE", "CURRENT_TIMESTAMP"}


def sql_expression_problem(expr: str) -> str | None:
    """Why a contract's source_expression is not one SQL expression for one column, or None: a comma or an AS
    outside brackets, unbalanced brackets or quotes, or two bare words in a row outside brackets (prose or an
    alias). The plugin's own check (live test 1: a description in prose was written into the SELECT list)."""
    text = (expr or "").strip()
    if not text:
        return "it is empty"
    depth = 0
    quote = ""
    words: list[str] = []          # top-level tokens, in order
    token = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    i += 2
                    continue
                quote = ""
                words.append("'lit'")
            i += 1
            continue
        if ch in "'\"`":
            if token:
                words.append(token)
                token = ""
            if ch == "`":
                j = text.find("`", i + 1)
                if j < 0:
                    return "a ` quote is not closed"
                if depth == 0:
                    words.append("ident")
                i = j + 1
                continue
            quote = ch
            i += 1
            continue
        if ch in "([":
            if token:
                words.append(token)
                token = ""
            if depth == 0:
                words.append("(")
            depth += 1
        elif ch in ")]":
            depth -= 1
            if depth < 0:
                return "a closing bracket has no opening bracket"
        elif depth == 0 and ch == ",":
            return "it holds a comma outside brackets, so the SELECT list would get two columns"
        elif depth == 0 and (ch.isalnum() or ch in "_.$"):
            token += ch
        elif depth == 0:
            if token:
                words.append(token)
                token = ""
            if not ch.isspace():
                words.append(ch)
        i += 1
    if quote:
        return f"a {quote} quote is not closed"
    if depth:
        return "a bracket is not closed"
    if token:
        words.append(token)
    keywords = GOOGLESQL_RESERVED | _SQL_TYPE_WORDS
    for k, w in enumerate(words):
        if w.upper() == "AS" and not any(x.upper() == "CAST" for x in words[:k]):
            return "it holds AS outside brackets; the column's name comes from the contract"
        prev = words[k - 1] if k else ""

        def bare(x: str) -> bool:
            return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.$]*", x)) and x.upper() not in keywords
        if k and bare(w) and bare(prev):
            return f"two words in a row ('{prev} {w}') outside brackets: prose, or an alias"
    return None


def _source_sql(expr: str, quote: str | None) -> str:
    """A contract's source expression in the SQL: the contract's text as written. With `quote` (the SQL inside a
    database Power Query), an expression that is exactly one backtick-quoted GoogleSQL identifier (as contract
    propose writes a header that is a reserved keyword or not a plain identifier, v4 A-aera-11) is written with
    that connector's quoting instead."""
    if quote is not None:
        m = _BACKTICK_IDENT_RE.fullmatch(expr.strip())
        if m:
            return _sql_ident(re.sub(r"\\(.)", r"\1", m.group(1)), quote)
    return expr


def sql_text(contract: dict[str, Any], *, quote: str | None = None) -> str:
    """SELECT <source_expression> AS <name>, ... FROM <source.from> WHERE <source.where> ORDER BY <key> (design 7.4).
    A source expression that is not one SQL expression (sql_expression_problem) is written as NULL with a comment
    that says what to replace. Names are quoted as GoogleSQL quotes them, or with `quote` (see _sql_ident) for the
    SQL inside a database Power Query; source expressions, FROM and WHERE are the contract's text as written,
    except that with `quote` one backtick-quoted identifier gets the connector's quoting (_source_sql). A column is
    written as its name alone, with no AS, when the contract gives no source expression, or when the source
    expression is the column's name and that name needs no quotes; else as `<source expression> AS <name>`, so a
    quoted source identifier shows on both sides of AS (`order` AS `order`)."""
    table = contract["table"]
    src = contract.get("source") or {}
    ag = contract.get("agreement") or {}
    lines = [f"-- rule-of-thumb-model: SQL for raw table '{table}' (contracts/{table}.contract.json)."]
    if ag.get("status") == "agreed":
        lines.append(f"-- Contract agreed by {ag.get('by')} on {ag.get('at')}.")
    lines += ["-- Shape only (spec 2.2 P2): select, rename, cast and filter to the contract. Every calculation after",
              "-- landing is a workbook formula. Column names and order are the contract's.",
              "-- Dialect: GoogleSQL (BigQuery, Connected Sheets). For Power Query, adapt it to the source's SQL."]
    if not src.get("from") and not src.get("object"):
        lines.append("-- The contract records no source table: replace <source table> below.")
    lines.append("SELECT")
    cols = contract["columns"]
    for i, c in enumerate(cols):
        given = c.get("source_expression")
        alias = _sql_ident(c["name"], quote)
        sep = "," if i < len(cols) - 1 else ""
        why = sql_expression_problem(given) if given else None
        if why:
            lines.append(f"  -- {c['name']}: replace NULL with the SQL that produces this column. The contract's "
                         f"source expression is not one SQL expression ({why}): "
                         + re.sub(r"\s+", " ", str(given))[:200])
            lines.append(f"  NULL AS {alias}{sep}")
            continue
        if not given or (given == c["name"] and alias == c["name"]):
            lines.append(f"  {alias}{sep}")
        else:
            lines.append(f"  {_source_sql(given, quote)} AS {alias}{sep}")
    frm = src.get("from") or src.get("object") or "<source table>"
    lines.append(f"FROM {frm}")
    if src.get("where"):
        lines.append(f"WHERE {src['where']}")
    lines.append("ORDER BY " + ", ".join(_sql_ident(k, quote) for k in contract["key"]))
    return "\n".join(lines) + "\n"


def _m_string(text: str) -> str:
    """An M text literal: quotes doubled, line breaks as #(lf)."""
    return '"' + text.replace('"', '""').replace("\r\n", "\n").replace("\n", "#(lf)") + '"'


def _m_name(name: str) -> str:
    return _m_string(name)


def _typing_step(prev: str, contract: dict[str, Any]) -> str:
    pairs = ", ".join("{" + _m_name(c["name"]) + ", " + PQ_TYPES[c["type"]] + "}" for c in contract["columns"])
    return f'Table.TransformColumnTypes({prev}, {{{pairs}}}, "en-US")'


def m_text(contract: dict[str, Any], *, csv_path: str | Path | None = None) -> str:
    """Power Query M: Csv.Document for a local file, Value.NativeQuery with EnableFolding for a database (design 7.3).

    With `csv_path` the query reads that CSV (the model's raw/<table>.csv): header promoted, the contract's columns
    selected in order, empty fields made null, then typed with the contract types (culture en-US). A `csv_path`
    that starts with MODEL_FOLDER is written as given (backslashes), for attach_power_query to fill in. Without it
    the query runs the SQL of sql_text() through Value.NativeQuery against the database the contract names, with
    names quoted for that system and the server and database left as placeholders <...> (not tested here: no
    database is reachable)."""
    table = contract["table"]
    names = [c["name"] for c in contract["columns"]]
    name_list = "{" + ", ".join(_m_name(n) for n in names) + "}"
    if csv_path is not None:
        text = str(csv_path)
        if text.startswith(MODEL_FOLDER):
            p = text.replace("/", "\\")
        else:
            p = str(Path(os.path.abspath(common.native_path(csv_path))))
        steps = [
            f"    // rule-of-thumb-model: Power Query for raw table '{table}' (contracts/{table}.contract.json).",
            "    // Reads the landed snapshot; shape only: promote the header, keep the contract's columns, type them.",
            f"    Source = Csv.Document(File.Contents({_m_string(p)}), [Delimiter = \",\", Encoding = 65001, "
            "QuoteStyle = QuoteStyle.Csv]),",
            "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
            f"    Selected = Table.SelectColumns(Promoted, {name_list}),",
            f"    Nulls = Table.ReplaceValue(Selected, \"\", null, Replacer.ReplaceValue, {name_list}),",
            f"    Typed = {_typing_step('Nulls', contract)}",
        ]
        return "let\n" + "\n".join(steps) + "\nin\n    Typed\n"
    src = contract.get("source") or {}
    system = str(src.get("system") or "").lower()
    target, folding, quote = 'Sql.Database("<server>", "<database>")', False, "bracket"
    known = False
    for keys, expr, fold, q in _PQ_CONNECTORS:
        if any(k in system for k in keys):
            target, folding, quote, known = expr, fold, q, True
            break
    sql = "\n".join(line for line in sql_text(contract, quote=quote).splitlines() if not line.startswith("--"))
    options = ", [EnableFolding = true]" if folding else ""
    connector = target.split("(", 1)[0]
    steps = [
        f"    // rule-of-thumb-model: Power Query for raw table '{table}' (contracts/{table}.contract.json).",
        "    // A template: replace every <...> placeholder; rotm.py did not connect to any database.",
        f"    // The SQL below quotes column names for {connector} as {_QUOTE_STYLES[quote]}; queries/{table}.sql "
        "holds the GoogleSQL text. Source expressions, FROM and WHERE are the contract's text as written, except a "
        f"source expression that is one name in backticks, which is quoted as {_QUOTE_STYLES[quote]} too.",
    ]
    if not known:
        steps.append("    // The contract names no known system: replace Sql.Database with your source's connector "
                     "(Get Data > From Database).")
    steps += [
        f"    Target = {target},",
        f"    Result = Value.NativeQuery(Target, {_m_string(sql)}, null{options}),",
        f"    Typed = {_typing_step('Result', contract)}",
    ]
    return "let\n" + "\n".join(steps) + "\nin\n    Typed\n"


_FILE_CONTENTS_RE = re.compile(r'File\.Contents\("((?:[^"]|"")*)"\)')


def _stale_generated_m(path: Path, root: Path, table: str, text: str | None = None) -> bool:
    """True for a queries/<table>.pq.m that rotm.py generated for a local CSV in another folder (a model folder
    copied or moved before query stamps existed): it would read the other folder's snapshot. The placeholder form
    (File.Contents("<model folder>\\raw\\<table>.csv")) is current wherever the folder lies (O-1)."""
    if path.suffix != ".m":
        return False
    if text is None:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            return False
    if "rule-of-thumb-model: Power Query" not in text or "Reads the landed snapshot" not in text:
        return False
    m = _FILE_CONTENTS_RE.search(text)
    if not m:
        return False
    target = m.group(1).replace('""', '"')
    if target.startswith(MODEL_FOLDER):
        return False
    here = root / "raw" / f"{table}.csv"
    return os.path.normcase(os.path.abspath(Path(target))) != os.path.normcase(os.path.abspath(here))


def _hex(value: Any) -> str:
    """A sha256 as 64 lower-case hex digits ('sha256:' prefix dropped)."""
    text = str(value or "").strip().lower()
    return text[7:] if text.startswith("sha256:") else text


def _short(value: Any) -> str:
    return "sha256:" + _hex(value)[:12] + "..."


def _read_query(path: Path) -> str | None:
    """The file's text with LF line ends, or None when it is absent."""
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def _m_kind(text: str | None) -> str:
    """'database' for a Value.NativeQuery template, else 'csv'."""
    return "database" if text and "Value.NativeQuery" in text else "csv"


def _generated_query(contract: dict[str, Any], path: Path, table: str, pq_source: str) -> str:
    """The text rotm.py writes below the stamp line of queries/<table>.sql or .pq.m."""
    if path.suffix == ".sql":
        return sql_text(contract)
    if pq_source == "database":
        return m_text(contract)
    return m_text(contract, csv_path=f"{MODEL_FOLDER}\\raw\\{table}.csv")


@dataclass
class QueryPlan:
    """What land does with one query file: write (absent), keep (stamped for the agreed contract), rewrite
    (stamped for another contract, not edited), replace (--rewrite-queries), stamp (--keep-queries), legacy (an
    unstamped generated M text of another folder), switch (a .pq.m of the other kind than --pq-source asks for,
    not edited since land wrote it: written again for the asked kind, note N-PQ-SWITCH)."""
    path: Path
    action: str
    text: str = ""
    note: str = ""


def plan_queries(model_root: str | Path, table: str, contract: dict[str, Any] | None = None, *,
                 rewrite: bool = False, keep: bool = False, pq_source: str | None = None,
                 suffixes: tuple[str, ...] = (".sql", ".m")) -> list[QueryPlan]:
    """Decide, without writing anything, what land does with queries/<table>.sql and queries/<table>.pq.m
    (A-aera-2, C-sol-2). Raises common.Refusal naming each file that was edited by hand for another contract or has
    no stamp, unless `rewrite` (--rewrite-queries) or `keep` (--keep-queries).

    `pq_source` (--pq-source) that names the other kind (_m_kind) than an existing queries/<table>.pq.m holds
    ([A-aera-12, C-sol-9]): a file with a stamp and not edited since land wrote it is written again for the asked
    kind (note N-PQ-SWITCH); a file edited by hand or without a stamp is refused (F-PQ-SWITCH, exit 2, nothing
    written; also with --keep-queries) with the command that writes it again. So land never ends with exit 0 and a
    query of the other kind kept."""
    root = _root(model_root)
    if contract is None:
        contract = _contract(root, table)
    if rewrite and keep:
        raise common.Refusal("--rewrite-queries and --keep-queries were both given", key="--keep-queries",
                             fix="give one: --rewrite-queries writes the query files again from the agreed contract; "
                                 "--keep-queries stamps files you reconciled by hand with the agreed contract")
    current = _hex((contract.get("agreement") or {}).get("contract_sha256"))
    qdir = root / "queries"
    plans: list[QueryPlan] = []
    conflicts: list[str] = []
    for path in (qdir / f"{table}.sql", qdir / f"{table}.pq.m"):
        if path.suffix not in suffixes:
            continue
        rel = f"queries/{path.name}"
        comment = "--" if path.suffix == ".sql" else "//"
        existing = _read_query(path)
        kind = pq_source or _m_kind(existing)
        fresh = common.stamp_query(_generated_query(contract, path, table, kind), current, comment)
        if existing is None:
            plans.append(QueryPlan(path, "write", fresh, f"wrote {rel}"))
            continue
        stamp = common.read_query_stamp(existing)
        old_kind = _m_kind(existing)
        if path.suffix == ".m" and pq_source and pq_source != old_kind and not rewrite:
            if stamp is not None and not stamp.get("edited"):
                plans.append(QueryPlan(path, "switch", fresh,
                                       f"{rel} was written for a {old_kind} source and not edited since: written "
                                       f"again for --pq-source {pq_source}"))
                continue
            how = "edited by hand" if stamp is not None else "without a stamp"
            raise common.Refusal(f"{rel} holds a {old_kind} query {how}, and --pq-source asks for {pq_source}; "
                                 "nothing was written", key="F-PQ-SWITCH",
                                 fix=f"rotm.py land \"{root.as_posix()}\" --table {table} --rewrite-queries "
                                     f"--pq-source {pq_source} (the old file is kept as {rel}.bak_<time>)")
        if rewrite:
            if existing == fresh:
                plans.append(QueryPlan(path, "keep", note=f"kept {rel} (it equals the text written from the agreed "
                                                          "contract)"))
            else:
                plans.append(QueryPlan(path, "replace", fresh, f"wrote {rel} again from the agreed contract "
                                                               "(--rewrite-queries)"))
            continue
        if stamp is not None and _hex(stamp.get("contract_sha256")) == current:
            plans.append(QueryPlan(path, "keep", note=f"kept {rel} (its stamp names the agreed contract; "
                                                      "--rewrite-queries writes it again)"))
            continue
        if stamp is not None and not stamp.get("edited"):
            plans.append(QueryPlan(path, "rewrite", fresh,
                                   f"{rel} was written for another contract ({_short(stamp.get('contract_sha256'))}) "
                                   f"and not edited since: written again from the agreed contract ({_short(current)})"))
            continue
        if stamp is None and _stale_generated_m(path, root, table, existing):
            plans.append(QueryPlan(path, "legacy", fresh,
                                   f"{rel} was generated for the snapshot of another folder and has no stamp: written "
                                   "again from the agreed contract"))
            continue
        if keep:
            plans.append(QueryPlan(path, "stamp", common.stamp_query(existing, current, comment),   # old stamp dropped
                                   f"stamped {rel}, edited by hand, with the agreed contract ({_short(current)}; "
                                   "--keep-queries)"))
            continue
        if stamp is None:
            conflicts.append(f"{rel} has no rotm-stamp line, so the contract it was written for is not known")
        else:
            conflicts.append(f"{rel} was edited by hand and its stamp names another contract "
                             f"({_short(stamp.get('contract_sha256'))}) than the agreed one ({_short(current)})")
    if conflicts:
        model = root.as_posix()
        files = " and ".join(c.split(" ", 1)[0] for c in conflicts)
        raise common.Refusal("; ".join(conflicts) + "; nothing was written",
                             fix=f"compare {files} with contracts/{table}.contract.md; then run rotm.py land "
                                 f"\"{model}\" --table {table} --rewrite-queries to write the file again from the "
                                 "agreed contract (the old file is kept as <file>.bak_<time>), or, after editing it by "
                                 "hand to fit the agreed contract, the same land command with --keep-queries to stamp "
                                 "it with the agreed contract")
    return plans


def apply_query_plans(plans: list[QueryPlan], model_root: str | Path, contract: dict[str, Any],
                      report: Any = None) -> list[Path]:
    """Carry out plan_queries' decisions. A file that --rewrite-queries replaces, or an unstamped generated text that
    is written again, is first kept as <file>.bak_<time>. Returns the files written."""
    root = Path(model_root)
    written: list[Path] = []
    when = time.strftime("%Y%m%d_%H%M%S")
    for p in plans:
        if p.action == "keep":
            continue
        p.path.parent.mkdir(parents=True, exist_ok=True)
        if p.action in ("replace", "legacy") and p.path.exists():
            bak = p.path.with_name(f"{p.path.name}.bak_{when}")
            shutil.copy2(p.path, bak)
            if report is not None:
                report.note(f"kept the old {common.relpath_posix(p.path, root)} as {common.relpath_posix(bak, root)}")
        tmp = p.path.with_name(p.path.name + ".tmp")
        tmp.write_text(p.text, encoding="utf-8", newline="\n")
        os.replace(tmp, p.path)
        written.append(p.path)
        if report is not None and p.action in ("rewrite", "replace", "stamp", "legacy", "switch"):
            report.note(p.note)
        if report is not None and p.path.suffix == ".sql" and p.action != "stamp":
            table = contract["table"]
            for c in contract["columns"]:
                why = sql_expression_problem(c["source_expression"]) if c.get("source_expression") else None
                if why:
                    report.warning("L-SQL-EXPR", f"queries/{table}.sql: column {c['name']}: the contract's source "
                                   f"expression is not one SQL expression ({why}); the SQL holds NULL for it",
                                   f"write the SQL that produces {c['name']} in queries/{table}.sql (for a row number "
                                   "of a file: ROW_NUMBER() OVER (), or Table.AddIndexColumn in Power Query); put "
                                   "prose in the column's description", data={"column": c["name"]})
    return written


def write_queries(model_root: str | Path, table: str, *, overwrite: bool = False, keep: bool = False,
                  pq_source: str | None = None, report: Any = None) -> list[Path]:
    """queries/<table>.sql and queries/<table>.pq.m from the agreed contract, each with its rotm-stamp line
    (plan_queries decides; `overwrite` is --rewrite-queries, `keep` is --keep-queries). An edit made after the
    current agreement stays (design 7.4). Returns the files written. With `report`, the notes say what was written
    again, and a source expression that is not one SQL expression gives a warning L-SQL-EXPR when the SQL file is
    written (the SQL then holds NULL and a comment for that column). `pq_source` csv or database; None: the kind
    of the existing .pq.m, else csv."""
    root = _root(model_root)
    contract = _contract(root, table)
    plans = plan_queries(root, table, contract, rewrite=overwrite, keep=keep, pq_source=pq_source)
    return apply_query_plans(plans, root, contract, report)


# ----------------------------------------------------------------------------- landing

def _workbook_of(root: Path) -> Path:
    from . import modelfolder
    try:
        marker = modelfolder.read_marker(root)
        return root / marker.get("workbook", "model.xlsx")
    except common.RotmError:
        return root / "model.xlsx"


CONTROL_NEEDS_CSV = "--control needs --csv: a source total belongs to the extract landed with it"


def given_controls(table: str, contract: dict[str, Any], items: Any) -> tuple[dict[str, float], dict[str, str]]:
    """The --control texts of land (v5, A-aera-6 and C-sol-3) as ({column: source total}, {column: the text as
    typed}). The syntax is check's (check.parse_controls: TABLE.COLUMN=VALUE); TABLE must be the table landed, and
    COLUMN a number or integer column of its contract, the rule of check --control. common.Refusal otherwise."""
    from . import check
    values: dict[str, float] = {}
    texts: dict[str, str] = {}
    cols = {c["name"]: c["type"] for c in contract["columns"]}
    for item in items or []:
        ((key, value),) = check.parse_controls([item]).items()
        t, col = key.split(".", 1)
        if t != table:
            raise common.Refusal(f"--control {item}: table '{t}' is not the table landed (--table {table})",
                                 fix=f"give --control {table}.<column>=<source total> for the extract of '{table}'; "
                                     f"land the extract of '{t}' with --table {t} and its own --control")
        if col not in cols:
            from .schemas import nearest
            near = nearest(col, list(cols))
            raise common.Refusal(f"--control {item}: the contract of '{table}' has no column '{col}'"
                                 + (f"; did you mean '{near}'?" if near else ""),
                                 fix="use a number column of the contract")
        if cols[col] not in ("integer", "number"):
            raise common.Refusal(f"--control {item}: column '{col}' has type {cols[col]}; a control total needs a "
                                 "number column", fix="choose an integer or number column")
        values[col] = float(value)
        texts[col] = str(item)
    return values, texts


def land_csv(model_root: str | Path, table: str, csv_path: str | Path, *, force: bool = False,
             encoding: str = "utf-8-sig", delimiter: str = ",", rewrite_queries: bool = False,
             keep_queries: bool = False, pq_source: str | None = None, controls: Any = None) -> Any:
    """Validate a CSV against the agreed contract; write raw/<table>.csv (normalised), queries/<table>.sql and
    queries/<table>.pq.m; update the manifest digest when a workbook exists. Returns a common.Report.
    The query files are checked first (plan_queries): a file edited by hand for another contract, or without a
    stamp, is a refusal before anything is written.
    `controls` (v5, A-aera-6 and C-sol-3): the --control texts (TABLE.COLUMN=VALUE, given_controls). The file's sum
    of each such column is compared with the total given, in place of the contract's or the accepted total; when
    every check passes, the totals the rows were compared with are recorded for the snapshot's digest in
    raw/<table>.controls.json with the texts given (controls.record_acceptance, the record check --accept-refresh
    --control writes), and check and build then use them for that snapshot. A total the sum does not meet is
    L-TOTAL and nothing is written."""
    from . import controls as controls_mod
    from . import manifest, modelfolder
    root = _root(model_root)
    contract = _contract(root, table)
    given, given_texts = given_controls(table, contract, controls)
    if given:
        controls_mod.load(root, table)      # an invalid controls file is a refusal before anything is written
    plans = plan_queries(root, table, contract, rewrite=rewrite_queries, keep=keep_queries, pq_source=pq_source)
    src = Path(os.path.abspath(common.native_path(csv_path)))
    report = common.Report("land", src.as_posix())
    header, body = _read_csv(src, encoding, delimiter)
    used: list[dict[str, Any]] = []
    typed = validate_rows(contract, header, body, report, source=src.name, model_root=root, controls=given,
                          totals_used=used)
    snap = root / "raw" / f"{table}.csv"
    types = [c["type"] for c in contract["columns"]]
    names = [c["name"] for c in contract["columns"]]
    if typed is None or report.counts()["errors"]:
        report.note(f"nothing was written: {src.name} does not fit contract '{table}'")
        report.finish(False)
        return report
    digest = manifest.raw_digest(names, [[snapshot_text(v, types[i]) for i, v in enumerate(r)] for r in typed], types)

    def record_given() -> None:
        if not given:
            return
        controls_mod.record_acceptance(root, table, digest=digest, rows=len(typed), totals=used,
                                       given=list(given_texts.values()))
        for col, value in given.items():
            report.note(f"recorded the source total {col} = {_fmt(value)} for this snapshot in "
                        f"raw/{table}.controls.json (given: '{given_texts[col]}')")
        report.data["controls_recorded"] = [dict(u) for u in used]

    if snap.exists():
        old, _n, _h = manifest.csv_digest(snap, types)
        if old == digest:
            report.note(f"raw/{table}.csv already holds these {len(typed):,} rows (same digest); nothing changed")
            written = apply_query_plans(plans, root, contract, report)
            record_given()
            report.data.update({"table": table, "rows": len(typed), "digest": digest, "changed": False,
                                "written": [common.relpath_posix(p, root) for p in written]})
            report.finish(False)
            return report
        if not force:
            raise common.Refusal(f"raw/{table}.csv exists and holds other rows; nothing was written",
                                 fix="pass --force to replace the snapshot (then rebuild, or refresh the Power Query "
                                     "table, so the workbook shows the new rows)")
    write_snapshot(snap, names, types, typed)
    check_digest, n, _h = manifest.csv_digest(snap, types)
    if check_digest != digest:
        raise common.RotmError(f"raw/{table}.csv was written but its digest differs from the landed rows",
                               fix="run land again; if it repeats, report it")
    written = [snap] + apply_query_plans(plans, root, contract, report)
    wb = _workbook_of(root)
    if manifest.exists(wb):
        try:
            data = manifest.load_manifest(wb)
            if data is not None and manifest.raw_sheet(data, table) is not None:
                manifest.update_raw(wb, table, digest=digest, rows=n, snapshot=f"raw/{table}.csv")
                entry = manifest.raw_sheet(data, table) or {}
                how = ("refresh it with rotm.py check \"" + root.as_posix() + "\" --refresh"
                       if entry.get("landing") == "power_query" else
                       "rebuild it with rotm.py build \"" + root.as_posix() + "\"")
                report.note(f"{wb.name} still shows the rows it was built with; {how}")
                # v6 (r5 verdict C-sol-6): values with more decimals than the column's number format shows
                report.extend(_hidden_decimals(root, table, data, names, typed, contract))
        except common.RotmError as exc:
            report.note(f"the manifest was not updated: {exc.line()}")
    record_given()
    try:
        modelfolder.record_step(root, wb.as_posix(), "land", {"exit_code": 0, "table": table, "rows": n,
                                                              "digest": digest, "source": src.as_posix()})
    except (common.RotmError, OSError, ValueError) as exc:
        report.note(f"status not recorded: {exc}")
    report.data.update({"table": table, "rows": n, "digest": digest, "changed": True,
                        "written": [common.relpath_posix(p, root) for p in written]})
    report.note(f"landed {n:,} rows of '{table}' into raw/{table}.csv ({digest[:19]}...)")
    report.finish(False)
    return report


def _hidden_decimals(root: Path, table: str, man: dict[str, Any], names: list[str], rows: list[Any],
                     contract: dict[str, Any]) -> list[common.Finding]:
    """I-HIDDEN-DECIMALS (v6, r5 verdict C-sol-6) for rows landed into a model whose manifest `man` has a raw entry
    for `table`: check.hidden_decimal_findings with the model's style dictionary (as build used it). [] when the
    dictionary cannot be read."""
    from . import check
    from . import manifest
    entry = manifest.raw_sheet(man, table) or {}
    try:
        dictionary = check._model_dictionary(root, man)
    except (common.RotmError, OSError, ValueError):
        return []
    return check.hidden_decimal_findings(root, table, entry, names, rows, contract, dictionary)


CONNECTED_SHEETS_STEPS = (
    "Connected Sheets (Google Sheets; not tested here, no Google access): Data > Data connectors > Connect to "
    "BigQuery > choose the project > Saved queries and query editor; paste queries/{t}.sql; Insert results; "
    "create an extract of every column on sheet raw_{t} at A1, with nothing else on that sheet; refresh with "
    "More > Refresh options (references/google-sheets.md).")


def _fill_model_folder(m: str, root: Path) -> str:
    """The M text with MODEL_FOLDER replaced by the model folder's path, written as M text-literal content (a
    double quote doubled, '#(' escaped), so File.Contents reads this folder's snapshot (O-1)."""
    path = str(root).replace('"', '""').replace("#(", "#(#)(")
    return m.replace(MODEL_FOLDER, path)


def query_body_sha256(text: str) -> str:
    """The hash of the body of a query file, as common.stamp_query hashes it: the text with LF line ends and
    without its rotm-stamp line, UTF-8 (64 lower-case hexadecimal digits). attach_power_query records it for the
    text it attached as the raw entry's query_body_sha256 (round 4 verdict, A-aera-2, C-sol-1 and C-sol-4); check
    --refresh compares it with queries/<t>.pq.m."""
    body = common._query_lines(text)
    first, _sep, rest = body.partition("\n")
    if common.read_query_stamp(text) is not None:
        body = rest
    return common._body_sha(body)


def query_formula_sha256(text: str) -> str:
    """The hash of a query formula as Excel holds it (v6, r5 verdict A-aera-2): line ends \\r\\n and \\r made \\n,
    spaces and tabs at the end of each line removed, empty lines at the end removed; sha256 of the UTF-8 bytes (64
    lower-case hexadecimal digits). The one function for the attach (attach_power_query, build's carry-over of a
    query attached by hand) and the refresh (check --refresh)."""
    import hashlib
    lines = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [ln.rstrip(" \t") for ln in lines]
    while lines and lines[-1] == "":
        lines.pop()
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


FORMULA_NOT_READ_NOTE = ("the formula of {q} could not be read back from the workbook; check --refresh will not "
                         "compare it")


def read_query_formula(workbook: str | Path, query_name: str, *, job: Any = None) -> str | None:
    """The formula (M text) of the query `query_name` as Excel gives it back from the saved `workbook`
    (lib.excel.inventory, opened read-only), or None when Excel cannot give it (v6, r5 verdict A-aera-2). `job`: the
    result of the attach job; when given, the formula is read only after a job that names the Excel process it ran
    in (a job result without one did not come from Excel)."""
    from . import excel
    if job is not None and not getattr(job, "pid", None):
        return None
    try:
        inv = excel.inventory(workbook)
    except (common.RotmError, OSError, ValueError):
        return None
    for q in inv.get("query_formulas") or []:
        if isinstance(q, dict) and q.get("name") == query_name:
            f = q.get("formula")
            return f if isinstance(f, str) and f.strip() else None
    return None


def attached_query_text(model_root: str | Path, table: str, contract: dict[str, Any] | None = None,
                        report: Any = None) -> str:
    """The M text attach_power_query sends to Excel: queries/<table>.pq.m with the model folder filled in. Writes
    an absent query file first. Refuses (common.Refusal) a file without a stamp or stamped for another contract
    than the agreed one, and a file that still holds placeholders such as <server>; Excel is not started."""
    root = _root(model_root)
    if contract is None:
        contract = _contract(root, table)
    current = _hex((contract.get("agreement") or {}).get("contract_sha256"))
    qdir = root / "queries"
    absent = [QueryPlan(p, "write", common.stamp_query(_generated_query(contract, p, table, "csv"), current,
                                                       "--" if p.suffix == ".sql" else "//"))
              for p in (qdir / f"{table}.sql", qdir / f"{table}.pq.m") if not p.is_file()]
    for p in apply_query_plans(absent, root, contract, report):
        if report is not None:
            report.note(f"wrote {common.relpath_posix(p, root)}")
    m = _read_query(qdir / f"{table}.pq.m") or ""
    stamp = common.read_query_stamp(m)
    if stamp is None or _hex(stamp.get("contract_sha256")) != current:
        why = ("has no rotm-stamp line, so the contract it was written for is not known" if stamp is None else
               f"was written for another contract ({_short(stamp.get('contract_sha256'))}) than the agreed one "
               f"({_short(current)})")
        model = root.as_posix()
        raise common.Refusal(f"queries/{table}.pq.m {why}; the query was not attached",
                             fix=f"run rotm.py land \"{model}\" --table {table} --power-query: land writes the query "
                                 "again from the agreed contract when the file was not edited by hand (and names the "
                                 "file and the next step when it was), then attaches it")
    m = _fill_model_folder(m, root)
    if "<" in m and ">" in m and re.search(r"<[a-z][a-z ]*>", m):
        raise common.Refusal(f"queries/{table}.pq.m still holds placeholders such as <server>",
                             fix="fill them in, or write the local-file query with rotm.py land "
                                 f"\"{root.as_posix()}\" --table {table} --rewrite-queries --pq-source csv")
    return m


def attach_power_query(workbook: str | Path, model_root: str | Path, table: str) -> Any:
    """excel.pq_attach with queries/<table>.pq.m (attached_query_text: stamped for the agreed contract, the model
    folder filled in); manifest landing = power_query, query_contract_sha256 = the agreed contract's hash,
    query_body_sha256 = query_body_sha256 of the file text that was attached (v5; land --keep-queries and land --csv
    do not change it), query_formula_sha256 = query_formula_sha256 of the formula Excel gives back for q_<table>
    after the attach (v6, r5 verdict A-aera-2; none and a note when it cannot be read), and workbook_digest = the
    loaded rows' sheet digest when it equals the raw entry's digest (plan 5.1). The status
    record of step land holds sha256 and output_sha256 (the workbook after the Excel job) and input_sha256 (before
    it), so build can follow the write history (plan 5.2). Returns a common.Report."""
    from . import excel, manifest, modelfolder
    root = _root(model_root)
    contract = _contract(root, table)
    wb = Path(os.path.abspath(common.native_path(workbook)))
    report = common.Report("land", wb.as_posix())
    if not wb.is_file():
        raise common.Refusal(f"{wb.name} not found: build the workbook before attaching a query",
                             fix=f"run rotm.py build \"{root.as_posix()}\" first")
    data = manifest.load_manifest(wb)
    entry = manifest.raw_sheet(data, table) if data else None
    if entry is None:
        raise common.Refusal(f"{wb.name} has no raw sheet for table '{table}' in its manifest",
                             fix=f"add {{\"name\": \"raw_{table}\", \"type\": \"raw\", \"table\": \"{table}\"}} to "
                                 "the spec and rebuild")
    m = attached_query_text(root, table, contract, report)
    # the body hash of the file text the attach sends (before the model folder is filled in): check --refresh
    # compares queries/<t>.pq.m with it (round 4 verdict, A-aera-2, C-sol-1 and C-sol-4)
    body_sha = query_body_sha256(_read_query(root / "queries" / f"{table}.pq.m") or "")
    status = excel.excel_status()
    if not status.available:
        raise common.EnvUnavailable(f"Excel is not available ({status.reason}); the Power Query table was not "
                                    f"attached and raw_{table} keeps its static values",
                                    fix="run this on a PC with Excel")
    input_sha = common.file_sha256(wb)          # the workbook before the Excel job (A-aera-2: the write history)
    res = excel.pq_attach(wb, sheet=f"raw_{table}", table_name=f"tbl_raw_{table}", query_name=f"q_{table}",
                          m_text=m)
    if not res.ok:
        raise excel.ExcelJobFailed(f"the query q_{table} was not attached: {res.message()}",
                                   fix=f"open queries/{table}.pq.m, correct it, and attach again")
    step = res.step("pq_attach")
    # v6 (r5 verdict A-aera-2): the formula Excel holds for the attached query, read back from the saved workbook;
    # check --refresh reads it again before a refresh and compares the hashes (X-QUERY-CHANGED)
    formula = read_query_formula(wb, f"q_{table}", job=res)
    types = entry.get("types") or [c["type"] for c in contract["columns"]]
    sheet_digest, rows, header = manifest.sheet_digest(wb, f"raw_{table}", types)
    data = manifest.load_manifest(wb) or {}
    e = manifest.raw_sheet(data, table)
    e["landing"] = "power_query"
    e["query_name"] = f"q_{table}"
    e["query_contract_sha256"] = _hex((contract.get("agreement") or {}).get("contract_sha256"))
    e["query_body_sha256"] = body_sha
    if formula is not None:
        e["query_formula_sha256"] = query_formula_sha256(formula)
    else:
        e.pop("query_formula_sha256", None)          # a hash of an earlier attach would not describe this query
        report.note(FORMULA_NOT_READ_NOTE.format(q=f"q_{table}"))
    if sheet_digest == e.get("digest"):
        # [A-aera-1, A-aera-4, C-sol-1]: the rows the workbook holds as this command wrote them; recorded only when
        # they are the landed snapshot's rows, so a later acceptance still tells a refresh from a later landing
        e["workbook_digest"] = sheet_digest
    data["updated_at"] = common.now_iso()
    manifest.write_manifest(wb, data)
    if sheet_digest != e.get("digest"):
        report.warning("W-PQ-DIGEST", f"the attached query loaded {rows:,} rows into raw_{table} that differ from "
                       f"the manifest's raw digest (raw/{table}.csv)",
                       f"run rotm.py check \"{root.as_posix()}\" --accept-refresh to validate the new rows against "
                       "the contract and record them", sheet=f"raw_{table}")
    output_sha = common.file_sha256(wb)
    try:
        modelfolder.record_step(root, wb.as_posix(), "land", {"exit_code": 0, "table": table, "rows": rows,
                                                              "power_query": True, "sha256": output_sha,
                                                              "input_sha256": input_sha, "output_sha256": output_sha})
    except (common.RotmError, OSError, ValueError) as exc:
        report.note(f"status not recorded: {exc}")
    report.data.update({"table": table, "query": f"q_{table}", "rows": rows, "step": {k: step.get(k) for k in
                                                                                       ("query", "table", "range")},
                        "pid": res.pid})
    report.note(f"attached Power Query q_{table} to raw_{table} ({rows:,} rows); BackgroundQuery off; Excel PID "
                f"{res.pid} closed")
    report.finish(False)
    return report


def add_land_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("dir", help="the model folder")
    p.add_argument("--table", required=True, help="raw table name (its contract must be agreed)")
    p.add_argument("--csv", help="the CSV to land (without it: write the SQL and Power Query texts only)")
    p.add_argument("--power-query", action="store_true",
                   help="attach a Power Query table to the built workbook's raw sheet through Excel")
    p.add_argument("--force", action="store_true", help="replace an existing snapshot that holds other rows")
    p.add_argument("--control", action="append", default=[], metavar="TABLE.COLUMN=VALUE",
                   help="with --csv: the source's control total for the extract landed (the syntax of check "
                        "--control; TABLE is the table landed; may be given once per column). The file's sum is "
                        "compared with it in place of the contract's total, and when the file fits, the total is "
                        "recorded for the new snapshot in raw/<table>.controls.json, which check and build then use")
    p.add_argument("--encoding", default="utf-8-sig", help="text encoding of the CSV (default utf-8-sig; cp949 for "
                   "a file saved by Korean Excel)")
    p.add_argument("--delimiter", default=",", help="field delimiter of the CSV (default ',')")
    p.add_argument("--rewrite-queries", action="store_true",
                   help="write queries/<table>.sql and .pq.m again from the agreed contract even when they exist "
                        "(a replaced file is kept as <file>.bak_<time>)")
    p.add_argument("--keep-queries", action="store_true",
                   help="keep query files edited by hand that were refused because their stamp names another "
                        "contract (or they have none): after you reconciled them with the agreed contract, this "
                        "stamps them with it")
    p.add_argument("--pq-source", choices=("csv", "database"), default=None,
                   help="queries/<table>.pq.m reads raw/<table>.csv (csv, the default for a new file) or is a "
                        "database template with placeholders (database). Without it an existing file keeps its "
                        "kind. Naming the other kind writes a file not edited since land wrote it again for that "
                        "kind; a file edited by hand or without a stamp is refused (exit 2): then add "
                        "--rewrite-queries")
    p.add_argument("--strict", action="store_true", help="exit 1 on any warning")
    p.add_argument("--json", metavar="FILE", help="write the report as JSON ('-' = stdout)")


def run_land(args: argparse.Namespace) -> int:
    root = _root(args.dir)
    table = args.table
    delimiter = "\t" if args.delimiter in ("\\t", "tab") else args.delimiter
    keep = getattr(args, "keep_queries", False)
    given = list(getattr(args, "control", None) or [])
    if given and not args.csv:
        raise common.Refusal(CONTROL_NEEDS_CSV, key="--control",
                             fix=f"land the export with rotm.py land \"{root.as_posix()}\" --table {table} --csv "
                                 f"<export> --force --control {table}.<column>=<source total>")
    if not args.csv:
        # every land call checks the query files first (A-aera-2): a refusal here writes nothing
        report = common.Report("land", root.as_posix())
        contract = _contract(root, table)
        plans = plan_queries(root, table, contract, rewrite=args.rewrite_queries, keep=keep,
                             pq_source=args.pq_source)
        written = apply_query_plans(plans, root, contract, report)
        kept = [p for p in plans if p.action == "keep"]
        report.data.update({"table": table, "written": [common.relpath_posix(p, root) for p in written],
                            "kept": [common.relpath_posix(p.path, root) for p in kept]})
        for p in plans:
            if p.action == "write":
                report.note(p.note)
        for p in kept:
            report.note(p.note)
        if not args.power_query:
            report.note(CONNECTED_SHEETS_STEPS.format(t=table))
            report.finish(args.strict)
            return report.emit(args.json)
    else:
        report = land_csv(root, table, args.csv, force=args.force, encoding=args.encoding, delimiter=delimiter,
                          rewrite_queries=args.rewrite_queries, keep_queries=keep, pq_source=args.pq_source,
                          controls=given)
        if report.counts()["errors"]:
            report.finish(args.strict)
            return report.emit(args.json)
    if args.power_query:
        sub = attach_power_query(_workbook_of(root), root, table)
        report.extend(sub.findings)
        report.notes.extend(sub.notes)
        report.data.update({"power_query": sub.data})
        report.verified["recalculated"] = False
    report.finish(args.strict)
    return report.emit(args.json)
