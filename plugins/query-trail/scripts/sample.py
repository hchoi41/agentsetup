"""sample.py: the P8 draw: targeted strata, then a salted hash-rank random N (design spec v1 sections 2.2 P8, 3.1, 6.2).

Sampling unit (v4, spec 6.2): a NULL-safe row id = SHA-256 of the canonical row (lib.canon,
D-04 rules, sidecar types, no FLOAT rounding) plus the row's file position (0-based data row),
so duplicates stay distinct units. For a level-80 stream sample the file position is replaced
by the occurrence index among identical canonical rows (spec 6.2 implementation note,
assumed), which ignores BigQuery's arbitrary result order. Draw: rank = SHA-256(salt, row id),
ascending, tie-break by row id, never by a business key. Same salt and same rows give the
same draw (tested twice in tests/unit/test_sample.py).

Policy (v4 P8): targeted strata first, examined and excluded from N (largest absolute values
and negatives per measure column, boundary dates (min and max) per date column, NULL and
'other' buckets per dimension column, multi-match keys when a key is known); an empty
stratum is reported, because "stratum empty" can itself be the finding (T06); then random N
from the residual population; residual <= N traces all of it.

Source per level (spec 3.1, 6.2): 60 and 70 draw from the deliverable output (per-step sampling
is "not run at this level"); 80 ranks each step's retained 1,000-row sample (sample_path) and
takes the top N; 90 and 100 draw from each step's full CSV (a step degraded to 80 uses its
sample). N is run_config.sample_n (Standard 60; Light 22 and Deep 299 are card overrides).
The salt is run_config.sample_salt, else the salt already recorded in samples/sample_manifest.json,
else --salt, else the run id (run_steps.py's fallback for the level-80 sample); a recorded salt is
never changed. Canonical rows use run_config.float_tolerance and ignore `_qt_` bookkeeping columns,
exactly as run_steps.py's level-80 sample does; when that file records _qt_row_id and _qt_rank,
both are recomputed and any difference refuses the draw. Its strata file (_qt_stratum) supplies
the targeted units at level 80, up to --strata-cap per stratum.

Outputs: samples/<unit>.csv (unit rows plus _qt_selection, _qt_stratum, _qt_position,
_qt_row_id, _qt_rank) and samples/sample_manifest.json (salt, N, sources, strata, wording).

CLI:  sample.py --run-dir R [--salt S] [--strata-cap 5]
Exit: 0 drawn; 2 refused (state, missing input, salt change); 4 engine error.
"""
from __future__ import annotations

import dataclasses
import heapq
import math
import sys
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import duckdb  # noqa: E402

from lib import runctx as c2  # noqa: E402
from lib import canon, sampling  # noqa: E402
from lib import csv_contract as cc  # noqa: E402
from lib.exit_codes import ExitCode, RefusedError  # noqa: E402

SAMPLES = "samples"
SAMPLE_MANIFEST = "sample_manifest.json"
DEFAULT_N = 60
DEFAULT_STRATA_CAP = 5
TIERS = {22: ("Light", 90, 10.0), 60: ("Standard", 95, 5.0), 299: ("Deep", 95, 1.0)}
OTHER_VALUES = {"other", "others", "기타", "unknown", "n/a", "na", "misc"}
META_COLUMNS = (("_qt_selection", "VARCHAR"), ("_qt_stratum", "VARCHAR"), ("_qt_position", "BIGINT"),
                ("_qt_row_id", "VARCHAR"), ("_qt_rank", "VARCHAR"))
NUMERIC = canon.INT_TYPES | canon.FLOAT_TYPES | canon.DECIMAL_TYPES
DATES = {"DATE"} | canon.TIMESTAMP_NAIVE_TYPES | canon.TIMESTAMP_TZ_TYPES
DIMENSIONS = canon.STRING_TYPES | {"BOOLEAN", "BOOL"}


# ---------------------------------------------------------------- row id and rank (one implementation: lib.sampling)
row_id = sampling.row_id
rank_key = sampling.rank_key


@dataclasses.dataclass(frozen=True)
class Unit:
    """One sampling unit: values, position, id, rank."""

    values: tuple[Any, ...]
    position: int
    rid: str
    rank: str

    @property
    def order(self) -> tuple[str, str]:
        """Draw order: rank, then row id."""
        return (self.rank, self.rid)


def data_columns(path: Path) -> tuple[cc.Column, ...]:
    """The unit's own columns: every sidecar column except run_steps.py's `_qt_` bookkeeping columns."""
    return tuple(c for c in cc.read_sidecar(path).columns if not c.name.startswith("_qt_"))


