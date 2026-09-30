# ADR-031 · Wrapper contract v1: rows with declared keys, loud failures, one parse path

- **Date:** 2026-09-30
- **Status:** accepted

## Context

Partners are going to write wrappers for their own applications, and the value of a wrapper is
that it is shared: one parser per application, so everyone who runs it gets the same numbers.
That only works if the contract they write against is stable and if a broken parser cannot go
unnoticed. The contract until now had four problems:
- `read_data()` returned one list of samples per metric, which cannot express a sweep printed in
  one run (for example latency for every message size).
- A parser that raised aborted the whole experiment. Some wrappers returned zeros on a parse
  failure, and zeros look like a converged measurement.
- A missing binary produced an empty command.
- Every co-running app shared the worker's working directory.

We compared how ReFrame, JUBE, Ramble and SbatchMan model results. All of them use a record per
result with named fields, and a sweep dimension is a field of that record.

## Decision

`crab.wrappers.base.base` implements contract version 1 (`wrapper_api = 1`). A wrapper declares:
- **`metadata`:** the metrics, each with `name`, `unit` and `conv`. It can optionally add `type`
  (`float` by default, or `int`, `str` or `bool`) and `role: "check"`.
- **`keys`:** the names of its sweep dimensions (default: none).
- **`executable`:** the program name to find on `PATH` (optional).
- **`get_extra_artifacts()`:** files to keep from each run (optional).

- `read_data()` returns rows: one dict per sample, holding every key and every metric and
  nothing else. The old one-list-per-metric shape is still accepted, converted to rows, and
  logged as deprecated; it stops being accepted at v1.0.
- Every parse goes through one function (`core/data/parse.py`), used by runs and by
  `crab parse`. A result that raises, has the wrong shape, or has a value of the wrong type is a
  parse failure. That app contributes nothing to the run, and the run counts as failed, the same
  as a non-zero exit. Nothing is truncated, padded or replaced with zeros.
- Convergence is computed per metric and per value of the keys, so each size of a sweep has to
  converge on its own. Text and boolean fields are recorded but never used for convergence.
- A field with `role: "check"` must be boolean. When it is false, the row is kept in the CSV,
  the run counts as failed, and that sample is left out of convergence.
- The binary comes from the app's `binary` key in the config, then the receipt, then
  `executable` on `PATH`. For wrappers that launch through the base class, a missing binary
  fails the experiment before any run, with a message listing what was tried.
- Each app runs in its own working directory, `<experiment>/run_<n>/app_<id>/`, available to
  the wrapper as `self.run_dir`. The files listed by `get_extra_artifacts()` are copied to
  `<experiment>/artifacts/run_<n>/app_<id>/`, so they survive `retain_files: false`.
- The CSV keeps `run_id` and `msg_size` first, then the key columns, then one
  `<app>_<metric>_<unit>` column per metric. A keyless wrapper's CSV is unchanged. `run_id` is
  now the real run number. Before, a run that came after a failed one was numbered as if the
  failed one had not happened.
- Every job records `crab_run.json`: the CRAB version and commit, `wrapper_api`, the wrappers
  checkout's commit and whether it had local edits, and each wrapper file's hash.

## Alternatives considered

- Freeze the list-per-metric shape: nothing to migrate, but it cannot express sweeps, per-rank
  values, checks or text, and it pools samples across what should be separate series.
- Rows plus separate "probe" sources for external measurements (energy, counters) in v1: more
  complete, but a large engine change that is hard to teach. Probes can be added later without
  touching wrappers; the key names `node`, `rank`, `t` and `device` are reserved for them.
- Keep units in a separate schema file instead of column names: cleaner, but it breaks every
  existing analysis script that reads the columns.
- A stdlib-only parser that can be used without CRAB: more portable, but helpers get duplicated
  and there are two sources of truth. Wrappers may import `crab`, and anyone can call the
  parser through `crab parse`.

## Consequences

Existing wrappers keep working unchanged, with a deprecation warning until they return rows.
Each wrapper in the shared repository must be migrated before v1.0. A parser bug now shows up as
failed runs with the reason in the log, instead of plausible numbers. Result folders gain
`app_<id>/` subfolders inside each `run_<n>/`, and an `artifacts/` folder when a wrapper asks
for one. A result recorded after a failed run gets a different `run_id` than it would have before.
