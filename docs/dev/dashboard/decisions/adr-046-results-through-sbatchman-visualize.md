# ADR-046 · Results of SbatchMan-launched runs are explored with `sbatchman visualize`, on a laptop copy

- **Date:** 2026-11-10
- **Status:** accepted

## Context

On the SbatchMan line, a partner launches jobs with `sbatchman launch` and each job leaves its CRAB CSVs under
`SbatchMan/experiments/<cluster>/<config>/<tag>/<timestamp>/run/data_app_<i>.csv`. Until now the docs said to read those
files by hand, and an earlier note said CRAB benchmarks ship no `parser.py` or `plots.json`.

`sbatchman visualize` (SbatchMan 2.0.4) is a plot builder that loads results only through a `parse(job)` function in a
`parser.py`. Facts checked against the installed 2.0.4 source and a run on a laptop copy of a fetched project:

- It serves on `127.0.0.1:8765`; the port is hard-coded. `crab web` can be moved with `--port` or `CRAB_WEB_PORT`.
- `--parser` defaults to `./parser.py`, resolved against the current directory.
- `--presets` is accepted and ignored: the plots are always read from `./plots.json` in the current directory.
- `parse(job)` runs once per job, failed ones included. A dict return becomes one SQLite table per key in
  `SbatchMan/data.sqlite` (database name `data`); `None` skips the job.
- Starting it refreshes job status only for jobs that are not finished, through `sacct`. A missing or failing `sacct`
  leaves the status unchanged and is not an error, so a copy on a machine without Slurm works.
- Column names that start with a digit or contain `-` (CRAB's `0_Avg-Duration_s`) must be double-quoted in SQL.

## Decision

- CRAB's SbatchMan examples ship one generic `parser.py` and one starter `plots.json` per benchmark. The parser returns
  `None` unless the job is `COMPLETED`; otherwise it reads every `data_app_*.csv` under the job's `run/` folder and adds
  the app index, tag, config, job id and the job's variables as columns.
- Results are explored on the laptop: fetch the project's `SbatchMan/` folder, then run `sbatchman visualize` in the
  benchmark folder that holds `parser.py` and `plots.json`. Nothing long-running starts on a login node.
- `crab web` stays the tool for writing and watching experiments; the two dashboards are separate and the docs tell the
  laptop to run `crab web` on another port.
- CRAB does not patch SbatchMan: the fixed port and the ignored `--presets` are documented, and are questions for its
  developer.

## Alternatives considered

- Run `visualize` on a login node and tunnel with `ssh -L` - keeps results in one place, but a long-running web server on
  a shared login node; kept as a documented fallback, not tested.
- A plots editor in `crab web` - duplicates SbatchMan's builder and is open until its developer answers.
- A per-benchmark parser - the CRAB CSV layout is the same for every benchmark, so one parser is enough.

## Consequences

A change in the CSV layout or in SbatchMan's `parse` contract touches only `parser.py` and the `plots.json` files. If the
developer prefers another format, those files and one docs section change; CRAB's engine does not.
