"""discover.py: P2 table discovery over offline warehouse results (design spec v1 sections 1.3, 1.4, 2.2 P2; D-15).

The privilege ladder is a plan of queries that stage 2a's runner (bq.py, dry run and cap)
executes; this script never touches the network. Order, per v4 P2 and BQ01:

  rung R: region-qualified INFORMATION_SCHEMA.SCHEMATA (lists datasets; denied even for the
          owner in BQ01, so it is tried, never relied on);
  rung D: the per-dataset loop (TABLES, COLUMN_FIELD_PATHS + COLUMNS, TABLE_OPTIONS,
          PARTITIONS, KEY_COLUMN_USAGE) over the datasets R returned, else over the aliases
          named in discovery_terms.json (the default listing method, spec 2.2 P2);
  rung S: the sidecar lane (sidecar.json, declared by Max), at any rung;
  rung P: profiling queries for the shortlist: grain test COUNT(*) vs COUNT(DISTINCT key) in
          the derived-table form, NULL key count; freshness as three signals (business date
          MAX(date), ingestion watermark from a named control table, completeness against
          expected partitions); NULL rates of the required columns.

Rule in code (spec 1.3, "discover never reports absence from one narrow search"): a rung is
exhausted only when its query ran and returned (`status: ok`). A term with no candidate is
reported "not found" only when every listing rung is exhausted; otherwise "not established",
naming the rungs that were denied or not run, and `build` exits 3.

Inputs (run folder):
  discovery_terms.json   D1 proposal: terms, required_columns, datasets (env aliases),
                         key_hypotheses, date_columns, period {start, end}, reference_date,
                         control_table, top_k, stale_days.
  discovery_inputs/<query_id>.json   written by the 2a runner, one per plan query:
                         {"query_id", "status": ok|denied|error|over_cap|not_run, "rows": [...]}.
  discovery_inputs/sidecar.json      optional sidecar lane: {"tables": [{dataset, table,
                         description, columns: [{name, type, description}]}]}.
Identifiers: datasets are env-file aliases and the project is the {{project}} placeholder;
real IDs never enter run files (D-12).

Outputs: discover_plan.json, 05_catalog.csv, 06_profiles.csv, 07_candidates.csv,
08_shortlist.csv and discover_status.json (build), 14_discovery_lock.json (lock), 13_table_decision.md
(decide), drift report (drift).

Fix r3 (spec v4 2.2 P2):
- B3-5: discovery_terms.json is validated against config/discovery_terms.schema.json before anything is read; a
  wrong shape is refused (exit 2) naming the field and the expected shape, never a traceback; a bare string where a
  list is expected is refused, not split into characters. Listing or profiling queries the warehouse refused
  (status denied or error) are listed as a "sources refused" line of 08_shortlist.csv, in discover_status.json
  `sources_refused` and in the G2 sentence (`g2_sentence`). A table whose name appears in 00_question.txt gets
  QUESTION_BONUS on its score.
- B3-8: `lock --tables` writes the tables Max kept at G2 and the shortlisted ones he dropped (`dropped_at_g2`).
  After the lock, plan profiles only locked tables, build shortlists only locked tables (dropped ones appear as one
  "dropped at G2" row each and nothing else), and warehouse_jobs.py, trap_scan.py and pin_plan.py refuse (exit 2)
  a table outside the lock.
- B3-11: after the lock, a locked table whose freshness signals disagree (data ending before the period end, missing
  days, rows dated after the period, future-dated rows) opens G13 (exit 3, gate_request.json) unless a value-map row
  of kind `period` names that table and its date column; the shortlist's freshness_agree column says which applied.
- B3-12: `decide` writes 13_table_decision.md, the packet's table decision, from the lock, the profiles, the value
  map, the metric card bindings and the pin plan when there is one.
- W32: the grain detail says what was counted (rows inside the period on the date column, or the whole table).

Fix r4 (spec v5 2.2 P2):
- C4-4: `lock --tables` requires the run's current G2 decision to be an approve whose input_hash is the table-set
  sha256 of exactly those tables (decide.py --gate G2 --tables; lib.g2lock); a different set is refused ("table set
  differs from the G2 decision: ask G2 again with --supersede"). Nothing is profiled before the lock (`plan` lists
  listing queries only); after it, plan, build and decide refuse while the lock and the G2 decision disagree.
- C4-7, C4-9: a freshness disagreement is settled by a period row on the table's date column, a `not_used` row on it,
  or a G13 row with outcome `answered` and check_id `freshness:<table>`; the shortlist says which. The G13 request
  lists one check id per table (`details.check_ids`).
- C4-10: a later exit 0 of build archives build's own open gate_request.json (runctx.archive_own_request).

CLI:  discover.py --run-dir R plan | build | lock --tables a,b | decide | drift
Exit: 0 PASS; 1 drift against the lock (G8); 2 refused: no warehouse call is made by this script; local effects:
      none (stderr names the input to fix); 3 rung 0, absence not established, a profile over the per-job cap,
      nothing shortlisted (G2/G15) or a freshness disagreement after the lock (G13); 05 to 08 and
      discover_status.json are written first so Max sees what the gate is about.

Fix r1 B-M7 (spec v2 2.2 P2): a table with an expiration is penalised (EXPIRATION_PENALTY on the name
signal) and flagged in notes, not excluded: a frozen copy with an expiration date is often the very table
(the slice's qt_* copies expire in 2027); a build that shortlists nothing opens G2 instead of exiting 0.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import g2lock  # noqa: E402  fix r4 C4-4: the lock is bound to the G2 decision
from lib import runctx as c2, schema_check, valuemap  # noqa: E402
from lib import refusal  # noqa: E402  fix r3 B3-4: exit-2 local effects on stderr
from lib.exit_codes import CheckFailedError, ExitCode, RefusedError  # noqa: E402

TERMS = "discovery_terms.json"
TERMS_SCHEMA = c2.CONFIG_DIR / "discovery_terms.schema.json"
QUESTION = "00_question.txt"
VALUE_MAP = "11_value_map.csv"
TABLE_DECISION = "13_table_decision.md"
QUESTION_BONUS = 4
REFUSED_STATUSES = ("denied", "error")
DROPPED = "dropped at G2"
INPUTS = "discovery_inputs"
PLAN = "discover_plan.json"
CATALOG = "05_catalog.csv"
PROFILES = "06_profiles.csv"
CANDIDATES = "07_candidates.csv"
SHORTLIST = "08_shortlist.csv"
LOCK = "14_discovery_lock.json"
EXHAUSTED = "ok"

EXCLUDE_TOKENS = {"tmp", "temp", "bak", "backup", "old", "test", "copy", "scratch", "sandbox", "dev", "deprecated"}
LAYER_PLUS = ("fct_", "fact_", "dim_", "mart_", "agg_", "gold_")
LAYER_MINUS = ("stg_", "raw_", "src_", "staging_")
PERSONAL_DATASET = re.compile(r"^(analytics_|dev_|scratch|sandbox|tmp_|personal_)", re.I)
DATE_STAMP = re.compile(r"_(19|20)\d{6}$")
EXPIRATION_PENALTY = 2
STOP = set("the a an of by for in on to and per what was is were are as at from with total show give me my our".split())
DATE_TYPES = ("DATE", "TIMESTAMP", "DATETIME")

CATALOG_COLUMNS = (("dataset", "VARCHAR"), ("table_name", "VARCHAR"), ("table_ref", "VARCHAR"),
                   ("table_type", "VARCHAR"), ("table_description", "VARCHAR"), ("expiration", "VARCHAR"),
                   ("last_modified_utc", "VARCHAR"), ("column_name", "VARCHAR"), ("data_type", "VARCHAR"),
                   ("column_description", "VARCHAR"), ("is_partitioning", "BOOLEAN"),
                   ("is_declared_key", "BOOLEAN"), ("evidence_class", "VARCHAR"), ("rung", "VARCHAR"),
                   ("source_query", "VARCHAR"))
PROFILE_COLUMNS = (("table_ref", "VARCHAR"), ("profile", "VARCHAR"), ("query_id", "VARCHAR"),
                   ("status", "VARCHAR"), ("metric", "VARCHAR"), ("value", "VARCHAR"),
                   ("assertion", "VARCHAR"), ("detail", "VARCHAR"))
CANDIDATE_COLUMNS = (("table_ref", "VARCHAR"), ("score", "INTEGER"), ("excluded", "BOOLEAN"),
                     ("exclusion_reason", "VARCHAR"), ("name_signal", "INTEGER"), ("column_signal", "INTEGER"),
                     ("freshness_signal", "INTEGER"), ("grain_signal", "INTEGER"), ("matched_terms", "VARCHAR"),
                     ("missing_required", "VARCHAR"), ("notes", "VARCHAR"), ("evidence_class", "VARCHAR"))
SHORTLIST_COLUMNS = (("rank", "INTEGER"), ("table_ref", "VARCHAR"), ("score", "INTEGER"),
                     ("key_hypothesis", "VARCHAR"), ("date_column", "VARCHAR"), ("grain_test", "VARCHAR"),
                     ("freshness_business", "VARCHAR"), ("freshness_watermark", "VARCHAR"),
                     ("freshness_completeness", "VARCHAR"), ("freshness_agree", "VARCHAR"),
                     ("matched_terms", "VARCHAR"), ("notes", "VARCHAR"))


# ---------------------------------------------------------------- small helpers
def toks(text: Any) -> list[str]:
    """Lower-case word tokens without stop words; a plural 's' is dropped (T16 shortlist rule)."""
    out = []
    for w in re.split(r"[^a-z0-9]+", str(text or "").lower()):
        if not w or w in STOP:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def table_ref(dataset: str, table: str) -> str:
    """Run-file table reference: <dataset alias>.<table> (no project ID, D-12)."""
    return f"{dataset}.{table}"


def sql_ref(ref: str) -> str:
    """SQL text for a table reference, with the project placeholder the runner renders."""
    ds, tb = ref.split(".", 1)
    return f"`{{{{project}}}}.{{{{{ds}}}}}.{tb}`"


def _ident(name: str) -> str:
    return "`" + name.replace("\\", "\\\\").replace("`", "\\`") + "`"


def _lit(v: Any) -> str:
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _qid(*parts: str) -> str:
    return "_".join(re.sub(r"[^A-Za-z0-9]+", "_", p).strip("_") for p in parts)


def validate_terms(t: Any) -> None:
    """Refuse (exit 2) a discovery_terms.json that does not match config/discovery_terms.schema.json (fix r3 B3-5).

    The message names each wrong field and its expected shape, so the author fixes the file without reading code.
    """
    schema = schema_check.load_schema(TERMS_SCHEMA)
    errs = schema_check.validate(t, schema)
    if not errs:
        return
    props = schema.get("properties", {})
    lines = []
    for e in errs[:8]:
        path = e.split(":", 1)[0]
        field = path.split("/")[1] if path.count("/") >= 1 and path != "$" else ""
        shape = (props.get(field) or {}).get("x-shape") if field else None
        if shape is None and "unexpected property" in e:
            shape = "one of: " + ", ".join(props)
        lines.append(f"{e}" + (f"; expected shape of {field or 'the file'}: {shape}" if shape else ""))
    raise RefusedError(f"{TERMS} does not match config/{TERMS_SCHEMA.name}: " + " | ".join(lines))


def load_terms(run_dir: Path) -> dict[str, Any]:
    """discovery_terms.json, validated (fix r3 B3-5), with defaults."""
    t = c2.read_input_json(run_dir / TERMS, TERMS)
    validate_terms(t)
    t.setdefault("terms", [])
    t.setdefault("required_columns", [])
    t.setdefault("datasets", [])
    t.setdefault("key_hypotheses", {})
    t.setdefault("date_columns", {})
    t.setdefault("top_k", 5)
    t.setdefault("stale_days", 30)
    return t


def load_lock(run_dir: Path) -> dict[str, Any] | None:
    """14_discovery_lock.json or None (before G2)."""
    p = run_dir / LOCK
    return c2.read_input_json(p, LOCK) if p.is_file() else None


def locked_tables(run_dir: Path) -> list[str] | None:
    """The table refs Max kept at G2 (the lock), or None before the lock (fix r3 B3-8)."""
    lock = load_lock(run_dir)
    return None if lock is None else [str(r) for r in lock.get("tables", {})]


def refuse_outside_lock(run_dir: Path, refs: Iterable[str], who: str) -> None:
    """Exit 2 when a table ref is outside the lock (after G2); a no-op before the lock (fix r3 B3-8)."""
    keep = locked_tables(run_dir)
    if keep is None:
        return
    norm = {_bare(r) for r in keep}
    out = sorted({str(r) for r in refs if _bare(str(r)) not in norm})
    if out:
        dropped = set((load_lock(run_dir) or {}).get("dropped_at_g2") or [])
        raise RefusedError(f"{who}: table(s) outside the G2 lock (14_discovery_lock.json): " + ", ".join(
            f"{r}{' (dropped at G2)' if _bare(r) in {_bare(d) for d in dropped} else ''}" for r in out)
            + "; locked: " + ", ".join(keep))


def _bare(ref: str) -> str:
    """`{{project}}.{{ds}}.t`, `{{ds}}.t` and `ds.t` all compare as `ds.t`."""
    parts = [p.strip("`{} ") for p in str(ref).replace("`", "").split(".")]
    if len(parts) == 3 and parts[0] == "project":
        parts = parts[1:]
    return ".".join(parts).lower()


def question_text(run_dir: Path) -> str:
    """00_question.txt (Max's words), or '' when it does not exist."""
    p = run_dir / QUESTION
    return p.read_text(encoding="utf-8-sig").lower() if p.is_file() else ""


def load_result(run_dir: Path, query_id: str) -> dict[str, Any] | None:
    """One runner result file, or None when it does not exist (the query has not run)."""
    p = run_dir / INPUTS / f"{query_id}.json"
    if not p.is_file():
        return None
    r = c2.read_input_json(p, p.name)
    if r.get("status") not in ("ok", "denied", "error", "over_cap", "not_run"):
        raise RefusedError(f"{p.name}: status must be ok, denied, error, over_cap or not_run")
    r.setdefault("rows", [])
    return r


def exhausted(res: Mapping[str, Any] | None) -> bool:
    """A rung counts as exhausted only when its query ran and returned (spec 1.3, v4 §6)."""
    return res is not None and res.get("status") == EXHAUSTED


def _status(res: Mapping[str, Any] | None) -> str:
    return "not_run" if res is None else str(res["status"])


# ---------------------------------------------------------------- the ladder as a plan
def listing_queries(dataset: str) -> list[dict[str, Any]]:
    """Per-dataset INFORMATION_SCHEMA loop for one dataset alias (rung D)."""
    base = f"`{{{{project}}}}.{{{{{dataset}}}}}`.INFORMATION_SCHEMA"
    q = [
        ("tables", f"SELECT table_name, table_type, creation_time FROM {base}.TABLES"),
        ("columns", "SELECT c.table_name, c.column_name, c.data_type, f.description, c.is_partitioning_column "
                    f"FROM {base}.COLUMNS c LEFT JOIN {base}.COLUMN_FIELD_PATHS f "
                    "ON f.table_name = c.table_name AND f.field_path = c.column_name"),
        ("options", "SELECT table_name, option_name, option_value FROM "
                    f"{base}.TABLE_OPTIONS WHERE option_name IN ('description', 'expiration_timestamp', 'labels')"),
        ("partitions", "SELECT table_name, MAX(last_modified_time) AS last_modified_time, COUNT(*) AS n_partitions "
                       f"FROM {base}.PARTITIONS GROUP BY table_name"),
        ("keys", f"SELECT table_name, column_name, constraint_name FROM {base}.KEY_COLUMN_USAGE"),
    ]
    return [{"query_id": _qid("D", dataset, k), "rung": "D", "dataset": dataset, "kind": k, "sql": s,
             "engine": "bigquery", "dry_run_first": True} for k, s in q]


def datasets_in_scope(run_dir: Path, terms: Mapping[str, Any]) -> tuple[list[str], str]:
    """Datasets from rung R when it returned, else the aliases named in discovery_terms.json."""
    r0 = load_result(run_dir, "R_region_schemata")
    if exhausted(r0):
        names = [str(r.get("schema_name")) for r in r0["rows"] if r.get("schema_name")]
        return sorted(set(names) | set(terms["datasets"])), "R"
    return list(dict.fromkeys(terms["datasets"])), "D"


def build_plan(run_dir: Path) -> dict[str, Any]:
    """The queries the runner should execute next, given the results present now."""
    terms = load_terms(run_dir)
    queries: list[dict[str, Any]] = [{
        "query_id": "R_region_schemata", "rung": "R", "kind": "schemata", "engine": "bigquery", "dry_run_first": True,
        "sql": "SELECT schema_name, location FROM `region-{{region}}`.INFORMATION_SCHEMA.SCHEMATA"}]
    datasets, source = datasets_in_scope(run_dir, terms)
    for ds in datasets:
        queries += listing_queries(ds)
    # fix r4 C4-4: no profiling before the G2 lock; after it, only while the lock is bound to the G2 decision
    profiles = profiling_queries(run_dir, terms) if (run_dir / LOCK).is_file() else []
    queries += profiles
    pending = [q["query_id"] for q in queries if load_result(run_dir, q["query_id"]) is None]
    return {"generated_utc": c2.utc_now_text(), "dataset_source": source, "datasets": datasets,
            "queries": queries, "pending": pending,
            "note": "the runner renders {{project}}, {{region}} and dataset aliases from the env file (D-12)"}


# ---------------------------------------------------------------- catalog (05)
def _rows(run_dir: Path, qid: str) -> list[dict[str, Any]]:
    r = load_result(run_dir, qid)
    return r["rows"] if exhausted(r) else []


def collect_catalog(run_dir: Path, datasets: Sequence[str]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Catalog rows (one per column) and the status of every listing query."""
    statuses: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for ds in datasets:
        ids = {k: _qid("D", ds, k) for k in ("tables", "columns", "options", "partitions", "keys")}
        for k, qid in ids.items():
            statuses[qid] = _status(load_result(run_dir, qid))
        tables = {str(r["table_name"]): r for r in _rows(run_dir, ids["tables"])}
        opts: dict[str, dict[str, Any]] = {}
        for r in _rows(run_dir, ids["options"]):
            opts.setdefault(str(r["table_name"]), {})[str(r["option_name"])] = r.get("option_value")
        parts = {str(r["table_name"]): r for r in _rows(run_dir, ids["partitions"])}
        keys: dict[str, set[str]] = {}
        for r in _rows(run_dir, ids["keys"]):
            keys.setdefault(str(r["table_name"]), set()).add(str(r["column_name"]))
        cols: dict[str, list[dict[str, Any]]] = {}
        for r in _rows(run_dir, ids["columns"]):
            cols.setdefault(str(r["table_name"]), []).append(r)
        for tname in sorted(set(tables) | set(cols)):
            t = tables.get(tname, {})
            o = opts.get(tname, {})
            base = {"dataset": ds, "table_name": tname, "table_ref": table_ref(ds, tname),
                    "table_type": t.get("table_type"), "table_description": o.get("description"),
                    "expiration": o.get("expiration_timestamp"),
                    "last_modified_utc": parts.get(tname, {}).get("last_modified_time"),
                    "evidence_class": "exact", "rung": "D", "source_query": ids["columns"]}
            for c in cols.get(tname, []) or [{}]:
                name = c.get("column_name")
                out.append({**base, "column_name": name, "data_type": c.get("data_type"),
                            "column_description": c.get("description"),
                            "is_partitioning": str(c.get("is_partitioning_column", "")).upper() in ("YES", "TRUE"),
                            "is_declared_key": name in keys.get(tname, set())})
    sidecar = run_dir / INPUTS / "sidecar.json"
    if sidecar.is_file():
        for t in c2.read_input_json(sidecar, "sidecar.json").get("tables", []):
            ref = table_ref(str(t["dataset"]), str(t["table"]))
            if any(r["table_ref"] == ref for r in out):
                continue
            base = {"dataset": t["dataset"], "table_name": t["table"], "table_ref": ref, "table_type": t.get("type"),
                    "table_description": t.get("description"), "expiration": None, "last_modified_utc": None,
                    "evidence_class": "declared", "rung": "S", "source_query": "sidecar.json"}
            for c in t.get("columns", []) or [{}]:
                out.append({**base, "column_name": c.get("name"), "data_type": c.get("type"),
                            "column_description": c.get("description"), "is_partitioning": False,
                            "is_declared_key": bool(c.get("key"))})
    return out, statuses


def group_tables(catalog: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Table-level view of catalog rows: fields plus a columns list."""
    tabs: dict[str, dict[str, Any]] = {}
    for r in catalog:
        t = tabs.setdefault(r["table_ref"], {**{k: r[k] for k in ("dataset", "table_name", "table_ref", "table_type",
                                                                   "table_description", "expiration",
                                                                   "last_modified_utc", "evidence_class")},
                                            "columns": []})
        if r.get("column_name"):
            t["columns"].append(r)
    return tabs


# ---------------------------------------------------------------- scoring (07)
def score_table(t: Mapping[str, Any], terms: Mapping[str, Any], reference_date: dt.date | None,
                question: str = "") -> dict[str, Any]:
    """Name, column, freshness and grain signals for one table (D0 filter and rank); a table named in
    00_question.txt gets QUESTION_BONUS (fix r3 B3-5, walk W26)."""
    q = set(toks(" ".join(map(str, terms.get("terms", [])))))
    name = str(t["table_name"])
    ntoks = set(toks(name))
    notes, excl = [], []
    name_sig = 3 * len(q & ntoks) + 2 * len(q & set(toks(t.get("table_description"))))
    if question and re.search(rf"(?<![a-z0-9_]){re.escape(name.lower())}(?![a-z0-9_])", question):
        name_sig += QUESTION_BONUS
        notes.append(f"named in {QUESTION}")
    low = name.lower()
    if low.startswith(LAYER_PLUS):
        name_sig += 1
    if low.startswith(LAYER_MINUS):
        name_sig -= 1
    bad = sorted(EXCLUDE_TOKENS & set(re.split(r"[^a-z0-9]+", low)))
    if bad:
        excl.append("name token " + ",".join(bad))
    if DATE_STAMP.search(low):
        excl.append("date-stamped copy")
    if PERSONAL_DATASET.match(str(t["dataset"])):
        excl.append("personal or sandbox dataset")
    if t.get("expiration"):
        name_sig -= EXPIRATION_PENALTY
        notes.append(f"expiration set ({t.get('expiration')}): penalised, not excluded; confirm it is not a "
                     "temporary copy")
    ttype = str(t.get("table_type") or "").upper()
    if ttype == "EXTERNAL":
        name_sig -= 2
        notes.append("external table: unpinnable")
    elif ttype in ("SNAPSHOT", "CLONE"):
        name_sig -= 1
        notes.append(f"{ttype.lower()}: prefer the base table")
    elif ttype == "VIEW":
        notes.append("view: base tables are resolved for pinning (spec 5.1)")
    colnames = [str(c["column_name"]) for c in t["columns"]]
    col_sig = sum(1 for c in t["columns"] if q & (set(toks(c["column_name"])) | set(toks(c.get("column_description")))))
    req = [str(x) for x in terms.get("required_columns", [])]
    missing = [x for x in req if x.casefold() not in {n.casefold() for n in colnames}]
    if req and not missing:
        col_sig += 2
    fresh_sig = 0
    lm = t.get("last_modified_utc")
    if lm and reference_date is not None:
        try:
            age = (reference_date - c2.parse_utc(str(lm)).date()).days
            if age > int(terms.get("stale_days", 30)):
                fresh_sig -= 2
                notes.append(f"last write {age} days before the reference date")
        except ValueError:
            notes.append("last_modified unparsable")
    grain_sig = 1 if any(c.get("is_declared_key") for c in t["columns"]) else 0
    matched = sorted(q & (ntoks | set(toks(t.get("table_description"))) | {w for c in colnames for w in toks(c)}))
    return {"table_ref": t["table_ref"], "score": name_sig + col_sig + fresh_sig + grain_sig, "excluded": bool(excl),
            "exclusion_reason": "; ".join(excl) or None, "name_signal": name_sig, "column_signal": col_sig,
            "freshness_signal": fresh_sig, "grain_signal": grain_sig, "matched_terms": ",".join(matched),
            "missing_required": ",".join(missing) or None, "notes": "; ".join(notes) or None,
            "evidence_class": t.get("evidence_class")}


def rank_candidates(cands: Sequence[Mapping[str, Any]], top_k: int) -> list[Mapping[str, Any]]:
    """Top-k non-excluded candidates with a positive score; ties broken by table_ref."""
    pool = [c for c in cands if not c["excluded"] and c["score"] > 0]
    return sorted(pool, key=lambda c: (-int(c["score"]), str(c["table_ref"])))[:top_k]


def absence_report(terms: Mapping[str, Any], cands: Sequence[Mapping[str, Any]],
                   statuses: Mapping[str, str], rung_r: str) -> list[dict[str, Any]]:
    """Per search term: found, not found (every listing rung exhausted) or not established."""
    unexhausted = sorted(q for q, s in statuses.items() if s != EXHAUSTED)
    out = []
    for term in terms.get("terms", []):
        tt = set(toks(term))
        hits = [c["table_ref"] for c in cands if tt & set(str(c["matched_terms"] or "").split(","))]
        if hits:
            out.append({"term": term, "status": "found", "detail": ",".join(hits)})
        elif statuses and not unexhausted:
            out.append({"term": term, "status": "not found",
                        "detail": f"every listing rung ran and returned ({len(statuses)} queries; datasets from {rung_r})"})
        else:
            out.append({"term": term, "status": "not established",
                        "detail": "rungs not exhausted: " + (",".join(unexhausted) or "no listing query ran")})
    return out


# ---------------------------------------------------------------- profiling (06)
def _date_column(t: Mapping[str, Any], terms: Mapping[str, Any]) -> str | None:
    named = terms.get("date_columns", {}).get(t["table_ref"])
    if named:
        return str(named)
    part = [c["column_name"] for c in t["columns"] if c.get("is_partitioning")
            and str(c.get("data_type", "")).upper().startswith(DATE_TYPES)]
    if part:
        return str(part[0])
    dates = [c["column_name"] for c in t["columns"] if str(c.get("data_type", "")).upper().startswith(DATE_TYPES)]
    return str(dates[0]) if dates else None


def _key(t: Mapping[str, Any], terms: Mapping[str, Any]) -> list[str]:
    k = terms.get("key_hypotheses", {}).get(t["table_ref"])
    if k:
        return [str(x) for x in k]
    return [str(c["column_name"]) for c in t["columns"] if c.get("is_declared_key")]


def _period_filter(dcol: str | None, terms: Mapping[str, Any]) -> str:
    p = terms.get("period") or {}
    if not dcol or not p.get("start") or not p.get("end"):
        return "TRUE"
    return f"CAST({_ident(dcol)} AS DATE) BETWEEN DATE {_lit(p['start'])} AND DATE {_lit(p['end'])}"


def profiling_sql(t: Mapping[str, Any], terms: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Profiling queries for one shortlisted table (warehouse-side aggregates, D-15)."""
    ref, src = t["table_ref"], sql_ref(t["table_ref"])
    dcol, key = _date_column(t, terms), _key(t, terms)
    where = _period_filter(dcol, terms)
    out = []
    if key:
        k = ", ".join(_ident(x) for x in key)
        nulls = " OR ".join(f"{_ident(x)} IS NULL" for x in key)
        out.append(("grain", f"SELECT COUNT(*) AS n_rows, (SELECT COUNT(*) FROM (SELECT DISTINCT {k} FROM {src} "
                              f"WHERE {where}) AS qt_d) AS n_distinct_key, COUNTIF({nulls}) AS n_null_key "
                              f"FROM {src} WHERE {where}"))
    if dcol:
        ref_date = terms.get("reference_date") or (terms.get("period") or {}).get("end")
        future = f"COUNTIF(CAST({_ident(dcol)} AS DATE) > DATE {_lit(ref_date)})" if ref_date else "NULL"
        out.append(("freshness", f"SELECT MIN(CAST({_ident(dcol)} AS DATE)) AS min_date, "
                                 f"MAX(CAST({_ident(dcol)} AS DATE)) AS max_date, {future} AS n_future FROM {src}"))
        out.append(("completeness", f"SELECT COUNT(DISTINCT CAST({_ident(dcol)} AS DATE)) AS n_days "
                                    f"FROM {src} WHERE {where}"))
    ctl = terms.get("control_table")
    if ctl:
        cond = f"{_ident(ctl['table_column'])} = {_lit(t['table_name'])}"
        if ctl.get("status_column"):
            cond += f" AND {_ident(ctl['status_column'])} = {_lit(ctl.get('success_value', 'SUCCESS'))}"
        out.append(("watermark", f"SELECT MAX({_ident(ctl['watermark_column'])}) AS watermark "
                                 f"FROM {sql_ref(ctl['ref'])} WHERE {cond}"))
    req = [c for c in terms.get("required_columns", []) if c in {x["column_name"] for x in t["columns"]}]
    if req:
        exprs = ", ".join(f"COUNTIF({_ident(c)} IS NULL) AS {_ident('null_' + c)}" for c in req)
        out.append(("nulls", f"SELECT COUNT(*) AS n_rows, {exprs} FROM {src} WHERE {where}"))
    return [{"query_id": _qid("P", ref, kind), "rung": "P", "table_ref": ref, "kind": kind, "sql": s,
             "engine": "bigquery", "dry_run_first": True, "key": key, "date_column": dcol} for kind, s in out]


def profiling_queries(run_dir: Path, terms: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Profiling queries for every table in 08_shortlist.csv and every table Max confirmed at G2 (the lock).

    The grain test always runs on the tables the query will read (spec 2.2 P2): a table Max confirmed at G2
    is profiled even when the candidate scoring excluded it (for example an expiring frozen copy). After the lock
    only the locked tables are profiled: a table dropped at G2 is never queried again (fix r3 B3-8).
    """
    datasets, _ = datasets_in_scope(run_dir, terms)
    tabs = group_tables(collect_catalog(run_dir, datasets)[0])
    if load_lock(run_dir) is None:
        return []  # fix r4 C4-4: nothing is profiled before the G2 lock
    refs = [str(r) for r in g2lock.require_bound(run_dir, "discover.py plan").get("tables", {})]
    return [q for ref in refs if ref in tabs for q in profiling_sql(tabs[ref], terms)]


def _expected_days(terms: Mapping[str, Any]) -> int | None:
    p = terms.get("period") or {}
    if not p.get("start") or not p.get("end"):
        return None
    return (dt.date.fromisoformat(str(p["end"])) - dt.date.fromisoformat(str(p["start"]))).days + 1


def _grain_scope(q: Mapping[str, Any], terms: Mapping[str, Any]) -> str:
    """What the grain query counted (walk W32): the rows inside the period on the date column, or the whole table."""
    p = terms.get("period") or {}
    if q.get("date_column") and p.get("start") and p.get("end"):
        return f"the rows with {q['date_column']} in {p['start']}..{p['end']} (not the whole table)"
    return "the whole table"


def evaluate_profiles(run_dir: Path, terms: Mapping[str, Any], queries: Sequence[Mapping[str, Any]]
                      ) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]], list[str]]:
    """06_profiles rows, a per-table summary, and the queries over the per-job cap."""
    rows, summary, over = [], {}, []
    period_end = (terms.get("period") or {}).get("end")
    exp_days = _expected_days(terms)
    for q in queries:
        res = load_result(run_dir, q["query_id"])
        s = summary.setdefault(q["table_ref"], {"grain_test": "not run", "freshness_business": "not run",
                                                "freshness_watermark": "not named" if not terms.get("control_table")
                                                else "not run", "freshness_completeness": "not run"})
        stat = _status(res)
        if stat == "over_cap":
            over.append(q["query_id"])
        if not exhausted(res) or not res["rows"]:
            rows.append({"table_ref": q["table_ref"], "profile": q["kind"], "query_id": q["query_id"], "status": stat,
                         "metric": None, "value": None, "assertion": "NA", "detail": res.get("error") if res else None})
            continue
        r0 = res["rows"][0]
        kind = q["kind"]
        if kind == "grain":
            ok = int(r0["n_rows"]) == int(r0["n_distinct_key"]) and int(r0["n_null_key"]) == 0
            s["grain_test"] = "PASS" if ok else "FAIL"
            scope = _grain_scope(q, terms)
            detail = (f"key {','.join(q['key'])}, counted over {scope}: {r0['n_rows']} rows, {r0['n_distinct_key']} "
                      f"distinct keys, {r0['n_null_key']} rows with a NULL key")
            for m in ("n_rows", "n_distinct_key", "n_null_key"):
                rows.append({"table_ref": q["table_ref"], "profile": kind, "query_id": q["query_id"], "status": stat,
                             "metric": m, "value": str(r0[m]), "assertion": s["grain_test"], "detail": detail})
            continue
        if kind == "freshness":
            mx = r0.get("max_date")
            ok = None if not period_end or mx is None else str(mx) >= str(period_end)
            s["freshness_business"] = "unknown" if ok is None else ("covers period" if ok else f"ends {mx}")
            if r0.get("n_future") not in (None, 0, "0"):
                s["freshness_business"] += f"; {r0['n_future']} future-dated rows"
            a = "NA" if ok is None else ("PASS" if ok else "FAIL")
        elif kind == "watermark":
            wm = r0.get("watermark")
            ok = None if not period_end or wm is None else str(wm)[:10] >= str(period_end)
            s["freshness_watermark"] = "unknown" if ok is None else ("covers period" if ok else f"loaded until {wm}")
            a = "NA" if ok is None else ("PASS" if ok else "FAIL")
        elif kind == "completeness":
            nd = int(r0.get("n_days") or 0)
            ok = None if exp_days is None else nd == exp_days
            s["freshness_completeness"] = "unknown" if ok is None else (
                "all expected partitions" if ok else f"{nd} of {exp_days} days")
            a = "NA" if ok is None else ("PASS" if ok else "FAIL")
        else:
            a = "NA"
        for m, v in r0.items():
            rows.append({"table_ref": q["table_ref"], "profile": kind, "query_id": q["query_id"], "status": stat,
                         "metric": str(m), "value": None if v is None else str(v), "assertion": a, "detail": None})
    for s in summary.values():
        sig = [s["freshness_business"], s["freshness_watermark"], s["freshness_completeness"]]
        good = [x in ("covers period", "all expected partitions") for x in sig if x not in ("not named",)]
        if any(x in ("not run", "unknown") for x in sig):
            s["freshness_agree"] = "incomplete"
        else:
            s["freshness_agree"] = "agree" if all(good) else "disagree: a question for Max"
    return rows, summary, over


