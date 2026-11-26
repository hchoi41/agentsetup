"""Tests of the v8 change of package P5 to land (round 7 verdict, [A-aera-8, C-sol-2]).

- `land --csv` reads the file with csv.reader(strict=True) (land.csv_records). Only a physically blank line is
  skipped. A record of only delimiters (',,': every field empty) is validated like any other row, with its physical
  line: the first column is never null (lib.schemas), so L-NULL refuses it, names the line, says that the line holds
  only delimiters and gives the fix to remove such records from the extract; nothing is written.
- A quoted field still open at the end of the file is refused (exit 2, nothing written) with the line where the
  field starts; another quoting error is refused with its line.
- The line a finding names is the physical line of the file: a line break inside quotes and a blank line count.
- The fixture of test_csv_in_excel_form_lands is written in the form Excel saves "CSV UTF-8 (Comma delimited)":
  a byte order mark, CRLF at the end of each record, a field with a comma in quotes, a doubled quote, a line break
  (LF) inside quotes. It is written by the test, not saved by Excel: lib/excel.py saves only .xlsx, .xlsm, .xlsb and
  .xls files.
- The six shipped model folders land their data/<t>.csv into raw/<t>.csv byte for byte as before.

Model folders lie outside the plugin (fixture outside_tmp); the commands run with ROTM_NO_EXCEL=1.
"""
from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lib import common, land, modelfolder

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
EXAMPLE = SKILL / "examples" / "sales_vs_target"
BY = "Owner"
REPLY = "The ledger contract is agreed"
ONLY = "holds only delimiters: every field is empty"
OPEN_QUOTE = "a quoted field starts here and its closing quote is missing at the end of the file; nothing was written"


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    e = dict(os.environ if env is None else env)
    e["ROTM_NO_EXCEL"] = "1"
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", env=e, timeout=300)


def raw_bytes(root: Path) -> dict[str, bytes]:
    raw = root / "raw"
    return {p.name: p.read_bytes() for p in sorted(raw.iterdir())} if raw.is_dir() else {}


def errors(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("ERR")]


def example_copy(base: Path) -> tuple[Path, dict[str, str]]:
    """A copy of the worked example and the environment that lets its 'example' agreement count there."""
    dest = base / "sales_vs_target"
    shutil.copytree(EXAMPLE, dest, ignore=shutil.ignore_patterns("__pycache__", "reports"))
    return dest, dict(os.environ, ROTM_EXAMPLE_ROOTS=str(base), PYTHONUTF8="1")


def target_lines() -> list[str]:
    """The lines of the worked example's target extract (header first), without their line ends."""
    return (EXAMPLE / "data" / "target.csv").read_text(encoding="utf-8").splitlines()


def write_text(path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(encoding))
    return path


def agreed(base: Path, sample_text: str, *, extra: tuple[str, ...] = ()) -> Path:
    """A model folder with the contract of 'ledger' proposed from `sample_text` and agreed on the task-file
    channel."""
    root = base / "m"
    sample = write_text(base / "sample" / "ledger.csv", sample_text)
    r = run_cli("contract", "propose", str(root), "--table", "ledger", "--csv", str(sample), "--key", "id",
                "--unit", "amount=KRW", *extra)
    assert r.returncode == 0, r.stdout + r.stderr
    task = write_text(base / "task.md", REPLY + ".\n")
    r = run_cli("contract", "agree", str(root), "--table", "ledger", "--reply", REPLY, "--by", BY,
                "--channel", "task-file", "--source-file", str(task))
    assert r.returncode == 0, r.stdout + r.stderr
    return root


# ============================================================================= records of only delimiters

WHERE = {"at_the_end": None, "in_the_middle": 4}


