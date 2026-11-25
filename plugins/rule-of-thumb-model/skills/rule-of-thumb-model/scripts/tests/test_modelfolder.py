"""Tests of lib.modelfolder: the marker, find_model_root, init (with templates), contract states, the status
file and the init/status subcommands (design sections 3.1, 4, 5, 17; F16 a). Owner: CORE-IO."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from lib import common, modelfolder, schemas

SCRIPTS = Path(__file__).resolve().parents[1]


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SCRIPTS / "rotm.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)


def write_contract(root: Path, table: str = "sales", *, agreed: bool = True, channel: str = "session") -> dict:
    c = {"rotm_contract": 1, "table": table, "raw_sheet": f"raw_{table}", "excel_table": f"tbl_raw_{table}",
         "grain": "one row per line", "key": ["id"],
         "columns": [{"name": "id", "type": "text", "null_rule": "never"},
                     {"name": "amount", "type": "number", "null_rule": "allowed", "unit": "KRW"}],
         "expect": {"rows": {"min": 1, "max": 1000}}, "agreement": {"status": "proposed"}}
    if agreed:
        c["agreement"] = {"status": "agreed", "contract_sha256": modelfolder.contract_hash(c), "reply": "ok, agreed",
                          "by": "Max", "at": "2026-11-25T01:20:00+09:00", "channel": channel, "source_file": None,
                          "recorded_by": "rotm.py contract agree"}
    common.write_json(modelfolder.contract_path(root, table), c)
    return c


# ----------------------------------------------------------------------------- init and the marker

def test_init_creates_layout_and_a_valid_marker(tmp_path: Path) -> None:
    root = tmp_path / "sales"
    marker = modelfolder.init_model(root)
    for sub in modelfolder.LAYOUT:
        assert (root / sub).is_dir()
    on_disk = common.read_json(root / modelfolder.MARKER)
    assert on_disk == marker
    assert schemas.validate(on_disk, "marker") == []
    assert marker["name"] == "sales" and marker["created_by"] == "rotm.py init" and marker["product"] == "both"
    assert marker["spec"] == "model.spec.json" and marker["workbook"] == "model.xlsx"
    assert modelfolder.is_model_root(root) and modelfolder.read_marker(root) == marker


def test_init_is_idempotent_and_refuses_another_name(tmp_path: Path) -> None:
    root = tmp_path / "m"
    first = modelfolder.init_model(root, name="alpha")
    assert modelfolder.init_model(root, name="alpha") == first
    assert modelfolder.init_model(root) == first                  # no --name: keeps the folder's marker
    with pytest.raises(common.Refusal, match="already the model folder of 'alpha'"):
        modelfolder.init_model(root, name="beta")
    renamed = modelfolder.init_model(root, name="beta", force=True)
    assert renamed["name"] == "beta" and renamed["created"] == first["created"]


def test_init_refuses_a_folder_inside_another_model_and_a_file(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    modelfolder.init_model(outer)
    with pytest.raises(common.Refusal, match="lies inside the model folder"):
        modelfolder.init_model(outer / "raw" / "inner")
    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(common.Refusal, match="is a file"):
        modelfolder.init_model(f)
    with pytest.raises(common.Refusal, match="product"):
        modelfolder.init_model(tmp_path / "p", product="web")


def test_init_sanitises_a_folder_name_with_bad_characters(tmp_path: Path) -> None:
    root = tmp_path / "매출 model"
    assert modelfolder.init_model(root)["name"] == "매출 model"


def make_template(base: Path) -> Path:
    tpl = base / "tpl_budget"
    modelfolder.init_model(tpl, name="tpl_budget")
    write_contract(tpl, "gl", agreed=True, channel="example")
    md = tpl / "contracts" / "gl.contract.md"
    md.write_text("# Contract gl\n\nReply 'agreed' or say what to change.\nAgreed by Max on 2026-11-25: 'ok'\n",
                  encoding="utf-8")
    (tpl / "model.spec.json").write_text('{"rotm_spec": 1}', encoding="utf-8")
    common.write_json(tpl / "reports" / "status.json", {"rotm_status": 1, "workbooks": {}})
    return tpl


def test_init_from_a_template_folder_resets_agreements(tmp_path: Path) -> None:
    tpl = make_template(tmp_path)
    root = tmp_path / "new_model"
    marker = modelfolder.init_model(root, template=str(tpl))
    assert marker["template"] == "tpl_budget" and marker["reset_tables"] == ["gl"]
    assert marker["name"] == "new_model"
    assert (root / "model.spec.json").is_file()
    assert not (root / "reports" / "status.json").exists()          # reports are not copied
    c = common.read_json(root / "contracts" / "gl.contract.json")
    assert c["agreement"]["status"] == "proposed" and "copied from template" in c["agreement"]["note"]
    assert c["agreement"]["history"][0]["channel"] == "example"
    assert modelfolder.contract_state(root, "gl") == "proposed"
    md = (root / "contracts" / "gl.contract.md").read_text(encoding="utf-8")
    assert "Agreed by" not in md and "Not agreed: copied from template 'tpl_budget'" in md
    assert modelfolder.contract_state(tpl, "gl") == "agreed"        # the template itself is unchanged


def test_init_template_refuses_to_overwrite_and_names_unknown_templates(tmp_path: Path) -> None:
    tpl = make_template(tmp_path)
    root = tmp_path / "busy"
    root.mkdir()
    (root / "model.spec.json").write_text("{}", encoding="utf-8")
    with pytest.raises(common.Refusal, match="would overwrite"):
        modelfolder.init_model(root, template=str(tpl))
    with pytest.raises(common.Refusal, match="no template named 'no_such_template'"):
        modelfolder.init_model(tmp_path / "x", template="no_such_template")


def test_template_dirs_point_into_the_skill() -> None:
    for d in modelfolder.template_dirs():
        assert d.name == "templates" and d.is_dir()


def test_read_marker_refusals(tmp_path: Path) -> None:
    with pytest.raises(common.Refusal) as e:
        modelfolder.read_marker(tmp_path)
    assert "is not a model folder" in e.value.message and "rotm.py init" in e.value.fix
    (tmp_path / modelfolder.MARKER).write_text('{"rotm_model": 1}', encoding="utf-8")
    with pytest.raises(common.Refusal, match="not valid"):
        modelfolder.read_marker(tmp_path)


# ----------------------------------------------------------------------------- find_model_root

def test_find_model_root_walks_up_from_files_and_missing_paths(tmp_path: Path) -> None:
    root = tmp_path / "model"
    modelfolder.init_model(root)
    (root / "queries").mkdir(exist_ok=True)
    assert modelfolder.find_model_root(root) == root
    assert modelfolder.find_model_root(root / "queries" / "sales.sql") == root          # not yet written
    assert modelfolder.find_model_root(root / "a" / "b" / "c" / "new.xlsx") == root
    assert modelfolder.find_model_root(tmp_path) is None
    assert modelfolder.find_model_root(tmp_path / "other" / "x.xlsx") is None


def test_find_model_root_respects_max_up(tmp_path: Path) -> None:
    root = tmp_path / "model"
    modelfolder.init_model(root)
    deep = root / "1" / "2" / "3"
    deep.mkdir(parents=True)                 # an existing folder: the walk starts at the folder itself
    assert modelfolder.find_model_root(deep, max_up=2) is None
    assert modelfolder.find_model_root(deep, max_up=3) == root


def test_a_marker_with_another_version_is_not_a_model(tmp_path: Path) -> None:
    (tmp_path / modelfolder.MARKER).write_text('{"rotm_model": 2, "name": "x"}', encoding="utf-8")
    assert not modelfolder.is_model_root(tmp_path)
    (tmp_path / modelfolder.MARKER).write_text("not json", encoding="utf-8")
    assert not modelfolder.is_model_root(tmp_path)
    assert modelfolder.find_model_root(tmp_path) is None


@pytest.mark.skipif(os.name != "nt", reason="Git Bash paths exist on Windows only")
def test_find_model_root_accepts_git_bash_paths(tmp_path: Path) -> None:
    root = tmp_path / "model"
    modelfolder.init_model(root)
    posix = root.resolve().as_posix()
    bash = "/" + posix[0].lower() + posix[2:]
    assert modelfolder.find_model_root(bash + "/raw/sales.csv") == root.resolve()


# ----------------------------------------------------------------------------- contracts

def test_contract_states(tmp_path: Path) -> None:
    root = tmp_path / "m"
    modelfolder.init_model(root)
    assert modelfolder.contract_state(root, "sales") == "missing"
    c = write_contract(root, agreed=False)
    assert modelfolder.contract_state(root, "sales") == "proposed"
    c = write_contract(root, agreed=True)
    assert modelfolder.contract_state(root, "sales") == "agreed"
    c["grain"] = "one row per invoice"                         # edited after the agreement
    common.write_json(modelfolder.contract_path(root, "sales"), c)
    state, data, why = modelfolder.read_contract(root, "sales")
    assert state == "stale" and "edited after it was agreed" in why[0]
    modelfolder.contract_path(root, "sales").write_text("{bad", encoding="utf-8")
    assert modelfolder.contract_state(root, "sales") == "invalid"
    common.write_json(modelfolder.contract_path(root, "sales"), {"rotm_contract": 1})
    state, _, why = modelfolder.read_contract(root, "sales")
    assert state == "invalid" and any("table: is required" in w for w in why)


def test_contract_file_name_must_match_its_table(tmp_path: Path) -> None:
    root = tmp_path / "m"
    modelfolder.init_model(root)
    write_contract(root, "sales")
    modelfolder.contract_path(root, "sales").rename(modelfolder.contract_path(root, "orders"))
    state, _, why = modelfolder.read_contract(root, "orders")
    assert state == "invalid" and "named for 'orders'" in why[0]


def test_tables_lists_contract_files(tmp_path: Path) -> None:
    root = tmp_path / "m"
    modelfolder.init_model(root)
    write_contract(root, "target")
    write_contract(root, "sales")
    assert modelfolder.tables(root) == ["sales", "target"]
    assert modelfolder.tables(tmp_path / "none") == []


def test_contract_hash_ignores_the_agreement() -> None:
    a = {"table": "t", "agreement": {"status": "proposed"}}
    b = {"table": "t", "agreement": {"status": "agreed", "by": "Max"}}
    assert modelfolder.contract_hash(a) == modelfolder.contract_hash(b) == common.canonical_hash({"table": "t"})


# ----------------------------------------------------------------------------- status file

def test_status_records_steps_atomically(tmp_path: Path) -> None:
    root = tmp_path / "m"
    modelfolder.init_model(root)
    assert modelfolder.read_status(root) == {"rotm_status": 1, "workbooks": {}}
    modelfolder.record_step(root, "model.xlsx", "build", {"exit_code": 0, "sha256": "a" * 64, "errors": 0})
    modelfolder.record_step(root, str(root / "model.xlsx"), "audit", {"exit_code": 0, "strict": True})
    st = modelfolder.read_status(root)
    assert set(st["workbooks"]) == {"model.xlsx"}
    assert set(st["workbooks"]["model.xlsx"]) == {"build", "audit"}
    assert st["workbooks"]["model.xlsx"]["build"]["at"]
    assert schemas.validate(st, "status") == []
    assert not list((root / "reports").glob("*.lock"))
    with pytest.raises(ValueError, match="unknown step"):
        modelfolder.record_step(root, "model.xlsx", "publish", {})


def test_status_survives_a_damaged_file(tmp_path: Path) -> None:
    root = tmp_path / "m"
    modelfolder.init_model(root)
    modelfolder.status_path(root).write_text("{damaged", encoding="utf-8")
    assert modelfolder.read_status(root)["workbooks"] == {}
    modelfolder.record_step(root, "model.xlsx", "check", {"exit_code": 3, "recalculated": False})
    assert list((root / "reports").glob("status.json.bad_*"))
    assert modelfolder.read_status(root)["workbooks"]["model.xlsx"]["check"]["exit_code"] == 3


def test_status_concurrent_writers_lose_nothing(tmp_path: Path) -> None:
    root = tmp_path / "m"
    modelfolder.init_model(root)
    steps = list(modelfolder.STEPS)
    errors: list[BaseException] = []

    def worker(step: str) -> None:
        try:
            modelfolder.record_step(root, "model.xlsx", step, {"exit_code": 0})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(s,)) for s in steps]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert set(modelfolder.read_status(root)["workbooks"]["model.xlsx"]) == set(steps)


# ----------------------------------------------------------------------------- subcommands

def test_cli_init_and_status(tmp_path: Path) -> None:
    root = tmp_path / "cli_model"
    proc = cli("init", str(root), "--name", "demo")
    assert proc.returncode == 0, proc.stderr
    assert "created model folder" in proc.stdout and "contract propose" in proc.stdout
    again = cli("init", str(root), "--name", "demo")
    assert again.returncode == 0 and "already exists" in again.stdout
    write_contract(root, "sales")
    (root / "model.xlsx").write_bytes(b"PK fake")
    modelfolder.record_step(root, "model.xlsx", "build", {"exit_code": 0, "sha256": common.file_sha256(root / "model.xlsx")})
    st = cli("status", str(root), "--json", "-")
    assert st.returncode == 0, st.stderr
    data = json.loads(st.stdout)
    assert data["marker"]["name"] == "demo"
    assert data["contracts"][0]["state"] == "agreed" and data["contracts"][0]["by"] == "Max"
    assert data["workbooks"][0]["steps"]["build"]["current"] is True
    assert "sales  agreed  by Max" in st.stderr


def test_cli_init_refusal_exit_2(tmp_path: Path) -> None:
    root = tmp_path / "m"
    modelfolder.init_model(root, name="one")
    proc = cli("init", str(root), "--name", "two")
    assert proc.returncode == 2 and "Fix:" in proc.stderr and "Traceback" not in proc.stderr
    proc = cli("status", str(tmp_path / "nothing"))
    assert proc.returncode == 2 and "is not a model folder" in proc.stderr


def test_cli_list_templates() -> None:
    proc = cli("init", "unused", "--list-templates")
    assert proc.returncode == 0


def test_modelfolder_is_stdlib_only() -> None:
    code = (f"import sys; sys.path.insert(0, {str(SCRIPTS)!r}); from lib import modelfolder; "
            "bad = [m for m in sys.modules if m.split('.')[0] in ('openpyxl', 'pandas')]; sys.exit(1 if bad else 0)")
    assert subprocess.run([sys.executable, "-I", "-B", "-c", code], timeout=60).returncode == 0


def test_init_from_a_template_copies_no_built_workbook(tmp_path: Path) -> None:
    """The new model is built from its own data after its contracts are agreed (integrator, live test s2)."""
    tpl = make_template(tmp_path)
    (tpl / "model.xlsx").write_bytes(b"PK\x03\x04 not a real workbook")
    (tpl / "model.manifest.json").write_text("{}", encoding="utf-8")
    root = tmp_path / "from_tpl"
    modelfolder.init_model(root, template=str(tpl))
    assert (root / "model.spec.json").is_file()
    assert not (root / "model.xlsx").exists() and not (root / "model.manifest.json").exists()


def test_record_step_takes_a_path_relative_to_the_current_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "models" / "m"
    modelfolder.init_model(root)
    (root / "model.xlsx").write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    modelfolder.record_step(root, "models/m/model.xlsx", "audit", {"exit_code": 0})
    modelfolder.record_step(root, "model.xlsx", "build", {"exit_code": 0})
    assert set(modelfolder.read_status(root)["workbooks"]) == {"model.xlsx"}


def test_cli_list_templates_needs_no_folder_and_init_without_one_is_refused() -> None:
    assert cli("init", "--list-templates").returncode == 0
    proc = cli("init")
    assert proc.returncode == 2 and "no model folder given" in (proc.stdout + proc.stderr)