def _rows(con: duckdb.DuckDBPyConnection, path: Path, columns: Sequence[cc.Column]) -> Iterator[tuple[Any, ...]]:
    sc = cc.read_sidecar(path)
    sql = "SELECT " + cc.projection_sql(columns) + " FROM " + cc.read_csv_sql(path, sc.columns)
    return canon.iter_query_rows(con, sql, columns)


def iter_units(path: Path, salt: str, occurrence: bool = False, float_tolerance: float | None = None) -> Iterator[Unit]:
    """Stream the units of a contract CSV in file order (read through the lib reader).

    Canonical rows use the run's FLOAT tolerance, as run_steps.py does for the level-80 sample, so a
    unit's row id is the same in both scripts.
    """
    cols = data_columns(path)
    specs = canon.specs_for(cols, float_tolerance)
    con = duckdb.connect()
    try:
        cc.run_sql(con, "SET threads = 1")
        cc.read_csv(con, path, validate=True)
        seen: dict[bytes, int] = {}
        for pos, vals in enumerate(_rows(con, path, cols)):
            rb = canon.canon_row(vals, specs)
            if occurrence:
                k = seen.get(rb, 0)
                seen[rb] = k + 1
                rid = row_id(rb, k)
            else:
                rid = row_id(rb, pos)
            yield Unit(tuple(vals), pos, rid, rank_key(salt, rid))
    finally:
        con.close()


def verify_recorded_ranks(path: Path, salt: str, float_tolerance: float | None) -> int:
    """When the source records _qt_row_id and _qt_rank (run_steps.py level-80 sample), recompute both;
    any difference means a different salt or row-id rule, and the draw is refused. Returns rows checked."""
    names = [c.name for c in cc.read_sidecar(path).columns]
    if "_qt_row_id" not in names or "_qt_rank" not in names:
        return 0
    _, recs = c2.read_records(path)
    units = list(iter_units(path, salt, True, float_tolerance))
    bad = [i for i, (u, r) in enumerate(zip(units, recs)) if (u.rid, u.rank) != (r["_qt_row_id"], r["_qt_rank"])]
    if bad:
        raise RefusedError(f"{path.name}: recorded row id or rank differs from the recomputed one in {len(bad)} row(s): "
                           "the salt or the row-id rule differs from run_steps.py")
    return len(units)


# ---------------------------------------------------------------- strata
class _TopK:
    """Keep the k smallest items by a sort key (a bounded max-heap)."""

    def __init__(self, k: int) -> None:
        self.k, self.heap, self.members = k, [], 0
        self._tie = 0

    def offer(self, key: Any, unit: Unit) -> None:
        self.members += 1
        if self.k <= 0:
            return
        self._tie += 1
        item = (_Neg(key), self._tie, unit)
        if len(self.heap) < self.k:
            heapq.heappush(self.heap, item)
        elif key < self.heap[0][0].v:
            heapq.heapreplace(self.heap, item)

    def items(self) -> list[Unit]:
        return [u for _, _, u in sorted(self.heap, key=lambda x: x[0].v)]


@dataclasses.dataclass(frozen=True)
class _Neg:
    v: Any

    def __lt__(self, other: "_Neg") -> bool:
        return other.v < self.v


class _Boundary:
    """Members at the current extreme value of a column (min or max), k smallest by rank."""

    def __init__(self, k: int, low: bool) -> None:
        self.k, self.low, self.value, self.top, self.members = k, low, None, _TopK(k), 0

    def offer(self, v: Any, unit: Unit) -> None:
        if v is None:
            return
        better = self.value is None or (v < self.value if self.low else v > self.value)
        if better:
            self.value, self.top, self.members = v, _TopK(self.k), 0
        if v == self.value:
            self.members += 1
            self.top.offer(unit.order, unit)

    def items(self) -> list[Unit]:
        return self.top.items()


def _base(t: str) -> str:
    return canon.parse_type(t)[0]


def strata_plan(columns: Sequence[cc.Column], keys: Sequence[str]) -> dict[str, list[str]]:
    """Which columns get which strata (measure, date and dimension columns by sidecar type)."""
    keyset = set(keys)
    plan: dict[str, list[str]] = {"measure": [], "date": [], "dimension": []}
    for c in columns:
        b = _base(c.type)
        id_like = c.name.lower() == "id" or c.name.lower().endswith(("_id", "_key", "_no", "_code"))
        if b in NUMERIC and c.name not in keyset and not id_like:
            plan["measure"].append(c.name)
        elif b in DATES:
            plan["date"].append(c.name)
        elif b in DIMENSIONS or c.name in keyset or id_like:
            plan["dimension"].append(c.name)
    return plan


