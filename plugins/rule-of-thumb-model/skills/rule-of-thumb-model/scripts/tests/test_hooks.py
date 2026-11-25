"""Tests of the four plugin hooks (design section 13.7; plan F16). Owner: M6.

Each hook is run the way Claude Code runs it: a fresh `python -I -X utf8 -B PLUGIN/hooks/<hook>.py` process
with the hook payload on stdin (payload shapes in data/hooks/, from Claude Code 2.1.331's hook input schema).
The wiring tests run the exact command lines of hooks.json under Git Bash, as Claude Code does on Windows.

Per hook: an allow case and a refuse case inside a model folder; the outside case (no model folder: a workbook
write, an SQL write and a session end give exit 0 with empty stdout and stderr); the failure case
(ROTM_HOOK_FAIL_TEST: exit 1, one stderr line naming the hook and "let through", no traceback). The finish
check refuses once, lets the next stop through, and refuses again after a new write.

Skipped as a whole when PLUGIN/hooks is absent (the skill copied alone, F02). The audit-on-write tests use a
stand-in for rotm.py (ROTM_HOOK_ROTM) so that they do not depend on the audit module; one test runs the real
`rotm.py audit` and is skipped while that module is not written.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from lib import common, modelfolder

SKILL = Path(__file__).resolve().parents[2]
PLUGIN = SKILL.parents[1]
HOOKS = PLUGIN / "hooks"
DATA = Path(__file__).resolve().parent / "data" / "hooks"
HOOK_NAMES = ("contract_gate", "raw_guard", "audit_on_write", "finish_check")

pytestmark = pytest.mark.skipif(not (HOOKS / "hooks.json").is_file(),
                                reason="PLUGIN/hooks is absent (the skill is used without its plugin)")


# ----------------------------------------------------------------------------- running a hook

@dataclass
class Out:
    code: int
    stdout: str
    stderr: str

    def json(self) -> dict:
        return json.loads(self.stdout)


def run_hook(name: str, payload: dict, *, env: dict | None = None, cwd: Path | None = None) -> Out:
    e = dict(os.environ)
    e.update(env or {})
    e["PYTHONUTF8"] = "1"
    proc = subprocess.run([sys.executable, "-I", "-X", "utf8", "-B", str(HOOKS / f"{name}.py")],
                          input=json.dumps(payload).encode("utf-8"), capture_output=True, env=e,
                          cwd=str(cwd or payload.get("cwd") or Path.cwd()), timeout=120)
    return Out(proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace"))


def template(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


_COUNTER = [0]


def _tuid() -> str:
    _COUNTER[0] += 1
    return f"toolu_test_{_COUNTER[0]:05d}"


def pre(tool: str, cwd: Path, *, sid: str = "sess-1", tuid: str | None = None, transcript: Path | None = None,
        **tool_input: object) -> dict:
    p = template("payload_pre_write.json" if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit")
                 else "payload_pre_bash.json")
    p.update(session_id=sid, cwd=str(cwd), tool_name=tool, tool_input=dict(tool_input), tool_use_id=tuid or _tuid(),
             transcript_path=str(transcript) if transcript else str(cwd / "no_transcript.jsonl"))
    return p


def bash(command: str, cwd: Path, **kw: object) -> dict:
    return pre("Bash", cwd, command=command, description="test", **kw)


def pwsh(command: str, cwd: Path, **kw: object) -> dict:
    return pre("PowerShell", cwd, command=command, **kw)


def post(of: dict, *, failure: bool = False) -> dict:
    p = template("payload_post_failure_bash.json" if failure else "payload_post_bash.json")
    for k in ("session_id", "cwd", "tool_name", "tool_input", "tool_use_id", "transcript_path"):
        p[k] = copy.deepcopy(of[k])
    return p


def stop(cwd: Path, *, sid: str = "sess-1", active: bool = False, last: str | None = "Done.") -> dict:
    p = template("payload_stop.json")
    p.update(session_id=sid, cwd=str(cwd), stop_hook_active=active,
             transcript_path=str(cwd / "no_transcript.jsonl"))
    if last is None:
        p.pop("last_assistant_message", None)
    else:
        p["last_assistant_message"] = last
    return p


def assert_silent(out: Out) -> None:
    assert out.code == 0, out
    assert out.stdout == "" and out.stderr == "", out


def assert_refused(out: Out, *words: str) -> None:
    assert out.code == 2, out
    assert out.stdout == "", out
    for w in words:
        assert w in out.stderr, (w, out.stderr)
    assert "Traceback" not in out.stderr


# ----------------------------------------------------------------------------- fixtures

def write_contract(root: Path, table: str, *, agreed: bool) -> dict:
    c = {"rotm_contract": 1, "table": table, "raw_sheet": f"raw_{table}", "excel_table": f"tbl_raw_{table}",
         "grain": "one row per line", "key": ["id"],
         "columns": [{"name": "id", "type": "text", "null_rule": "never"},
                     {"name": "amount", "type": "number", "null_rule": "allowed", "unit": "KRW"}],
         "expect": {"rows": {"min": 1, "max": 1000}}, "agreement": {"status": "proposed"}}
    if agreed:
        c["agreement"] = {"status": "agreed", "contract_sha256": modelfolder.contract_hash(c), "reply": "ok, agreed",
                          "by": "Max", "at": "2026-11-25T01:20:00+09:00", "channel": "session", "source_file": None,
                          "recorded_by": "rotm.py contract agree"}
    common.write_json(modelfolder.contract_path(root, table), c)
    return c


def make_workbook(path: Path, value: int = 1) -> None:
    try:
        import openpyxl
    except ImportError:                                  # pragma: no cover - openpyxl is present on the build PC
        path.write_bytes(b"PK\x03\x04 not a real workbook " + str(value).encode())
        return
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "calc"
    ws["A1"] = "value"
    ws["B1"] = value
    wb.save(path)


def touch_later(path: Path, value: int) -> None:
    """Rewrite a workbook so that its (mtime_ns, size) differ from before."""
    before = path.stat().st_mtime_ns if path.exists() else 0
    make_workbook(path, value)
    if path.stat().st_mtime_ns == before:
        os.utime(path, ns=(before + 1_000_000, before + 1_000_000))


@pytest.fixture
def model(tmp_path: Path) -> Path:
    """A model folder: marker, contract 'sales' proposed, contract 'target' agreed, a raw snapshot, a workbook."""
    root = tmp_path / "sales_model"
    modelfolder.init_model(root, name="sales_model")
    write_contract(root, "sales", agreed=False)
    write_contract(root, "target", agreed=True)
    (root / "raw" / "sales.csv").write_text("id,amount\nA1,10\n", encoding="utf-8")
    make_workbook(root / "model.xlsx")
    return root


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A folder with no model anywhere above it inside the test's temporary folder."""
    d = tmp_path / "plain_work"
    d.mkdir()
    (d / "queries").mkdir()
    (d / "raw").mkdir()
    (d / "raw" / "sales.csv").write_text("id\n1\n", encoding="utf-8")
    make_workbook(d / "book.xlsx")
    assert modelfolder.find_model_root(d) is None
    return d


ROTM = f'python -P -X utf8 -B "{(SKILL / "scripts" / "rotm.py").as_posix()}"'


# ============================================================================= hooks.json

