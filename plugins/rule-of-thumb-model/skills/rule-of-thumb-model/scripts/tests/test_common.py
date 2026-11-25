"""Tests of lib.common: exit codes, findings, reports, JSON helpers, A1 helpers, paths, the command wrapper
(design sections 3.2, 3.3, 19). Owner: CORE-IO."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lib import common, schemas

SCRIPTS = Path(__file__).resolve().parents[1]


# ----------------------------------------------------------------------------- exit codes

@pytest.mark.parametrize("errors,warnings,strict,code", [
    (0, 0, False, 0), (0, 0, True, 0), (0, 3, False, 0), (0, 3, True, 1), (1, 0, False, 1), (2, 5, True, 1)])
def test_exit_code_for(errors: int, warnings: int, strict: bool, code: int) -> None:
    assert common.exit_code_for(errors, warnings, strict) == code


def test_exit_code_constants_match_design() -> None:
    assert (common.EXIT_OK, common.EXIT_FINDINGS, common.EXIT_REFUSED, common.EXIT_NOT_AVAILABLE,
            common.EXIT_INTERNAL, common.EXIT_BUSY) == (0, 1, 2, 3, 4, 5)
    assert common.Refusal("x").exit_code == 2
    assert common.EnvUnavailable("x").exit_code == 3
    assert common.ExcelBusy("x").exit_code == 5
    assert common.RotmError("x").exit_code == 4


# ----------------------------------------------------------------------------- findings and reports

def test_finding_line_and_where() -> None:
    f = common.Finding("F-DEPTH", "error", "nesting depth 3 is over the limit 2", "move MATCH(...) into a helper",
                       sheet="calc", cell="D7", count=48)
    assert f.where() == "calc!D7 (48 cells)"
    assert f.line() == "ERR  calc!D7 (48 cells)  F-DEPTH: nesting depth 3 is over the limit 2. Fix: move MATCH(...) into a helper"
    assert f.cells == ["D7"]
    assert common.Finding("X", "info", "m").where() == "-"
    with pytest.raises(ValueError):
        common.Finding("X", "fatal", "m")


def test_report_groups_findings_of_one_rule_sheet_and_fix() -> None:
    r = common.Report("audit", "model.xlsx")
    for row in range(2, 40):
        r.error("H-INCONSISTENT", "helper column differs", "copy the first row down", sheet="h_sales", cell=f"G{row}")
    r.warning("F-LENGTH", "too long", "split it", sheet="calc", cell="D7")
    assert len(r.findings) == 2
    grouped = r.findings[0]
    assert grouped.count == 38 and len(grouped.cells) == 20 and grouped.cells[0] == "G2"
    assert r.counts() == {"errors": 1, "warnings": 1, "info": 0}


@pytest.mark.parametrize("strict,code,status", [(False, 1, "fail"), (True, 1, "fail")])
def test_report_finish_with_error(strict: bool, code: int, status: str) -> None:
    r = common.Report("audit", "x.xlsx")
    r.error("A", "m")
    assert r.finish(strict) == code and r.status == status


def test_report_finish_warning_only_depends_on_strict() -> None:
    r = common.Report("audit", "x.xlsx")
    r.warning("W", "m")
    assert r.finish(False) == 0 and r.status == "pass"
    assert r.finish(True) == 1 and r.status == "fail"
    r.info("I", "m")
    assert r.counts()["info"] == 1


def test_report_finish_refused_and_not_available() -> None:
    r = common.Report("check", "m")
    assert r.finish(refused=True) == 2 and r.status == "refused"
    assert r.finish(not_available=True) == 3 and r.status == "not_verified"


def test_report_dict_follows_the_report_schema() -> None:
    r = common.Report("audit", "C:/m/model.xlsx", strict=True)
    r.error("F-DEPTH", "depth 3", "move it", sheet="calc", cell="D7", data={"depth": 3})
    r.note("values not verified")
    r.finish(True)
    d = r.to_dict()
    assert schemas.validate(d, "report") == []
    assert d["rotm_report"] == 1 and d["tool"] == "rule-of-thumb-model" and d["exit_code"] == 1
    assert d["summary"] == {"errors": 1, "warnings": 0, "info": 0}


def test_report_emit_json_to_stdout_puts_console_on_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    r = common.Report("audit", "x.xlsx")
    r.warning("W-1", "a warning", "fix it", sheet="s", cell="A1")
    r.finish(True)
    code = r.emit("-")
    out, err = capsys.readouterr()
    assert code == 1
    assert json.loads(out)["summary"]["warnings"] == 1
    assert "warn s!A1  W-1: a warning. Fix: fix it" in err
    assert err.strip().endswith("errors: 0  warnings: 1  info: 0")


def test_report_emit_quiet_prints_only_the_count(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    r = common.Report("audit", "x.xlsx")
    r.error("E", "e")
    r.finish(False)
    r.emit(tmp_path / "r.json", quiet=True)
    out, _ = capsys.readouterr()
    assert out.strip() == "errors: 1  warnings: 0  info: 0"
    assert common.read_json(tmp_path / "r.json")["status"] == "fail"


def test_report_extend() -> None:
    r = common.Report("x", "y")
    r.extend([common.Finding("A", "error", "m", sheet="s", cell="A1"),
              common.Finding("A", "error", "m", sheet="s", cell="A2")])
    assert len(r.findings) == 1 and r.findings[0].count == 2


# ----------------------------------------------------------------------------- JSON and hashing

def test_canonical_hash_ignores_key_order_and_keeps_unicode() -> None:
    a = {"b": 1, "a": [1, 2], "한": "값"}
    b = {"한": "값", "a": [1, 2], "b": 1}
    assert common.canonical_json(a) == common.canonical_json(b)
    assert "값" in common.canonical_json(a)
    assert common.canonical_hash(a) == common.canonical_hash(b)
    assert common.canonical_hash(a) != common.canonical_hash({"b": 2, "a": [1, 2], "한": "값"})


def test_write_json_is_atomic_and_read_json_accepts_bom(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "x.json"
    common.write_json(target, {"a": "é"})
    assert common.read_json(target) == {"a": "é"}
    assert [p.name for p in target.parent.iterdir()] == ["x.json"]
    bom = tmp_path / "bom.json"
    bom.write_bytes(b"\xef\xbb\xbf" + json.dumps({"k": 1}).encode())
    assert common.read_json(bom) == {"k": 1}


def test_file_sha256(tmp_path: Path) -> None:
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    assert common.file_sha256(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_load_json_file_refusals_name_the_file_line_and_fix(tmp_path: Path) -> None:
    with pytest.raises(common.Refusal, match="not found"):
        common.load_json_file(tmp_path / "missing.json", "spec")
    bad = tmp_path / "bad.json"
    bad.write_text('{"a": 1,\n "b" 2}', encoding="utf-8")
    with pytest.raises(common.Refusal) as e:
        common.load_json_file(bad)
    assert "line 2 column" in e.value.message and e.value.fix
    latin = tmp_path / "latin.json"
    latin.write_bytes(b'{"a": "\xe9"}')
    with pytest.raises(common.Refusal, match="not UTF-8"):
        common.load_json_file(latin)


# ----------------------------------------------------------------------------- A1 helpers

def test_column_letters_round_trip() -> None:
    for n in list(range(1, 800)) + [16384]:
        assert common.col_index(common.col_letter(n)) == n
    assert common.col_letter(1) == "A" and common.col_letter(27) == "AA" and common.col_letter(16384) == "XFD"
    assert common.col_index("$D") == 4
    for bad in ("", "A1", "$"):
        with pytest.raises(ValueError):
            common.col_index(bad)
    with pytest.raises(ValueError):
        common.col_letter(0)


def test_split_cell_a1_and_ranges() -> None:
    assert common.split_cell("$D$7") == (4, 7)
    assert common.a1(4, 7) == "D7" and common.a1(4, 7, abs_col=True, abs_row=True) == "$D$7"
    assert common.range_bounds("$B$6:$D$9") == (2, 6, 4, 9)
    assert common.range_bounds("D9:B6") == (2, 6, 4, 9)
    assert common.range_bounds("calc!C2") == (3, 2, 3, 2)
    assert list(common.iter_range("A1:B2")) == [(1, 1), (1, 2), (2, 1), (2, 2)]
    with pytest.raises(ValueError):
        common.split_cell("A:A")
    with pytest.raises(ValueError):
        common.range_bounds("A1:B2:C3")


@pytest.mark.parametrize("name,expected", [
    ("calc", "calc"), ("raw_sales", "raw_sales"), ("my sheet", "'my sheet'"), ("it's", "'it''s'"),
    ("AB12", "'AB12'"), ("R1C1", "'R1C1'"), ("C5", "'C5'"), ("2026", "'2026'"), ("매출", "'매출'"), ("TRUE", "'TRUE'")])
def test_quote_sheet(name: str, expected: str) -> None:
    assert common.quote_sheet(name) == expected


def test_parse_ref() -> None:
    assert common.parse_ref("calc!D7") == ("calc", "D7")
    assert common.parse_ref("'it''s'!A1") == ("it's", "A1")
    assert common.parse_ref("D7") == (None, "D7")
    for name in ("calc", "my sheet", "it's"):
        assert common.parse_ref(f"{common.quote_sheet(name)}!B2") == (name, "B2")


# ----------------------------------------------------------------------------- paths

@pytest.mark.skipif(os.name != "nt", reason="Git Bash paths exist on Windows only")
def test_native_path_converts_git_bash_paths() -> None:
    assert common.native_path("/c/models/x").as_posix() == "C:/models/x"
    assert common.native_path("/cygdrive/d/a").as_posix() == "D:/a"
    assert common.native_path("/c").as_posix() == "C:/"
    assert common.native_path("C:/a/b").as_posix() == "C:/a/b"


def test_is_within_and_relpath(tmp_path: Path) -> None:
    inner = tmp_path / "a" / "b.txt"
    assert common.is_within(inner, tmp_path) and common.is_within(tmp_path, tmp_path)
    assert not common.is_within(tmp_path, inner)
    assert not common.is_within(tmp_path.parent / (tmp_path.name + "x"), tmp_path)
    assert common.relpath_posix(inner, tmp_path) == "a/b.txt"


def test_locations() -> None:
    skill = common.skill_dir()
    assert (skill / "SKILL.md").is_file() and common.scripts_dir() == skill / "scripts"
    assert (common.references_dir() / "style_dictionary.json").is_file()
    plugin = common.plugin_dir()
    if plugin is not None:
        assert (plugin / ".claude-plugin" / "plugin.json").is_file()


def test_state_dir_uses_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROTM_STATE_DIR", str(tmp_path / "state"))
    d = common.state_dir(create=True)
    assert d == tmp_path / "state" and d.is_dir()


# ----------------------------------------------------------------------------- errors and the command wrapper

def test_error_line_and_lines() -> None:
    e = common.Refusal("spec has 2 problems", key="model.spec.json", fix="change the keys",
                       details=["sheets[0].type: bad", "sheets[1].name: bad"])
    assert e.line() == "model.spec.json: spec has 2 problems. Fix: change the keys | sheets[0].type: bad; sheets[1].name: bad"
    assert e.lines() == ["model.spec.json: spec has 2 problems. Fix: change the keys",
                         "  sheets[0].type: bad", "  sheets[1].name: bad"]
    assert str(e) == e.line()


@pytest.mark.parametrize("exc,code", [(common.Refusal("bad input", fix="change it"), 2),
                                      (common.EnvUnavailable("no Excel"), 3),
                                      (common.ExcelBusy("lock held"), 5),
                                      (NotImplementedError("lib.x.y"), 4)])
def test_run_command_maps_errors(exc: Exception, code: int, capsys: pytest.CaptureFixture[str]) -> None:
    def fn(_args: object) -> int:
        raise exc
    assert common.run_command(fn, None, name="demo") == code
    err = capsys.readouterr().err
    assert err.startswith("rotm.py demo: ") and "Traceback" not in err


def test_run_command_internal_error_is_one_line_and_saved(capsys: pytest.CaptureFixture[str]) -> None:
    def fn(_args: object) -> int:
        return {}["missing"]  # type: ignore[return-value]
    assert common.run_command(fn, None, name="demo") == 4
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "KeyError" in err and "Traceback" not in err
    saved = common.state_dir() / "last_error.txt"
    assert saved.is_file() and "Traceback" in saved.read_text(encoding="utf-8")


def test_run_command_refusal_details_on_separate_lines(capsys: pytest.CaptureFixture[str]) -> None:
    def fn(_args: object) -> int:
        raise common.Refusal("2 problems", details=["a: x", "b: y"])
    assert common.run_command(fn, None, name="build") == 2
    assert capsys.readouterr().err.splitlines() == ["rotm.py build: 2 problems", "  a: x", "  b: y"]


def test_run_command_returns_code_or_zero() -> None:
    assert common.run_command(lambda a: None, None) == 0
    assert common.run_command(lambda a: 3, None) == 3


def test_common_imports_under_isolated_mode() -> None:
    code = (f"import sys; sys.path.insert(0, {str(SCRIPTS)!r}); from lib import common; "
            "sys.exit(0 if common.VERSION == '0.1.0' else 1)")
    assert subprocess.run([sys.executable, "-I", "-B", "-c", code], timeout=60).returncode == 0


def test_chunks() -> None:
    assert list(common.chunks(range(5), 2)) == [[0, 1], [2, 3], [4]]