@dataclasses.dataclass
class Draw:
    """Result of drawing one unit's sample."""

    columns: tuple[cc.Column, ...]
    population: int
    targeted: list[tuple[str, Unit]]
    random: list[Unit]
    residual: int
    strata: dict[str, dict[str, int]]
    census: bool


def _key_counts(path: Path, keys: Sequence[str]) -> dict[tuple[Any, ...], int]:
    cols = data_columns(path)
    idx = [i for i, c in enumerate(cols) if c.name in keys]
    con = duckdb.connect()
    try:
        cc.run_sql(con, "SET threads = 1")
        counts: dict[tuple[Any, ...], int] = {}
        for vals in _rows(con, path, cols):
            k = tuple(vals[i] for i in idx)
            counts[k] = counts.get(k, 0) + 1
        return counts
    finally:
        con.close()


def strata_file_units(path: Path, salt: str, float_tolerance: float | None, cap: int) -> list[tuple[str, Unit]]:
    """Targeted units from run_steps.py's level-80 strata file (`_qt_stratum` plus the step columns),
    at most `cap` per stratum, lowest rank first."""
    _, recs = c2.read_records(path)
    labels = [str(r.get("_qt_stratum")) for r in recs]
    by: dict[str, list[Unit]] = {}
    for label, u in zip(labels, iter_units(path, salt, True, float_tolerance)):
        by.setdefault("T_stream_" + label, []).append(u)
    return [(lab, u) for lab in sorted(by) for u in sorted(by[lab], key=lambda u: u.order)[:cap]]


def draw(path: Path, n: int, salt: str, keys: Sequence[str] = (), occurrence: bool = False,
         strata_cap: int = DEFAULT_STRATA_CAP, float_tolerance: float | None = None,
         extra_targeted: Sequence[tuple[str, Unit]] = ()) -> Draw:
    """Draw targeted strata and a random N from one contract CSV (spec 6.2)."""
    cols = data_columns(path)
    names = [c.name for c in cols]
    keys = [k for k in keys if k in names]
    plan = strata_plan(cols, keys)
    idx = {nme: i for i, nme in enumerate(names)}
    coll: dict[str, Any] = {}
    for m in plan["measure"]:
        coll[f"T_largest:{m}"] = _TopK(strata_cap)
        coll[f"T_negative:{m}"] = _TopK(strata_cap)
    for d in plan["date"]:
        coll[f"T_boundary_lo:{d}"] = _Boundary(strata_cap, True)
        coll[f"T_boundary_hi:{d}"] = _Boundary(strata_cap, False)
    for d in plan["dimension"]:
        coll[f"T_null:{d}"] = _TopK(strata_cap)
        if _base(cols[idx[d]].type) in canon.STRING_TYPES:
            coll[f"T_other:{d}"] = _TopK(strata_cap)
    kcounts = _key_counts(path, keys) if keys else {}
    if keys:
        coll[f"T_multi_match:{','.join(keys)}"] = _TopK(strata_cap)
    extra_ids = {u.rid for _, u in extra_targeted}
    reserve = n + strata_cap * len(coll) + len(extra_ids)
    rand = _TopK(reserve)
    population = 0
    in_extra = 0
    for u in iter_units(path, salt, occurrence, float_tolerance):
        population += 1
        in_extra += u.rid in extra_ids
        rand.offer(u.order, u)
        for m in plan["measure"]:
            v = u.values[idx[m]]
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            coll[f"T_largest:{m}"].offer((-abs(v), u.rank, u.rid), u)
            if v < 0:
                coll[f"T_negative:{m}"].offer(u.order, u)
        for d in plan["date"]:
            coll[f"T_boundary_lo:{d}"].offer(u.values[idx[d]], u)
            coll[f"T_boundary_hi:{d}"].offer(u.values[idx[d]], u)
        for d in plan["dimension"]:
            v = u.values[idx[d]]
            if v is None:
                coll[f"T_null:{d}"].offer(u.order, u)
            elif f"T_other:{d}" in coll and isinstance(v, str) and v.strip().casefold() in OTHER_VALUES:
                coll[f"T_other:{d}"].offer(u.order, u)
        if keys and kcounts.get(tuple(u.values[idx[k]] for k in keys), 0) > 1:
            coll[f"T_multi_match:{','.join(keys)}"].offer(u.order, u)
    targeted: list[tuple[str, Unit]] = []
    seen: set[str] = set()
    strata: dict[str, dict[str, int]] = {}
    own = 0
    for label, u in extra_targeted:
        st = strata.setdefault(label, {"members": 0, "examined": 0, "source": "run_steps strata file"})
        st["members"] += 1
        if u.rid not in seen:
            seen.add(u.rid)
            targeted.append((label, u))
            st["examined"] += 1
    for label in sorted(coll):
        items = coll[label].items()
        strata[label] = {"members": coll[label].members, "examined": 0}
        for u in items:
            if u.rid not in seen:
                seen.add(u.rid)
                targeted.append((label, u))
                strata[label]["examined"] += 1
                own += 1
    residual = population - own - in_extra
    pool = [u for u in rand.items() if u.rid not in seen]
    census = residual <= n
    if census and len(pool) < residual:  # reserve too small cannot happen when residual <= n; guard anyway
        raise RefusedError("internal: census pool smaller than the residual population")
    return Draw(cols, population, targeted, pool[:n], residual, strata, census)


