"""``/api/jobs`` reports: one submission's `crab history` rows
(``GET /{record_id}/experiments``) and a use case's rows across every cluster
(``GET /report/{config_name}``).

Split out of `api/jobs.py`, whose shared helpers (`_jobs_store`, `_profiles`,
`_live_or_cached`) this module imports; `jobs.py` never imports it back. The
router is included right after `jobs.router` in `server.py` so the registration
order of the `/api/jobs` routes stays the same as before the split.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from crab.web.api.jobs import _jobs_store, _live_or_cached, _profiles
from crab.web.connections.manager import ConnectionManager
from crab.web.connections.transport import Transport
from crab.web.errors import NotFoundError, RemoteConnectionError
from crab.web.remoteops.crab_cli import run_crab_json
from crab.web.store.jobs import JobRecord
from crab.web.store.profiles import Profile

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _manager(request: Request) -> ConnectionManager:
    # A copy of jobs.py's helper, deliberate: two small independent copies
    # (same as remotes.py and results.py), not a shared abstraction.
    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        raise RemoteConnectionError("Connection manager is not initialised.")
    return manager


def _live_transport(profile_name: str, request: Request) -> Transport:
    """The profile's existing live transport, or a clean error if not connected.

    No implicit reconnect: the user must have connected the cluster first.
    """
    transport = _manager(request).get(profile_name)
    if transport is None:
        raise RemoteConnectionError(
            f"'{profile_name}' is not connected. Connect the cluster first, then retry."
        )
    return transport


class ReportExperiment(BaseModel):
    """One `crab history` row for a use case, joined with local submit metadata if known."""

    cluster: str
    system: str
    job_name: str
    experiment_name: str
    timestamp: str
    numnodes: str
    ppn: str
    apps_list: str
    status: str
    tags: str
    relative_path: str
    # How many of this experiment's internal runs (min/maxruns
    # retry loop) failed, vs how many were attempted -- lets a client tell
    # "one bad run tainted the overall status, but most runs succeeded and
    # have real data" apart from "everything failed." Empty string (not
    # None) for a `metadata.csv` written before this field existed, matching
    # the contract's degrade-gracefully convention -- never migrated in
    # place (owner's explicit call, see ADR-023).
    total_runs: str = ""
    failed_runs: str = ""
    # The allocation seed of a random experiment; "" for deterministic
    # allocations and for a `metadata.csv` written before this field existed.
    seed: str = ""
    record_id: str | None = None
    job_id: str | None = None
    submitted_at: str | None = None


class StaleCluster(BaseModel):
    """One cluster whose history in this report came from the local cache, not live."""

    cluster: str
    cached_at: str


class UseCaseReport(BaseModel):
    config_name: str
    experiments: list[ReportExperiment]
    clusters_skipped: list[str]
    clusters_stale: list[StaleCluster]


def _job_basename(relative_path: str) -> str:
    """First path component of a history row's ``./<job_basename>/<exp_basename>``."""
    parts = [p for p in relative_path.split("/") if p and p != "."]
    return parts[0] if parts else ""


class JobDetail(BaseModel):
    """Every `crab history` row for one exact submission."""

    record_id: str
    config_name: str
    cluster: str
    system: str
    job_id: str
    submitted_at: str
    experiments: list[ReportExperiment]
    stale: bool
    cached_at: str | None = None
    rerun_of: JobRecord | None = None
    rerun_experiments: list[str] | None = None
    reruns: list[JobRecord]