def test_hooks_json_wires_four_hooks_to_existing_scripts() -> None:
    data = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
    hooks = data["hooks"]
    assert set(hooks) == {"PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop"}
    seen = []
    for event, groups in hooks.items():
        for g in groups:
            for h in g["hooks"]:
                assert h["type"] == "command" and isinstance(h["timeout"], int)
                cmd = h["command"]
                assert cmd.startswith("PYTHONUTF8=1 python -I -X utf8 -B \"${CLAUDE_PLUGIN_ROOT}/hooks/")
                script = cmd.split("/hooks/")[1].split('"')[0]
                assert (HOOKS / script).is_file()
                assert cmd.endswith(f"run_hook.sh\" $? {script}")
                seen.append((event, g.get("matcher"), script))
    assert ("PreToolUse", "Write|Edit|MultiEdit|Bash|PowerShell", "contract_gate.py") in seen
    assert ("PreToolUse", "Write|Edit|MultiEdit|NotebookEdit|Bash|PowerShell", "raw_guard.py") in seen
    assert ("PostToolUse", "Bash|PowerShell", "audit_on_write.py") in seen
    assert ("PostToolUseFailure", "Bash|PowerShell", "audit_on_write.py") in seen
    assert ("Stop", None, "finish_check.py") in seen
    assert "rotm_model.json" in data["description"] and "rotm.py init" in data["description"]


def test_hook_library_imports_under_isolated_python() -> None:
    """-I hides the user site-packages (where openpyxl lives): the hooks must need the standard library only."""
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import _hooklib, contract_gate, raw_guard, "
            "audit_on_write, finish_check; print('openpyxl' in sys.modules)")
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(HOOKS)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False"


# ============================================================================= outside a model folder (F16 b)

OUTSIDE_CASES = [
    ("Write", {"file_path": "queries/sales.sql", "content": "SELECT 1"}),
    ("Write", {"file_path": "book.xlsx", "content": "x"}),
    ("Write", {"file_path": "raw/sales.csv", "content": "id\n2\n"}),
    ("Edit", {"file_path": "raw/sales.csv", "old_string": "1", "new_string": "2"}),
    ("Bash", {"command": "echo 'SELECT 1' > queries/sales.sql"}),
    ("Bash", {"command": "python -c \"import openpyxl; wb=openpyxl.load_workbook('book.xlsx'); wb.save('book.xlsx')\""}),
    ("Bash", {"command": "cp other.csv raw/sales.csv"}),
    ("PowerShell", {"command": "Set-Content -Path queries\\sales.sql -Value 'SELECT 1'"}),
    ("PowerShell", {"command": "$xl = New-Object -ComObject Excel.Application; $wb = $xl.Workbooks.Open('book.xlsx'); $wb.Save()"}),
]


@pytest.mark.parametrize("hook", ["contract_gate", "raw_guard"])
@pytest.mark.parametrize("tool,tool_input", OUTSIDE_CASES, ids=[f"{t}-{i}" for i, (t, _) in enumerate(OUTSIDE_CASES)])
def test_outside_pre_hooks_say_nothing(hook: str, tool: str, tool_input: dict, outside: Path,
                                       _isolated_state: Path) -> None:
    ti = dict(tool_input)
    if "file_path" in ti:
        ti["file_path"] = str(outside / ti["file_path"])
    assert_silent(run_hook(hook, pre(tool, outside, **ti)))
    assert not (_isolated_state / "sessions").exists()


def test_outside_workbook_write_and_session_end_say_nothing(outside: Path, _isolated_state: Path) -> None:
    payload = bash("python -c \"import openpyxl; wb=openpyxl.load_workbook('book.xlsx'); wb.save('book.xlsx')\"",
                   outside)
    for hook in ("contract_gate", "raw_guard"):
        assert_silent(run_hook(hook, payload))
    touch_later(outside / "book.xlsx", 7)                    # the command ran and wrote the workbook
    assert_silent(run_hook("audit_on_write", post(payload)))
    assert_silent(run_hook("audit_on_write", post(payload, failure=True)))
    assert_silent(run_hook("finish_check", stop(outside)))
    assert_silent(run_hook("finish_check", stop(outside, last=None)))
    assert not (_isolated_state / "sessions").exists()


@pytest.mark.parametrize("hook", HOOK_NAMES)
def test_outside_forced_failure_is_silent(hook: str, outside: Path) -> None:
    payload = stop(outside) if hook == "finish_check" else bash("echo x > q.sql", outside)
    if hook == "audit_on_write":
        payload = post(payload)
    assert_silent(run_hook(hook, payload, env={"ROTM_HOOK_FAIL_TEST": hook}))


# ============================================================================= contract_gate

def test_gate_allows_sql_for_an_agreed_table(model: Path) -> None:
    assert_silent(run_hook("contract_gate", pre("Write", model, file_path=str(model / "queries" / "target.sql"),
                                                content="SELECT id, amount FROM t")))
    assert_silent(run_hook("contract_gate", bash("echo 'SELECT 1' > queries/target.sql", model)))
    assert_silent(run_hook("contract_gate", pre("Write", model, file_path=str(model / "queries" / "target.pq.m"),
                                                content="let Source = 1 in Source")))


def test_gate_refuses_sql_for_a_proposed_table(model: Path) -> None:
    out = run_hook("contract_gate", pre("Write", model, file_path=str(model / "queries" / "sales.sql"),
                                        content="SELECT 1"))
    assert_refused(out, "rule-of-thumb-model contract gate: queries/sales.sql is SQL for raw table 'sales'",
                   "proposed", "contracts/sales.contract.md", "contract agree", "--table sales", "--reply")
    assert len(out.stderr.strip().splitlines()) == 1


def test_gate_refuses_sql_for_a_table_with_no_contract(model: Path) -> None:
    out = run_hook("contract_gate", pre("Write", model, file_path=str(model / "queries" / "orders.sql"),
                                        content="SELECT 1"))
    assert_refused(out, "'orders'", "no contract yet", "contract propose")


def test_gate_refuses_sql_for_a_stale_contract(model: Path) -> None:
    path = modelfolder.contract_path(model, "target")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["grain"] = "one row per invoice"                   # edited after it was agreed
    path.write_text(json.dumps(data), encoding="utf-8")
    assert modelfolder.contract_state(model, "target") == "stale"
    assert_refused(run_hook("contract_gate", bash("echo 'SELECT 1' > queries/target.sql", model)),
                   "edited after it was agreed")


def test_gate_refuses_sql_outside_queries(model: Path) -> None:
    assert_refused(run_hook("contract_gate", pre("Write", model, file_path=str(model / "extract.sql"),
                                                 content="SELECT 1")),
                   "extract.sql", "queries/<table>.sql")
    assert_refused(run_hook("contract_gate", pre("Write", model, file_path=str(model / "queries" / "sales.m"),
                                                 content="let x = 1 in x")),
                   "queries/<table>.pq.m")


