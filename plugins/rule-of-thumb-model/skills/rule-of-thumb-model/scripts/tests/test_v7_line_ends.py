"""Tests of the v7 change of package P6 to the line ends of the published plugin (round 6 verdict, [J-5], with
condition 2 of agreements/further_loop_seq2.json).

- PLUGIN/.gitattributes holds the rule lines '* -text' and '*.sh text eol=lf', in this order (a later line wins
  for .sh files). '* -text' keeps every file's bytes as committed: the plugin hashes and compares shipped files
  byte for byte (specifications against the hash in the manifest, landed snapshots, contracts), and 27 of its
  text files hold CR bytes, so a conversion either way would change them. '*.sh text eol=lf' writes the shell
  scripts with LF line ends, which bash needs (hooks/run_hook.sh is the hooks' fallback).
- hooks/run_hook.sh holds no CR byte.
- A Windows checkout with core.autocrlf=true writes every file of the plugin back byte for byte; without the
  .gitattributes file the same checkout changes hashed files (the clone of the published v6 failed 11 tests).

The checkout test needs git on the PATH and the plugin folder around the skill folder (skipped otherwise, as in
selftest step f02). It copies the plugin without __pycache__, .pytest_cache, .git and pytest's base temporary
folder (which lies inside the skill folder when the suite runs with --basetemp there). It passes every git
setting with -c, reads no user or system git configuration, changes no git configuration file, and runs no git
hook (git add and git checkout-index run none).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[2]
PLUGIN = SKILL.parents[1]
HAS_PLUGIN = (PLUGIN / ".claude-plugin" / "plugin.json").is_file()
ATTRIBUTES = PLUGIN / ".gitattributes"
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}

needs_plugin = pytest.mark.skipif(not HAS_PLUGIN, reason="skill copied without its plugin")


def _rules(path: Path) -> list[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


@needs_plugin
def test_gitattributes_keeps_every_file_as_committed_and_sh_files_with_lf() -> None:
    assert ATTRIBUTES.is_file()
    text = ATTRIBUTES.read_text(encoding="utf-8")
    assert _rules(ATTRIBUTES) == ["* -text", "*.sh text eol=lf"]
    assert "*.sh text eol=lf" in text.splitlines()
    assert any(ln.startswith("#") for ln in text.splitlines()), "the comment line that says why"


@needs_plugin
def test_run_hook_sh_holds_no_cr() -> None:
    assert b"\r" not in (PLUGIN / "hooks" / "run_hook.sh").read_bytes()
    for sh in sorted(PLUGIN.rglob("*.sh")):
        assert b"\r" not in sh.read_bytes(), sh.relative_to(PLUGIN).as_posix()


# ----------------------------------------------------------------------------- the checkout round trip

def _holds(folder: Path, path: Path) -> bool:
    """Whether `path` is `folder` or lies inside it."""
    f = os.path.normcase(os.path.abspath(str(folder)))
    p = os.path.normcase(os.path.abspath(str(path)))
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


def _copy_plugin(dest: Path, basetemp: Path, *, attributes: bool) -> list[str]:
    """Copy the plugin's files into `dest`: not __pycache__, .pytest_cache, .git, and not the folder that holds the
    test's temporary folders (pytest's base temporary folder: the suite also runs with --basetemp inside the skill
    folder; the folders above it are copied). Returns the relative paths copied."""
    copied: list[str] = []
    for dirpath, dirnames, filenames in os.walk(PLUGIN):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not _holds(basetemp, here / d))
        for name in sorted(filenames):
            src = here / name
            rel = src.relative_to(PLUGIN).as_posix()
            if rel == ".gitattributes" and not attributes:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copyfile(src, target)
            except FileNotFoundError:          # a file another process removed while the copy ran
                continue
            copied.append(rel)
    return copied


def _git(args: list[str], cwd: Path, scratch: Path) -> subprocess.CompletedProcess[str]:
    none = (scratch / "no_such_file").as_posix()
    env = dict(os.environ, GIT_CONFIG_GLOBAL=none, GIT_CONFIG_SYSTEM=none, GIT_CONFIG_NOSYSTEM="1",
               GIT_ATTR_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG", "GIT_CONFIG_COUNT"):
        env.pop(k, None)
    settings = ["core.autocrlf=true", "core.safecrlf=false", "core.longpaths=true", "core.hooksPath=",
                f"core.excludesFile={none}", f"core.attributesFile={none}", "init.defaultBranch=main",
                "user.name=rotm checkout test", "user.email=rotm checkout test"]
    cmd = ["git"] + [w for s in settings for w in ("-c", s)] + args
    proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)
    assert proc.returncode == 0, (args, proc.stdout[-2000:], proc.stderr[-2000:])
    return proc


def _round_trip(tmp_path: Path, basetemp: Path, name: str, *, attributes: bool) -> tuple[list[str], list[str]]:
    """Copy the plugin into a new repository, `git add -A` and write every file out again with
    `git checkout-index -a` into a second folder, both with core.autocrlf=true. Returns (files copied, files whose
    bytes differ or that are missing after the checkout)."""
    work = tmp_path / f"{name}_w"
    out = tmp_path / f"{name}_c"
    copied = _copy_plugin(work, basetemp, attributes=attributes)
    _git(["init", "-q"], work, tmp_path)
    _git(["add", "-A", "-f"], work, tmp_path)
    _git(["checkout-index", "-a", "-f", f"--prefix={out.as_posix()}/"], work, tmp_path)
    differ = []
    for rel in copied:
        back = out / rel
        if not back.is_file() or back.read_bytes() != (work / rel).read_bytes():
            differ.append(rel)
    return copied, differ


def _hashed(rel: str) -> bool:
    """A file whose bytes the plugin hashes or compares: a specification, a contract, a manifest, a landed
    snapshot or its controls file, a sample extract."""
    name = rel.rsplit("/", 1)[-1]
    return (name == "model.spec.json" or name.endswith((".contract.json", ".manifest.json", "controls.json"))
            or "/raw/" in f"/{rel}" or ("/data/" in f"/{rel}" and name.endswith(".csv")))


@needs_plugin
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not on the PATH")
def test_a_windows_checkout_with_autocrlf_keeps_every_file_byte_for_byte(
        tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    basetemp = tmp_path_factory.getbasetemp()
    copied, differ = _round_trip(tmp_path, basetemp, "with", attributes=True)
    assert ".gitattributes" in copied and "hooks/run_hook.sh" in copied
    assert "skills/rule-of-thumb-model/SKILL.md" in copied and len(copied) > 100, len(copied)
    assert differ == [], differ[:20]
    # without .gitattributes the same checkout changes files the plugin hashes (the fail-before of J-5)
    _copied, changed = _round_trip(tmp_path, basetemp, "without", attributes=False)
    hashed = [rel for rel in changed if _hashed(rel)]
    assert hashed, changed[:20]
    assert "hooks/run_hook.sh" in changed
