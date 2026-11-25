"""Write the starter template's source extract (deterministic, seed 20261125).

    python -P -X utf8 -B make_data.py        # writes data/lines.csv and data/controls.json

data/lines.csv: expense lines, January to March 2026 (one row per line): line_id, line_date, category, amount.
Standard library only.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATEGORIES = ("Rent", "Staff", "Supplies", "Travel")


def main() -> int:
    rng = random.Random(20261125)
    rows = []
    for n in range(1, 61):
        day = dt.date(2026, 1, 1) + dt.timedelta(days=rng.randrange(90))       # 2026-01-01 to 2026-03-31
        cat = CATEGORIES[n % 4]
        amount = rng.randrange(20, 4000) * 500                                  # 10,000 to 1,999,500 KRW
        rows.append([f"L{n:04d}", day.isoformat(), cat, str(amount)])
    (HERE / "data").mkdir(exist_ok=True)
    with open(HERE / "data" / "lines.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["line_id", "line_date", "category", "amount"])
        w.writerows(rows)
    controls = {"note": "Row count and total as the source system's own report gives them (generated with the data).",
                "lines": {"rows": len(rows), "amount": sum(int(r[3]) for r in rows)}}
    (HERE / "data" / "controls.json").write_text(json.dumps(controls, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(controls))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