@pytest.mark.parametrize("where", list(WHERE.values()), ids=list(WHERE))
def test_record_of_only_delimiters_is_refused_with_its_line(outside_tmp: Path, where: int | None) -> None:
    root, env = example_copy(outside_tmp)
    before = raw_bytes(root)
    lines = target_lines()
    at = len(lines) if where is None else where          # index in the list = physical line - 1
    lines.insert(at, ",,")
    extract = write_text(outside_tmp / "export" / "target.csv", "\n".join(lines) + "\n")
    line = at + 1
    for flags in ((), ("--force",), ("--force", "--control", "target.target_amount=268000000")):
        r = run_cli("land", str(root), "--table", "target", "--csv", str(extract), *flags, env=env)
        out = r.stdout + r.stderr
        assert r.returncode != 0, (flags, out)
        nulls = [e for e in errors(out) if "L-NULL" in e]
        assert nulls and all(f"(line {line} {ONLY})" in e for e in nulls), (flags, out)
        assert all(f"Fix: remove the records whose fields are all empty from the extract (line {line})" in e
                   for e in nulls), out
        assert "--known-nulls" not in " ".join(nulls)            # no contract can accept such a record
        assert f"nothing was written: target.csv does not fit contract 'target'" in out
        assert raw_bytes(root) == before, flags


def test_record_of_only_delimiters_of_another_width_is_named_by_l_fields(outside_tmp: Path) -> None:
    root, env = example_copy(outside_tmp)
    before = raw_bytes(root)
    lines = target_lines()
    lines.insert(3, ",,,,")
    extract = write_text(outside_tmp / "export" / "target.csv", "\n".join(lines) + "\n")
    r = run_cli("land", str(root), "--table", "target", "--csv", str(extract), "--force", env=env)
    errs = errors(r.stdout)
    assert r.returncode == 1 and len(errs) == 1 and "L-FIELDS" in errs[0], r.stdout + r.stderr
    assert f"(lines 4) (line 4 {ONLY})" in errs[0], errs[0]
    assert errs[0].endswith("Fix: remove the records whose fields are all empty from the extract (line 4)"), errs[0]
    assert raw_bytes(root) == before


def test_no_contract_can_accept_a_record_of_only_delimiters(outside_tmp: Path) -> None:
    """Every column but the first allows nulls, the key is the first column: land still refuses the record (L-NULL
    on the first column, which the schema keeps never null and without known_nulls), so no landed snapshot holds
    one, and check.read_snapshot_rows and manifest.csv_digest, which leave empty rows out, read every landed row."""
    text = "id,note,amount\nA1,x,1\nA2,,\nA3,y,3\n"
    root = agreed(outside_tmp, text, extra=("--null", "note=allowed", "--null", "amount=allowed"))
    data = common.read_json(modelfolder.contract_path(root, "ledger"))
    assert [c["null_rule"] for c in data["columns"]] == ["never", "allowed", "allowed"]
    extract = write_text(outside_tmp / "export" / "ledger.csv", text + ",,\n")
    r = run_cli("land", str(root), "--table", "ledger", "--csv", str(extract))
    errs = errors(r.stdout)
    assert r.returncode == 1 and len(errs) == 1, r.stdout + r.stderr
    assert "L-NULL" in errs[0] and "column id" in errs[0] and f"(line 5 {ONLY})" in errs[0], errs[0]
    assert not (root / "raw").is_dir() or not (root / "raw" / "ledger.csv").exists()
    # the first column cannot get known empty cells: the schema refuses it (exit 2)
    r = run_cli("contract", "propose", str(root), "--table", "ledger", "--csv", str(extract), "--key", "id",
                "--known-nulls", "id=1", "--force")
    assert r.returncode == 2, r.stdout + r.stderr
    # a key column that is not the first cannot either
    r = run_cli("contract", "propose", str(root), "--table", "ledger", "--csv", str(extract), "--key", "id,note",
                "--known-nulls", "note=1", "--force")
    assert r.returncode == 2, r.stdout + r.stderr


BLANK = {"lf_in_the_middle": (4, "\n"), "lf_at_the_end": (None, "\n"), "crlf_in_the_middle": (4, "\r\n")}


