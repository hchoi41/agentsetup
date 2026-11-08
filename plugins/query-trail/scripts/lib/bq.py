"""BigQuery transport (design spec v1 sections 0.2, 1.6, 2.2 P0 and P6, 3.2, 5.1; stage 2a).

Importing this module makes no network call and does not import the BigQuery client; the
google libraries are imported inside the functions that need them.

Contract (spec references in brackets):
- Credentials: `google.auth.default(scopes=cloud-platform)`, then `.with_quota_project(project)`
  (the login's ADC defaults to another quota project, build/verify_identity.py), then
  `impersonated_credentials.Credentials(target_principal=<sa>)` when the env file names a service
  account. Owner mode (no impersonation) must be explicit (`owner_mode: research` in run_config)
  and is labelled [2.2 P0, D-08]. ADC missing is an environment error, exit 4 [2.2 P0].
- Every job: one statement, `maximum_bytes_billed` from the caps, `use_query_cache = False`,
  labels `query_trail_run=<run id>` and `step=<nn>` (plus run_config job_labels), location from
  the env file [2.2 P6, 5.1]. A job over the cap fails and bills nothing (L, BQ02): CapExceeded,
  exit 3 (G15).
- Results stream once as Arrow record batches: the Storage Read API when the identity may open a
  read session, else the REST row pages (the route is recorded); the caller never holds more
  than the stream cap [3.1, D-04].
- Rights probe [2.2 P0]: projects.testIamPermissions (Cloud Resource Manager), tables
  testIamPermissions (effective permissions, inherited grants included), dataset access lists,
  dataset time-travel windows, and one write statement that must be refused.
- Identifiers are never written into run files: callers pass rendered SQL in memory only [D-12].
- Per-run budget [spec v4 5.3; fix r1 B-H3, r2 B2-H2, r3 B3-2]: `metered(engine, run_dir, cfg, phase)` puts any
  engine behind the run's one ledger (manifest.json `budget.ledger`). Admission: reserved = dry-run bytes + 10 MiB x
  distinct tables referenced; committed + reserved over caps.warehouse_bytes_per_run raises BudgetExceeded (G15,
  exit 3) and the job does not run. Authorisation rule (v4): authorized = min(job cap, remaining budget); the job is
  sent with maximum_bytes_billed = authorized; the ledger entry is written before submission with
  `authorized_bytes`, `bytes_billed` null and `reconciled_utc` null, and holds `authorized` until the bill is
  reconciled, then the actual (0 for a job BigQuery refused over its cap). An unknown bill (the job failed after
  submission, or the process died) keeps `authorized` held until `Ledger.reconcile` records it.
  remaining = budget - (reconciled bills + authorised amounts of unreconciled entries), across phases and attempts,
  so an unknown bill never lets a later job pass the budget. Each entry carries the phase of the script that ran it.
  Fix r4 C4-2, r5 H1: an entry written before v4 (no authorized_bytes) with no bill is "unresolved legacy". It is
  admitted into the arithmetic only when its historical submitted cap (maximum_bytes_billed) is recorded, and then
  holds exactly that cap; without one, no new job is admitted (exit 2, naming the entry; fix r6: "no command records a
  legacy bill: close this run with close.py and start a new run"), whatever reservation it recorded and whatever the current configuration cap is.
  Every script that runs a job wraps its engine this way (tests/unit/test_bq_ledger.py checks it). Single writer:
  the manifest is read, extended and rewritten per entry (entries another ledger wrote on the same run are re-read
  first), so run one script at a time per run, as the orchestrator does (dec r2 L3).
"""
from __future__ import annotations

import dataclasses
import re
import uuid
from typing import Any, Iterator, Mapping, Sequence

from lib.exit_codes import ExitCode, QueryTrailError, RefusedError

DEFAULT_MAX_BYTES_BILLED = 5 * 1024 ** 3
CLOUD_PLATFORM = ("https://www.googleapis.com/auth/cloud-platform",)
WRITE_PERMISSIONS = ("bigquery.datasets.create", "bigquery.datasets.update", "bigquery.datasets.delete",
                     "bigquery.tables.create", "bigquery.tables.update", "bigquery.tables.updateData",
                     "bigquery.tables.delete", "bigquery.tables.setIamPolicy", "bigquery.routines.create",
                     "bigquery.models.create", "resourcemanager.projects.setIamPolicy")
TABLE_WRITE_PERMISSIONS = ("bigquery.tables.update", "bigquery.tables.updateData", "bigquery.tables.delete",
                           "bigquery.tables.setIamPolicy")
READ_PERMISSIONS = ("bigquery.jobs.create", "bigquery.tables.getData")
_LABEL_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_LABEL_VALUE = re.compile(r"^[a-z0-9_-]{0,63}$")
_MOD128 = 1 << 128


class LabelError(RefusedError):
    """Labels that BigQuery would reject (lowercase letters, digits, _ and -; 63 chars)."""


class CapError(RefusedError):
    """A job configuration without a positive byte cap, or a cache-on job."""


class CapExceeded(QueryTrailError):
    """BigQuery refused a job over maximum_bytes_billed (bytesBilledLimitExceeded): G15, exit 3."""

    exit_code = ExitCode.NEEDS_DECISION


class EngineError(QueryTrailError):
    """Engine or environment failure (exit 4, one unchanged retry after the cause is fixed)."""

    exit_code = ExitCode.ENGINE_ERROR


class RightsUnverifiable(RefusedError):
    """A rights check could not be run (API disabled, access read denied): fail closed (exit 2)."""


