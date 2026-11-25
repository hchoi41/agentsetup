"""The worked example's key results computed from the CSV files alone (not from the workbook): expected.json.

    python -P -X utf8 -B expected.py

Standard library only. Amounts in KRW thousand (the model's display scale, 1,000 KRW per unit shown).
"""
from __future__ import annotations

import calendar
import csv
import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGIONS = ("East", "North", "South", "West")
MONTHS = [dt.date(2026, m, calendar.monthrange(2026, m)[1]).isoformat() for m in range(1, 7)]
SCALE = Decimal(1000)


def month_end(text: str) -> str:
    d = dt.date.fromisoformat(text)
    return dt.date(d.year, d.month, calendar.monthrange(d.year, d.month)[1]).isoformat()


def main() -> int:
    actual = {(r, m): Decimal(0) for r in REGIONS for m in MONTHS}
    with open(HERE / "data" / "sales.csv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            actual[(row["region"], month_end(row["inv_date"]))] += Decimal(row["amount"]) / SCALE
    target = {(r, m): Decimal(0) for r in REGIONS for m in MONTHS}
    with open(HERE / "data" / "target.csv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            target[(row["region"], row["month_end"])] += Decimal(row["target_amount"]) / SCALE
    ytd = {}
    for r in REGIONS:
        a = sum(actual[(r, m)] for m in MONTHS)
        t = sum(target[(r, m)] for m in MONTHS)
        ytd[r] = {"actual": float(a), "target": float(t), "variance": float(a - t),
                  "variance_pct": float((a - t) / t) if t else 0.0}
    a_all = sum(actual.values())
    t_all = sum(target.values())
    cum, running = [], Decimal(0)
    for m in MONTHS:
        running += sum(actual[(r, m)] for r in REGIONS) - sum(target[(r, m)] for r in REGIONS)
        cum.append(float(running))
    out = {
        "unit": "KRW thousand",
        "note": "computed from data/sales.csv and data/target.csv by expected.py, without the workbook",
        "actual_by_region_month": {r: [float(actual[(r, m)]) for m in MONTHS] for r in REGIONS},
        "target_by_region_month": {r: [float(target[(r, m)]) for m in MONTHS] for r in REGIONS},
        "ytd_by_region": ytd,
        "total": {"actual": float(a_all), "target": float(t_all), "variance": float(a_all - t_all),
                  "variance_pct": float((a_all - t_all) / t_all)},
        "cumulative_variance_by_month": cum,
        "months": MONTHS,
    }
    (HERE / "expected.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(out["total"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