@pytest.mark.parametrize("case", list(BLANK), ids=list(BLANK))
def test_blank_line_is_skipped_as_before(outside_tmp: Path, case: str) -> None:
    root, env = example_copy(outside_tmp)
    before = raw_bytes(root)
    where, end = BLANK[case]
    lines = target_lines()
    lines.insert(len(lines) if where is None else where, "")
    extract = write_text(outside_tmp / "export" / "target.csv", end.join(lines) + end)
    r = run_cli("land", str(root), "--table", "target", "--csv", str(extract), "--force", env=env)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "raw/target.csv already holds these 24 rows (same digest); nothing changed" in out, out
    assert raw_bytes(root) == before
    # the digest of the file with the blank line is the digest of the file without it
    header, body, _lines = land._read_csv(extract, "utf-8-sig", ",")
    plain = (EXAMPLE / "data" / "target.csv")
    header2, body2, _lines2 = land._read_csv(plain, "utf-8-sig", ",")
    assert (header, body) == (header2, body2)


# ============================================================================= quoting

def test_open_quote_at_the_end_is_refused_with_its_line(outside_tmp: Path) -> None:
    root, env = example_copy(outside_tmp)
    before = raw_bytes(root)
    lines = target_lines()
    last = lines[-1].rsplit(",", 1)
    lines[-1] = last[0] + ',"' + last[1]                     # an opening quote before the last amount
    extract = write_text(outside_tmp / "export" / "target.csv", "\n".join(lines) + "\n")
    for flags in ((), ("--force",)):
        r = run_cli("land", str(root), "--table", "target", "--csv", str(extract), *flags, env=env)
        out = r.stdout + r.stderr
        assert r.returncode == 2, out
        assert f"target.csv: line {len(lines)}: {OPEN_QUOTE}" in out, out
        assert "Fix: close the quote in the source export, or export the file again" in out
        assert raw_bytes(root) == before
    # an open quote in line 10 runs to the end of the file: the line where the field starts is named
    lines = target_lines()
    region, rest = lines[9].split(",", 1)
    lines[9] = '"' + region + "," + rest
    extract = write_text(outside_tmp / "export2" / "target.csv", "\n".join(lines) + "\n")
    r = run_cli("land", str(root), "--table", "target", "--csv", str(extract), "--force", env=env)
    assert r.returncode == 2 and f"target.csv: line 10: {OPEN_QUOTE}" in r.stdout + r.stderr, r.stdout + r.stderr
    # another quoting error: text after a closing quote
    lines = target_lines()
    region, month, amount = lines[5].split(",")
    lines[5] = f'{region},"{month}"x,{amount}'
    extract = write_text(outside_tmp / "export3" / "target.csv", "\n".join(lines) + "\n")
    r = run_cli("land", str(root), "--table", "target", "--csv", str(extract), "--force", env=env)
    out = r.stdout + r.stderr
    assert r.returncode == 2 and "target.csv: line 6: " in out and "; nothing was written" in out, out
    assert "Fix: export the file again as CSV UTF-8" in out
    assert raw_bytes(root) == before
    # contract propose reads a sample the same way and writes nothing
    sample = write_text(outside_tmp / "sample" / "t.csv", "id,amount\nA1,1\nA2,\"2\n")
    r = run_cli("contract", "propose", str(outside_tmp / "new"), "--table", "t", "--csv", str(sample))
    assert r.returncode == 2 and f"t.csv: line 3: {OPEN_QUOTE}" in r.stdout + r.stderr, r.stdout + r.stderr
    assert not (outside_tmp / "new").exists()


def test_open_quote_line_is_where_the_field_starts() -> None:
    """The record starts on line 2; a valid quoted line break comes first, the unclosed field starts on line 3."""
    text = 'id,note,amount\nA1,"one\ntwo",x,"three\nfour\n'
    with pytest.raises(common.Refusal) as exc:
        list(land.csv_records(io.StringIO(text, newline=""), name="f.csv"))
    assert exc.value.message == f"f.csv: line 3: {OPEN_QUOTE}"
    text = 'id,note\nA1,"he said ""hi"""\nA2,"x\n'
    with pytest.raises(common.Refusal) as exc:
        list(land.csv_records(io.StringIO(text, newline=""), name="f.csv"))
    assert exc.value.message == f"f.csv: line 3: {OPEN_QUOTE}"


EXCEL_FORM = ("﻿id,note,amount\r\n"
              "A1,\"Seoul, Korea\",10.5\r\n"
              "A2,\"He said \"\"yes\"\"\",20\r\n"
              "A3,\"line one\nline two\",30\r\n"
              "A4,plain,40\r\n")