@dataclasses.dataclass(frozen=True)
class JobConfig:
    """Settings every query-trail job carries (spec 2.2 P6, 5.1)."""

    maximum_bytes_billed: int
    labels: Mapping[str, str]
    use_query_cache: bool = False
    dry_run: bool = False
    location: str | None = None

    def validate(self) -> "JobConfig":
        """Refuse a missing cap, a cache-on job or invalid labels."""
        if self.maximum_bytes_billed <= 0:
            raise CapError("maximum_bytes_billed must be a positive byte cap")
        if self.use_query_cache:
            raise CapError("use_query_cache must be False on every job (spec 5.1)")
        validate_labels(self.labels)
        return self


@dataclasses.dataclass(frozen=True)
class DryRunResult:
    """What a dry run reports: estimated bytes and the result schema."""

    total_bytes_processed: int
    schema: tuple[tuple[str, str], ...]


@dataclasses.dataclass(frozen=True)
class JobResult:
    """What an executed job reports; rows are streamed separately through `handle`."""

    job_id: str
    total_rows: int
    total_bytes_billed: int
    cache_hit: bool
    schema: tuple[tuple[str, str], ...]
    total_bytes_processed: int | None = None
    result_bytes: int | None = None
    handle: Any = dataclasses.field(default=None, compare=False, repr=False)


@dataclasses.dataclass(frozen=True)
class Identity:
    """Who runs the jobs: the impersonated service account (its grant is the boundary; P0 records an observation of
    it, not a proof: fix r3 B3-1), or owner mode."""

    project: str
    location: str | None
    service_account: str | None
    owner_mode: bool = False

    @property
    def label(self) -> str:
        """Manifest/report label; never contains an identifier (D-12)."""
        if self.owner_mode:
            return "owner mode, non-compliant with D-08, research only"
        return "impersonated service account (rights observed at P0: 00_preflight.json rights.observation)"


@dataclasses.dataclass(frozen=True)
class TableInfo:
    """Metadata of one table reference (free metadata read, no job)."""

    ref: str
    table_type: str
    view_query: str | None
    dataset: str
    num_rows: int | None
    num_bytes: int | None


# ---------------------------------------------------------------- pure helpers
def validate_labels(labels: Mapping[str, str]) -> None:
    """Raise LabelError unless every key and value matches BigQuery's label rules."""
    for k, v in labels.items():
        if not _LABEL_KEY.match(k) or not _LABEL_VALUE.match(v):
            raise LabelError(f"invalid job label {k}:{v} (lowercase letters, digits, _ and -; max 63)")


def step_labels(run_id: str, step_nn: int | str) -> dict[str, str]:
    """Labels `query_trail_run=<run id>` and `step=<nn>`; values lowercased (BQ02)."""
    nn = f"{int(step_nn):02d}" if str(step_nn).isdigit() else str(step_nn).lower()
    labels = {"query_trail_run": run_id.lower(), "step": nn}
    validate_labels(labels)
    return labels


def job_config(run_id: str, step_nn: int | str, maximum_bytes_billed: int = DEFAULT_MAX_BYTES_BILLED,
               dry_run: bool = False, location: str | None = None,
               extra_labels: Mapping[str, str] | None = None) -> JobConfig:
    """A validated JobConfig for one step job; `extra_labels` cannot override the two fixed labels."""
    labels = dict(extra_labels or {})
    labels.update(step_labels(run_id, step_nn))
    return JobConfig(maximum_bytes_billed, labels, False, dry_run, location).validate()


def check_dry_run_against_cap(result: DryRunResult, cfg: JobConfig) -> ExitCode:
    """PASS when the estimate fits the per-job cap; NEEDS_DECISION (G15) when it does not."""
    return ExitCode.PASS if result.total_bytes_processed <= cfg.maximum_bytes_billed else ExitCode.NEEDS_DECISION


def field_type(field: Any) -> str:
    """Standard SQL type text of a SchemaField (INT64, NUMERIC(10,2), ARRAY<STRING>, STRUCT<a INT64>)."""
    legacy = {"INTEGER": "INT64", "FLOAT": "FLOAT64", "BOOLEAN": "BOOL", "RECORD": "STRUCT"}
    base = legacy.get(str(field.field_type).upper(), str(field.field_type).upper())
    if base == "STRUCT":
        inner = ", ".join(f"{f.name} {field_type(f)}" for f in field.fields)
        base = f"STRUCT<{inner}>"
    elif base in ("NUMERIC", "BIGNUMERIC") and getattr(field, "precision", None):
        base = f"{base}({field.precision}" + (f",{field.scale})" if field.scale is not None else ")")
    elif base in ("STRING", "BYTES") and getattr(field, "max_length", None):
        base = f"{base}({field.max_length})"
    return f"ARRAY<{base}>" if str(getattr(field, "mode", "") or "").upper() == "REPEATED" else base


def schema_tuple(fields: Sequence[Any] | None) -> tuple[tuple[str, str], ...]:
    """(name, standard SQL type) for every top-level field."""
    return tuple((f.name, field_type(f)) for f in (fields or ()))


def warehouse_digest_sql(sql: str) -> str:
    """Warehouse-side digest over a result (spec 3.2 fallback route; column order and names matter)."""
    return ("SELECT COUNT(*) AS qt_rows, SUM(CAST(FARM_FINGERPRINT(TO_JSON_STRING(qt_t)) AS BIGNUMERIC)) "
            f"AS qt_sum FROM (\n{sql}\n) AS qt_t")


def digest_from_sum(total: Any) -> str:
    """32 hex characters of a BIGNUMERIC fingerprint sum, reduced mod 2^128 (NULL sum = 0)."""
    return format(int(total or 0) % _MOD128, "032x")


