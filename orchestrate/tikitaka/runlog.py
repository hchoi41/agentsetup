"""Run directory and code-appended records (charter §8)."""
from __future__ import annotations

import json
import time
from pathlib import Path


class RunLog:
    def __init__(self, run_dir: Path):
        self.dir = run_dir
        for sub in ("artifacts", "reviews", "verdicts", "agreements", "consultations"):
            (run_dir / sub).mkdir(parents=True, exist_ok=True)
        self.tokens: list = []
        self.cutoffs: list = []
        self.events: list = []
        self._agreement_seq: dict = {}
        self._consult_n = 0
        (run_dir / "process.md").write_text("# Process record (appended by code)\n\n", encoding="utf-8")

    @staticmethod
    def now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S")

    def event(self, text: str, **data):
        self.events.append({"t": self.now(), "text": text, **data})
        with open(self.dir / "process.md", "a", encoding="utf-8") as f:
            f.write(f"- {self.now()} {text}\n")

    def narrative(self, text: str):
        with open(self.dir / "process.md", "a", encoding="utf-8") as f:
            f.write(f"\n## Orchestrator narrative\n{text}\n\n")

    def write_json(self, rel: str, obj):
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        return p

    def add_tokens(self, role: str, seat: str, milestone: str, rnd, n: int, usage: dict | None = None):
        rec = {"role": role, "seat": seat, "milestone": milestone, "round": rnd, "tokens": n}
        if usage:
            rec["usage"] = usage
        self.tokens.append(rec)
        self.write_json("tokens.json", self.tokens)

    def total_tokens(self) -> int:
        return sum(t["tokens"] for t in self.tokens)

    def add_cutoff(self, role: str, seat: str, reason: str, trigger: str):
        self.cutoffs.append({"t": self.now(), "role": role, "seat": seat, "trigger": trigger, "reason": reason})
        self.write_json("cutoffs.json", self.cutoffs)
        self.event(f"CUT-OFF {role}/{seat}: {reason} (trigger: {trigger}); output marked incomplete")

    def add_agreement(self, rec: dict, key: str) -> dict:
        seq = self._agreement_seq.get(key, 0) + 1
        self._agreement_seq[key] = seq
        rec = {**rec, "seq": seq, "timestamp": self.now()}
        self.write_json(f"agreements/{key}_seq{seq}.json", rec)
        return rec

    def latest_agreement(self, key: str) -> dict | None:
        seq = self._agreement_seq.get(key)
        if not seq:
            return None
        return json.loads((self.dir / "agreements" / f"{key}_seq{seq}.json").read_text(encoding="utf-8"))

    def gate_passed(self, key: str) -> bool:
        a = self.latest_agreement(key)
        return bool(a and a.get("agree") is True and (a.get("proposal") or {}).get("by") == "orchestrator")

    def add_consultation(self, step: str, question: str, answer: str, model: str):
        self._consult_n += 1
        self.write_json(f"consultations/{self._consult_n}.json",
                        {"step": step, "question": question, "answer": answer, "model": model, "timestamp": self.now()})
