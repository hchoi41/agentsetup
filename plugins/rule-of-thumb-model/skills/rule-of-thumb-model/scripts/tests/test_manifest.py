"""Tests of lib.manifest: the sidecar file, value normalisation, the raw digest (same for a CSV snapshot and a
workbook raw sheet), cell-type ranges and raw updates (design sections 6.8, 7, D11). Owner: CORE-IO."""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import pytest

from lib import common, manifest, modelfolder

TYPES = ["text", "integer", "date", "datetime", "number", "boolean"]
HEADER = ["invoice_id", "line_no", "inv_date", "posted_at", "amount", "paid"]
CSV_ROWS = [
    ["00123", "1", "2026-01-31", "2026-01-31 10:00:00", "1234.50", "TRUE"],
    ["00124", "2", "2026-02-28", "2026-02-28T09:30:15", "-0", "false"],
    ["00125", "3", "2026-03-31", "", "", ""],
]
TYPED_ROWS = [
    ["00123", 1, dt.datetime(2026, 1, 31), dt.datetime(2026, 1, 31, 10, 0, 0), 1234.5, True],
    ["00124", 2.0, dt.date(2026, 2, 28), dt.datetime(2026, 2, 28, 9, 30, 14, 999_900), 0, False],
    ["00125", 3, dt.datetime(2026, 3, 31), None, None, None],
]


def sample_manifest() -> dict:
    return {"rotm_manifest": 1, "workbook": "model.xlsx", "plugin_version": "0.1.0",
            "sheets": [
                {"name": "raw_sales", "type": "raw", "font_size": 8, "table": "sales", "excel_table": "tbl_raw_sales",
                 "landing": "static", "columns": HEADER, "types": TYPES, "rows": 3, "digest": None,
                 "snapshot": "raw/sales.csv"},
                {"name": "h_sales", "type": "helpers", "font_size": 8, "table": "sales", "first_row": 2, "last_row": 4,
                 "capacity": 3, "columns": [{"id": "row", "col": "A", "kind": "row", "formula": "=ROW()"}]},
                {"name": "inputs", "type": "inputs", "font_size": 9}],
            "cell_types": {"inputs": [["B6:B7", "row_header"], ["D6:D7", "input"], ["D7", "calc"]]}}


# ----------------------------------------------------------------------------- normalisation

@pytest.mark.parametrize("value,col_type,expected", [
    (None, "number", ""), ("", "integer", ""),
    ("00123", "text", "00123"), (123.0, "text", "123"), (1.5, "text", "1.5"), (True, "text", "TRUE"),
    (12, "integer", "12"), (12.0, "integer", "12"), ("12", "integer", "12"), (" 7 ", "integer", "7"),
    (True, "integer", "1"), ("x", "integer", "x"), (2.5, "integer", "2.5"),
    (1234.5, "number", "1234.5"), ("1234.50", "number", "1234.5"), (-0.0, "number", "0"), ("-0", "number", "0"),
    (0.1 + 0.2, "number", "0.3"), (1e20, "number", "1e+20"), (7, "number", "7"), ("abc", "number", "abc"),
    (dt.datetime(2026, 1, 31, 15, 0), "date", "2026-01-31"), (dt.date(2026, 1, 31), "date", "2026-01-31"),
    ("2026-01-31", "date", "2026-01-31"), ("2026-01-31 00:00:00", "date", "2026-01-31"),
    (46053.0, "date", "2026-01-31"), ("31/01/2026", "date", "31/01/2026"),
    (dt.datetime(2026, 1, 31, 10, 0, 0, 600_000), "datetime", "2026-01-31T10:00:00"),   # cut, not rounded (A-aera-6)
    (dt.datetime(2026, 1, 31, 10, 0, 0, 999_600), "datetime", "2026-01-31T10:00:01"),   # a serial's float error
    ("2026-01-31 10:00:00", "datetime", "2026-01-31T10:00:00"), (dt.date(2026, 1, 31), "datetime", "2026-01-31T00:00:00"),
    (46053.5, "datetime", "2026-01-31T12:00:00"),
    (True, "boolean", "TRUE"), ("false", "boolean", "FALSE"), (1, "boolean", "TRUE"), ("0", "boolean", "FALSE"),
    ("maybe", "boolean", "maybe"),
])
def test_normalise_value(value: object, col_type: str, expected: str) -> None:
    assert manifest.normalise_value(value, col_type) == expected


