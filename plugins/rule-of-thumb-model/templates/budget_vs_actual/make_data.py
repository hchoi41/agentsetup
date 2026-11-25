"""Write the budget_vs_actual template's source extract (deterministic, seed 20261125).

    python -P -X utf8 -B make_data.py        # writes data/gl_actuals.csv and data/controls.json

data/gl_actuals.csv: general-ledger expense postings, January to June 2026 (one row per posting):
entry_id, posting_date, cost_centre, account, amount. The budget is an input grid in model.spec.json.
Standard library only.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
CENTRES = {"CC100": 9.0, "CC200": 6.0, "CC300": 12.0, "CC400": 4.0}      # mean monthly spend, KRW million
ACCOUNTS = ("6100", "6200", "6300", "6400")


def main() -> int:
    rng = random.Random(20261125)
    rows = []
    n = 0
    for month in range(1, 7):
        for cc, mean in CENTRES.items():
            for _ in range(rng.randrange(14, 22)):
                n += 1
                day = dt.date(2026, month, rng.randrange(1, 29))
                acct = rng.choice(ACCOUNTS)
                amount = int(rng.gauss(mean * 1_000_000 / 18, mean * 1_000_000 / 60)) // 100 * 100
                amount = max(amount, 10_000)
                if rng.random() < 0.04:
                    amount = -amount // 2                  # a reversal: negative postings occur in a ledger
                rows.append([f"JE{n:05d}", day.isoformat(), cc, acct, str(amount)])
    (HERE / "data").mkdir(exist_ok=True)
    with open(HERE / "data" / "gl_actuals.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["entry_id", "posting_date", "cost_centre", "account", "amount"])
        w.writerows(rows)
    controls = {"note": "Row count and total as the ledger's own trial balance report gives them (generated with the data).",
                "gl_actuals": {"rows": len(rows), "amount": sum(int(r[4]) for r in rows)}}
    (HERE / "data" / "controls.json").write_text(json.dumps(controls, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(controls))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
