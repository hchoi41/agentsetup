"""Write the two source extracts of the worked example (deterministic, seed 20261125).

    python -P -X utf8 -B make_data.py            # writes data/sales.csv, data/target.csv, data/controls.json

The files stand in for what Power Query or Connected Sheets would load from the source system:
- data/sales.csv: invoice lines, January to June 2026, four regions (one row per invoice line);
- data/target.csv: the monthly sales target per region (one row per region and month);
- data/controls.json: the row counts and totals "the source system's own report" gives, which the contracts
  carry as control totals (a total computed from the landed rows would only be an internal tie).
Standard library only.
"""
from __future__ import annotations

import calendar
import csv
import datetime as dt
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 20261125
REGIONS = ("East", "North", "South", "West")
WEIGHTS = (0.30, 0.20, 0.22, 0.28)
PRODUCTS = ("P1", "P2", "P3", "P4", "P5", "P6")
MONTHS = [dt.date(2026, m, calendar.monthrange(2026, m)[1]) for m in range(1, 7)]


def sales_rows(rng: random.Random) -> list[list[str]]:
    rows: list[list[str]] = []
    invoice = 100
    while len(rows) < 600:
        invoice += 1
        day = dt.date(2026, 1, 1) + dt.timedelta(days=rng.randrange(181))   # 2026-01-01 to 2026-06-30
        region = rng.choices(REGIONS, WEIGHTS)[0]
        for line_no in range(1, rng.choice((1, 1, 2, 2, 3)) + 1):
            if len(rows) == 600:
                break
            product = "" if rng.random() < 0.03 else rng.choice(PRODUCTS)  # 3% of lines carry no product code
            amount = rng.randrange(100, 18000) * 50                          # 5,000 to 899,950 KRW
            rows.append([f"{invoice:06d}", str(line_no), day.isoformat(), region, product, str(amount)])
    return rows


def target_rows(sales: list[list[str]], rng: random.Random) -> list[list[str]]:
    """Target = the region's actual for the month times a factor from 0.92 to 1.08, rounded to 1,000,000 KRW,
    so some months miss and some beat the target."""
    actual: dict[tuple[str, dt.date], int] = {}
    for r in sales:
        d = dt.date.fromisoformat(r[2])
        me = dt.date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
        actual[(r[3], me)] = actual.get((r[3], me), 0) + int(r[5])
    out = []
    for region in REGIONS:
        for me in MONTHS:
            a = actual.get((region, me), 0)
            f = rng.choice((0.92, 0.96, 1.0, 1.04, 1.08))
            t = int(round(a * f / 1_000_000)) * 1_000_000
            out.append([region, me.isoformat(), str(t)])
    return out


def main() -> int:
    rng = random.Random(SEED)
    sales = sales_rows(rng)
    target = target_rows(sales, rng)
    data = HERE / "data"
    data.mkdir(exist_ok=True)
    with open(data / "sales.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["invoice_id", "line_no", "inv_date", "region", "product", "amount"])
        w.writerows(sales)
    with open(data / "target.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["region", "month_end", "target_amount"])
        w.writerows(target)
    controls = {
        "note": "Row counts and totals as the source system's own reports give them (generated with the data).",
        "sales": {"rows": len(sales), "amount": sum(int(r[5]) for r in sales)},
        "target": {"rows": len(target), "target_amount": sum(int(r[2]) for r in target)},
    }
    (data / "controls.json").write_text(json.dumps(controls, indent=1) + "\n", encoding="utf-8")
    print(f"data/sales.csv: {len(sales)} rows; data/target.csv: {len(target)} rows")
    print(json.dumps(controls))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
