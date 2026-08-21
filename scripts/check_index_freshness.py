#!/usr/bin/env python3
"""
check_index_freshness.py - detect PROJECT_INDEX <-> RAM drift.

WHY: PROJECT_INDEX.md is the second file every agent reads at startup. If its
`latest_ram_path` points at an older RAM than what's actually on disk, every
session starts from a stale state. This drift sat unfixed for 7 cycles once --
a deterministic check catches it in 1 second.

WHAT IT DOES (for a given project root):
  - reads the latest_ram_path pointer from 00.ABOUT/PROJECT_INDEX.md
  - finds the newest RAM in 12.MEMORY_RAM (by date + version in the filename,
    matching '*_ram_YYYYMMDD_vN.md'; ignores 'ram_lifecycle_*' reports)
  - reports MATCH or DRIFT

USAGE:
  python3 check_index_freshness.py [--root <path>]

EXIT CODES: 0 = fresh (or nothing to compare), 1 = drift detected.
This is advisory: run it on demand, in your weekly lifecycle task, or wire it
as a Stop/SubagentStop hook LATER once you've tested it in your environment.
Do not enable it as a blocking hook without that test -- an untested hook is how
the broken SQL-checker happened.
"""
import argparse
import os
import re
import sys

RAM_RE = re.compile(r"_ram_(\d{8})_v(\d+)\.md$", re.IGNORECASE)


def pointer_from_index(root):
    idx = os.path.join(root, "00.ABOUT", "PROJECT_INDEX.md")
    try:
        with open(idx, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        return None, f"cannot read PROJECT_INDEX.md ({e})"
    for token in text.replace("|", " ").split():
        t = token.strip().replace("\\", "/")
        if "_ram_" in t and t.endswith(".md"):
            return os.path.basename(t), None
    return None, "no latest_ram_path pointer found in PROJECT_INDEX.md"


def newest_ram_on_disk(root):
    ram_dir = os.path.join(root, "12.MEMORY_RAM")
    best, best_key = None, None
    try:
        names = os.listdir(ram_dir)
    except OSError as e:
        return None, f"cannot list 12.MEMORY_RAM ({e})"
    for name in names:
        m = RAM_RE.search(name)
        if not m:
            continue
        key = (m.group(1), int(m.group(2)))  # (YYYYMMDD, version)
        if best_key is None or key > best_key:
            best, best_key = name, key
    if best is None:
        return None, "no RAM files matching *_ram_YYYYMMDD_vN.md found"
    return best, None


def main():
    ap = argparse.ArgumentParser(description="Check PROJECT_INDEX vs newest RAM on disk.")
    ap.add_argument("--root", default=".", help="project root (default: cwd)")
    args = ap.parse_args()
    root = os.path.abspath(args.root)

    pointer, perr = pointer_from_index(root)
    newest, nerr = newest_ram_on_disk(root)

    print(f"Freshness check: {root}")
    print(f"  PROJECT_INDEX points to : {pointer or '(' + str(perr) + ')'}")
    print(f"  newest RAM on disk      : {newest or '(' + str(nerr) + ')'}")

    if pointer is None or newest is None:
        print("\nRESULT: cannot compare (see notes above).")
        return 0  # not a drift; nothing to gate on

    if pointer == newest:
        print("\nRESULT: FRESH - PROJECT_INDEX matches the newest RAM.")
        return 0
    print(f"\nRESULT: DRIFT - update PROJECT_INDEX latest_ram_path to '{newest}'.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
