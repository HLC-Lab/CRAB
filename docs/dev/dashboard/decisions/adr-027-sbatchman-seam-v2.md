# ADR-027 · SbatchMan worker seam v2: environment inheritance, GitHub-main install

- **Date:** 2026-09-22
- **Status:** accepted

## Context

Plan 089 set out to verify ADR-025's worker seam against a real `sbatchman` install and ship a
partner onboarding doc. That verification found ADR-025's central assumption false for the real
package: PyPI's `sbatchman` 1.0.8 (published 2026-05-20) never exports `SBATCHMAN_JOB_DIR` and
never reads the generated jobs YAML's `configs:` key — both grepped and confirmed absent from
the installed package, and confirmed live by launching a real probe job. Running the actual
generator-produced campaign against a real local `sbatchman configure local` preset reproduced
the failure exactly: an empty workdir, a heredoc write to `/` (permission denied), then
`crab worker --workdir ""` raising `FileNotFoundError`.

A follow-up investigation found this is a version gap, not a design flaw: SbatchMan's GitHub
`main` (2026-09-18 at investigation time) already auto-exports `SBATCHMAN_JOB_DIR` and makes the
jobs.yaml `configs:` key real. ADR-025 (written 2026-09-01, reading the repo) described where
SbatchMan was heading, not what `pip install sbatchman` gave a partner on the day this shipped.

Separately, reading `core/engine.py::_run_worker` confirmed the mechanism a SbatchMan-launched
job actually gets: the job's command inherits the entire environment of whoever ran
`sbatchman launch`, plus anything a preset's `env:`/`modules:` bake in at `sbatchman configure`
time — before the job's own command runs. `{EXP_DIR}`/`{CWD}`-style tokens are substituted only
inside `configure`-time fields (`--env`, `--custom-headers`, `modules`), never inside a jobs.yaml
`command:`/`preprocess:` — confirmed live: a literal `{EXP_DIR}` written into a job command
survives unfired. This is why `sbatchman configure --env 'SBATCHMAN_JOB_DIR={EXP_DIR}'` works
and a hypothetical `command: "... --workdir {EXP_DIR}"` would not.

A separate, smaller gap surfaced in the same investigation: the generator's `environment.json`
never included `CRAB_PATH_WRAPPERS`, so a relative wrapper path (exactly what the generator
emits) fails to resolve unless the process happens to run from inside the wrappers directory.

## Decision

- **Install SbatchMan from GitHub `main`**, not PyPI:
  `pip install git+https://github.com/LorenzoPichetti/SbatchMan.git`. This gets the working
  auto-export and the real `configs:` key now, rather than waiting on an unscheduled release.
  The exact commit tested is recorded in the plan 089 worklog and the onboarding doc.
- **`crab worker` sources its execution environment from the inherited process environment when
  no `environment.json` is present, instead of requiring a CRAB-written env file.**
  `execute_worker` (`cli/orchestrator.py`) keeps its existing behavior — load and use
  `environment.json` — when that file exists in the workdir; this is CRAB's own
  orchestrator/sbatch path, which always writes the file itself, and this branch's behavior does
  not change. When the file is absent (the SbatchMan-launched case), `execution_env` is built
  from `dict(os.environ)` instead. Both branches still run through the existing
  `prepare_execution_environment` (`__CWD__` resolution), unchanged.
- **The campaign generator (`webui/src/lib/sbatchman.ts`) stops writing `environment.json`
  entirely.** Only `config.json` is heredoc'd into `$SBATCHMAN_JOB_DIR` — it is per-job sweep
  data (varies per campaign job) and cannot come from a static preset `env:` entry either way.
  `CRAB_ROOT`, `CRAB_SYSTEM`, `CRAB_PATH_WRAPPERS`, and (as a defensive floor, harmless once
  `main`'s auto-export exists) `SBATCHMAN_JOB_DIR={EXP_DIR}` become required
  `sbatchman configure --env` entries, documented once in the partner onboarding doc, instead of
  a generated file duplicating what the partner's own preset already exports into the job's
  shell.
- **Preset setup stays doc-only.** No new generator/UI feature emits a ready-made
  `sbatchman configure` snippet; the onboarding doc gives the partner one copy-paste block to
  run once by hand.
- **Permanent constraint for anyone touching the generator later, independent of which
  SbatchMan version ships:** `{token}` substitution only fires inside `configure`-time fields
  (a preset's `--env`, `--custom-headers`, `modules`). It never fires inside a jobs.yaml
  `command:` or `preprocess:` block. Any future generator change that wants a per-job
  substituted value must route it through a preset field, not embed it in the job template
  text.

## Alternatives considered

- **Stay on PyPI 1.0.8 and document the `--env 'SBATCHMAN_JOB_DIR={EXP_DIR}'` workaround** —
  rejected: still leaves the `configs:` key inert (confusing UI copy describing a mechanism
  that doesn't exist in the released version) and gives a worse day-one experience than
  installing the version that already works correctly.
- **Wait for a PyPI release that includes `main`'s fixes** — rejected: no release was scheduled
  at decision time, and blocking partner onboarding on an upstream release date was worse than
  installing from a Git ref the partner can re-pin later once a release exists.
  Tracked as a future doc update, not blocking this plan (plan 089's Risks section).
- **Smaller fix: add the missing `CRAB_PATH_WRAPPERS` key to the generated `environment.json`
  and keep the file-based seam as-is** — the actual root cause found while chasing the wrapper
  gap (`core/engine.py::_run_worker` does `os.environ.update(expanded)`, so this one-line
  addition would have closed that specific gap without touching anything else). Rejected in
  favor of the larger redesign above, to remove the duplication between "generator writes an
  env file" and "the partner's SbatchMan preset already exports the same variables into the
  job's shell" — recorded here so the smaller-fix option is not lost to history if a future
  session wants to reconsider it.

## Consequences

Easier: no more duplicated environment data between what the generator writes and what the
partner's own SbatchMan preset already provides; a partner installing from GitHub `main` gets
working `configs:` resolution and `SBATCHMAN_JOB_DIR` auto-export without a workaround; the
generator's job template is one heredoc smaller. Harder: CRAB's own orchestrator/sbatch path and
the SbatchMan-launched path now genuinely diverge in how they source `execution_env` (file vs.
process environment), so a future change to `execute_worker` must keep both branches' tests
green independently; GitHub `main` is an unpinned moving target, so the exact tested commit must
be re-verified whenever the onboarding doc's install instructions are revisited, until a PyPI
release covers these features.