@pytest.mark.parametrize("command", [
    "echo 'SELECT 1' > queries/sales.sql",
    "echo 'SELECT 1' >> \"queries/sales.sql\"",
    "printf 'SELECT 1' | tee queries/sales.sql",
    "cp /tmp/draft.sql queries/sales.sql",
    "cp draft.sql queries/",
    "mv draft.sql queries/sales.sql",
    "cat > queries/sales.sql <<'EOF'\nSELECT a FROM t WHERE a > 0\nEOF",
    "python -c \"open('queries/sales.sql', 'w').write('SELECT 1')\"",
    "bash -c \"echo 'SELECT 1' > queries/sales.sql\"",
    "sed -i 's/1/2/' queries/sales.sql",
])
def test_gate_refuses_bash_writes_of_unagreed_sql(model: Path, command: str) -> None:
    (model / "draft.sql").write_text("SELECT 1", encoding="utf-8")
    out = run_hook("contract_gate", bash(command, model))
    assert out.code == 2, (command, out)
    assert "contract gate" in out.stderr and "Traceback" not in out.stderr


@pytest.mark.parametrize("command", [
    "Set-Content -Path queries\\sales.sql -Value 'SELECT 1'",
    "'SELECT 1' | Out-File queries/sales.sql",
    "Copy-Item C:\\temp\\draft.sql -Destination queries\\sales.sql",
    "New-Item -ItemType File -Path queries -Name sales.sql",
    "Add-Content queries/sales.sql 'SELECT 1'",
    "[IO.File]::WriteAllText('queries/sales.sql', 'SELECT 1')",
    "@'\nSELECT a FROM t WHERE a > 0\n'@ | Set-Content queries/sales.sql",
])
def test_gate_refuses_powershell_writes_of_unagreed_sql(model: Path, command: str) -> None:
    out = run_hook("contract_gate", pwsh(command, model))
    assert out.code == 2, (command, out)
    assert "contract gate" in out.stderr


def test_gate_follows_cd_into_a_model_folder(model: Path, outside: Path) -> None:
    out = run_hook("contract_gate", bash(f'cd "{model.as_posix()}" && echo "SELECT 1" > queries/sales.sql', outside))
    assert_refused(out, "queries/sales.sql")


def test_gate_lets_rotm_commands_through(model: Path) -> None:
    for cmd in (f'{ROTM} land "{model.as_posix()}" --table sales --csv data.csv',
                f'{ROTM} contract propose . --table sales --csv data.csv --force',
                f'{ROTM} status . && cat queries/target.sql',
                "cat queries/sales.sql", "ls queries"):
        assert_silent(run_hook("contract_gate", bash(cmd, model)))


def test_gate_refuses_editing_an_agreed_contract(model: Path) -> None:
    path = modelfolder.contract_path(model, "target")
    out = run_hook("contract_gate", pre("Edit", model, file_path=str(path), old_string='"one row per line"',
                                        new_string='"one row per invoice"'))
    assert_refused(out, "contracts/target.contract.json", "agreed by Max", "contract propose", "--force")
    assert_refused(run_hook("contract_gate", bash(f"echo '{{}}' > {path.as_posix()}", model)), "agreed by Max")


def test_gate_allows_editing_a_proposed_contract(model: Path) -> None:
    path = modelfolder.contract_path(model, "sales")
    assert_silent(run_hook("contract_gate", pre("Edit", model, file_path=str(path), old_string='"one row per line"',
                                                new_string='"one row per invoice line"')))


def test_gate_refuses_a_text_edit_that_marks_a_contract_agreed(model: Path) -> None:
    path = modelfolder.contract_path(model, "sales")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["agreement"] = {"status": "agreed", "reply": "ok", "by": "Max"}
    assert_refused(run_hook("contract_gate", pre("Write", model, file_path=str(path), content=json.dumps(data))),
                   "would mark the contract agreed", "contract agree")
    assert_refused(run_hook("contract_gate", pre("Edit", model, file_path=str(path), old_string='"proposed"',
                                                 new_string='"agreed"')),
                   "would mark the contract agreed")
    code = "import json; p='contracts/sales.contract.json'; c=json.load(open(p)); " \
           "c['agreement']['status']='agreed'; open('contracts/sales.contract.json','w').write(json.dumps(c))"
    assert_refused(run_hook("contract_gate", bash(f'python -c "{code}"', model)), "would mark the contract agreed")


# --------------------------------------------------------------------- agreement guard (contract agree)

def transcript(tmp_path: Path) -> Path:
    t = tmp_path / "transcript.jsonl"
    shutil.copy(DATA / "transcript_agree.jsonl", t)
    return t


def test_agree_with_the_users_own_words_is_allowed(model: Path, tmp_path: Path) -> None:
    cmd = f'{ROTM} contract agree . --table sales --reply "ok, agreed. amount is net of VAT." --by Max'
    assert_silent(run_hook("contract_gate", bash(cmd, model, transcript=transcript(tmp_path))))


def test_agree_with_words_the_user_did_not_type_is_refused(model: Path, tmp_path: Path) -> None:
    cmd = f'{ROTM} contract agree . --table sales --reply "agreed, go ahead" --by Max'
    out = run_hook("contract_gate", bash(cmd, model, transcript=transcript(tmp_path)))
    assert_refused(out, "not in any message from the user in this session", "pass their words exactly")


def _timed_transcript(tmp_path: Path, typed_at: str) -> Path:
    t = tmp_path / "timed.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "agreed"},
                             "timestamp": typed_at, "uuid": "u1"}) + "\n", encoding="utf-8")
    return t


def test_agree_with_words_typed_before_this_version_is_refused(model: Path, tmp_path: Path) -> None:
    """Live test 1: an agreement belongs to the contract version it was given for (proposed_at)."""
    path = modelfolder.contract_path(model, "sales")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["proposed_at"] = "2026-11-25T05:00:00+09:00"                  # 2026-11-24T20:00:00Z
    common.write_json(path, data)
    cmd = f'{ROTM} contract agree . --table sales --reply "agreed" --by Max'
    out = run_hook("contract_gate", bash(cmd, model, transcript=_timed_transcript(tmp_path, "2026-11-24T19:59:00Z")))
    assert_refused(out, "typed before this version of the contract was proposed", "new reply")
    out = run_hook("contract_gate", bash(cmd, model, transcript=_timed_transcript(tmp_path, "2026-11-24T20:01:00Z")))
    assert_silent(out)


def test_agree_ignores_tool_results_and_skill_text(model: Path, tmp_path: Path) -> None:
    t = transcript(tmp_path)
    for reply in ("yes, all fine", "Record the reply, for example", "Reply 'agreed' or say what to change."):
        cmd = f'{ROTM} contract agree . --table sales --reply "{reply}" --by Max'
        assert run_hook("contract_gate", bash(cmd, model, transcript=t)).code == 2, reply
    # the reply in the skill text (isMeta) alone does not count
    lines = [ln for ln in t.read_text(encoding="utf-8").splitlines() if '"uuid":"u4"' not in ln]
    t.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = f'{ROTM} contract agree . --table sales --reply "ok, agreed. amount is net of VAT." --by Max'
    assert run_hook("contract_gate", bash(cmd, model, transcript=t)).code == 2


def test_agree_ignores_entries_the_user_did_not_type(model: Path, tmp_path: Path) -> None:
    """Task notifications, local command output and hook feedback are user-role entries in a transcript, but
    not the user's words (measured on Claude Code 2.1.331 transcripts)."""
    t = transcript(tmp_path)
    for reply in ("approved by Max", "signed off by Max", "Max approves"):
        cmd = f'{ROTM} contract agree . --table sales --reply "{reply}" --by Max'
        assert run_hook("contract_gate", bash(cmd, model, transcript=t)).code == 2, reply
    # arguments the user typed after a slash command are the user's words
    cmd = f'{ROTM} contract agree . --table sales --reply "the columns are right" --by Max'
    assert_silent(run_hook("contract_gate", bash(cmd, model, transcript=t)))