def _api_error_reasons(exc: Any) -> list[str]:
    return [str(e.get("reason", "")) for e in (getattr(exc, "errors", None) or []) if isinstance(e, dict)]


def map_api_error(exc: Exception, what: str) -> QueryTrailError:
    """Translate a google.api_core exception into the query-trail exit-code family."""
    reasons = _api_error_reasons(exc)
    msg = str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__
    if "bytesBilledLimitExceeded" in reasons or "bytes billed" in msg.lower():
        return CapExceeded(f"{what}: over maximum_bytes_billed (bills nothing): {msg}")
    return EngineError(f"{what}: {type(exc).__name__}: {msg}")


# ---------------------------------------------------------------- credentials and client
def source_credentials(project: str, scopes: Sequence[str] = CLOUD_PLATFORM) -> Any:
    """ADC with the cloud-platform scope and the quota project set to `project`."""
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError

    try:
        creds, _ = google.auth.default(scopes=list(scopes))
    except DefaultCredentialsError as exc:
        raise EngineError(f"ADC missing or unusable (run gcloud auth application-default login): {exc}") from exc
    if hasattr(creds, "with_quota_project"):
        creds = creds.with_quota_project(project)
    return creds


def impersonated_credentials(target_principal: str, project: str, scopes: Sequence[str] = CLOUD_PLATFORM,
                             lifetime_s: int = 3600, source: Any = None) -> Any:
    """ADC credentials impersonating the env file's service account (spec 2.2 P0, decisions 4 Q6)."""
    from google.auth import impersonated_credentials as ic

    src = source if source is not None else source_credentials(project, scopes)
    return ic.Credentials(source_credentials=src, target_principal=target_principal,
                          target_scopes=list(scopes), lifetime=lifetime_s)


def credentials_for(identity: Identity, lifetime_s: int = 3600) -> Any:
    """Credentials for an Identity; refuses an implicit owner identity (no SA and no owner mode)."""
    if identity.owner_mode:
        return source_credentials(identity.project)
    if not identity.service_account:
        raise RefusedError("no service account in the env file and owner_mode is not 'research': "
                           "refusing to run as the login identity (spec 2.2 P0, D-08)")
    return impersonated_credentials(identity.service_account, identity.project, lifetime_s=lifetime_s)


def make_client(credentials: Any, project: str, location: str | None = None) -> Any:
    """A bigquery.Client bound to the env file's project and location."""
    from google.cloud import bigquery

    return bigquery.Client(project=project, credentials=credentials, location=location)


def _query_job_config(cfg: JobConfig, dry_run: bool) -> Any:
    from google.cloud import bigquery

    cfg.validate()
    return bigquery.QueryJobConfig(maximum_bytes_billed=cfg.maximum_bytes_billed, use_query_cache=False,
                                   labels=dict(cfg.labels), dry_run=dry_run or cfg.dry_run,
                                   use_legacy_sql=False)


# ---------------------------------------------------------------- jobs
def dry_run(client: Any, sql: str, cfg: JobConfig) -> DryRunResult:
    """Dry-run one statement under `cfg`; return the byte estimate and the result schema."""
    from google.api_core import exceptions as gexc

    try:
        job = client.query(sql, job_config=_query_job_config(cfg, True), location=cfg.location)
    except gexc.GoogleAPIError as exc:
        raise map_api_error(exc, "dry run") from exc
    return DryRunResult(int(job.total_bytes_processed or 0), schema_tuple(job.schema))


def run_query(client: Any, sql: str, cfg: JobConfig, timeout_s: float | None = None) -> JobResult:
    """Execute one statement with the byte cap, labels and cache off; wait for completion."""
    from google.api_core import exceptions as gexc

    try:
        job = client.query(sql, job_config=_query_job_config(cfg, False), location=cfg.location)
        rows = job.result(timeout=timeout_s)
    except gexc.GoogleAPIError as exc:
        raise map_api_error(exc, "query job") from exc
    result_bytes = None
    if job.destination is not None:
        try:
            result_bytes = int(client.get_table(job.destination).num_bytes or 0)
        except gexc.GoogleAPIError:
            result_bytes = None
    return JobResult(job.job_id, int(rows.total_rows or 0), int(job.total_bytes_billed or 0), bool(job.cache_hit),
                     schema_tuple(rows.schema), int(job.total_bytes_processed or 0), result_bytes, job)


class ArrowStream:
    """Iterator of pyarrow RecordBatches from a finished job; `route` says how rows came back."""

    def __init__(self, client: Any, job: Any, batch_rows: int = 100_000, use_storage: bool = True) -> None:
        self.client, self.job, self.batch_rows, self.use_storage = client, job, batch_rows, use_storage
        self.route = "not_started"
        self.fallback_reason: str | None = None

    def _storage_client(self) -> Any:
        from google.cloud import bigquery_storage

        return bigquery_storage.BigQueryReadClient(credentials=self.client._credentials)

    def __iter__(self) -> Iterator[Any]:
        from google.api_core import exceptions as gexc

        if self.use_storage:
            try:
                it = self.job.result(page_size=self.batch_rows).to_arrow_iterable(bqstorage_client=self._storage_client())
                first = next(it, None)
                self.route = "storage_read_api"
            except (gexc.GoogleAPIError, ImportError, ValueError) as exc:
                self.fallback_reason = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}"
                self.route = "rest_row_pages"
            else:
                if first is not None:
                    yield first
                yield from it
                return
        else:
            self.route = "rest_row_pages"
        try:
            yield from self.job.result(page_size=self.batch_rows).to_arrow_iterable(bqstorage_client=None)
        except gexc.GoogleAPIError as exc:
            raise map_api_error(exc, "result stream") from exc


