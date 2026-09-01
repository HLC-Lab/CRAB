# ADR-025 · SbatchMan integration: file-based worker seam, generator not driver

- **Date:** 2026-09-01
- **Status:** accepted

## Context

CRAB and SbatchMan stay independent tools (see `docs/dev/roadmap.md`, "Relationship with
SbatchMan"): CRAB owns downloading/compiling benchmarks and co-running semantics; SbatchMan
owns scheduler configuration and job submission. The owner asked for the first cut of that
boundary, constrained to "least changes, correct end to end": a `crab web --sbatchman` mode
that authors a SbatchMan campaign — one or more CRAB experiment templates, each with `{var}`
sweep placeholders — and generates a SbatchMan jobs YAML that runs CRAB inside the allocation
SbatchMan obtains.

SbatchMan's `sbatchman launch -f <jobs.yaml>` expands a `variables:` cartesian product into one
job per combination, substituting `{var}` into `command`/`preprocess`/`tag`/`config` at launch
time (its own tested expander). Its generated sbatch template exports `SBATCHMAN_JOB_DIR` (the
per-job working directory) and fills `{PREPROCESS}`/`{CMD}` slots. Presets (SLURM + ENV) live in
a SbatchMan `configs.yaml`, referenced by the jobs YAML, not authored by CRAB.

On the CRAB side, `crab worker --workdir <dir>` already existed (a hidden subcommand) and runs
one experiment config inside an existing allocation, reading `<dir>/config.json` +
`<dir>/environment.json` from disk. The one real gap: `execute_worker` never resolved the
`__CWD__` placeholder `config/presets.json` uses for `CRAB_ROOT`, so a worker run under
SbatchMan would corrupt `CRAB_ROOT` to the literal string `"__CWD__"`.

## Decision

- **File-based `crab worker --workdir` is the seam, not a new `crab run --worker` CLI surface
  reading a config from stdin.** Each generated SbatchMan job's `preprocess` step writes
  `config.json` + `environment.json` into `$SBATCHMAN_JOB_DIR` via a bash heredoc, and its
  `command` is `crab worker --workdir $SBATCHMAN_JOB_DIR`. SbatchMan's `{var}` substitution
  regex ignores `{` followed by whitespace, so JSON object braces embedded in the heredoc are
  safe — only bare `{token}` gets substituted, and only inside the heredoc text, which is where
  the sweep placeholders live.
- **`environment.json` carries a concrete, already-resolved `CRAB_ROOT`** (the connected
  profile's known remote CRAB checkout), so `__CWD__` never appears in the generated flow. The
  `execute_worker` fix — running the loaded environment through the same
  `prepare_execution_environment` every other entry point uses — is a defensive backstop for a
  hand-written or otherwise stale `environment.json`, not the primary mechanism.
- **The dashboard is a generator for v1, not a driver.** It composes and previews the jobs YAML,
  writes it locally and to a user-chosen remote directory, and can run `sbatchman launch`
  once — it does not poll job status or fetch results. Monitoring and a results view for
  SbatchMan campaigns are an explicit next step, not built here.
- **SbatchMan presets are referenced by name/path, never authored in CRAB.** The campaign editor
  has a plain text field for a preset name/template and the `configs.yaml` path; CRAB does not
  parse or validate SbatchMan's config schema.
- **`{var}` expansion is entirely SbatchMan's job.** The dashboard only *previews* the cartesian
  product (job count, sample tags) by reproducing SbatchMan's own expansion rules client-side
  for display; the emitted YAML keeps every placeholder literal and lets `sbatchman launch` do
  the real substitution.

## Alternatives considered

- Embed the CRAB config via a new `crab run --worker < stdin` flag (the SbatchMan developer's
  own sketch) instead of the file-based `crab worker --workdir` seam — rejected: shell quoting
  of an entire JSON document through a chain of `sbatch`/heredoc/stdin redirects is fragile, and
  it adds new CLI surface where a working hidden subcommand already covers the need.
- Pre-expand the cartesian product in the webui and emit one job per combination — rejected: it
  duplicates SbatchMan's own tested expander for no benefit, and produces a much larger
  generated YAML than the dev's own worked example.
- Let CRAB author SbatchMan presets (`configs.yaml`) directly — rejected: presets are SLURM +
  ENV configuration, which is SbatchMan's schema to own; CRAB only needs to reference one by
  name.
- Have the dashboard drive `sbatchman launch` end to end, including monitoring and pulling
  results back into the existing Results dashboard — rejected for this pass by explicit owner
  instruction ("first we make it just work, then we go further"); the seam above is designed so
  that step can be added later without reworking the generator.

## Consequences

Easier: the integration adds no new CRAB CLI surface beyond the `__CWD__` fix, reuses
SbatchMan's own cartesian expander instead of re-implementing it, and keeps both tools' release
cycles decoupled — CRAB never parses SbatchMan's schema, SbatchMan never gains a co-running or
dashboard mode. Harder: a generated campaign cannot be monitored or have its results plotted
from the dashboard yet, so a user still needs the SbatchMan CLI (or its own TUI) to check job
status after launch, and CRAB's `crab history`/registry-based views don't recognize a
SbatchMan-run job's directory layout — both are the deliberately deferred "full drive" step.