def test_agree_is_let_through_when_the_transcript_cannot_be_read(model: Path) -> None:
    cmd = f'{ROTM} contract agree . --table sales --reply "agreed" --by Max'
    assert_silent(run_hook("contract_gate", bash(cmd, model)))           # transcript path does not exist


def test_agree_from_a_task_file_is_left_to_rotm(model: Path, tmp_path: Path) -> None:
    cmd = (f'{ROTM} contract agree . --table sales --reply "agreed" --by Max --channel task-file '
           '--source-file task.md')
    assert_silent(run_hook("contract_gate", bash(cmd, model, transcript=transcript(tmp_path))))


def test_agree_reply_with_equals_sign_form(model: Path, tmp_path: Path) -> None:
    ok = f'{ROTM} contract agree . --table sales --reply="ok, agreed. amount is net of VAT." --by Max'
    assert_silent(run_hook("contract_gate", bash(ok, model, transcript=transcript(tmp_path))))
    bad = f'{ROTM} contract agree . --table sales --reply="fine by me" --by Max'
    assert run_hook("contract_gate", bash(bad, model, transcript=transcript(tmp_path))).code == 2


# ============================================================================= raw_guard

def test_guard_refuses_text_tool_edits_of_raw_snapshots(model: Path) -> None:
    raw = model / "raw" / "sales.csv"
    for payload in (pre("Write", model, file_path=str(raw), content="id,amount\nA1,99\n"),
                    pre("Edit", model, file_path=str(raw), old_string="10", new_string="99"),
                    pre("MultiEdit", model, file_path=str(raw), edits=[{"old_string": "10", "new_string": "99"}])):
        out = run_hook("raw_guard", payload)
        assert_refused(out, "rule-of-thumb-model raw-sheet guard: raw/sales.csv", "landed snapshot of raw table 'sales'",
                       "land", "--table sales")


def test_guard_refuses_text_tool_edits_of_workbooks(model: Path) -> None:
    out = run_hook("raw_guard", pre("Write", model, file_path=str(model / "model.xlsx"), content="x"))
    assert_refused(out, "model.xlsx", "a text tool cannot edit it")
    out = run_hook("raw_guard", pre("NotebookEdit", model, notebook_path=str(model / "raw" / "x.ipynb"),
                                    new_source="1"))
    assert_refused(out, "raw/x.ipynb")


def test_guard_allows_other_text_files_in_the_model_folder(model: Path) -> None:
    for name in ("notes.md", "model.spec.json", "queries/target.sql"):
        assert_silent(run_hook("raw_guard", pre("Write", model, file_path=str(model / name), content="{}")))


@pytest.mark.parametrize("command", [
    "cp new.csv raw/sales.csv",
    "cp new.csv raw/",
    "mv new.csv raw/sales.csv",
    "echo 'A2,5' >> raw/sales.csv",
    "sed -i 's/10/99/' raw/sales.csv",
    "python -c \"import pandas as pd; pd.DataFrame().to_csv('raw/sales.csv')\"",
])
def test_guard_refuses_bash_writes_into_raw(model: Path, command: str) -> None:
    (model / "new.csv").write_text("id,amount\n", encoding="utf-8")
    out = run_hook("raw_guard", bash(command, model))
    assert out.code == 2, (command, out)
    assert "raw-sheet guard: raw/" in out.stderr and "land" in out.stderr


@pytest.mark.parametrize("command", [
    "Copy-Item new.csv raw\\sales.csv",
    "Get-Content new.csv | Set-Content raw\\sales.csv",
    "Import-Csv new.csv | Export-Csv -Path raw/sales.csv -NoTypeInformation",
    "Move-Item -Path new.csv -Destination raw\\",
])
def test_guard_refuses_powershell_writes_into_raw(model: Path, command: str) -> None:
    out = run_hook("raw_guard", pwsh(command, model))
    assert out.code == 2, (command, out)
    assert "raw-sheet guard" in out.stderr


@pytest.mark.parametrize("command", [
    "python -c \"import openpyxl; wb = openpyxl.load_workbook('model.xlsx'); wb['calc']['B1'] = 5; wb.save('model.xlsx')\"",
    "python - <<'EOF'\nimport openpyxl\nwb = openpyxl.load_workbook('model.xlsx')\nwb.save('model.xlsx')\nEOF",
    "python -c \"import pandas as pd; pd.DataFrame({'a': [1]}).to_excel('model.xlsx')\"",
    "cp /tmp/edited.xlsx model.xlsx",
    "echo x > model.xlsx",
])
def test_guard_refuses_bash_workbook_writes(model: Path, command: str) -> None:
    out = run_hook("raw_guard", bash(command, model))
    assert_refused(out, "raw-sheet guard: this command writes model.xlsx outside rotm.py")


def test_guard_refuses_a_python_script_that_saves_the_workbook(model: Path, outside: Path) -> None:
    script = outside / "edit.py"
    script.write_text("import openpyxl\n"
                      f"wb = openpyxl.load_workbook(r'{model / 'model.xlsx'}')\n"
                      "wb['calc']['B1'] = 2\n"
                      f"wb.save(r'{model / 'model.xlsx'}')\n", encoding="utf-8")
    assert_refused(run_hook("raw_guard", bash(f'python "{script.as_posix()}"', outside)), "model.xlsx")
    script2 = outside / "edit2.py"
    script2.write_text("import sys, openpyxl\nwb = openpyxl.load_workbook(sys.argv[1])\nwb.save(sys.argv[1])\n",
                       encoding="utf-8")
    assert_refused(run_hook("raw_guard", bash(f'python edit2.py "{(model / "model.xlsx").as_posix()}"', outside)),
                   "model.xlsx")


def test_guard_refuses_excel_com_saves_in_powershell(model: Path) -> None:
    wb = str(model / "model.xlsx")
    cmd = (f"$xl = New-Object -ComObject Excel.Application\n$wb = $xl.Workbooks.Open('{wb}')\n"
           "$wb.Worksheets.Item(1).Range('B1').Value2 = 5\n$wb.Save()\n$xl.Quit()")
    assert_refused(run_hook("raw_guard", pwsh(cmd, model)), "model.xlsx", "'.Save('")
    assert_refused(run_hook("raw_guard", pwsh(f"Copy-Item C:\\temp\\x.xlsx -Destination '{wb}'", model)),
                   "model.xlsx")


@pytest.mark.parametrize("command", [
    f"{ROTM} build model.spec.json --force",
    f"{ROTM} theme model.xlsx --in-place",
    f"{ROTM} check . && {ROTM} audit model.xlsx --strict",
    f'python -P -X utf8 -B "{(SKILL / "scripts" / "formula_audit.py").as_posix()}" model.xlsx --strict',
    "python -c \"import openpyxl; wb = openpyxl.load_workbook('model.xlsx', data_only=True); print(wb['calc']['B1'].value)\"",
    "cp model.xlsx /tmp/model_copy.xlsx",
    "cp model.xlsx C:/temp/copy.xlsx && python -c \"import openpyxl; wb=openpyxl.load_workbook('C:/temp/copy.xlsx'); wb.save('C:/temp/copy.xlsx')\"",
    "cat raw/sales.csv | head -5",
    "wc -l raw/sales.csv",
    "ls -la",
])
def test_guard_allows_rotm_and_reads(model: Path, command: str) -> None:
    assert_silent(run_hook("raw_guard", bash(command, model)))