def stream_arrow(client: Any, job_or_id: Any, batch_rows: int = 100_000, use_storage: bool = True) -> ArrowStream:
    """RecordBatches of a finished job's result (Storage Read API, else REST pages)."""
    job = client.get_job(job_or_id) if isinstance(job_or_id, str) else job_or_id
    return ArrowStream(client, job, batch_rows, use_storage)


# ---------------------------------------------------------------- rights, metadata (spec 2.2 P0)
def _session(client: Any) -> Any:
    from google.auth.transport.requests import AuthorizedSession

    return AuthorizedSession(client._credentials)


def iam_permissions_held(client: Any, project: str, permissions: Sequence[str]) -> tuple[str, ...]:
    """projects.testIamPermissions; RightsUnverifiable when the API cannot be called."""
    url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}:testIamPermissions"
    try:
        r = _session(client).post(url, json={"permissions": list(permissions)}, timeout=60)
    except Exception as exc:  # noqa: BLE001 - any transport failure means the check did not run
        raise RightsUnverifiable(f"projects.testIamPermissions not reachable: {type(exc).__name__}") from exc
    if r.status_code != 200:
        status = ""
        try:
            status = r.json().get("error", {}).get("status", "")
        except ValueError:
            pass
        raise RightsUnverifiable(f"projects.testIamPermissions HTTP {r.status_code} {status}".strip())
    return tuple(sorted(r.json().get("permissions", [])))


def table_permissions_held(client: Any, project: str, dataset: str, table: str,
                           permissions: Sequence[str] = TABLE_WRITE_PERMISSIONS + ("bigquery.tables.getData",)
                           ) -> tuple[str, ...]:
    """tables.testIamPermissions: the caller's effective permissions on one table (inherited included)."""
    from google.api_core import exceptions as gexc

    path = f"/projects/{project}/datasets/{dataset}/tables/{table}:testIamPermissions"
    try:
        resp = client._connection.api_request(method="POST", path=path, data={"permissions": list(permissions)})
    except gexc.GoogleAPIError as exc:
        raise RightsUnverifiable(f"tables.testIamPermissions failed: {type(exc).__name__}") from exc
    return tuple(sorted(resp.get("permissions", [])))


def dataset_access(client: Any, project: str, dataset: str) -> list[dict[str, Any]]:
    """The dataset's access list as API dicts; RightsUnverifiable when it cannot be read."""
    from google.api_core import exceptions as gexc

    try:
        ds = client.get_dataset(f"{project}.{dataset}")
    except gexc.GoogleAPIError as exc:
        raise RightsUnverifiable(f"dataset access list not readable: {type(exc).__name__}") from exc
    return [e.to_api_repr() for e in ds.access_entries]


def dataset_time_travel_hours(client: Any, dataset: str) -> int | None:
    """maxTimeTravelHours from the dataset resource; None when it is absent or unreadable."""
    from google.api_core import exceptions as gexc

    try:
        ds = client.get_dataset(dataset)
    except gexc.GoogleAPIError:
        return None
    val = ds._properties.get("maxTimeTravelHours")
    return int(val) if val not in (None, "") else None


def table_info(client: Any, ref: str) -> TableInfo:
    """Type, view definition and size of a table (free metadata read)."""
    from google.api_core import exceptions as gexc

    try:
        t = client.get_table(ref)
    except gexc.GoogleAPIError as exc:
        raise map_api_error(exc, "table metadata") from exc
    return TableInfo(ref, str(t.table_type or "TABLE"), t.view_query, str(t.dataset_id),
                     int(t.num_rows) if t.num_rows is not None else None,
                     int(t.num_bytes) if t.num_bytes is not None else None)


def write_refusal_smoke(client: Any, project: str, dataset: str, cfg: JobConfig) -> tuple[bool, str]:
    """A DRY RUN of one CREATE TABLE in `dataset`; (True, reason) when it is refused with 403 (spec v5 0.2, 2.2 P0).

    Fix r6 H2: the probe is never submitted as a real job (dry_run=True: BigQuery checks bigquery.tables.create and
    creates nothing, bills nothing). A 403 is the expected refusal; an accepted dry run means the identity CAN create
    tables (`write_possible`) and preflight refuses (exit 2). No non-dry-run write statement exists in this plugin.
    Fix r10 (r9 LOW 3): the refusal is detected by the English error-message substring `bigquery.tables.create`; if
    Google rewords it, a real refusal becomes UNVERIFIABLE (fails safe, never a false pass).
    """
    from google.api_core import exceptions as gexc

    name = f"qt_write_probe_{uuid.uuid4().hex[:12]}"
    sql = (f"CREATE TABLE `{project}.{dataset}.{name}` (x INT64) OPTIONS("
           "expiration_timestamp = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR))")
    jc = _query_job_config(cfg, True)
    if not getattr(jc, "dry_run", False):  # defence in depth: a probe that can write is not a probe
        raise EngineError("write probe refused locally: its job config is not a dry run; nothing submitted")
    try:
        client.query(sql, job_config=jc, location=cfg.location)
    except gexc.Forbidden as exc:
        # fix r9 (r8 aera MEDIUM 1): only a 403 whose message names bigquery.tables.create is the refusal. A generic
        # accessDenied, a jobs.create denial, rateLimitExceeded or quotaExceeded is inconclusive (UNVERIFIABLE at
        # preflight, the raw reason recorded). See the docstring (r10).
        reasons = _api_error_reasons(exc)
        if "bigquery.tables.create" in str(exc):
            return True, "403 Forbidden on a CREATE TABLE dry run: bigquery.tables.create denied"
        raise EngineError(f"write-refusal dry run inconclusive: 403 {','.join(reasons) or 'no reason'}: "
                          f"{str(exc)[:300]}") from exc
    except gexc.GoogleAPIError as exc:
        raise EngineError(f"write-refusal dry run inconclusive: {type(exc).__name__}") from exc
    return False, "CREATE TABLE dry run accepted: write_possible (the identity can create tables; nothing was created)"


