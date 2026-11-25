"""Write the price_volume_mix template's source extract (deterministic, seed 20261125).

    python -P -X utf8 -B make_data.py        # writes data/sales.csv and data/controls.json

data/sales.csv: order lines of two periods, PY (prior year) and CY (current year), one row per order line:
line_id, period, product, qty, revenue. CY prices are 2% to 8% higher and the mix moves toward Pro.
Standard library only.
"""
from __future__ import annotations

import csv
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRICE = {"Basic": 20000, "Plus": 35000, "Pro": 60000, "Max": 100000}         # KRW per unit, PY list price
MIX = {"PY": (0.40, 0.30, 0.20, 0.10), "CY": (0.34, 0.30, 0.26, 0.10)}
RISE = {"Basic": 1.02, "Plus": 1.04, "Pro": 1.06, "Max": 1.08}


def main() -> int:
    rng = random.Random(20261125)
    rows = []
    n = 0
    for period, lines in (("PY", 120), ("CY", 130)):
        for _ in range(lines):
            n += 1
            product = rng.choices(list(PRICE), MIX[period])[0]
            qty = rng.randrange(1, 21)
            price = PRICE[product] * (RISE[product] if period == "CY" else 1.0)
            price = round(price * rng.uniform(0.97, 1.03) / 100) * 100        # discounts within 3%
            rows.append([f"O{n:05d}", period, product, str(qty), str(qty * price)])
    (HERE / "data").mkdir(exist_ok=True)
    with open(HERE / "data" / "sales.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["line_id", "period", "product", "qty", "revenue"])
        w.writerows(rows)
    controls = {"note": "Row count and totals as the sales system's own report gives them (generated with the data).",
                "sales": {"rows": len(rows), "qty": sum(int(r[3]) for r in rows),
                          "revenue": sum(int(r[4]) for r in rows)}}
    (HERE / "data" / "controls.json").write_text(json.dumps(controls, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(controls))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
