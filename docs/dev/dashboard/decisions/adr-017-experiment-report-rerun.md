# ADR-017 · Experiment report: history-sourced data, `--only` for partial rerun

- **Date:** 2026-07-05
- **Status:** accepted

## Context

The Jobs view only ever showed one row per Slurm submission, with no way to see the state of the
individual experiments inside a multi-experiment config, no way to see why a given experiment
failed beyond the job's overall slurm log, and no way to rerun anything short of the whole
config again. ADR-016's Consequences section already flagged the last of these — resubmitting
only the failed experiments from a job — as needing per-experiment status exposed somewhere
first. Three related questions needed answers: where does a per-experiment report get its data
from, how does the dashboard read the per-app files an experiment writes on failure, and how
does "rerun some experiments" actually work given a Slurm job can't be edited once submitted.

## Decision

**Report data source.** The per-use-case report (`GET /api/jobs/report/{config_name}`) calls
`crab history --json` once per connected cluster with no `-s` filter (scans every system under
that cluster's data root) and matches rows by `job_name == config_name`, joining the local job
registry only for submit metadata (job id, submitted timestamp). This makes `crab history` the
source of truth for status — the same precedent ADR-016 set for the sacct-purge fallback — so
the report also shows experiments run by hand outside the dashboard, not only ones it submitted
itself. A disconnected cluster is named in a `clusters_skipped` list rather than silently
dropped.

**Per-app logs.** `ExperimentRunner.execute` already writes `error_app_<id>.log` into an
experiment's own directory on any non-zero app exit. Reading it got a new `crab logs
--experiment <name> --json` flag (same command family and JSON schema versioning as the
existing job-level `crab logs`, rather than a new subcommand) backed by
`contract.gather_experiment_logs`, which glob-reads every `error_app_*.log` in that directory.
Unlike the job-level log reader, a missing experiment directory raises instead of degrading
gracefully: the caller always derives the experiment name from a real history row, so a missing
directory means the wrong data_dir was passed, not "no errors yet" (that case is an empty file
list, not a missing directory).

**Partial rerun.** Slurm jobs are immutable once submitted, so "rerun" is always a fresh
submission. A new `crab run --only key1,key2` flag filters the config's `experiments` dict down
to the given keys before `config.json` is ever written to the job's data directory — the worker
process (which only ever reads that file) needs no changes. An unknown key raises clearly rather
than silently running everything or nothing. Whole-config rerun needed no new capability at all:
it resubmits the job's already-stored `config_snapshot` through the existing submit endpoint.

## Alternatives considered

- Cache result CSVs / history rows locally instead of querying `crab history` live each time —
  faster on repeat views, but introduces a staleness question this dashboard doesn't otherwise
  have (job status is always polled live); deferred to whenever a results cache is built for
  other reasons.
- A generic "read any file under a job's data_dir" endpoint instead of a purpose-built
  `--experiment` flag — more flexible, but exposes arbitrary path handling over the CLI contract
  for no current need; the fixed `error_app_*.log` glob is what the dashboard actually has to
  show today.
- Re-link `start`/`end` sync points across a filtered subset when rerunning (e.g. warn if a
  selected experiment references a barrier from one that wasn't selected) — real usefulness, but
  real complexity; left as the user's judgment call for now, revisit if it causes confusion in
  practice.
- Group the report by a stable "use case ID" instead of the exact `config_name` string — more
  robust to renames, but no such ID exists anywhere in the config today; introducing one for
  this alone wasn't judged worth it yet.

## Consequences

Easier: the report reuses the same contract-command pattern as everything else in the backend
(ADR-002), so no cluster-specific behavior lives outside the versioned CLI seam. Harder: a config
renamed in the library starts a fresh report group, since grouping is by exact string; a job
whose data_dir was purged from Slurm accounting but whose `metadata.csv` rows are still on disk
still shows up correctly (same history-based read), but a job with genuinely no matching history
row (e.g. metadata not written yet) shows nothing until it is. "Rerun selected" across
experiments that span more than one job submission is intentionally not supported — the UI
disables it rather than guessing which config snapshot to resubmit.

**Amendment (2026-07-06):** the report described above is now the secondary view. Clicking a
Jobs card opens a new per-job detail view scoped to that exact submission first; the report
stays reachable from there as "every run of this use case, across every submission," for when
someone wants the wider history rather than one job's own experiments. Same data source and
matching logic in both places, just one collapsed to a single submission and the other spanning
every submission under the same config name.
