# Claude Operating Instructions

Startup, every session:

1. `AGENTS.md` (auto-loaded) and `00.ABOUT/PROJECT_INDEX.md`.
2. If `PROJECT_INDEX.md` names a `must_read` folder, read every file in it (top level only — the user curates it).

Execution routing is **determinism-first** (ruling 2026-08-22): anything that can be executed by code, a script, or an explicit assertion must be — prompts are not a substitute. LLM judgment is reserved (and required) where code cannot decide, and its output crosses a deterministic gate before it acts. Classify per `Execution Routing` in `PROJECT_GOVERNANCE.md` (`D0`/`D1`/`D2`) and record the label.

The **Fact & Framing Protocol** in `PROJECT_GOVERNANCE.md` is binding: on facts about Max's own life and work, flag + propose — never adjudicate. Max's adopted framing governs; non-adopted values are "미채택 프레임," not errors.

Everything else — RAM, INPUT, templates, OUTPUT_FINAL, HDD, protocols — loads on demand only, when the task needs it or the user points to it. Never read `13.MEMORY_HDD` unprompted.

Full rules live in `PROJECT_GOVERNANCE.md`. Coordination rules live in `orchestration_protocol.md` — read only when coordinating; it cannot override governance.