# ---------------------------------------------------------------- build
def build(run_dir: Path) -> int:
    """Write 05 to 08 from the results present; exit 3 when absence or rung 0 needs Max."""
    terms = load_terms(run_dir)
    datasets, rung_r = datasets_in_scope(run_dir, terms)
    catalog, statuses = collect_catalog(run_dir, datasets)
    c2.write_records(run_dir / CATALOG, CATALOG_COLUMNS, [tuple(r[n] for n, _ in CATALOG_COLUMNS) for r in catalog])
    tabs = group_tables(catalog)
    ref_date = None
    if terms.get("reference_date") or (terms.get("period") or {}).get("end"):
        ref_date = dt.date.fromisoformat(str(terms.get("reference_date") or terms["period"]["end"]))
    question = question_text(run_dir)
    cands = [score_table(t, terms, ref_date, question) for t in tabs.values()]
    c2.write_records(run_dir / CANDIDATES, CANDIDATE_COLUMNS,
                     [tuple(c[n] for n, _ in CANDIDATE_COLUMNS) for c in sorted(cands, key=lambda c: c["table_ref"])])
    lock = load_lock(run_dir)
    if lock is not None:
        g2lock.require_bound(run_dir, "discover.py build")  # fix r4 C4-4
    by_ref = {c["table_ref"]: c for c in cands}
    if lock is None:
        short = rank_candidates(cands, int(terms["top_k"]))
        dropped: list[str] = []
    else:  # after G2 only the locked tables, in score order; scoring cannot add or remove one (fix r3 B3-8)
        short = []
        for ref in lock.get("tables", {}):
            if ref not in by_ref:
                continue
            c = dict(by_ref[ref])
            c["notes"] = "; ".join(x for x in (c.get("notes"), "confirmed by Max at G2 (14_discovery_lock.json)"
                                               + (f", scoring excluded it: {c['exclusion_reason']}"
                                                  if c.get("exclusion_reason") else "")) if x)
            short.append(c)
        short.sort(key=lambda c: (-int(c["score"]), str(c["table_ref"])))
        dropped = [str(r) for r in lock.get("dropped_at_g2") or []]
    queries = [q for c in short for q in profiling_sql(tabs[c["table_ref"]], terms)]
    prows, summary, over = evaluate_profiles(run_dir, terms, queries)
    c2.write_records(run_dir / PROFILES, PROFILE_COLUMNS, [tuple(r[n] for n, _ in PROFILE_COLUMNS) for r in prows])
    vm_rows = valuemap.read(run_dir)
    answers = g13_answers(run_dir)
    g13: list[dict[str, Any]] = []
    for c in short:
        t, s = tabs[c["table_ref"]], summary.get(c["table_ref"], {})
        if lock is not None and str(s.get("freshness_agree", "")).startswith("disagree"):
            dcol = _date_column(t, terms)
            row = valuemap.period_for(vm_rows, c["table_ref"], dcol)
            unused = valuemap.not_used_for(vm_rows, c["table_ref"], dcol)
            fh = freshness_hash(c["table_ref"], s)
            ans = answers.get((freshness_check_id(c["table_ref"]), fh))  # fix r5: bound to these numbers
            if row:
                s["freshness_agree"] = f"disagree: settled by value map period row ({row}); not a filter"
            elif unused:
                s["freshness_agree"] = f"disagree: settled, {dcol} not used ({unused}); not a filter"
            elif ans is not None:
                s["freshness_agree"] = f"disagree: settled by data-owner answer: {ans}"
            else:
                s["freshness_agree"] = "disagree: G13 open (data-owner question)"
                g13.append({"table_ref": c["table_ref"], "date_column": dcol,
                            "check_id": freshness_check_id(c["table_ref"]), "input_hash": fh,
                            "freshness_business": s.get("freshness_business"),
                            "freshness_watermark": s.get("freshness_watermark"),
                            "freshness_completeness": s.get("freshness_completeness")})
    srows = []
    for i, c in enumerate(short, 1):
        t, s = tabs[c["table_ref"]], summary.get(c["table_ref"], {})
        srows.append((i, c["table_ref"], c["score"], ",".join(_key(t, terms)) or None, _date_column(t, terms),
                      s.get("grain_test", "not run"), s.get("freshness_business", "not run"),
                      s.get("freshness_watermark", "not run"), s.get("freshness_completeness", "not run"),
                      s.get("freshness_agree", "incomplete"), c["matched_terms"], c["notes"]))
    for ref in dropped:  # one line each, nothing else about them (fix r3 B3-8)
        srows.append((None, ref, None, None, None, None, None, None, None, None, None, DROPPED))
    refused = sources_refused(run_dir, statuses, queries)
    if refused:
        srows.append((None, None, None, None, None, None, None, None, None, None, None,
                      "sources refused: " + "; ".join(f"{r['query_id']} ({r['status']}"
                                                      + (f": {r['detail']}" if r.get("detail") else "") + ")"
                                                      for r in refused)))
    c2.write_records(run_dir / SHORTLIST, SHORTLIST_COLUMNS, srows)
    absence = absence_report(terms, cands, statuses, rung_r)
    sentence = g2_sentence(len(tabs), short, refused, dropped)
    c2.write_json(run_dir / "discover_status.json", {
        "built_utc": c2.utc_now_text(), "dataset_source": rung_r, "listing_queries": statuses,
        "profiles_over_cap": over, "absence": absence, "sources_refused": refused, "dropped_at_g2": dropped,
        "freshness_g13": g13, "g2_sentence": sentence,
        "rule": "a rung is exhausted only when its query ran and returned (status ok)"})
    print(sentence)
    no_listing = not any(s == EXHAUSTED for s in statuses.values())
    if no_listing and not any(t["evidence_class"] == "declared" for t in tabs.values()):
        c2.write_gate_request(run_dir, "G2", "P2", "discover.py", "rung 0: no listing query returned and no sidecar",
                              {"listing_queries": statuses})
        sys.stderr.write("NEEDS_DECISION: rung 0, sidecar lane only: Max supplies dictionaries or stops\n")
        return int(ExitCode.NEEDS_DECISION)
    if over:
        c2.write_gate_request(run_dir, "G15", "P2", "discover.py", "profile over the per-job cap", over)
        sys.stderr.write("NEEDS_DECISION: profile over the per-job cap: narrow it or raise the cap\n")
        return int(ExitCode.NEEDS_DECISION)
    open_terms = [a for a in absence if a["status"] == "not established"]
    if open_terms:
        c2.write_gate_request(run_dir, "G2", "P2", "discover.py", "absence not established", open_terms)
        sys.stderr.write("NEEDS_DECISION: no candidate for " + ", ".join(a["term"] for a in open_terms)
                         + " and the search is not exhausted; absence is not reported\n")
        return int(ExitCode.NEEDS_DECISION)
    if not short:
        c2.write_gate_request(run_dir, "G2", "P2", "discover.py", "0 shortlisted: no candidate table scored above 0",
                              {"tables_listed": len(tabs), "excluded": sum(1 for c in cands if c["excluded"]),
                               "options": ["add terms or datasets", "name the tables at G2", "stop"]})
        sys.stderr.write("NEEDS_DECISION: 0 shortlisted: Max names the tables, adds terms, or stops (G2)\n")
        return int(ExitCode.NEEDS_DECISION)
    if g13:
        c2.write_gate_request(run_dir, "G13", "P2", "discover.py",
                              "freshness signals disagree on a locked table (data-owner question)",
                              {"tables": g13, "check_ids": [x["check_id"] for x in g13],
                               "options": ["answer as data owner, one table at a time (decide.py --gate G13 "
                                           "--outcome answered --check-id freshness:<table>)",
                                                          "settle with a value-map row: value_map.py add --kind period "
                                                          "--table <table> --column <date column> --value <rule> "
                                                          "--reason <why>",
                                                          "the date column is not used: value_map.py add --kind "
                                                          "not_used --table <table> --column <date column> --reason "
                                                          "<why>", "stop"]})
        sys.stderr.write("NEEDS_DECISION: G13: freshness disagrees on " + ", ".join(x["table_ref"] for x in g13)
                         + ": a data-owner answer, or a value-map period row for that table and date column\n")
        return int(ExitCode.NEEDS_DECISION)
    print(f"discovery built: {len(tabs)} tables, {len(short)} shortlisted")
    moved = c2.archive_own_request(run_dir, "discover.py", ("G2", "G13", "G15"), "P2")  # fix r4 C4-10
    if moved:
        print(f"the earlier gate_request.json of discover.py build is settled: archived to {moved}")
    return int(ExitCode.PASS)


