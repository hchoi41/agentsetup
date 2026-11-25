"""formula_audit.py: audit any .xlsx against the rule-of-thumb-model rules (Max's outline names this file).

Run:  python -P -X utf8 -B <SKILL>/scripts/formula_audit.py WORKBOOK [--strict] [--json FILE] [--limit N]
Same as `rotm.py audit`. Exit codes: 0 nothing to fix, 1 findings (with --strict any warning), 2 refused,
3 not available, 4 internal error. Owner: M4 (design section 14).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def main(argv: list[str] | None = None) -> int:
    from lib import audit, common
    parser = argparse.ArgumentParser(prog="formula_audit.py",
                                     description="audit any workbook against the rule-of-thumb-model rules")
    audit.add_audit_arguments(parser)
    args = parser.parse_args(argv)
    args.command = "audit"
    return common.run_command(audit.run_audit, args, name="audit")


if __name__ == "__main__":
    sys.exit(main())
