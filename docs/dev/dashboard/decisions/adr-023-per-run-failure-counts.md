# ADR-023 · Per-run failure counts as additive fields, no `metadata.csv` migration

- **Date:** 2026-07-08
- **Status:** accepted

## Context

An experiment's overall status latches to `FAILED` on the first bad run within its
min/maxruns retry loop and never resets, even though every other run in that same loop that
succeeded still has its data collected and written to the CSV. A user seeing a `FAILED`
experiment that nonetheless shows real chart data in Results had no way to tell "3 of 10 runs
failed, here's data from the other 7" apart from "the whole thing is garbage" — the engine
tracked only the latched status, never how many runs were attempted or how many failed.

`_write_to_registry` (`core/experiment/runner.py`) appends to a per-system `metadata.csv` whose
header row is written once, only when the file is first created. Any cluster already in use has
a `metadata.csv` with the old column set; adding new trailing columns doesn't make `crab
history` see them by name on that file until it's reset, since `csv.DictReader` keys off the
header line written at creation time.

## Decision

Track `total_runs`/`failed_runs` as two new additive columns, threaded through
`ExperimentRunner` → `_HISTORY_COLUMNS` (`cli/contract.py`) → `ReportExperiment`
(`web/api/jobs.py`) → the frontend. `experiment_status` itself keeps latching to `FAILED` on any
failure; the new fields sit alongside it as detail, not a replacement.

No migration of existing `metadata.csv` files. The owner explicitly chose not to migrate:
single-user setup, no other researchers on the affected clusters, comfortable with existing
history simply not reporting the new fields until a system's `metadata.csv` is manually reset. A
pre-existing file keeps its old header forever; `gather_history`'s existing
degrade-to-empty-string handling for a missing column already covers this without any code
change beyond adding the two names to `_HISTORY_COLUMNS`.

The Results view needed a new endpoint, `GET
/api/results/{cluster}/{system}/{job_basename}/experiments`, rather than reusing the existing
`job_experiments` route. `job_experiments` is registry-dependent (looks up a `record_id`
first); Results must work identically for CLI-only jobs that were never submitted via the
dashboard (plan 077 decision 7's interoperability requirement), so the new route resolves
purely from a live/cached `crab history` query filtered by job basename, matching the pattern
`get_results_index` already uses.

## Alternatives considered

- Reset `experiment_status` to something like `PARTIAL` when later runs recover — rejected:
  "any failure taints the experiment" is itself useful signal on its own, and this ADR's fields
  add detail alongside it rather than replacing it with a third status value every caller would
  need to learn.
- An atomic temp-file-plus-rename migration that rewrites an existing `metadata.csv` header in
  place the first time a system is touched post-upgrade — designed during grilling (including a
  crash-mid-migration test plan), then explicitly declined by the owner as unnecessary
  complexity for a single-user setup with no compatibility obligations to other researchers.
- Reusing `job_experiments` for the Results sidebar's data — rejected because it would silently
  drop CLI-only jobs, breaking the interoperability guarantee plan 077 established.

## Consequences

Easier: a partial failure is now visible as "N/M runs failed" next to the status badge on both
`ExperimentCard.vue` and the Results sidebar, without touching the meaning of `experiment_status`
itself. Harder / accepted cost: a cluster with prior history keeps its old `metadata.csv` header
forever unless the owner manually deletes or resets that file — every future row appended to it,
old experiment or new, stays unreadable-by-name for these two fields until then. This is a known,
accepted limitation, not a bug to chase if the feature "doesn't work" on a specific cluster.