def freshness_check_id(ref: str) -> str:
    """Fix r4 C4-7: the G13 check id of one table's freshness question."""
    return f"freshness:{ref}"


def freshness_hash(ref: str, s: Mapping[str, Any]) -> str:
    """Fix r5 (cold reviewer LOW): sha256 of one table's freshness numbers, the evidence a G13 answer is bound to."""
    import hashlib
    import json

    body = {"table_ref": ref, **{k: s.get(k) for k in ("freshness_business", "freshness_watermark",
                                                        "freshness_completeness")}}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def g13_answers(run_dir: Path) -> dict[tuple[str, str], str]:
    """Fix r4 C4-7: (check_id, input_hash) -> reason of every G13 row with outcome `answered` in this run
    (freshness:<table>). Fix r5: keyed by the row's input_hash too, so an answer settles a table only while its
    freshness numbers are the ones Max answered about (decide.py records the request entry's input_hash)."""
    out: dict[tuple[str, str], str] = {}
    for r in c2.load_decisions(run_dir):
        if str(r.get("gate")) == "G13" and str(r.get("outcome", "")).strip().lower() == "answered" and r.get("check_id"):
            out[(str(r["check_id"]), str(r.get("input_hash") or ""))] = (
                str(r.get("reason") or r.get("answer_text") or "").strip() or "answered")
    return out