def test_guard_records_workbook_signatures_before_the_call(model: Path, _isolated_state: Path) -> None:
    payload = bash(f"{ROTM} build model.spec.json --force", model, sid="sess-pre")
    assert_silent(run_hook("raw_guard", payload))
    rec = json.loads((_isolated_state / "sessions" / "sess-pre.json").read_text(encoding="utf-8"))
    pre_rec = rec["pre"][payload["tool_use_id"]]
    wb = model / "model.xlsx"
    sig = [wb.stat().st_mtime_ns, wb.stat().st_size]
    assert [v["sig"] for v in pre_rec["workbooks"].values()] == [sig]
    assert list(rec["models"]) == [model.as_posix()] or Path(list(rec["models"])[0]) == model


# ============================================================================= audit_on_write

# v5 (A-aera-18): the state words when no passing check or recalc of the current file is on record
NOT_VERIFIED = "the audit read the saved values; recalculation was not verified by this hook"

FAKE_ROTM = r'''
import json, os, sys
args = sys.argv[1:]
log = os.environ.get("FAKE_ROTM_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(" ".join(args) + "\n")
mode = os.environ.get("FAKE_ROTM_MODE", "clean")
if mode == "crash":
    sys.stderr.write("rotm.py audit: not implemented yet (audit.run_audit)\n")
    sys.exit(4)
if mode == "slow":
    import time
    time.sleep(30)
findings = [{"rule": "V-NOT-RECALC", "severity": "info", "sheet": "calc", "cell": None,
             "message": "formulas with no cached values", "fix": "run rotm.py check"}]
if mode == "findings":
    findings += [
        {"rule": "F-DEPTH", "severity": "error", "sheet": "calc", "cell": "D7", "count": 48,
         "message": "nesting depth 3 is over the limit 2 (IFERROR > INDEX > MATCH)",
         "fix": "move MATCH($B7,inputs!$B$10:$B$13,0) into a helper cell and reference that cell"},
        {"rule": "F-LITERAL", "severity": "warning", "sheet": "calc", "cell": "E9",
         "message": "the number 1.1 is typed into the formula", "fix": "put the number in an input cell"},
        {"rule": "S-FONT", "severity": "warning", "sheet": "inputs", "cell": "B4", "count": 3,
         "message": "font Calibri, the dictionary says Arial", "fix": "rotm.py theme"}]
e = sum(f["severity"] == "error" for f in findings)
w = sum(f["severity"] == "warning" for f in findings)
report = {"rotm_report": 1, "tool": "rule-of-thumb-model", "version": "0.1.0", "command": "audit",
          "target": args[1], "at": "2026-11-25T02:10:04+09:00", "strict": True, "exit_code": 1 if e + w else 0,
          "status": "fail" if e + w else "pass", "summary": {"errors": e, "warnings": w, "info": 1},
          "findings": findings, "notes": [], "verified": {"recalculated": False, "rendered": False}, "data": {}}
sys.stdout.write(json.dumps(report) + "\n")
sys.exit(1 if e + w else 0)
'''


@pytest.fixture
def fake_rotm(tmp_path: Path) -> tuple[dict, Path]:
    script = tmp_path / "fake_rotm.py"
    script.write_text(FAKE_ROTM, encoding="utf-8")
    log = tmp_path / "fake_rotm.log"
    return {"ROTM_HOOK_ROTM": str(script), "FAKE_ROTM_LOG": str(log)}, log


def build_cycle(model: Path, env: dict, *, sid: str = "sess-1", write: bool = True, failure: bool = False,
                command: str | None = None) -> Out:
    """raw_guard before the call, the call writes model.xlsx, audit_on_write after it."""
    payload = bash(command or f"{ROTM} build model.spec.json --force", model, sid=sid)
    assert_silent(run_hook("raw_guard", payload, env=env))
    if write:
        touch_later(model / "model.xlsx", int(time.time() * 1000) % 100000)
    return run_hook("audit_on_write", post(payload, failure=failure), env=env)


