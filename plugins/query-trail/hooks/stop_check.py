"""stop_check.py: Stop hook (design spec v1 sections 1.5, 2.1; T14 F7).

While a run is active it refuses to end the turn (JSON {"decision": "block"} on stdout) when
- gate_request.json is open: a script exited 3 and Max has not answered (decide.py archives and removes
  the request once his answer is recorded), or
- run_state.json shows the phase `in_progress`: a phase was started and has no exit status yet, unless
  state.py recorded a blocked reason (`set --blocked`, fix r2 W7): a script exited 2 and Max must fix the cause,
  so the turn may end with the reason reported.
It honours stop_hook_active (the second Stop in a row is let through, so the loop guard holds, T14 F7).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _hooklib as hl  # noqa: E402


def reason(run: Path) -> str | None:
    """Why the turn may not end, or None."""
    req = hl.read_json(run / "gate_request.json")
    if isinstance(req, dict):
        return (f"gate {req.get('gate')} is open in {run.name} (gate_request.json from {req.get('script')}: "
                f"{req.get('reason')}). Ask Max with AskUserQuestion and record his answer with decide.py "
                "before ending the turn.")
    st = hl.run_state(run)
    if st and st.get("phase_status") == "in_progress" and not st.get("blocked_reason"):
        return (f"{st['phase']} is in_progress in {run.name} with no exit status. Finish the phase script and set "
                "the state (state.py set --status done or waiting_gate; after an exit 2, state.py set --blocked "
                "\"<reason>\") before ending the turn.")
    return None


def main() -> None:
    d = hl.read_payload()
    run = hl.active_run()
    if run is None or d.get("stop_hook_active"):
        sys.exit(0)
    why = reason(run)
    if why:
        sys.stdout.write(json.dumps({"decision": "block", "reason": "query-trail: " + why}) + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