# ---------------------------------------------------------------- wording
def assurance(n: int, residual: int, census: bool, deviations: int = 0) -> str:
    """Assurance sentence for one unit (T06 wording; zero-deviation binomial, AS 2315 .41)."""
    if census:  # fix r1 A-H5: no "no sampling risk" claim; what the trace compared is stated per unit by trace.py
        return (f"all {residual} row{'s' if residual != 1 else ''} of the residual population "
                f"{'were' if residual != 1 else 'was'} traced "
                f"(population <= N = {n}); the assurance is only as wide as what the trace compared for this unit")
    if deviations:
        return f"N={n} random, {deviations} deviation(s): the zero-deviation bound does not hold for this unit"
    tier = TIERS.get(n)
    if tier:
        name, conf, bound = tier
        lead = f"N={n} random ({name})"
    else:
        conf, bound = 95, math.ceil((1 - 0.05 ** (1 / n)) * 1000) / 10
        lead = f"N={n} random (custom)"
    return (f"{lead}, 0 deviations: at {conf}% confidence the rate of affected rows is below {bound:g}% "
            f"(for the sampled population of {residual} rows after targeted exclusions, and its period); "
            "targeted strata cover named rare cases; this does not establish semantic correctness")


# ---------------------------------------------------------------- units per level
def units_for_level(manifest: Mapping[str, Any], level: int) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Sampling units and their sources at a level, plus units not sampled (with reasons)."""
    units, not_run = [], []
    if level in (60, 70):
        for d in manifest["deliverables"]:
            if d.get("side") == "B" and d.get("csv"):
                units.append({"unit": "deliverable_B", "source_kind": "deliverable_full_csv",
                              "path": d["csv"]["path"], "occurrence": False, "keys": []})
        for s in manifest["steps"]:
            not_run.append({"unit": s["name"], "reason": "per-step sampling not run at this level"})
        return units, not_run
    for s in manifest["steps"]:
        csv = s.get("csv")
        if level >= 90 and csv and "full_csv" in s.get("artifacts", []):
            units.append({"unit": s["name"], "source_kind": "step_full_csv", "path": csv["path"], "occurrence": False,
                          "keys": list(s.get("grain", []))})
        elif s.get("sample_path"):
            units.append({"unit": s["name"], "source_kind": "step_sample_1000", "path": s["sample_path"],
                          "occurrence": True, "keys": list(s.get("grain", [])), "strata_path": s.get("strata_path")})
        else:
            not_run.append({"unit": s["name"], "reason": "no retained rows at this level (no sample or full CSV)"})
    return units, not_run


def write_unit_csv(path: Path, d: Draw) -> None:
    """samples/<unit>.csv: targeted rows (by stratum) then random rows (by rank)."""
    cols = [(c.name, c.type) for c in d.columns] + list(META_COLUMNS)
    rows = [(*u.values, "targeted", label, u.position, u.rid, u.rank) for label, u in d.targeted]
    rows += [(*u.values, "census" if d.census else "random", None, u.position, u.rid, u.rank) for u in d.random]
    c2.write_records(path, cols, rows)


def resolve_salt(run_dir: Path, rc: Mapping[str, Any], cli_salt: str | None, run_id: str) -> tuple[str, str]:
    """The salt and where it came from; a salt already recorded is never changed.

    Order: run_config.sample_salt; the salt already recorded; --salt; else the run id, which is the
    fallback run_steps.py uses for the level-80 sample, so the two scripts rank alike.
    """
    rec_path = run_dir / SAMPLES / SAMPLE_MANIFEST
    recorded = c2.read_input_json(rec_path).get("salt") if rec_path.is_file() else None
    if rc.get("sample_salt"):
        salt, src = str(rc["sample_salt"]), "run_config.sample_salt"
    elif recorded:
        salt, src = str(recorded), "recorded in samples/sample_manifest.json"
    elif cli_salt:
        salt, src = cli_salt, "--salt"
    else:
        salt, src = run_id, "run id (run_steps.py fallback), recorded"
    if cli_salt and cli_salt != salt:
        raise RefusedError(f"--salt differs from the salt in force ({src}); the sample skill never changes the sample")
    if recorded and recorded != salt:
        raise RefusedError("the sample is fixed: a different salt is already recorded (the sample skill never "
                           "changes the sample)")
    return salt, src


def run(run_dir: Path, cli_salt: str | None = None, strata_cap: int = DEFAULT_STRATA_CAP) -> dict[str, Any]:
    """Draw every unit at the run's level; write samples/ and the sample manifest."""
    run_dir = Path(run_dir)
    manifest = c2.load_manifest(run_dir)
    rc = c2.load_run_config_raw(run_dir, required=False)
    level = c2.run_level(run_dir)
    n = int(rc.get("sample_n") or DEFAULT_N)
    tol = rc.get("float_tolerance")
    salt, salt_src = resolve_salt(run_dir, rc, cli_salt, str(manifest["run_id"]))
    units, not_run = units_for_level(manifest, level)
    out_units = []
    for u in units:
        src = c2.resolve_in_run(run_dir, u["path"])
        if not src.is_file():
            raise c2.InputMissingError(f"{u['unit']}: retained rows missing: {u['path']}")
        checked = verify_recorded_ranks(src, salt, tol)
        extra: list[tuple[str, Unit]] = []
        if u.get("strata_path"):
            sp = c2.resolve_in_run(run_dir, u["strata_path"])
            if not sp.is_file():
                raise c2.InputMissingError(f"{u['unit']}: strata file missing: {u['strata_path']}")
            extra = strata_file_units(sp, salt, tol, strata_cap)
        d = draw(src, n, salt, u["keys"], u["occurrence"], strata_cap, tol, extra)
        rel = f"{SAMPLES}/{u['unit']}.csv"
        write_unit_csv(run_dir / rel, d)
        out_units.append({
            "unit": u["unit"], "source_kind": u["source_kind"], "source_path": u["path"],
            "source_sha256": cc.read_sidecar(src).csv_sha256, "population_rows": d.population,
            "targeted": len(d.targeted), "random_drawn": len(d.random), "residual_rows": d.residual,
            "census": d.census, "keys": u["keys"], "strata": d.strata, "recorded_ranks_verified": checked,
            "strata_path": u.get("strata_path"),
            "empty_strata": sorted(k for k, v in d.strata.items() if v["members"] == 0), "csv": rel,
            "row_id_rule": "sha256(canonical row + 0x1f + " + ("occurrence index)" if u["occurrence"] else "file position)"),
            "wording_if_no_deviation": assurance(n, d.residual, d.census)})
    man = {"drawn_utc": c2.utc_now_text(), "level": level, "n": n, "tier": TIERS.get(n, ("custom",))[0], "salt": salt,
           "salt_source": salt_src, "rank_rule": "sha256(salt + 0x1f + row id), ascending, tie-break row id",
           "strata_cap": strata_cap, "units": out_units, "not_run": not_run}
    c2.write_json(run_dir / SAMPLES / SAMPLE_MANIFEST, man)
    if level in (60, 70):
        c2.upsert_checks(run_dir, [c2.check_row("p8_per_step_sampling", "P8", "sampling", "NOT_RUN",
                                                not_run_reason=f"not run at level {level}",
                                                detail="per-step sampling and trace need level 80 or higher")])
    return man


def _main(a: Any) -> int:
    c2.phase_check(a.run_dir, ["P8"])
    man = run(a.run_dir, a.salt, a.strata_cap)
    print(f"{len(man['units'])} unit(s) drawn at level {man['level']}, N={man['n']}, salt from {man['salt_source']}")
    return int(ExitCode.PASS)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = c2.base_parser("sample.py", "P8 sampling draw (spec 6.2)")
    ap.add_argument("--salt", help="salt when run_config.json has none and none is recorded")
    ap.add_argument("--strata-cap", type=int, default=DEFAULT_STRATA_CAP, help="units examined per stratum")
    return c2.run_cli_args(_main, ap, argv)


if __name__ == "__main__":
    sys.exit(main())