@router.get("/{record_id}/experiments")
async def job_experiments(record_id: str, request: Request) -> JobDetail:
    """Every `crab history` row for this exact submission, not the whole use case.

    Same worst-first-independent matching as `_resolve_via_history`/`use_case_report`
    (a data_dir's basename against a history row's `relative_path` prefix), but
    returns every matching row instead of collapsing to one worst status.
    """
    jobs_store = _jobs_store(request)
    rec = jobs_store.get(record_id)
    profile = _profiles(request).get(rec.cluster)
    basename = Path(rec.data_dir).name

    parent = None
    if rec.rerun_of:
        try:
            parent = jobs_store.get(rec.rerun_of)
        except NotFoundError:
            parent = None
    reruns = [r for r in jobs_store.list() if r.rerun_of == rec.id]

    async def fetch() -> dict:
        transport = _live_transport(rec.cluster, request)
        return await run_crab_json(
            transport, profile, ["history", "-s", rec.system, "--json"], timeout=30.0
        )

    history, stale, cached_at = await _live_or_cached(
        request, "history", f"cluster:{rec.cluster}:system:{rec.system}", fetch
    )

    experiments = [
        ReportExperiment(
            cluster=rec.cluster,
            record_id=rec.id,
            job_id=rec.job_id,
            submitted_at=rec.submitted_at,
            **row,
        )
        for row in history["experiments"]
        if _job_basename(row.get("relative_path", "")) == basename
    ]

    return JobDetail(
        record_id=rec.id,
        config_name=rec.config_name,
        cluster=rec.cluster,
        system=rec.system,
        job_id=rec.job_id,
        submitted_at=rec.submitted_at,
        experiments=experiments,
        stale=stale,
        cached_at=cached_at,
        rerun_of=parent,
        rerun_experiments=rec.rerun_experiments,
        reruns=reruns,
    )


@router.get("/report/{config_name}")
async def use_case_report(config_name: str, request: Request) -> UseCaseReport:
    """Every experiment ever run under `config_name`, across every connected cluster.

    Sourced from `crab history --json` (authoritative: matches manual runs too,
    not only ones submitted through this dashboard), then joined against the
    local job registry just for submit metadata (job id, submitted_at). A
    disconnected cluster is skipped and named in `clusters_skipped` rather than
    silently omitted, same convention as `list_jobs`'s `connected` flag. A
    disconnected cluster with a prior cached fetch falls back to it instead of
    being skipped, named in `clusters_stale` rather than
    `clusters_skipped`: the caller can still tell the two cases apart.
    """
    records_by_cluster: dict[str, dict[str, JobRecord]] = {}
    for rec in _jobs_store(request).list():
        records_by_cluster.setdefault(rec.cluster, {})[Path(rec.data_dir).name] = rec

    experiments: list[ReportExperiment] = []
    clusters_skipped: list[str] = []
    clusters_stale: list[StaleCluster] = []
    for profile in _profiles(request).list():

        async def fetch(profile: Profile = profile) -> dict:
            transport = _live_transport(profile.name, request)
            return await run_crab_json(transport, profile, ["history", "--json"], timeout=30.0)

        try:
            history, stale, cached_at = await _live_or_cached(
                request, "history", f"cluster:{profile.name}", fetch
            )
        except RemoteConnectionError:
            clusters_skipped.append(profile.name)
            continue

        if stale:
            assert cached_at is not None
            clusters_stale.append(StaleCluster(cluster=profile.name, cached_at=cached_at))

        known_jobs = records_by_cluster.get(profile.name, {})
        for row in history["experiments"]:
            known = known_jobs.get(_job_basename(row["relative_path"]))
            # A row's `job_name` is the config's internal `global_options.name`,
            # which can differ from the library display name used as `config_name`
            # here (e.g. a submitted config named "msgsize_scaling_study" saved in
            # the library as "Message-size scaling study..."). The local registry
            # record carries the display name, so prefer that join; only fall back
            # to matching `job_name` directly for runs with no local record (manual
            # `crab run` on the cluster, never submitted through this dashboard).
            if known is not None:
                if known.config_name != config_name:
                    continue
            elif row["job_name"] != config_name:
                continue
            experiments.append(
                ReportExperiment(
                    cluster=profile.name,
                    record_id=known.id if known else None,
                    job_id=known.job_id if known else None,
                    submitted_at=known.submitted_at if known else None,
                    **row,
                )
            )

    return UseCaseReport(
        config_name=config_name,
        experiments=experiments,
        clusters_skipped=clusters_skipped,
        clusters_stale=clusters_stale,
    )
