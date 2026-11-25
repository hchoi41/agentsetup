"""Write the ar_aging template's source extract (deterministic, seed 20261125).

    python -P -X utf8 -B make_data.py        # writes data/open_items.csv and data/controls.json

data/open_items.csv: open receivables at 2026-06-30 (one row per open invoice):
invoice_id, customer, invoice_date, due_date, open_amount.
Standard library only.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
CUSTOMERS = ("Arden", "Birch", "Cobalt", "Dunmore", "Elm", "Fairview", "Granite", "Harbor")
TERMS = (30, 30, 45, 60)                     # payment terms in days


def main() -> int:
    rng = random.Random(20261125)
    rows = []
    for n in range(1, 241):
        cust = CUSTOMERS[rng.randrange(len(CUSTOMERS))]
        inv = dt.date(2026, 1, 1) + dt.timedelta(days=int(rng.triangular(0, 180, 170)))   # mostly recent
        due = inv + dt.timedelta(days=rng.choice(TERMS))
        amount = rng.randrange(10, 3000) * 1000                                          # 10,000 to 2,999,000 KRW
        rows.append([f"INV{n:05d}", cust, inv.isoformat(), due.isoformat(), str(amount)])
    (HERE / "data").mkdir(exist_ok=True)
    with open(HERE / "data" / "open_items.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["invoice_id", "customer", "invoice_date", "due_date", "open_amount"])
        w.writerows(rows)
    controls = {"note": "Row count and total as the receivables ledger's own report gives them (generated with the data).",
                "open_items": {"rows": len(rows), "open_amount": sum(int(r[4]) for r in rows)}}
    (HERE / "data" / "controls.json").write_text(json.dumps(controls, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(controls))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