def test_csv_in_excel_form_lands(outside_tmp: Path) -> None:
    sample = outside_tmp / "sample" / "ledger.csv"
    root = agreed(outside_tmp, EXCEL_FORM)
    assert sample.read_bytes().startswith(b"\xef\xbb\xbf") and b"\r\n" in sample.read_bytes()
    r = run_cli("land", str(root), "--table", "ledger", "--csv", str(sample))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "landed 4 rows of 'ledger'" in out, out
    with open(root / "raw" / "ledger.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert rows == [["id", "note", "amount"], ["A1", "Seoul, Korea", "10.5"], ["A2", 'He said "yes"', "20"],
                    ["A3", "line one\nline two", "30"], ["A4", "plain", "40"]]
    data = common.read_json(modelfolder.contract_path(root, "ledger"))
    assert not any("only delimiters" in n for n in data["notes"])
    assert [ln for ln, _r in land.csv_records(io.StringIO(EXCEL_FORM[1:], newline=""), name="x")] == [1, 2, 3, 4, 6]


def test_line_numbers_count_a_line_break_inside_quotes(outside_tmp: Path) -> None:
    root = agreed(outside_tmp, "id,note,amount\nA1,x,1\nA2,y,2\n", extra=("--null", "note=allowed"))
    text = ('id,note,amount\n'
            'A1,"two\nlines",1\n'          # lines 2 and 3
            '\n'                           # line 4: blank, skipped
            'A2,x,\n'                      # line 5: amount empty
            'A3,y,abc\n'                   # line 6: not a number
            'A1,z,2\n')                    # line 7: repeats the key of line 2
    extract = write_text(outside_tmp / "export" / "ledger.csv", text)
    r = run_cli("land", str(root), "--table", "ledger", "--csv", str(extract))
    errs = errors(r.stdout)
    assert r.returncode == 1, r.stdout + r.stderr
    by_rule = {e.split(":", 1)[0].split()[-1]: e for e in errs}
    assert "on 1 row(s) (lines 5)" in by_rule["L-NULL"], errs
    assert "line 6: 'abc' is not a whole number" in by_rule["L-TYPE"], errs
    assert "lines 2 and 7: A1" in by_rule["L-KEY"], errs
    assert ONLY not in r.stdout
    assert not (root / "raw" / "ledger.csv").exists()


# ============================================================================= the shipped folders

def _shipped() -> list[Path]:
    roots = [SKILL / "examples", SKILL / "templates"]
    if (PLUGIN / ".claude-plugin" / "plugin.json").is_file():
        roots.append(PLUGIN / "templates")
    out: list[Path] = []
    for r in roots:
        if r.is_dir():
            out += sorted(p for p in r.iterdir() if (p / "model.spec.json").is_file())
    return out


SHIPPED = _shipped()


def test_six_shipped_folders_are_found() -> None:
    assert len(SHIPPED) == (6 if (PLUGIN / ".claude-plugin" / "plugin.json").is_file() else 2)


@pytest.mark.parametrize("folder", SHIPPED, ids=[f"{p.parent.name}/{p.name}" for p in SHIPPED])
def test_shipped_folders_land_with_the_same_digests(folder: Path, outside_tmp: Path) -> None:
    dest = outside_tmp / folder.name
    shutil.copytree(folder, dest, ignore=shutil.ignore_patterns("__pycache__", "reports"))
    env = dict(os.environ, ROTM_EXAMPLE_ROOTS=str(outside_tmp), PYTHONUTF8="1")
    tables = modelfolder.tables(folder)
    assert tables
    for t in tables:
        (dest / "raw" / f"{t}.csv").unlink()
        r = run_cli("land", str(dest), "--table", t, "--csv", str(dest / "data" / f"{t}.csv"), env=env)
        assert r.returncode == 0, (t, r.stdout[-2000:], r.stderr[-2000:])
        assert (dest / "raw" / f"{t}.csv").read_bytes() == (folder / "raw" / f"{t}.csv").read_bytes(), t