def test_audit_on_write_clean_workbook_gives_context(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, log = fake_rotm
    out = build_cycle(model, env)
    assert out.code == 0 and out.stderr == "", out
    ctx = out.json()["hookSpecificOutput"]
    assert ctx["hookEventName"] == "PostToolUse"
    assert ctx["additionalContext"] == ("rule-of-thumb-model audit on write: model.xlsx: 0 errors, 0 warnings "
                                        f"({NOT_VERIFIED})")
    args = log.read_text(encoding="utf-8").split()
    assert args[0] == "audit" and Path(args[1]) == model / "model.xlsx" and args[2:] == ["--strict", "--json", "-",
                                                                                          "--quiet"]


def test_audit_on_write_findings_block_with_lines(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    out = build_cycle(model, dict(env, FAKE_ROTM_MODE="findings"))
    assert out.code == 0, out
    data = out.json()
    assert data["decision"] == "block"
    lines = data["reason"].splitlines()
    assert lines[0] == f"rule-of-thumb-model audit on write: model.xlsx: 1 error, 2 warnings ({NOT_VERIFIED})"
    assert lines[1].startswith("ERR  calc!D7 (48 cells)  F-DEPTH: nesting depth 3") and "Fix: move MATCH(" in lines[1]
    assert lines[2].startswith("warn calc!E9  F-LITERAL")
    assert lines[3].startswith("warn inputs!B4 (3 cells)  S-FONT")
    assert "V-NOT-RECALC" not in data["reason"]                     # info findings are not listed
    assert lines[-1].startswith("Fix: change the specification")


def test_audit_on_write_after_a_failed_command(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    out = build_cycle(model, env, failure=True)
    assert out.json()["hookSpecificOutput"]["hookEventName"] == "PostToolUseFailure"


def test_audit_on_write_unchanged_workbook_says_nothing(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, log = fake_rotm
    assert_silent(build_cycle(model, env, write=False, command="ls -la"))
    assert not log.exists()


def test_audit_on_write_without_a_pre_record_uses_the_600_s_window(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, log = fake_rotm
    payload = bash(f"{ROTM} build model.spec.json --force", model, sid="sess-nopre")
    touch_later(model / "model.xlsx", 3)                            # raw_guard did not run (or failed)
    out = run_hook("audit_on_write", post(payload), env=env)
    assert "0 errors, 0 warnings" in out.json()["hookSpecificOutput"]["additionalContext"]
    old = time.time() - 3600
    os.utime(model / "model.xlsx", (old, old))
    payload2 = bash("ls", model, sid="sess-nopre-2")
    assert_silent(run_hook("audit_on_write", post(payload2), env=env))   # an hour old, never seen: not audited
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def test_audit_on_write_reuses_a_strict_audit_the_command_ran(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, log = fake_rotm
    payload = bash(f"{ROTM} build model.spec.json --force && {ROTM} audit model.xlsx --strict", model)
    assert_silent(run_hook("raw_guard", payload, env=env))
    touch_later(model / "model.xlsx", 11)
    modelfolder.record_step(model, "model.xlsx", "audit", {"exit_code": 0, "strict": True, "errors": 0,
                                                           "warnings": 0, "sha256": common.file_sha256(model / "model.xlsx")})
    out = run_hook("audit_on_write", post(payload), env=env)
    assert "from the strict audit this command ran" in out.json()["hookSpecificOutput"]["additionalContext"]
    assert not log.exists()


def test_audit_on_write_reports_an_audit_that_could_not_run(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    out = build_cycle(model, dict(env, FAKE_ROTM_MODE="crash"))
    text = out.json()["hookSpecificOutput"]["additionalContext"]
    assert "the audit did not run (exit 4: rotm.py audit: not implemented yet" in text
    assert "--strict yourself" in text


def test_audit_on_write_records_the_write_and_the_audit(model: Path, fake_rotm: tuple[dict, Path],
                                                        _isolated_state: Path) -> None:
    env, _log = fake_rotm
    build_cycle(model, env, sid="sess-rec")
    rec = json.loads((_isolated_state / "sessions" / "sess-rec.json").read_text(encoding="utf-8"))
    (entry,) = rec["written"].values()
    assert entry["rel"] == "model.xlsx" and entry["seq"] == 1 and rec["write_seq"] == 1
    (audit,) = rec["audits"].values()
    assert audit["sha256"] == common.file_sha256(model / "model.xlsx") and audit["exit_code"] == 0
    assert rec["pre"] == {}                                         # the pre-call record was used up


def _check_then_hook(model: Path, env: dict, sid: str, step: str, value: int) -> Out:
    """raw_guard before `check`, the call recalculates and saves model.xlsx and records `step` for the saved file
    (as check and recalc do), then audit_on_write."""
    payload = bash(f"{ROTM} check . --strict", model, sid=sid)
    assert_silent(run_hook("raw_guard", payload, env=env))
    touch_later(model / "model.xlsx", value)                        # Excel recalculated and saved the workbook
    saved = common.file_sha256(model / "model.xlsx")
    entry: dict = {"exit_code": 0, "output_sha256": saved, "input_sha256": None}
    if step == "check":
        entry.update(strict=True, recalculated=True, checks=6, failed=0, master=0)
    else:
        entry.update(excel="16.0", error_cells=0)
    modelfolder.record_step(model, "model.xlsx", step, entry)
    return run_hook("audit_on_write", post(payload), env=env)


@pytest.mark.parametrize("step", ["check", "recalc"])
def test_hook_says_recalculated_after_a_check_that_recalculated(model: Path, fake_rotm: tuple[dict, Path],
                                                               _isolated_state: Path, step: str) -> None:
    """v5 (A-aera-18): 'recalculated' when reports/status.json holds a passing check that recalculated (or a passing
    recalc) whose output_sha256 is the workbook's current sha256."""
    env, _log = fake_rotm
    out = _check_then_hook(model, env, f"sess-rc-{step}", step, 41)
    assert out.code == 0 and out.stderr == "", out
    assert out.json()["hookSpecificOutput"]["additionalContext"] == (
        "rule-of-thumb-model audit on write: model.xlsx: 0 errors, 0 warnings (recalculated)")
    rec = json.loads((_isolated_state / "sessions" / f"sess-rc-{step}.json").read_text(encoding="utf-8"))
    (audit,) = rec["audits"].values()
    assert audit["recalculated"] is True and audit["sha256"] == common.file_sha256(model / "model.xlsx")


def test_hook_says_not_verified_after_a_later_edit(model: Path, fake_rotm: tuple[dict, Path],
                                                    _isolated_state: Path) -> None:
    env, _log = fake_rotm
    first = _check_then_hook(model, env, "sess-edit", "check", 42)
    assert first.json()["hookSpecificOutput"]["additionalContext"].endswith("(recalculated)")
    out = build_cycle(model, env, sid="sess-edit")                 # a later write: the check record is older
    assert out.json()["hookSpecificOutput"]["additionalContext"] == (
        f"rule-of-thumb-model audit on write: model.xlsx: 0 errors, 0 warnings ({NOT_VERIFIED})")
    rec = json.loads((_isolated_state / "sessions" / "sess-edit.json").read_text(encoding="utf-8"))
    (audit,) = rec["audits"].values()
    assert audit["recalculated"] is False and audit["sha256"] == common.file_sha256(model / "model.xlsx")
    # a failing check of the current file, or a check that did not recalculate, does not count either
    for value, entry in ((43, {"exit_code": 1, "recalculated": True}), (44, {"exit_code": 0, "recalculated": False})):
        payload = bash("ls", model, sid="sess-edit")
        assert_silent(run_hook("raw_guard", payload, env=env))
        touch_later(model / "model.xlsx", value)
        modelfolder.record_step(model, "model.xlsx", "check",
                                dict(entry, output_sha256=common.file_sha256(model / "model.xlsx")))
        text = run_hook("audit_on_write", post(payload), env=env).json()["hookSpecificOutput"]["additionalContext"]
        assert text.endswith(f"({NOT_VERIFIED})"), (entry, text)


@pytest.mark.excel
def test_hook_says_recalculated_after_a_real_check(tmp_path: Path, _isolated_state: Path) -> None:
    """v5 (A-aera-18), the reviewer's case end to end: the real `rotm.py check --strict` recalculates and saves a
    copy of the starter model in Excel; the hook then runs the real audit and reads the check record."""
    src = SKILL / "templates" / "starter"
    if not (src / "model.xlsx").is_file():
        pytest.skip("the starter template's workbook is absent")
    root = tmp_path / "starter"
    shutil.copytree(src, root)
    env = {"ROTM_EXAMPLE_ROOTS": str(tmp_path)}                   # the shipped 'example' agreement holds here
    payload = bash(f'{ROTM} check "{root.as_posix()}" --strict', root, sid="sess-real-check")
    assert_silent(run_hook("raw_guard", payload, env=env))
    proc = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SKILL / "scripts" / "rotm.py"), "check",
                           str(root), "--strict", "--json", "-", "--quiet"], capture_output=True, text=True,
                          encoding="utf-8", timeout=600, env=dict(os.environ, **env))
    rep = json.loads(proc.stdout)
    assert proc.returncode == 0 and rep["verified"]["recalculated"], (proc.returncode, rep["findings"], proc.stderr)
    rec = modelfolder.read_status(root)["workbooks"]["model.xlsx"]["check"]
    assert rec["output_sha256"] == common.file_sha256(root / "model.xlsx") and rec["recalculated"] is True
    out = run_hook("audit_on_write", post(payload), env=env)
    assert out.code == 0 and "Traceback" not in out.stderr, out
    data = out.json()
    text = data.get("reason") or data["hookSpecificOutput"]["additionalContext"]
    assert text.splitlines()[0].startswith("rule-of-thumb-model audit on write: model.xlsx: "), text
    assert text.splitlines()[0].endswith("(recalculated)"), text


def test_audit_on_write_runs_the_real_audit(model: Path) -> None:
    """The real rotm.py audit through the hook (skipped while lib.audit is a stub)."""
    probe = subprocess.run([sys.executable, "-P", "-X", "utf8", "-B", str(SKILL / "scripts" / "rotm.py"), "audit",
                            str(model / "model.xlsx"), "--strict", "--json", "-", "--quiet"],
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
    if probe.returncode == 4 or "not implemented" in probe.stderr:
        pytest.skip(f"rotm.py audit is not available yet: {probe.stderr.strip()[:200]}")
    out = build_cycle(model, {})
    assert out.code == 0 and "Traceback" not in out.stderr, out
    data = out.json()
    text = data.get("reason") or data["hookSpecificOutput"]["additionalContext"]
    assert text.startswith("rule-of-thumb-model audit on write: model.xlsx: ")


# ============================================================================= finish_check

def status_pass(model: Path, *, audit: bool = True, check: bool = True) -> None:
    sha = common.file_sha256(model / "model.xlsx")
    if audit:
        modelfolder.record_step(model, "model.xlsx", "audit", {"exit_code": 0, "strict": True, "errors": 0,
                                                               "warnings": 0, "sha256": sha})
    if check:
        modelfolder.record_step(model, "model.xlsx", "check", {"exit_code": 0, "recalculated": True, "checks": 6,
                                                               "failed": 0, "master": 0, "input_sha256": sha,
                                                               "output_sha256": sha})


def test_finish_no_session_record_says_nothing_even_in_a_model_folder(model: Path) -> None:
    assert_silent(run_hook("finish_check", stop(model, sid="never-seen")))


def test_finish_session_that_wrote_nothing_says_nothing(model: Path, _isolated_state: Path) -> None:
    assert_silent(run_hook("raw_guard", bash("ls", model, sid="sess-read")))
    assert (_isolated_state / "sessions" / "sess-read.json").is_file()
    assert_silent(run_hook("finish_check", stop(model, sid="sess-read")))


def test_finish_refuses_once_then_lets_through_then_refuses_after_a_new_write(model: Path,
                                                                             fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    build_cycle(model, env, sid="sess-f")
    first = run_hook("finish_check", stop(model, sid="sess-f"))
    assert first.code == 0 and first.stderr == "", first
    data = first.json()
    assert data["decision"] == "block"
    assert data["reason"].startswith("rule-of-thumb-model finish check: model.xlsx changed after its last passing "
                                     "audit or recalculation.")
    assert "not recalculated: no passing check of this version" in data["reason"]
    assert " check \"" in data["reason"] and "say in your report that the workbook was not recalculated" in data["reason"]
    # the stop that follows a refusal (stop_hook_active) goes through, with one line for the user (v2: A-aera-12)
    through = run_hook("finish_check", stop(model, sid="sess-f", active=True))
    assert through.code == 0 and through.stderr == "" and "decision" not in through.json(), through
    assert through.json()["systemMessage"].startswith("rule-of-thumb-model finish check: not verified")
    # a later stop with no new workbook write also goes through: at most one refusal per session end
    later = run_hook("finish_check", stop(model, sid="sess-f"))
    assert "decision" not in later.json() and later.json()["systemMessage"] == through.json()["systemMessage"]
    # a new write makes it check again
    build_cycle(model, env, sid="sess-f")
    again = run_hook("finish_check", stop(model, sid="sess-f"))
    assert again.json()["decision"] == "block"


def test_finish_passes_after_audit_and_check(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    build_cycle(model, env, sid="sess-p")
    status_pass(model)
    assert_silent(run_hook("finish_check", stop(model, sid="sess-p")))


def test_finish_passes_when_check_saved_the_audited_file(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    build_cycle(model, env, sid="sess-c", command=f"{ROTM} build model.spec.json --force")
    audited = common.file_sha256(model / "model.xlsx")
    modelfolder.record_step(model, "model.xlsx", "audit", {"exit_code": 0, "strict": True, "errors": 0,
                                                           "warnings": 0, "sha256": audited})
    payload = bash(f"{ROTM} check .", model, sid="sess-c")
    assert_silent(run_hook("raw_guard", payload, env=env))
    touch_later(model / "model.xlsx", 99)                           # check recalculated and saved
    saved = common.file_sha256(model / "model.xlsx")
    modelfolder.record_step(model, "model.xlsx", "check", {"exit_code": 0, "recalculated": True, "failed": 0,
                                                           "master": 0, "input_sha256": audited, "output_sha256": saved})
    modelfolder.record_step(model, "model.xlsx", "audit", {"exit_code": 0, "strict": True, "errors": 0,
                                                           "warnings": 0, "sha256": audited})
    run_hook("audit_on_write", post(payload), env=env)
    assert_silent(run_hook("finish_check", stop(model, sid="sess-c")))


def test_finish_accepts_not_recalculated_in_the_report(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    build_cycle(model, env, sid="sess-n")                           # the hook's own strict audit was clean
    assert_silent(run_hook("finish_check", stop(model, sid="sess-n",
                                                last="Built and audited. Excel was absent: the workbook was NOT "
                                                     "recalculated.")))


def test_finish_reads_the_last_message_from_the_transcript(model: Path, fake_rotm: tuple[dict, Path],
                                                           tmp_path: Path) -> None:
    env, _log = fake_rotm
    build_cycle(model, env, sid="sess-t")
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "Audited; the workbook was not recalculated (no Excel)."}]}}) + "\n", encoding="utf-8")
    payload = stop(model, sid="sess-t", last=None)
    payload["transcript_path"] = str(t)
    assert_silent(run_hook("finish_check", payload))


def test_finish_not_recalculated_does_not_excuse_a_failed_audit(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    build_cycle(model, dict(env, FAKE_ROTM_MODE="findings"), sid="sess-a")
    out = run_hook("finish_check", stop(model, sid="sess-a", last="The workbook was not recalculated."))
    reason = out.json()["reason"]
    assert "audit not passed: the strict audit of this version found 1 errors and 2 warnings" in reason
    assert "not recalculated: no passing check" not in reason


def test_finish_checks_a_second_session_in_the_same_folder(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    build_cycle(model, env, sid="sess-1st")
    assert run_hook("finish_check", stop(model, sid="sess-1st")).json()["decision"] == "block"
    build_cycle(model, env, sid="sess-2nd")
    assert run_hook("finish_check", stop(model, sid="sess-2nd")).json()["decision"] == "block"


def test_finish_sees_a_write_no_post_hook_saw(model: Path, _isolated_state: Path) -> None:
    payload = bash(f"{ROTM} build model.spec.json --force --strict", model, sid="sess-u")
    assert_silent(run_hook("raw_guard", payload))
    touch_later(model / "model.xlsx", 42)                           # no PostToolUse hook ran for this call
    out = run_hook("finish_check", stop(model, sid="sess-u"))
    assert out.json()["decision"] == "block"
    rec = json.loads((_isolated_state / "sessions" / "sess-u.json").read_text(encoding="utf-8"))
    assert rec["write_seq"] == 1 and rec["finish_refused_at_seq"] == 1


def test_finish_ignores_a_written_workbook_that_was_deleted(model: Path, fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    build_cycle(model, env, sid="sess-d")
    (model / "model.xlsx").unlink()
    assert_silent(run_hook("finish_check", stop(model, sid="sess-d")))


# ============================================================================= failure (F16 d)

@pytest.mark.parametrize("hook", HOOK_NAMES)
def test_a_failing_hook_lets_the_action_through_with_one_line(hook: str, model: Path,
                                                              fake_rotm: tuple[dict, Path]) -> None:
    env, _log = fake_rotm
    if hook == "finish_check":
        build_cycle(model, env, sid="sess-x")
        payload = stop(model, sid="sess-x")
    elif hook == "audit_on_write":
        payload = post(bash(f"{ROTM} build model.spec.json", model))
    else:
        payload = pre("Write", model, file_path=str(model / "queries" / "sales.sql"), content="SELECT 1")
    out = run_hook(hook, payload, env=dict(env, ROTM_HOOK_FAIL_TEST=hook))
    assert out.code == 1, out
    assert out.stdout == ""
    assert out.stderr.splitlines() == [f"rule-of-thumb-model hook {hook}: internal error (RuntimeError); "
                                       "the action was let through."]
    assert "Traceback" not in out.stderr


def test_a_failing_hook_saves_its_traceback_to_the_state_folder(model: Path, _isolated_state: Path) -> None:
    payload = pre("Write", model, file_path=str(model / "queries" / "sales.sql"), content="SELECT 1")
    run_hook("contract_gate", payload, env={"ROTM_HOOK_FAIL_TEST": "all"})
    text = (_isolated_state / "hook_last_error.txt").read_text(encoding="utf-8")
    assert "contract_gate" in text and "Traceback" in text and "ROTM_HOOK_FAIL_TEST" in text


def test_unreadable_payload_is_let_through_silently(model: Path) -> None:
    for hook in HOOK_NAMES:
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", "-B", str(HOOKS / f"{hook}.py")],
                              input=b"this is not json", capture_output=True, cwd=str(model), timeout=60)
        assert proc.returncode == 0 and proc.stdout == b"", (hook, proc)


# ============================================================================= wiring through Git Bash

def git_bash() -> str | None:
    cands = [os.environ.get("ROTM_BASH"), r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"]
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        cands.append(found)
    for c in cands:
        if c and Path(c).is_file():
            return c
    return None


BASH = git_bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="Git Bash not found (set ROTM_BASH)")


def hook_command(event: str, script: str) -> str:
    data = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
    for g in data["hooks"][event]:
        for h in g["hooks"]:
            if f"/hooks/{script}" in h["command"]:
                return h["command"]
    raise AssertionError(f"{script} is not wired for {event}")


def run_wired(event: str, script: str, payload: dict | bytes, *, plugin: Path = PLUGIN, cwd: Path,
              env: dict | None = None) -> Out:
    e = dict(os.environ)
    e.update(env or {})
    e["CLAUDE_PLUGIN_ROOT"] = str(plugin)
    e.pop("PYTHONUTF8", None)
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    proc = subprocess.run([BASH, "-c", hook_command(event, script)], input=data, capture_output=True, env=e,
                          cwd=str(cwd), timeout=120)
    return Out(proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace"))


@needs_bash
def test_wired_contract_gate_refuses_and_allows(model: Path, outside: Path) -> None:
    refused = run_wired("PreToolUse", "contract_gate.py", bash("echo 'SELECT 1' > queries/sales.sql", model), cwd=model)
    assert_refused(refused, "contract gate: queries/sales.sql")
    assert_silent(run_wired("PreToolUse", "contract_gate.py", bash("echo 'SELECT 1' > queries/sales.sql", outside),
                            cwd=outside))


@needs_bash
def test_wired_hooks_are_silent_outside(outside: Path) -> None:
    payload = bash("python -c \"import openpyxl; wb=openpyxl.load_workbook('book.xlsx'); wb.save('book.xlsx')\"",
                   outside)
    assert_silent(run_wired("PreToolUse", "raw_guard.py", payload, cwd=outside))
    assert_silent(run_wired("PreToolUse", "contract_gate.py", payload, cwd=outside))
    assert_silent(run_wired("PostToolUse", "audit_on_write.py", post(payload), cwd=outside))
    assert_silent(run_wired("PostToolUseFailure", "audit_on_write.py", post(payload, failure=True), cwd=outside))
    assert_silent(run_wired("Stop", "finish_check.py", stop(outside), cwd=outside))


@needs_bash
def test_wired_raw_guard_refusal_reaches_stderr_with_exit_2(model: Path) -> None:
    out = run_wired("PreToolUse", "raw_guard.py", pre("Write", model, file_path=str(model / "raw" / "sales.csv"),
                                                       content="x"), cwd=model)
    assert_refused(out, "raw-sheet guard: raw/sales.csv")


@needs_bash
def test_wired_failure_inside_gives_one_line_and_exit_1(model: Path) -> None:
    payload = pre("Write", model, file_path=str(model / "queries" / "sales.sql"), content="SELECT 1")
    out = run_wired("PreToolUse", "contract_gate.py", payload, cwd=model, env={"ROTM_HOOK_FAIL_TEST": "contract_gate"})
    assert out.code == 1 and out.stdout == ""
    assert out.stderr.splitlines() == ["rule-of-thumb-model hook contract_gate: internal error (RuntimeError); "
                                       "the action was let through."]


@needs_bash
@pytest.mark.parametrize("script,event", [("contract_gate.py", "PreToolUse"), ("raw_guard.py", "PreToolUse"),
                                          ("audit_on_write.py", "PostToolUse"), ("finish_check.py", "Stop")])
def test_wired_broken_hook_library_is_reported_by_run_hook_sh(script: str, event: str, model: Path, outside: Path,
                                                              tmp_path: Path) -> None:
    """The hook library cannot load (python exits 3): run_hook.sh prints one line inside a model folder, nothing
    outside, and never blocks."""
    plugin = tmp_path / "broken_plugin"
    shutil.copytree(HOOKS, plugin / "hooks")
    (plugin / "hooks" / "_hooklib.py").write_text("raise ImportError('broken on purpose')\n", encoding="utf-8")
    name = script[:-3]
    inside_payload = stop(model) if event == "Stop" else bash("ls", model)
    out = run_wired(event, script, inside_payload, plugin=plugin, cwd=model)
    assert out.code == 1 and out.stdout == "", out
    assert out.stderr.splitlines() == [f"rule-of-thumb-model hook {name}: could not run (python exit 3); "
                                       "the action was let through."]
    assert_silent(run_wired(event, script, stop(outside) if event == "Stop" else bash("ls", outside), plugin=plugin,
                            cwd=outside))


@needs_bash
def test_run_hook_sh_passes_0_1_2_and_reports_other_codes(model: Path, outside: Path) -> None:
    sh = (HOOKS / "run_hook.sh").as_posix()

    def run(code: int, payload: bytes, cwd: Path) -> Out:
        proc = subprocess.run([BASH, sh, str(code), "raw_guard.py"], input=payload, capture_output=True,
                              cwd=str(cwd), timeout=60)
        return Out(proc.returncode, proc.stdout.decode(), proc.stderr.decode())

    for code in (0, 1, 2):
        r = run(code, b"", outside)
        assert r.code == code and r.stdout == "" and r.stderr == ""
    # Python not found (127): the payload's cwd (JSON-escaped Windows path) lies in a model folder
    r = run(127, json.dumps(bash("ls", model)).encode(), outside)
    assert r.code == 1 and r.stderr.strip() == ("rule-of-thumb-model hook raw_guard: could not run (python exit 127); "
                                                "the action was let through.")
    # the payload's file_path lies in a model folder
    p = pre("Write", outside, file_path=str(model / "raw" / "sales.csv"), content="x")
    assert run(9009, json.dumps(p).encode(), outside).code == 1
    # $PWD in a model folder, payload already consumed
    assert run(127, b"", model / "queries").code == 1
    # outside: nothing, exit 0
    assert_silent(run(127, json.dumps(bash("ls", outside)).encode(), outside))