def test_excel_serial_of_2026_01_31() -> None:
    assert (dt.datetime(2026, 1, 31) - manifest.EXCEL_EPOCH).days == 46053


# ----------------------------------------------------------------------------- digest

def test_csv_text_and_typed_values_digest_the_same() -> None:
    a = manifest.raw_digest(HEADER, CSV_ROWS, TYPES)
    b = manifest.raw_digest(HEADER, TYPED_ROWS, TYPES)
    assert a == b and a.startswith("sha256:") and len(a) == 71


def test_digest_ignores_trailing_empty_rows_but_not_inner_changes() -> None:
    base = manifest.raw_digest(HEADER, CSV_ROWS, TYPES)
    assert manifest.raw_digest(HEADER, CSV_ROWS + [[""] * 6, [None] * 6, []], TYPES) == base
    changed = [list(r) for r in CSV_ROWS]
    changed[1][4] = "1"
    assert manifest.raw_digest(HEADER, changed, TYPES) != base
    assert manifest.raw_digest(HEADER, CSV_ROWS[::-1], TYPES) != base
    assert manifest.raw_digest(["id"] + HEADER[1:], CSV_ROWS, TYPES) != base
    with_gap = CSV_ROWS[:1] + [[""] * 6] + CSV_ROWS[1:]
    assert manifest.raw_digest(HEADER, with_gap, TYPES) != base
    lost_zero = [list(r) for r in CSV_ROWS]
    lost_zero[0][0] = 123
    assert manifest.raw_digest(HEADER, lost_zero, TYPES) != base          # leading zeros lost = a change


def test_digest_pads_short_rows_and_keeps_extra_cells() -> None:
    assert manifest.raw_digest(["a", "b"], [["1"]], ["text", "text"]) == \
        manifest.raw_digest(["a", "b"], [["1", ""]], ["text", "text"])
    assert manifest.raw_digest(["a"], [["1", "x"]], ["text"]) != manifest.raw_digest(["a"], [["1"]], ["text"])


def write_csv(path: Path, header: list, rows: list) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def test_csv_digest_and_sheet_digest_agree(tmp_path: Path) -> None:
    import openpyxl
    p = write_csv(tmp_path / "sales.csv", HEADER, CSV_ROWS)
    cd, crows, cheader = manifest.csv_digest(p, TYPES)
    assert crows == 3 and cheader == HEADER
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "raw_sales"
    ws.append(HEADER)
    for r in TYPED_ROWS:
        ws.append(r)
    ws.cell(row=10, column=1).number_format = "0"            # a formatted empty cell below the table
    xlsx = tmp_path / "model.xlsx"
    wb.save(xlsx)
    sd, srows, sheader = manifest.sheet_digest(xlsx, "raw_sales", TYPES)
    assert (sd, srows, sheader) == (cd, 3, HEADER)
    with pytest.raises(common.Refusal, match="no sheet 'raw_x'"):
        manifest.sheet_digest(xlsx, "raw_x", TYPES)


def test_sheet_digest_sees_a_formula_typed_over_a_value(tmp_path: Path) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "raw_t"
    ws.append(["id", "amount"])
    ws.append(["a", 5])
    p1 = tmp_path / "a.xlsx"
    wb.save(p1)
    ws["B2"] = "=2+3"
    p2 = tmp_path / "b.xlsx"
    wb.save(p2)
    assert manifest.sheet_digest(p1, "raw_t", ["text", "number"])[0] != manifest.sheet_digest(p2, "raw_t", ["text", "number"])[0]


def test_csv_with_bom_reads_the_same(tmp_path: Path) -> None:
    plain = write_csv(tmp_path / "a.csv", ["id"], [["x"]])
    bom = tmp_path / "b.csv"
    bom.write_bytes(b"\xef\xbb\xbf" + plain.read_bytes())
    assert manifest.csv_digest(plain, ["text"])[0] == manifest.csv_digest(bom, ["text"])[0]