def sources_refused(run_dir: Path, statuses: Mapping[str, str], queries: Sequence[Mapping[str, Any]]
                    ) -> list[dict[str, Any]]:
    """Listing and profiling queries the warehouse refused (denied or error), with their reason (fix r3 B3-5)."""
    out = []
    ids = list(statuses) + ["R_region_schemata"] + [q["query_id"] for q in queries]
    for qid in dict.fromkeys(ids):
        res = load_result(run_dir, qid)
        if res is not None and res.get("status") in REFUSED_STATUSES:
            out.append({"query_id": qid, "status": res["status"],
                        "detail": str(res.get("detail") or res.get("error") or "")[:200] or None})
    return out


def g2_sentence(n_tables: int, short: Sequence[Mapping[str, Any]], refused: Sequence[Mapping[str, Any]],
                dropped: Sequence[str]) -> str:
    """The sentence the run skill reads to Max at G2 (fix r3 B3-5)."""
    s = (f"G2: {n_tables} tables listed, {len(short)} on the shortlist ("
         + (", ".join(str(c["table_ref"]) for c in short) or "none") + "); sources refused: "
         + ("; ".join(f"{r['query_id']} ({r['status']})" for r in refused) if refused else "none"))
    if dropped:
        s += f"; {DROPPED}: " + ", ".join(dropped)
    return s + "."


