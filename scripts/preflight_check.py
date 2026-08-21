#!/usr/bin/env python3
"""
preflight_check.py - fail-fast readiness check for an agents_setup workspace.

WHY: Batch / migration operations have stalled mid-run when OneDrive files were
"online-only" (not downloaded locally) and could not be read from the sandbox,
or when required writable folders were missing. This script verifies, in ~1s,
that the control files an agent needs are actually readable and the writable
roles exist -- BEFORE a task commits to work it can't finish.

WHAT IT CHECKS (for a given project root):
  - AGENTS.md is readable
  - 00.ABOUT/PROJECT_INDEX.md is readable
  - 00.ABOUT/PROJECT_REGISTRY.json is readable AND valid JSON
  - 00.ABOUT/PROJECT_GOVERNANCE.md is readable
  - the latest RAM named in PROJECT_INDEX is readable (if a pointer is present)
  - writable roles exist: 11.OUTPUT_ROUGH, 12.MEMORY_RAM, 13.MEMORY_HDD,
    14.LESSONS_LEARNED, 15.WIKI

An unreadable file usually means OneDrive online-only: open it locally (or run
`attrib -U +P` / right-click > "Always keep on this device") to hydrate it.

USAGE:
  python3 preflight_check.py [--root <path>]
  (default root = current working directory)

EXIT CODES: 0 = PASS, 1 = STOP (issues found). Safe to use as a gate:
  python3 preflight_check.py --root . && <do the migration>
"""
import argparse
import json
import os
import sys

REQUIRED_FILES = [
    "AGENTS.md",
    os.path.join("00.ABOUT", "PROJECT_INDEX.md"),
    os.path.join("00.ABOUT", "PROJECT_REGISTRY.json"),
    os.path.join("00.ABOUT", "PROJECT_GOVERNANCE.md"),
]
WRITABLE_FOLDERS = [
    "11.OUTPUT_ROUGH",
    "12.MEMORY_RAM",
    "13.MEMORY_HDD",
    "14.LESSONS_LEARNED",
    "15.WIKI",
]


def can_read(path):
    """Return (ok, detail). Tries to read 1 byte; online-only files raise OSError."""
    try:
        with open(path, "rb") as fh:
            fh.read(1)
        return True, ""
    except FileNotFoundError:
        return False, "missing"
    except OSError as e:
        # EIO / EINVAL here typically means a OneDrive online-only placeholder.
        return False, f"unreadable ({e.errno}: likely OneDrive online-only)"


def latest_ram_from_index(root):
    """Best-effort parse of the latest_ram_path pointer from PROJECT_INDEX.md."""
    idx = os.path.join(root, "00.ABOUT", "PROJECT_INDEX.md")
    try:
        with open(idx, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    for token in text.replace("|", " ").split():
        t = token.strip().replace("\\", "/")
        if "_ram_" in t and t.endswith(".md"):
            return os.path.normpath(os.path.join(root, t))
    return None


def main():
    ap = argparse.ArgumentParser(description="Preflight readiness check for an agents_setup workspace.")
    ap.add_argument("--root", default=".", help="project root (default: cwd)")
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    issues = []
    print(f"Preflight: {root}\n")

    for rel in REQUIRED_FILES:
        ok, detail = can_read(os.path.join(root, rel))
        print(f"  [{'OK ' if ok else 'XX '}] {rel}{'' if ok else '  -> ' + detail}")
        if not ok:
            issues.append(f"{rel}: {detail}")
        elif rel.endswith(".json"):
            try:
                with open(os.path.join(root, rel), "r", encoding="utf-8") as fh:
                    json.load(fh)
            except (OSError, json.JSONDecodeError) as e:
                print(f"  [XX ] {rel}  -> invalid JSON ({e})")
                issues.append(f"{rel}: invalid JSON")

    ram = latest_ram_from_index(root)
    if ram:
        ok, detail = can_read(ram)
        shown = os.path.relpath(ram, root)
        print(f"  [{'OK ' if ok else 'XX '}] latest RAM -> {shown}{'' if ok else '  -> ' + detail}")
        if not ok:
            issues.append(f"latest RAM ({shown}): {detail}")
    else:
        print("  [-- ] latest RAM pointer not found in PROJECT_INDEX (skipped)")

    print()
    for rel in WRITABLE_FOLDERS:
        present = os.path.isdir(os.path.join(root, rel))
        print(f"  [{'OK ' if present else 'XX '}] {rel}{'' if present else '  -> missing'}")
        if not present:
            issues.append(f"{rel}: missing writable folder")

    print()
    if issues:
        print(f"PREFLIGHT STOP: {len(issues)} issue(s).")
        print("Hydrate online-only files (open locally / 'Always keep on this device') "
              "or create missing folders, then re-run.")
        return 1
    print("PREFLIGHT PASS: workspace is readable and complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