# ---------------------------------------------------------------- engine object used by the scripts
def redact(text: str, identity: "Identity") -> str:
    """Replace the project ID and the service-account address in an API message by placeholders (D-12).

    BigQuery error texts quote the project in the request URL ("projects/<id>/jobs"); without this the ID
    reached gate requests, warehouse_jobs results and logs (found by secret_scan.py in the thin slice).
    """
    out = str(text)
    if identity.service_account:
        out = out.replace(identity.service_account, "{{service_account}}")
    if identity.project:
        out = out.replace(identity.project, "{{project}}")
    return out


def _redacting(fn: Any) -> Any:
    """Method decorator: QueryTrailError messages leave the engine with identifiers redacted."""
    import functools

    @functools.wraps(fn)
    def wrapper(self: "BigQueryEngine", *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(self, *args, **kwargs)
        except QueryTrailError as exc:
            msg = redact(str(exc), self.identity)
            if msg == str(exc):
                raise
            raise type(exc)(msg) from None
    return wrapper


class UnmeteredJob(RefusedError):
    """A job asked of a BigQueryEngine that has no budget ledger (exit 2, nothing runs; fix r1 B-H3)."""


class BigQueryEngine:
    """The warehouse engine run_steps, preflight, pin_plan and contract_check talk to.

    Jobs (run, warehouse_digest, write_smoke) run only through the run's ledger (spec v2 5.3): the engine is
    given one at construction (`ledger=`) or by `metered(engine, ...)`, which attaches it. Without a ledger
    every job is refused (UnmeteredJob, exit 2); dry runs and metadata reads stay free. `allow_unmetered=True`
    is for transport probes outside a run only (tests/live/test_live_bq.py, build/verify_identity.py-style
    scripts); no plugin script passes it (tests/unit/test_bq_ledger.py checks).
    """

    name = "bigquery"

    def __init__(self, identity: Identity, lifetime_s: int = 3600, ledger: "Ledger | None" = None,
                 allow_unmetered: bool = False) -> None:
        self.identity = identity
        self.ledger = ledger
        self.allow_unmetered = bool(allow_unmetered)
        self._dry: dict[str, int] = {}
        self.credentials = credentials_for(identity, lifetime_s)
        self.client = make_client(self.credentials, identity.project, identity.location)

    def _dry_bytes(self, sql: str, cfg: JobConfig) -> int:
        if sql not in self._dry:
            self._dry[sql] = int(dry_run(self.client, sql, dataclasses.replace(cfg, dry_run=True)).total_bytes_processed)
        return self._dry[sql]

    def _job(self, sql: str, cfg: JobConfig, call: Any) -> Any:
        """`call(cfg)` runs the job with the config it is given (the ledger bounds its cap)."""
        if self.ledger is not None:
            return metered_call(self.ledger, self._dry_bytes, sql, cfg, call)
        if self.allow_unmetered:
            return call(cfg)
        raise UnmeteredJob("BigQueryEngine has no budget ledger: every job goes through bq.metered(...) or "
                           "ledger= (spec v2 5.3); nothing ran")

    @property
    def client_version(self) -> str:
        """google-cloud-bigquery version."""
        from google.cloud import bigquery

        return str(bigquery.__version__)

    @_redacting
    def dry_run(self, sql: str, cfg: JobConfig) -> DryRunResult:
        r = dry_run(self.client, sql, cfg)
        self._dry[sql] = int(r.total_bytes_processed or 0)
        return r

    @_redacting
    def run(self, sql: str, cfg: JobConfig) -> JobResult:
        return self._job(sql, cfg, lambda c: run_query(self.client, sql, c))

    def stream(self, result: JobResult, batch_rows: int = 100_000) -> ArrowStream:
        return stream_arrow(self.client, result.handle, batch_rows)

    @_redacting
    def warehouse_digest(self, sql: str, cfg: JobConfig) -> tuple[int, str, JobResult]:
        def call(c: JobConfig) -> tuple[int, str, JobResult]:
            res = run_query(self.client, warehouse_digest_sql(sql), c)
            row = next(iter(res.handle.result()))
            return int(row["qt_rows"]), digest_from_sum(row["qt_sum"]), res
        return self._job(sql, cfg, call)

    @_redacting
    def table_info(self, ref: str) -> TableInfo:
        return table_info(self.client, ref)

    @property
    def principal(self) -> str | None:
        """The service account the jobs run as (None in owner mode); never written to files (D-12)."""
        return None if self.identity.owner_mode else self.identity.service_account

    @_redacting
    def project_permissions(self, permissions: Sequence[str]) -> tuple[str, ...]:
        return iam_permissions_held(self.client, self.identity.project, permissions)

    @_redacting
    def list_tables(self, dataset: str) -> list[str]:
        from google.api_core import exceptions as gexc

        try:  # the iterator pages through every table (no cap; fix r2 B2-H1)
            return sorted(t.table_id for t in self.client.list_tables(f"{self.identity.project}.{dataset}",
                                                                       page_size=1000))
        except gexc.GoogleAPIError as exc:
            raise RightsUnverifiable(f"tables of a dataset not listable: {type(exc).__name__}") from exc

    @_redacting
    def table_permissions(self, dataset: str, table: str, permissions: Sequence[str]) -> tuple[str, ...]:
        return table_permissions_held(self.client, self.identity.project, dataset, table, permissions)

    @_redacting
    def dataset_access(self, dataset: str) -> list[dict[str, Any]]:
        return dataset_access(self.client, self.identity.project, dataset)

    @_redacting
    def write_smoke(self, dataset: str, cfg: JobConfig) -> tuple[bool, str]:
        def call(c: JobConfig) -> tuple[bool, str]:
            try:
                refused, detail = write_refusal_smoke(self.client, self.identity.project, dataset, c)
            except EngineError as exc:  # r9: the raw reason is recorded, redacted (D-12)
                raise EngineError(redact(str(exc), self.identity)) from exc
            return refused, redact(detail, self.identity)
        if self.ledger is not None:
            return metered_write_smoke(self.ledger, cfg, call)
        if self.allow_unmetered:
            return call(cfg)
        raise UnmeteredJob("BigQueryEngine has no budget ledger: the write probe goes through bq.metered(...) "
                           "(spec v2 5.3); nothing ran")

    @_redacting
    def time_travel_hours(self, dataset: str) -> int | None:
        return dataset_time_travel_hours(self.client, f"{self.identity.project}.{dataset}"
                                         if "." not in dataset else dataset)


# ---------------------------------------------------------------- per-run budget ledger (fix r1 B-H3; spec v2 5.3)
MIN_BILLED_PER_TABLE = 10 * 1024 ** 2  # BigQuery bills at least 10 MiB per table a query references (slice, L)
LEDGER_FIELDS = ("entry_id", "job_label", "phase", "dry_run_bytes", "reserved_bytes", "authorized_bytes",
                 "bytes_billed", "reconciled_utc", "time_utc", "maximum_bytes_billed", "outcome")


class BudgetExceeded(QueryTrailError):
    """spent + held + reserved would pass the per-run budget: G15 (filter, raise cap, stop), exit 3, nothing runs."""

    exit_code = ExitCode.NEEDS_DECISION

    def __init__(self, message: str, details: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.details = dict(details)


def tables_referenced(sql: str) -> int:
    """Distinct base tables a statement reads (CTE names and table functions excluded).

    Unparsable text counts every backtick-quoted dotted reference, at least one (a reservation never
    under-counts because the parser failed).
    """
    from lib import sqltext

    try:
        return len(sqltext.base_table_keys(sqltext.parse_one_query(sql, "bigquery")))
    except Exception:  # noqa: BLE001 - fall back to a conservative textual count
        return max(1, len(set(re.findall(r"`[^`]+\.[^`]+`", sql))))


def reservation(dry_run_bytes: int | None, sql: str) -> int:
    """Admission estimate before a job: dry-run estimate + 10 MiB x distinct tables referenced (fix r2 B2-H2).

    It refuses a job that cannot fit (the per-table minimum may apply to each table on top of what the dry run
    counted). What the ledger holds while a job is unreconciled is its authorised amount, the maximum_bytes_billed it
    was sent with, not this estimate: dry-run bytes need not bound a later execution (fix r3 B3-2, aera r3 H2).
    """
    return int(dry_run_bytes or 0) + (MIN_BILLED_PER_TABLE * tables_referenced(sql) if sql else 0)


def is_unresolved_legacy(e: Mapping[str, Any]) -> bool:
    """Fix r4 C4-2: an entry written before v4 (no authorized_bytes) whose bill was never recorded (bytes_billed null,
    no reconciled_utc). Its submitted cap may be larger than the reservation it recorded."""
    return e.get("authorized_bytes") is None and e.get("bytes_billed") is None and not e.get("reconciled_utc")


def legacy_hold(e: Mapping[str, Any], job_cap: int | None = None) -> int | None:
    """Fix r5 H1: what an unresolved legacy entry holds: its historical submitted cap (maximum_bytes_billed), the
    only amount BigQuery bounded its bill by; None when that cap is not recorded. A reservation is an estimate and the
    current configuration cap need not have bounded the old submission, so neither stands in for it (`job_cap` is
    accepted and ignored)."""
    cap = e.get("maximum_bytes_billed")
    return None if cap is None else int(cap)


def entry_charge(e: Mapping[str, Any], job_cap: int | None = None) -> int:
    """What one ledger entry counts against the budget: its reconciled bill, else the authorised amount it holds.

    An entry written before v4 (no authorized_bytes) with a recorded bill counts that bill. An unresolved legacy entry
    (no authorized_bytes, no bill) holds its recorded maximum_bytes_billed (fix r5 H1); one without it counts 0
    here, and Ledger.check refuses every new job while it exists.
    """
    if e.get("bytes_billed") is not None:
        return int(e["bytes_billed"])
    if is_unresolved_legacy(e):
        return int(legacy_hold(e, job_cap) or 0)
    return int(e.get("authorized_bytes") or e.get("maximum_bytes_billed") or e.get("reserved_bytes") or 0)


class UnboundLegacyEntry(RefusedError):
    """Fix r5 H1: an unresolved legacy ledger entry records no submitted cap: no job is admitted."""


class Ledger:
    """The run's one budget ledger, kept in manifest.json `budget` (spec v4 5.3; decisions 9 ruling 5).

    Every job of every phase is held here before it is submitted (authorized_bytes = the maximum_bytes_billed it
    is sent with, bytes_billed null) and reconciled after it ran (the actual bytes billed; a job BigQuery refused
    over its cap bills 0). A job whose bill is unknown keeps holding its authorised amount until reconciled.
    Entries survive attempts, so retries count.
    """

    def __init__(self, run_dir: Any, cfg: Mapping[str, Any], phase: str, attempt: int | None = None,
                 now_fn: Any = None) -> None:
        from pathlib import Path

        from lib import manifest

        self.run_dir, self.cfg, self.phase = Path(run_dir), cfg, phase
        m = manifest.load(self.run_dir) or {}
        self.attempt = int(attempt or m.get("attempt") or 1)
        self.entries: list[dict[str, Any]] = [dict(e) for e in (m.get("budget") or {}).get("ledger", [])]
        self._now = now_fn

    @property
    def budget(self) -> int:
        return int(self.cfg["caps"]["warehouse_bytes_per_run"])

    @property
    def spent(self) -> int:
        """Reconciled bills only: what BigQuery is known to have billed."""
        return sum(int(e["bytes_billed"]) for e in self.entries if e.get("bytes_billed") is not None)

    @property
    def held(self) -> int:
        """Authorised amounts of the entries whose bill is not reconciled (pending or unknown)."""
        return sum(entry_charge(e, self.job_cap) for e in self.entries if e.get("bytes_billed") is None)

    @property
    def job_cap(self) -> int:
        return int((self.cfg.get("caps") or {}).get("warehouse_bytes_per_job") or DEFAULT_MAX_BYTES_BILLED)

    def unbound_legacy(self) -> list[str]:
        """Fix r5 H1: ids (or job labels) of unresolved legacy entries that recorded no submitted cap
        (maximum_bytes_billed), whether or not they recorded a reservation."""
        return [str(e.get("entry_id") or e.get("job_label") or "?") for e in self.entries
                if is_unresolved_legacy(e) and legacy_hold(e, self.job_cap) is None]

    @property
    def committed(self) -> int:
        return self.spent + self.held

    @property
    def remaining(self) -> int:
        return self.budget - self.committed

    def refresh(self) -> None:
        """Re-read the ledger from manifest.json (another ledger on this run may have written since; dec r2 L3)."""
        from lib import manifest

        m = manifest.load(self.run_dir) or {}
        disk = [dict(e) for e in (m.get("budget") or {}).get("ledger", [])]
        if len(disk) >= len(self.entries):
            self.entries = disk

    def _details(self, what: str, reserved: int, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {"phase": self.phase, "what": what, "spent_bytes": self.spent, "held_bytes": self.held,
                "reserved_bytes": int(reserved), "budget_bytes": self.budget, "remaining_bytes": self.remaining,
                "options": ["filter", "raise cap", "stop"], **dict(extra or {})}

    def bound(self, cfg: JobConfig, what: str) -> JobConfig:
        """The job config with maximum_bytes_billed = authorized = min(job cap, remaining budget) (fix r3 B3-2)."""
        cap = min(int(cfg.maximum_bytes_billed), self.remaining)
        if cap <= 0:
            raise BudgetExceeded(f"{what}: no budget left (spent {self.spent}, held {self.held} of {self.budget} "
                                 "bytes; G15: filter, raise the cap, or stop)", self._details(what, 0))
        return dataclasses.replace(cfg, maximum_bytes_billed=cap)

    def block(self) -> dict[str, Any]:
        """manifest.budget fields owned by the ledger."""
        return {"ledger": [dict(e) for e in self.entries], "spent_bytes": self.spent, "held_bytes": self.held,
                "budget_bytes": self.budget, "remaining_bytes": self.remaining}

    def check(self, reserved: int, what: str, extra: Mapping[str, Any] | None = None) -> None:
        """Raise BudgetExceeded when spent + held + reserved > budget (nothing is written).

        Fix r5 H1: refuse (exit 2) every new job while an unresolved legacy entry records no submitted cap, whatever
        reservation it recorded and whatever the current configuration cap: nothing bounds what it may still bill."""
        self.refresh()
        unbound = self.unbound_legacy()
        if unbound:
            raise UnboundLegacyEntry(f"{what}: ledger entry {', '.join(unbound)} was written before v4 with no bill "
                                     "and no recorded maximum_bytes_billed: nothing bounds what it may still bill; "
                                     "no command records a legacy bill: close this run with close.py and start a "
                                     "new run (run_init.py); no job admitted")
        if self.committed + int(reserved) > self.budget:
            raise BudgetExceeded(
                f"{what}: spent {self.spent} + held {self.held} + reserved {int(reserved)} bytes > per-run budget "
                f"{self.budget} (G15: filter, raise the cap, or stop)", self._details(what, reserved, extra))

    def _stamp(self) -> str:
        from lib import runctx

        return runctx.fmt_utc(self._now() if self._now else runctx.utc_now())

    def hold(self, job_label: str, dry_run_bytes: int | None, reserved: int, authorized: int) -> str:
        """Write the entry before the job is submitted (authorized_bytes held, bytes_billed null); return its id."""
        self.refresh()
        e = {"entry_id": uuid.uuid4().hex, "job_label": str(job_label), "phase": self.phase,
             "dry_run_bytes": None if dry_run_bytes is None else int(dry_run_bytes), "reserved_bytes": int(reserved),
             "authorized_bytes": int(authorized), "bytes_billed": None, "reconciled_utc": None,
             "time_utc": self._stamp(), "maximum_bytes_billed": int(authorized), "outcome": "submitted"}
        self.entries.append(e)
        self._save()
        return str(e["entry_id"])

    def reconcile(self, entry_id: str, bytes_billed: int | None, outcome: str) -> dict[str, Any]:
        """Record the bill of a held entry; None = unknown, so the authorised amount stays held."""
        self.refresh()
        e = next((x for x in self.entries if x.get("entry_id") == entry_id), None)
        if e is None:
            raise RefusedError(f"ledger entry {entry_id} not found; nothing reconciled")
        e["outcome"] = outcome
        if bytes_billed is not None:
            e["bytes_billed"], e["reconciled_utc"] = int(bytes_billed), self._stamp()
        self._save()
        return dict(e)

    def record(self, job_label: str, dry_run_bytes: int | None, reserved: int, bytes_billed: int | None,
               outcome: str, maximum_bytes_billed: int | None = None) -> dict[str, Any]:
        """Hold and reconcile in one call (a caller outside metered_call that already knows the outcome)."""
        auth = int(maximum_bytes_billed if maximum_bytes_billed is not None else reserved)
        return self.reconcile(self.hold(job_label, dry_run_bytes, reserved, auth), bytes_billed, outcome)

    def _save(self) -> None:
        from lib import manifest

        m = manifest.load(self.run_dir) or manifest.new_manifest(self.cfg, self.attempt)
        m["budget"] = {**(m.get("budget") or {}), **self.block()}
        manifest.save(self.run_dir, m)


class MeteredEngine:
    """Any engine (BigQuery or a test stand-in) behind the run's ledger: the one query path (B-H3).

    dry_run results are remembered by SQL text, so a job the caller dry-ran is not dry-run twice; a job
    with no remembered dry run is dry-run here first (free) to size its reservation.
    """

    def __init__(self, engine: Any, ledger: Ledger) -> None:
        self._engine, self.ledger = engine, ledger
        self._dry: dict[str, int] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._engine, name)

    @property
    def inner(self) -> Any:
        return self._engine

    def dry_run(self, sql: str, cfg: JobConfig) -> DryRunResult:
        r = self._engine.dry_run(sql, cfg)
        self._dry[sql] = int(r.total_bytes_processed or 0)
        return r

    def dry_bytes(self, sql: str, cfg: JobConfig) -> int:
        """Remembered dry-run bytes of `sql`, else a dry run now."""
        if sql not in self._dry:
            self.dry_run(sql, dataclasses.replace(cfg, dry_run=True))
        return self._dry[sql]

    def run(self, sql: str, cfg: JobConfig) -> JobResult:
        return metered_call(self.ledger, self.dry_bytes, sql, cfg, lambda c: self._engine.run(sql, c))

    def warehouse_digest(self, sql: str, cfg: JobConfig) -> tuple[int, str, JobResult]:
        return metered_call(self.ledger, self.dry_bytes, sql, cfg, lambda c: self._engine.warehouse_digest(sql, c))

    def write_smoke(self, dataset: str, cfg: JobConfig) -> tuple[bool, str]:
        return metered_write_smoke(self.ledger, cfg, lambda c: self._engine.write_smoke(dataset, c))


def metered_call(ledger: Ledger, dry_bytes: Any, sql: str, cfg: JobConfig, call: Any) -> Any:
    """Admit, authorise, hold, run, reconcile: the one accounting path for every job (fix r3 B3-2).

    `call(cfg)` runs the job with maximum_bytes_billed = authorized = min(job cap, remaining budget); the ledger
    holds `authorized` from before submission until the bill is reconciled; an unknown bill keeps it held.
    """
    label = str(cfg.labels.get("step", "?"))
    dry = dry_bytes(sql, cfg)
    res = reservation(dry, sql)
    ledger.check(res, f"job {label}", {"job_label": label})
    bounded = ledger.bound(cfg, f"job {label}")
    eid = ledger.hold(label, dry, res, bounded.maximum_bytes_billed)
    try:
        out = call(bounded)
    except CapExceeded:
        ledger.reconcile(eid, 0, "refused_over_cap")
        raise
    except Exception:
        ledger.reconcile(eid, None, "bill_unknown_authorized_held")
        raise
    billed = out[2].total_bytes_billed if isinstance(out, tuple) else out.total_bytes_billed
    ledger.reconcile(eid, int(billed or 0), "ok")
    return out


def metered_write_smoke(ledger: Ledger, cfg: JobConfig, call: Any) -> tuple[bool, str]:
    """The P0 write probe (a dry-run DDL, fix r6 H2) on the ledger: held at its authorised cap, reconciled to 0 when
    BigQuery answered (a dry run bills nothing; outcome dry_run_refused or write_possible); an inconclusive probe
    keeps its authorised amount held."""
    label = str(cfg.labels.get("step", "preflight"))
    ledger.check(0, "write-refusal smoke")
    bounded = ledger.bound(cfg, "write-refusal smoke")
    eid = ledger.hold(label, None, 0, bounded.maximum_bytes_billed)
    try:
        refused, detail = call(bounded)
    except Exception:
        ledger.reconcile(eid, None, "write_smoke_inconclusive")
        raise
    ledger.reconcile(eid, 0, "dry_run_refused" if refused else "write_possible")
    return refused, detail


def metered(engine: Any, run_dir: Any, cfg: Mapping[str, Any], phase: str, attempt: int | None = None) -> Any:
    """The engine behind the run's ledger.

    A BigQueryEngine gets the ledger attached and meters itself (it refuses jobs without one); any other
    engine (a test stand-in) is wrapped in a MeteredEngine; an engine that is already metered keeps its
    wrapper, and its ledger takes this call's phase, attempt and caps (dec r2 L4: every entry carries the phase
    of the script that ran it). A different run folder gets a fresh ledger.
    """
    from pathlib import Path

    def fresh_or_retag(led: "Ledger | None") -> Ledger:
        if led is None or Path(led.run_dir).resolve() != Path(run_dir).resolve():
            return Ledger(run_dir, cfg, phase, attempt)
        led.phase, led.cfg = phase, cfg
        if attempt:
            led.attempt = int(attempt)
        return led

    if isinstance(engine, (MeteredEngine, BigQueryEngine)):
        engine.ledger = fresh_or_retag(engine.ledger)
        return engine
    return MeteredEngine(engine, Ledger(run_dir, cfg, phase, attempt))