# ---------------------------------------------------------------- lock and drift (14)
def _snapshot(t: Mapping[str, Any]) -> list[list[str]]:
    return [[str(c["column_name"]), str(c.get("data_type"))] for c in t["columns"]]


def write_lock(run_dir: Path, chosen: Sequence[str]) -> dict[str, Any]:
    """Freeze the tables Max kept at G2, with a column snapshot hash per table, and the shortlisted tables he
    dropped (fix r3 B3-8: every later P2 and P3 step reads only the locked list)."""
    terms = load_terms(run_dir)
    datasets, rung_r = datasets_in_scope(run_dir, terms)
    catalog, statuses = collect_catalog(run_dir, datasets)
    tabs = group_tables(catalog)
    missing = [r for r in chosen if r not in tabs]
    if missing:
        raise RefusedError("tables not in the catalog: " + ", ".join(missing))
    g2_row, g2_hash = g2lock.check_tables(run_dir, chosen)  # fix r4 C4-4: bound to the G2 approve
    short = run_dir / SHORTLIST
    shortlisted = [str(r["table_ref"]) for r in c2.read_records(short, SHORTLIST)[1]
                   if r.get("table_ref") and (r.get("rank") is not None or r.get("notes") == DROPPED)] \
        if short.is_file() else []
    prior = load_lock(run_dir) or {}
    dropped = sorted({r for r in shortlisted + list(prior.get("tables", {})) + list(prior.get("dropped_at_g2") or [])
                      if r not in chosen})
    g2 = [r for r in c2.load_decisions(run_dir) if str(r.get("gate")) == "G2"]
    lock = {"locked_utc": c2.utc_now_text(), "dataset_source": rung_r, "listing_queries": statuses,
            "dropped_at_g2": dropped,
            "g2_decision": ({k: g2[-1].get(k) for k in ("time_utc", "outcome", "attempt", "answer_text")}
                            if g2 else None),
            "g2_decision_row": g2_row, "g2_input_hash": g2_hash,
            "tables": {r: {"columns": _snapshot(tabs[r]), "columns_sha256": c2.sha256_obj(_snapshot(tabs[r])),
                           "key_hypothesis": _key(tabs[r], terms), "date_column": _date_column(tabs[r], terms),
                           "table_type": tabs[r].get("table_type"), "evidence_class": tabs[r]["evidence_class"]}
                       for r in chosen}}
    lock["lock_sha256"] = c2.sha256_obj(lock["tables"])
    c2.write_json(run_dir / LOCK, lock)
    for stale in (PLAN,):  # a plan written before the lock may still list dropped tables' profiles
        if (run_dir / stale).is_file():
            plan = c2.read_input_json(run_dir / stale, stale)
            keep = {_bare(r) for r in chosen}
            plan["queries"] = [q for q in plan.get("queries", []) if q.get("rung") != "P"
                               or _bare(str(q.get("table_ref"))) in keep]
            ids = {q["query_id"] for q in plan["queries"]}
            plan["pending"] = [p for p in plan.get("pending", []) if p in ids]
            c2.write_json(run_dir / stale, plan)
    return lock