# ----------------------------------------------------------------------------- the sidecar file

def test_manifest_path_and_round_trip(tmp_path: Path) -> None:
    wb = tmp_path / "model.xlsx"
    assert manifest.manifest_path(wb) == tmp_path / "model.manifest.json"
    assert manifest.load_manifest(wb) is None and not manifest.exists(wb)
    written = manifest.write_manifest(wb, sample_manifest())
    assert written == tmp_path / "model.manifest.json"
    assert manifest.load_manifest(wb) == sample_manifest() and manifest.exists(wb)


def test_manifest_invalid_is_refused(tmp_path: Path) -> None:
    wb = tmp_path / "model.xlsx"
    bad = sample_manifest()
    bad["sheets"][0]["digest"] = "md5:x"
    with pytest.raises(common.Refusal, match="model.manifest.json is not valid"):
        manifest.write_manifest(wb, bad)
    assert not manifest.manifest_path(wb).exists()
    manifest.manifest_path(wb).write_text("{", encoding="utf-8")
    with pytest.raises(common.Refusal, match="not valid JSON"):
        manifest.load_manifest(wb)


def test_update_raw(tmp_path: Path) -> None:
    wb = tmp_path / "model.xlsx"
    with pytest.raises(common.Refusal, match="has no manifest"):
        manifest.update_raw(wb, "sales", digest="sha256:" + "a" * 64, rows=5)
    manifest.write_manifest(wb, sample_manifest())
    manifest.update_raw(wb, "sales", digest="sha256:" + "a" * 64, rows=5, snapshot="raw/sales.csv")
    entry = manifest.raw_sheet(manifest.load_manifest(wb), "sales")
    assert entry["digest"] == "sha256:" + "a" * 64 and entry["rows"] == 5
    assert manifest.load_manifest(wb)["updated_at"]
    with pytest.raises(common.Refusal, match="no raw sheet for table 'orders'"):
        manifest.update_raw(wb, "orders", digest="sha256:" + "a" * 64, rows=1)


# ----------------------------------------------------------------------------- lookups

def test_cell_types_expand_and_later_ranges_win() -> None:
    m = sample_manifest()
    types = manifest.cell_types(m, "inputs")
    assert types[("inputs", "B6")] == "row_header" and types[("inputs", "D6")] == "input"
    assert types[("inputs", "D7")] == "calc"
    ranges = manifest.cell_type_ranges(m, "inputs")
    assert manifest.cell_type_at(ranges, 7, 4) == "calc" and manifest.cell_type_at(ranges, 6, 4) == "input"
    assert manifest.cell_type_at(ranges, 99, 99) is None
    assert manifest.cell_types(m) == types


def test_sheet_lookups() -> None:
    m = sample_manifest()
    assert manifest.raw_sheet(m, "sales")["name"] == "raw_sales"
    assert manifest.helper_sheet(m, "sales")["name"] == "h_sales"
    assert manifest.raw_sheet(m, "x") is None and manifest.helper_sheet(m, "x") is None
    assert manifest.sheet_entry(m, "inputs")["type"] == "inputs"
    assert manifest.raw_tables(m) == ["sales"]
    assert manifest.raw_types(m, "sales") == TYPES


def test_raw_types_from_the_contract(tmp_path: Path) -> None:
    root = tmp_path / "m"
    modelfolder.init_model(root)
    common.write_json(modelfolder.contract_path(root, "sales"), {
        "rotm_contract": 1, "table": "sales", "grain": "line", "key": ["id"],
        "columns": [{"name": "id", "type": "text", "null_rule": "never"},
                    {"name": "amount", "type": "number", "null_rule": "allowed"}],
        "expect": {"rows": {"exact": 2}}})
    m = sample_manifest()
    del m["sheets"][0]["types"]
    assert manifest.raw_types(m, "sales", root) == ["text", "number"]
    assert manifest.raw_types(m, "sales") is None
