# What a Repeat Needs — by kind of work

Files are no longer chosen by job type: `10.INPUT` and `30.OUTPUT` always sync in full, and `20.PROCESS` never does. What still differs by kind of work is **what the recipe must record** and **what should already sit in `30.OUTPUT`** before syncing, because that is all a future project will have.

Use the closest profile in Step 4. If none fits, use `default_unknown`, propose a profile, and record it after confirmation.

---

## Profiles

### 1. `job_application` — applications & take-home assignments
Resumes, cover letters, essays, JD analysis, case studies, pitch decks, answer workbooks.

- **The recipe must record:** which JD requirements drove which choices; where each claim came from (this is the interview-defense trail); which versions were tried and why the final one won.
- **Should be in `30.OUTPUT`:** every file actually submitted, with the submitted version preserved; the validation / primary-source set that backs the claims; comparison/scoring models and the final pitch blueprint or outline when needed for interview defense. Record locked/ready snapshots and their role in the recipe; do not prune versions during sync.
- **Why:** months later this is interview prep. The recipe is what lets the user *defend* the work.

### 2. `financial_model` / `quantitative_research`
Valuation/forecast models, market or sector research, dashboards.

- **The recipe must record:** the assumptions and their sources; how the model is structured; the checks that were run and what they showed.
- **Should be in `30.OUTPUT`:** the final model, report and dashboard; the sources list.

### 3. `research_report`
Narrative research with mixed `.md` / `.xlsx` / `.html` outputs.

- **The recipe must record:** the question, the method, the sources, and how conflicting sources were resolved.
- **Should be in `30.OUTPUT`:** the report, supporting tables, the cited-sources file.

### 4. `analytics_code` — Excel / SQL / R / Python
Notebooks, query scripts, transformation/analysis code, charts.

- **The recipe must record:** how to rebuild the environment; the run order; the data schema; what each script is for.
- **Should be in `30.OUTPUT`:** the scripts, the environment spec (`requirements.txt`, `environment.yml`, `renv.lock`, `pyproject.toml`, `uv.lock`), the final report or chart; a data sample and schema when the raw data is not in `10.INPUT`.
- **Why:** a script + environment spec + recipe reproduces the result anywhere. Virtualenvs and caches stay in `20.PROCESS`.
- **Notebook note:** when embedded outputs make a working notebook unwieldy, prepare a clean executable notebook and a rendered export as explicit deliverables. Preserve the original working notebook locally; do not rewrite an existing INPUT or OUTPUT file merely for sync.

### 5. `scraping`
Scrapers/crawlers and the data they collect.

- **The recipe must record:** what was fetched, from where, when, and how many; the source's ToS and robots constraints; any personal data in the result.
- **Should be in `30.OUTPUT`:** the scraper scripts, selectors/config (non-secret), the cleaned dataset, a run manifest.

### 6. `automation` / `pipelines`
Scheduled jobs, ETL, glue scripts, agent tooling. Usually on its way to becoming an operation.

- **The recipe must record:** the trigger and schedule; failure handling; what "healthy" looks like and how it is checked.
- **Should be in `30.OUTPUT`:** the scripts, schedule/trigger definitions, non-secret config, the runbook.

### 7. `default_unknown` — fallback
- **The recipe must record:** the goal, the steps, the reason for each, the checks.
- **Should be in `30.OUTPUT`:** whatever was delivered.

---

## How to add a profile

When the user's work drifts into something these don't cover:

1. **Infer** — from the project's files, identify what a repeat would need that a generic recipe would miss.
2. **Propose** — show the user a draft profile (what it covers · the recipe must record · should be in `30.OUTPUT`) and confirm.
3. **Record** — append the confirmed profile here. Keep it terse.
4. **Promote a lesson (if durable)** — log a reusable rule in `14.LESSONS_LEARNED/`.

Keep profiles small and orthogonal. Adding a new kind of work is a one-entry change, never a rewrite of the skill.
