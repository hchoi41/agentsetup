"""Tests of the v7 change of package P1 to land, the land side of [J-2, J-3, J-4] part (3) (round 6 verdict).

- Every land run on a model folder prints the note "files in raw/" when raw/ holds a file that land did not write:
  land writes only raw/<t>.csv and raw/<t>.controls.json of a table with a contract (and their temporary files
  while it writes them).
- land --csv MODEL/raw/<t>.csv (the snapshot path itself) reads the whole file before it writes anything and
  writes through a temporary file moved over the target: a refused landing leaves the file byte for byte, and an
  accepted one leaves a file that holds every row, with the destination names in the header.

Model folders lie outside the plugin (fixture outside_tmp); the commands run with ROTM_NO_EXCEL=1.
"""
from __future__ import annotations

import csv
from pathlib import Path

from test_v7_contract import HEADER, NAMES, ROWS, agreed, land_cli, parsed, run_cli, write_csv

NOTE_START = "raw/ holds 1 file(s) that land did not write: export_2026.csv. raw/ holds only the snapshots land writes"


def _note_tail(root: Path) -> str:
    return (f"(raw/<t>.csv and raw/<t>.controls.json); copy a file of your own to {root.as_posix()}/data/ and land it "
            "from there, and name the files left in raw/ in the report")


def _note_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if "that land did not write" in ln]


def _append(path: Path, row: list[str]) -> None:
    with open(path, "a", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerow(row)


def test_land_notes_the_files_in_raw_it_did_not_write(outside_tmp: Path) -> None:
    root = agreed(outside_tmp)
    r = land_cli(root, outside_tmp / "sample" / "orders.csv", "--control", "orders.net_sales=60.5")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (root / "raw" / "orders.controls.json").is_file()
    assert not _note_lines(r.stdout), r.stdout                  # only land's own files in raw/: no note
    write_csv(root / "raw" / "export_2026.csv", HEADER, ROWS)    # the user's own extract
    for args in (("land", str(root), "--table", "orders"),
                 ("land", str(root), "--table", "orders", "--csv", str(outside_tmp / "sample" / "orders.csv"))):
        r = run_cli(*args)
        assert r.returncode == 0, r.stdout + r.stderr
        lines = _note_lines(r.stdout)
        assert len(lines) == 1, r.stdout
        assert NOTE_START in lines[0] and _note_tail(root) in lines[0], lines[0]
        assert "orders.csv," not in lines[0] and "orders.controls.json," not in lines[0]
    # a refused landing prints it too
    bad = write_csv(outside_tmp / "bad.csv", HEADER[:2], [r[:2] for r in ROWS])
    r = land_cli(root, bad)
    assert r.returncode == 1 and len(_note_lines(r.stdout)) == 1, r.stdout


def test_a_refused_landing_of_the_snapshot_path_leaves_it_byte_for_byte(outside_tmp: Path) -> None:
    root = agreed(outside_tmp)
    assert land_cli(root, outside_tmp / "sample" / "orders.csv").returncode == 0
    snap = root / "raw" / "orders.csv"
    _append(snap, ["A1", "2026-01-08", "0"])                    # repeats the key A1
    before = snap.read_bytes()
    for extra in ((), ("--force",), ("--force", "--control", "orders.net_sales=60.5")):
        r = land_cli(root, snap, *extra)
        assert r.returncode == 1 and "L-KEY" in r.stdout, (extra, r.stdout + r.stderr)
        assert snap.read_bytes() == before, extra


def test_an_accepted_landing_of_the_snapshot_path_keeps_every_row(outside_tmp: Path) -> None:
    root = agreed(outside_tmp)
    assert land_cli(root, outside_tmp / "sample" / "orders.csv").returncode == 0
    snap = root / "raw" / "orders.csv"
    _append(snap, ["A4", "2026-01-08", "4.5"])
    r = land_cli(root, snap, "--force", "--control", "orders.net_sales=65")
    assert r.returncode == 0, r.stdout + r.stderr
    header, rows = parsed(snap)
    assert header == NAMES and [x[0] for x in rows] == ["A1", "A2", "A3", "A4"]
    # the snapshot path overwritten with the sample's headers: landed by them, written with the names
    write_csv(snap, HEADER, ROWS)
    r = land_cli(root, snap, "--force", "--control", "orders.net_sales=60.5")
    assert r.returncode == 0, r.stdout + r.stderr
    header, rows = parsed(snap)
    assert header == NAMES and [x[0] for x in rows] == ["A1", "A2", "A3"]
    assert not [p.name for p in snap.parent.iterdir() if p.name.endswith(".tmp")]