def drift(run_dir: Path) -> list[str]:
    """Re-snapshot the locked tables from the current results; list every difference."""
    lock = c2.read_input_json(run_dir / LOCK, LOCK)
    terms = load_terms(run_dir)
    datasets, _ = datasets_in_scope(run_dir, terms)
    catalog, statuses = collect_catalog(run_dir, datasets)
    tabs = group_tables(catalog)
    out = []
    for ref, entry in lock["tables"].items():
        ds = ref.split(".", 1)[0]
        if statuses.get(_qid("D", ds, "columns")) != EXHAUSTED and entry["evidence_class"] != "declared":
            raise RefusedError(f"drift check for {ref} needs a fresh column listing ({_qid('D', ds, 'columns')} ok)")
        if ref not in tabs:
            out.append(f"{ref}: table no longer listed")
        elif c2.sha256_obj(_snapshot(tabs[ref])) != entry["columns_sha256"]:
            before = {tuple(x) for x in entry["columns"]}
            after = {tuple(x) for x in _snapshot(tabs[ref])}
            out.append(f"{ref}: columns changed; removed {sorted(before - after)}, added {sorted(after - before)}")
    return out


def _main(a: Any) -> int:
    run_dir = Path(a.run_dir)
    if a.cmd == "decide":  # fix r4 C4-13: rewritten at P3 or P4 when packet_freeze finds it stale
        c2.phase_check(a.run_dir, ["P2", "P3", "P4"])
        if (run_dir / "packet.sha256").is_file():
            raise RefusedError("the packet is frozen: 13_table_decision.md is part of it (a changed packet is a new run)")
    else:
        c2.phase_check(a.run_dir, ["P2"])
    if a.cmd == "plan":
        plan = build_plan(run_dir)
        c2.write_json(run_dir / PLAN, plan)
        print(f"{len(plan['queries'])} queries, {len(plan['pending'])} pending")
        return int(ExitCode.PASS)
    if a.cmd == "build":
        return build(run_dir)
    if a.cmd == "lock":
        chosen = [x.strip() for x in a.tables.split(",") if x.strip()]
        if not chosen:
            raise RefusedError("--tables needs the table refs Max confirmed at G2")
        lock = write_lock(run_dir, chosen)
        print(f"locked {len(chosen)} tables" + (f"; {DROPPED}: {', '.join(lock['dropped_at_g2'])}"
                                                if lock["dropped_at_g2"] else ""))
        return int(ExitCode.PASS)
    if a.cmd == "decide":
        from lib import table_decision
        g2lock.require_bound(run_dir, "discover.py decide")  # fix r4 C4-4
        path = table_decision.write(run_dir)
        print(f"{path.name} written from the G2 lock, the profiles, the value map and the card bindings")
        return int(ExitCode.PASS)
    diffs = drift(run_dir)
    c2.write_json(run_dir / "15_drift_diff.json", {"checked_utc": c2.utc_now_text(), "differences": diffs})
    if diffs:
        raise CheckFailedError("discovery-lock drift (G8): " + "; ".join(diffs))
    print("no drift against the discovery lock")
    return int(ExitCode.PASS)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = c2.base_parser("discover.py", "P2 table discovery over offline results (spec 2.2 P2)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    sub.add_parser("build")
    lk = sub.add_parser("lock")
    lk.add_argument("--tables", required=True, help="comma-separated table refs confirmed at G2")
    sub.add_parser("decide", help="write 13_table_decision.md (the packet's table decision) after the lock")
    sub.add_parser("drift")
    return c2.run_cli_args(lambda a: refusal.guard(a.run_dir, lambda: _main(a)), ap, argv)


if __name__ == "__main__":
    sys.exit(main())
