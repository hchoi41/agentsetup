"""v2 tests of package P6: the portable scan (O-1), the plugin author (O-2), the Google Sheets fallback and
download check (A-aera-15, C-sol-8, C-sol-9) and a Power Query workbook the test builds itself (J-2, A-aera-17).

The strings that the portable scan must find are built at run time from parts, so that this file itself holds
no machine-specific path, e-mail address or phone number (a test scans this file).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

from lib import common, selftest

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
PLUGIN = SKILL.parents[1]
REFS = SKILL / "references"


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                          env={**os.environ, **(env or {})})


# ----------------------------------------------------------------------------- strings built at run time

def _user_folder() -> str:
    return "C" + ":" + "\\" + "Us" + "ers" + "\\" + "jdoe" + "\\Documents\\model.xlsx"


def _hub_folder() -> str:
    return "C" + ":/" + "0" * 3 + ".hub/notes/plan.md"


def _onedrive_folder() -> str:
    return "D:\\" + "One" + "Drive - Contoso" + "\\Finance\\raw.csv"


def _email() -> str:
    return "jane.doe" + "@" + "example" + ".org"


def _phone() -> str:
    return "010" + "-" + "1234" + "-" + "5678"


def _png(chunks: list[tuple[bytes, bytes]]) -> bytes:
    out = b"\x89PNG\r\n\x1a\n"
    for kind, body in [(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))] + chunks + [(b"IEND", b"")]:
        out += struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    return out


def _workbook(path: Path, *, last_modified_by: str, abs_path: str | None = None) -> Path:
    from openpyxl import Workbook
    wb = Workbook()
    wb.active["A1"] = "value"
    wb.properties.creator = f"{common.TOOL} {common.VERSION}"
    wb.properties.lastModifiedBy = last_modified_by
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    if abs_path is not None:                      # what Excel writes into xl/workbook.xml when it saves a file
        tmp = path.with_suffix(".tmp")
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                data = src.read(info)
                if info.filename == "xl/workbook.xml":
                    text = data.decode("utf-8")
                    head_end = text.index(">", text.index("<workbook")) + 1
                    text = (text[:head_end] + f'\n<x15ac:absPath url="{abs_path}\\"/>' + text[head_end:])
                    data = text.encode("utf-8")
                dst.writestr(info, data)
        os.replace(tmp, path)
    return path


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def _dirty_tree(root: Path) -> Path:
    """One hit of each kind; the expected hits are in DIRTY_EXPECTED."""
    _write(root / "notes.md", "# Notes\n\nThe source file was " + _user_folder() + " on the old PC.\n")
    _write(root / "data" / "report.json", '{\n "target": "' + _hub_folder() + '"\n}\n')
    _write(root / "queries" / "t.pq.m", 'Source = Csv.Document(File.Contents("' + _onedrive_folder() + '"))\n')
    _write(root / "code.py", f'CONTACT = "{_email()}"\n')
    _write(root / "contact.txt", "Call " + _phone() + " for access.\n")
    _workbook(root / "model.xlsx", last_modified_by="another author", abs_path=_user_folder().rsplit("\\", 1)[0])
    (root / "image.png").write_bytes(_png([(b"tEXt", b"Source\x00" + _user_folder().encode("ascii"))]))
    return root


DIRTY_EXPECTED = {
    ("notes.md", None, "user_folder"),
    ("data/report.json", None, "hub_folder"),
    ("queries/t.pq.m", None, "onedrive_folder"),
    ("code.py", None, "email"),
    ("contact.txt", None, "phone"),
    ("model.xlsx", "xl/workbook.xml", "user_folder"),
    ("model.xlsx", "docProps/core.xml", "workbook_author"),
    ("image.png", None, "user_folder"),
}


def _clean_tree(root: Path) -> Path:
    """Dates, times, versions, digests, amounts, identifiers and placeholders: none is a hit."""
    digest = hashlib.sha256(b"rotm").hexdigest()
    _write(root / "notes.md", "\n".join([
        "Built 2026-11-25 at 2026-11-25T03:07:51+09:00 with version 0.1.0 (Excel 16.0.19328).",
        f"sha256:{digest}; contract sha256 {digest[:16]}",
        "Control total 57,514,500 KRW; amounts 1158000, 655000, -1,234.50, 0.0123, 999,999,999,500; 1e-09.",
        "Lines L0001 to L9015; tbl_raw_lines; dates 2026-03-15 and 20261124_120000.",
        "Install: claude --plugin-dir <plugin folder>; the model folder is <model folder>\\raw\\lines.csv.",
        "Placeholders: C:\\Users\\<name>\\models and %USERPROFILE%\\models stay placeholders.",
        "OneDrive stays the source of record; decorators such as @pytest.mark.excel are code.",
    ]) + "\n")
    _write(root / "lines.csv", "line_id,line_date,category,amount\nL0001,2026-01-02,Staff,655000\n"
                               "L0002,2026-02-27,Supplies,1158000\n")
    _workbook(root / "model.xlsx", last_modified_by=f"{common.TOOL} {common.VERSION}")
    idat = (b"\x00a" + b"@" + b"b.cd " + _email().encode("ascii") + b" " + _phone().encode("ascii"))
    (root / "image.png").write_bytes(_png([(b"IDAT", idat)]))
    return root


# ----------------------------------------------------------------------------- O-1: the portable scan

def test_portable_scan_reports_each_kind_with_file_member_and_line(tmp_path: Path) -> None:
    root = _dirty_tree(tmp_path / "dirty")
    hits = selftest.portable_scan(root)
    assert {(h.file, h.member, h.kind) for h in hits} == DIRTY_EXPECTED
    by_file = {(h.file, h.member, h.kind): h for h in hits}
    assert by_file[("notes.md", None, "user_folder")].line == 3
    assert by_file[("data/report.json", None, "hub_folder")].line == 2
    assert by_file[("contact.txt", None, "phone")].text == _phone()
    assert by_file[("code.py", None, "email")].text == _email()
    png = by_file[("image.png", None, "user_folder")]
    assert png.line is None and png.offset and png.offset > 8
    for h in hits:
        where = h.where()
        assert h.file in where
        assert (h.member or "") in where
        assert (f"line {h.line}" in where) if h.line is not None else (f"byte {h.offset}" in where)
    author = by_file[("model.xlsx", "docProps/core.xml", "workbook_author")]
    assert author.text == "cp:lastModifiedBy" and "another author" not in author.where()


def test_portable_scan_of_a_clean_tree_reports_nothing(tmp_path: Path) -> None:
    root = _clean_tree(tmp_path / "clean")
    assert selftest.portable_scan(root) == []


def test_png_image_data_is_left_out_but_its_text_chunks_are_scanned(tmp_path: Path) -> None:
    text = tmp_path / "t.png"
    text.write_bytes(_png([(b"zTXt", b"Author\x00\x00" + zlib.compress(_email().encode("ascii")))]))
    pixels = tmp_path / "p.png"
    pixels.write_bytes(_png([(b"IDAT", _email().encode("ascii"))]))
    assert [h.kind for h in selftest.portable_scan_file(text, tmp_path)] == ["email"]
    assert selftest.portable_scan_file(pixels, tmp_path) == []


def test_the_scan_patterns_do_not_match_their_own_source_or_this_test_file() -> None:
    assert selftest.portable_scan_file(SCRIPTS / "lib" / "selftest.py", SCRIPTS) == []
    assert selftest.portable_scan_file(Path(__file__), SCRIPTS) == []


def _inside(path: Path, folder: Path) -> bool:
    try:
        return path.resolve().is_relative_to(folder)
    except OSError:
        return False


def test_shipped_data_digests_and_amounts_give_no_phone_or_email_hit(tmp_path_factory: pytest.TempPathFactory) -> None:
    root = PLUGIN if (PLUGIN / ".claude-plugin" / "plugin.json").is_file() else SKILL
    # v5 ([A-aera-8, C-sol-8]: the suite passes wherever the temporary folder is): the files this pytest session
    # writes are not shipped, also when --basetemp puts them inside the plugin folder; they are left out
    session = tmp_path_factory.getbasetemp().resolve()
    files = [f for f in selftest.portable_files(root) if not _inside(f, session)]
    hits = [h for f in files for h in selftest.portable_scan_file(f, root) if h.kind in ("phone", "email")]
    assert hits == [], [h.where() + " " + h.text for h in hits[:10]]


def test_folders_that_are_not_shipped_are_left_out(tmp_path: Path) -> None:
    root = tmp_path / "p"
    for folder in selftest.PORTABLE_SKIP_DIRS:
        _write(root / folder / "x.txt", _email() + "\n")
    _write(root / "kept.txt", "nothing here\n")
    assert [p.name for p in selftest.portable_files(root)] == ["kept.txt"]


def _run_portable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tree: Path, *, strict: bool):
    monkeypatch.setattr(selftest, "_portable_root", lambda: tree)
    rep = common.Report("selftest", "x")
    run = selftest._Run(rep, tmp_path / "work", strict=strict, quick=True, excel_ok=False, excel_why="test",
                        foreign_dir=None)
    run.run("portable", run.step_portable)
    return run, rep


def test_portable_step_warns_without_strict_and_fails_with_strict(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    tree = _dirty_tree(tmp_path / "dirty")
    run, rep = _run_portable(tmp_path, monkeypatch, tree, strict=False)
    assert run.results == {"portable": "pass"}
    hits = [f for f in rep.findings if f.rule == "SELFTEST-PORTABLE"]
    assert len(hits) == len(DIRTY_EXPECTED) and {f.severity for f in hits} == {"warning"}
    assert any("model.xlsx [xl/workbook.xml] line" in f.message for f in hits)
    assert len(rep.data["portable_hits"]) == len(DIRTY_EXPECTED)
    assert selftest.exit_code_for_steps(run.results, strict=False) == 0
    run, rep = _run_portable(tmp_path, monkeypatch, tree, strict=True)
    assert run.results == {"portable": "fail"}
    assert {f.severity for f in rep.findings if f.rule == "SELFTEST-PORTABLE"} == {"error"}
    assert selftest.exit_code_for_steps(run.results, strict=True) == 1
    run, rep = _run_portable(tmp_path, monkeypatch, _clean_tree(tmp_path / "clean"), strict=True)
    assert run.results == {"portable": "pass"}
    assert not [f for f in rep.findings if f.rule == "SELFTEST-PORTABLE"]


def test_portable_step_runs_in_quick_mode_and_scans_the_skill_folder_when_it_is_alone(
        tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    assert "portable" in selftest.QUICK_STEPS and "portable" not in selftest.EXCEL_STEPS
    alone = tmp_path / "skills" / "rule-of-thumb-model"
    skip = shutil.ignore_patterns(*selftest.COPY_SKIP, "reports", "examples", "templates", "references", "tests")
    session = tmp_path_factory.getbasetemp().resolve()

    def ignore(folder: str, names: list[str]) -> set[str]:
        # v5 ([A-aera-8, C-sol-8]): with --basetemp inside the skill folder, the copy would hold this session's
        # temporary folder, the copy itself among it, again and again; it is left out
        return set(skip(folder, names)) | {n for n in names if _inside(Path(folder) / n, session)}

    shutil.copytree(SKILL, alone, ignore=ignore)
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from lib import selftest; "
            "print(selftest._portable_root().as_posix())")
    proc = subprocess.run([sys.executable, "-P", "-B", "-c", code, str(alone / "scripts")], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == alone.resolve().as_posix()


# ----------------------------------------------------------------------------- O-2: the plugin's author

def test_plugin_manifest_names_the_author() -> None:
    path = PLUGIN / ".claude-plugin" / "plugin.json"
    if not path.is_file():
        pytest.skip("the skill folder was copied alone (F02): no plugin.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["author"] == {"name": "Max"}
    assert data["name"] == "rule-of-thumb-model" and data["version"] == "0.9.0" and data["description"]


# ----------------------------------------------------------------------------- A-aera-15, C-sol-8, C-sol-9

def _sections(name: str) -> dict[str, str]:
    text = (REFS / name).read_text(encoding="utf-8")
    parts = re.split(r"^## ", text, flags=re.M)
    return {p.split("\n", 1)[0].strip(): p for p in parts[1:]}


def test_google_sheets_says_nothing_was_tested_there() -> None:
    text = (REFS / "google-sheets.md").read_text(encoding="utf-8")
    assert "Nothing in this file was tested in Google Sheets" in text


def test_google_sheets_fallback_gives_the_cutover_and_its_confirmation() -> None:
    sec = _sections("google-sheets.md")
    steps = sec["Landing a raw table: the steps"]
    cut = sec["Cutover when the extract is on a new sheet"]
    assert "Cutover when the extract is on a new sheet" in steps
    for words in ("contents", "rename", "header in row 1", "the specification", "build", "convert",
                  "header", "row count", "control total", "before you report any result",
                  "the model still reads the static snapshot", "not tested"):
        assert words in cut, f"the cutover section must say: {words}"


def test_google_sheets_download_check_names_where_to_save_and_what_excel_checks() -> None:
    sec = _sections("google-sheets.md")
    steps = sec["Landing a raw table: the steps"]
    kept = sec["Models kept in Google Sheets"]
    assert "Models kept in Google Sheets" in steps, "step 7 points to the download procedure"
    for words in ("rotm.py audit", "rotm.py check", "rotm.py recalc", "rotm_model.json", "model.manifest.json",
                  "workbook name", "beside", "computed by Google Sheets", "R-CHANGED", "V-ERROR", "C-FAIL",
                  "recalculates"):
        assert words in kept, f"'Models kept in Google Sheets' must say: {words}"


# ----------------------------------------------------------------------------- J-2 (3): a Power Query fixture

def _pq_source(csv_path: Path, n: int) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        f.write("id,region,amount\n" + "".join(f"{i:04d},R{i % 3},{i * 10}\n" for i in range(1, n + 1)))


def _pq_m_text(csv_path: Path) -> str:
    win = str(csv_path).replace("/", "\\")
    return ("let\n"
            f'    Source = Csv.Document(File.Contents("{win}"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n'
            "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),\n"
            '    Typed = Table.TransformColumnTypes(Promoted, {{"id", type text}, {"region", type text}, '
            '{"amount", type number}}, "en-US")\n'
            "in\n    Typed")


def make_power_query_workbook(folder: Path, rows: int = 6) -> Path:
    """A workbook the plugin did not build: raw_sales filled by a Power Query table over a local CSV (attached
    through Excel), and a calc sheet with a total, a formula nested three deep (for split) and a CONCATENATE
    (for compat)."""
    from openpyxl import Workbook
    from openpyxl.worksheet.table import Table
    from lib import excel
    csv_path = folder / "source" / "sales.csv"
    _pq_source(csv_path, rows)
    wb = Workbook()
    raw = wb.active
    raw.title = "raw_sales"
    raw.append(["id", "region", "amount"])
    for i in range(1, rows + 1):
        raw.append([f"{i:04d}", f"R{i % 3}", i * 10])
    raw.add_table(Table(displayName="tbl_raw_sales", ref=f"A1:C{rows + 1}"))
    calc = wb.create_sheet("calc")
    calc["A1"], calc["B1"] = "Total", "=SUM(raw_sales!C:C)"
    calc["A2"], calc["B2"] = "Rounded", "=IF(raw_sales!C2>0,ROUND(SUM(raw_sales!C2:C3),0),0)"
    calc["A3"], calc["B3"] = "Label", '=CONCATENATE(raw_sales!B2,"-",raw_sales!A2)'
    path = folder / "pq_workbook.xlsx"
    wb.save(path)
    res = excel.pq_attach(path, sheet="raw_sales", table_name="tbl_raw_sales", query_name="q_sales",
                          m_text=_pq_m_text(csv_path))
    assert res.ok, res.errors
    return path


def _keeps_the_query(path: Path) -> bool:
    from lib import excel
    inv = excel.inventory(path)
    raw = next((s for s in inv["sheets"] if s["name"] == "raw_sales"), None)
    return inv["queries"] == ["q_sales"] and raw is not None and any(t.get("query") for t in raw["tables"])


@pytest.mark.excel
@pytest.mark.slow
def test_audit_theme_compat_and_split_on_a_power_query_workbook_the_test_builds(tmp_path: Path) -> None:
    from lib import excel
    since = common.now_iso()
    original = make_power_query_workbook(tmp_path / "fixture")
    assert _keeps_the_query(original), "the fixture has its query"
    work = tmp_path / "work"
    work.mkdir()
    audited = shutil.copy2(original, work / "audit_copy.xlsx")
    proc = run_cli("audit", str(audited), "--json", "-", "--no-record")
    assert proc.returncode in (0, 1), proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
    report = json.loads(proc.stdout)
    assert report["command"] == "audit" and report["summary"] is not None
    outcomes = {}
    for name, extra in (("theme", []), ("compat", []), ("split", ["calc!B2", "--cell-only"])):
        copy = shutil.copy2(original, work / f"{name}_copy.xlsx")
        out = work / f"{name}_out.xlsx"
        rep_path = work / f"{name}.json"
        proc = run_cli(name, str(copy), *extra, "-o", str(out), "--json", str(rep_path))
        assert "Traceback" not in proc.stderr, (name, proc.stderr[-2000:])
        rep = json.loads(rep_path.read_text(encoding="utf-8"))
        if out.is_file():
            assert proc.returncode in (0, 1), (name, proc.stdout[-2000:])
            assert _keeps_the_query(out), f"{name} lost the Power Query table"
            outcomes[name] = "kept the query"
        else:
            reasons = [f["message"] for f in rep.get("findings") or [] if f.get("severity") == "error"]
            assert proc.returncode in (common.EXIT_REFUSED, common.EXIT_NOT_AVAILABLE) and reasons, \
                (name, proc.returncode, proc.stdout[-2000:])
            outcomes[name] = "refused: " + reasons[0]
    assert set(outcomes) == {"theme", "compat", "split"}
    assert excel.orphan_pids(excel.read_job_log(since=since)) == []
