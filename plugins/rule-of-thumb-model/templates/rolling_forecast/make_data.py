"""Write the rolling_forecast template's source extract (deterministic, seed 20261125).

    python -P -X utf8 -B make_data.py        # writes data/sales.csv and data/controls.json

data/sales.csv: sales lines of the closed months, January to March 2026 (one row per line):
line_id, sale_date, product, revenue. The forecast drivers (growth, budget) are inputs in model.spec.json.
Standard library only.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRODUCTS = {"Alpha": 0.40, "Beta": 0.28, "Gamma": 0.20, "Delta": 0.12}


def main() -> int:
    rng = random.Random(20261125)
    rows = []
    n = 0
    for month, lines in ((1, 95), (2, 100), (3, 110)):
        for _ in range(lines):
            n += 1
            day = dt.date(2026, month, rng.randrange(1, 29))
            product = rng.choices(list(PRODUCTS), list(PRODUCTS.values()))[0]
            revenue = rng.randrange(2, 400) * 5000                       # 10,000 to 1,995,000 KRW
            rows.append([f"S{n:05d}", day.isoformat(), product, str(revenue)])
    (HERE / "data").mkdir(exist_ok=True)
    with open(HERE / "data" / "sales.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["line_id", "sale_date", "product", "revenue"])
        w.writerows(rows)
    controls = {"note": "Row count and total as the sales system's own report gives them (generated with the data).",
                "sales": {"rows": len(rows), "revenue": sum(int(r[3]) for r in rows)}}
    (HERE / "data" / "controls.json").write_text(json.dumps(controls, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(controls))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
