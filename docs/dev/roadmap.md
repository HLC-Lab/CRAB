# CRAB roadmap

The plan of record for the whole project: what v1.0 means, the milestones that get there in
order, and what comes after. It supersedes the stream-based
[July 2026 edition](roadmap-2026-07-streams.md) and the earlier
[web dashboard roadmap](dashboard/roadmap.md), which remains the record of what the dashboard
shipped. Decisions with alternatives live in the
[decision records](dashboard/decisions/index.md); work explicitly not planned is in
[deferred.md](dashboard/deferred.md).

Milestones have exit criteria, not dates. Each one is planned in detail when it starts, lands
independently, and ends with the verification gate green on both the product line and the
`sbatchman` branch, with the product line merged into `sbatchman` ([ADR-028](dashboard/decisions/adr-028-branch-model.md)).

## What v1.0 means

CRAB 1.0 is a mature, installable product, not a feature milestone:

- Installs cleanly on any machine with `pip install` or `pipx install`: no git checkout, no
  `make`, the same on clusters, and `crab setup` can run non-interactively.
- The measurements it reports can be trusted: known silent-wrong-data paths in the engine,
  config handling, and benchmark wrappers are fixed, and the launch and parse code is tested.
- It runs on Slurm and without Slurm: a local backend, on one machine or several, is documented
  and supported like the Slurm one, with `srun` or `mpirun` as the launcher
  ([ADR-029](dashboard/decisions/adr-029-scheduler-and-launcher.md)).
- A small, documented, semver-stable Python API: run an experiment configuration
  programmatically, parse results as a library, and extend CRAB with a wrapper or recipe.
- Complete user documentation, including the web dashboard, an accurate CLI reference, and a
  statistical methodology page; a rewritten README; a LICENSE file.
- CI runs the full verification gate (Python and frontend) on every push and pull request.
- `feature/web-dashboard` is merged to master, the release is tagged, and the package is
  published to PyPI as `crab-hpc` (the name `crab` is taken; the import name stays `crab`).

Interface direction at v1.0: the CLI and the web dashboard are the two supported interfaces.
The Textual TUI is frozen now, marked deprecated at v1.0 with a pointer to `crab web`, and
removed in a later release. The legacy `crab export` static viewer remains until the dashboard
gains its own standalone export (1.2).

## Relationship with SbatchMan

CRAB and [SbatchMan](https://github.com/LorenzoPichetti/SbatchMan) stay independent, fully
standalone tools, with an agreed division of labor for teams using both: downloading and
compiling benchmarks is CRAB's recipe system; scheduler configuration, job submission,
monitoring, and results visualization are SbatchMan's, end to end, through its own
CLI/TUI/dashboard. A SbatchMan job runs the CRAB worker inside the allocation it obtained
(`crab worker --workdir`), taking its execution environment from what the partner's own
SbatchMan preset exports rather than from a CRAB-written file (ADR-027); parsing stays in CRAB's
wrappers.

The SbatchMan campaign editor lives only on the long-lived `sbatchman` branch, which partners
install with git; the v1.0 product has no SbatchMan authoring
([ADR-026](dashboard/decisions/adr-026-sbatchman-dedicated-branch.md),
[ADR-028](dashboard/decisions/adr-028-branch-model.md)). CRAB's dashboard has no SbatchMan launch,
monitoring, or results view; everything downstream of the generated jobs YAML is SbatchMan's
job. The partner guide, *Using CRAB with SbatchMan*, is part of that branch's documentation.

## Milestones to v1.0

**M0 · Pilot-ready (done).** The `sbatchman` branch is pushed and in sync with the product line;
generic fixes made there were brought back; a missing preset is now an error instead of a silent
fallback to `local`; the dashboard no longer offers to install the unrelated PyPI `crab`
package; CI runs `make verify` on every branch; the campaign editor emits its YAML with a real
serializer, keeps numeric `{var}` placeholders numeric, validates a campaign before writing it,
and has an end-to-end test; the partner guide covers installing both tools side by side and
troubleshooting.

**M1 · Measurement trustworthiness.** No run records fabricated or wrong data silently.
Wrapper parsers stop inventing values on parse failure (Quantum Espresso timing for runs over a
minute; NCCL, amg, miniFE and g500 fallbacks); ph.x no longer runs pw.x through a shared
`benchmark_id`; the ember and amg wrappers load and collect; config coercions become strict (the
string `"false"` is not true, an unknown `outformat` or allocation mode is an error); a missing
benchmark binary is an error, not an empty launch; interrupting during `sbatch` cancels the job;
`msgsize` is recorded correctly; the convergence check is tested to add runs when needed. One
engine-side shape check compares parsed columns with each wrapper's declared metrics, and a
golden-fixture parser suite covers every wrapper family. The unused HDF output is fixed or
dropped.

**M2 · Execution backends.** Slurm and Local are both real, tested backends behind one scheduler
interface, with the launcher chosen separately (`srun` or `mpirun`, OpenMPI and MPICH), a host
list or hostfile in the preset, co-runs of several apps on local machines, and a dashboard
profile that drives a machine without Slurm like a cluster. The worker pool, the leftover
worker node-list file, the allocation logic and `module purge` behavior on execution nodes are
reviewed in the same pass ([ADR-029](dashboard/decisions/adr-029-scheduler-and-launcher.md)).
Exit includes one manual multi-node run on machines without Slurm.

**M3 · Dashboard correctness and input hygiene.** Re-fetching results does not nest the cached
tree; one unreachable cluster does not fail the whole jobs list; system-scoped history no longer
shares a cache key across clusters; the `~/` rewrite applies only to path arguments; profile
names and results paths are validated; the session token is not logged.

**M4 · Packaging and relocatability.** One path-resolution module replaces the seven
checkout-relative `CRAB_ROOT` computations; wrappers, default config and examples ship in the
wheel, with a user directory for user-authored wrappers and a user presets file that overrides
the shipped one; shipped presets carry placeholders instead of real project accounts; the
dependency list matches what is imported; the version is single-sourced; a LICENSE file exists;
`crab setup` gains a non-interactive mode and can install to a chosen path; a clean-machine
`pip`/`pipx` install is tested in CI.

**M5 · Config schema and validation.** One typed, versioned schema for experiment JSON,
including the scheduler, launcher and host settings, and one pre-submit validation gate that
every producer passes through: hand-written files, the dashboard, and SbatchMan-generated
configs. Cluster-specific settings are passed explicitly instead of through ambient environment
variables; the config format stays JSON.

**M6 · Public API.** `crab/__init__.py` exports the small public surface: programmatic run,
result parsing, and the wrapper and recipe base classes, with a decision record for the
integration boundary.

**M7 · Wrapper contract.** An explicit, documented wrapper base class that all shipped wrappers
migrate to; an isolated working directory per app run; a hook that copies the files a benchmark
produces into its results; one binary-lookup scheme (receipts), with missing receipts and
missing `benchmark_id`s as loud errors. This is what makes wrappers practical for simulation
codes that write files rather than print results.

**M8 · Documentation and polish.** A `crab web` user guide, a rewritten README and install
guide, a complete CLI reference, a statistical methodology page (convergence criterion, run
counts, failure semantics), backend and launcher documentation, the public API reference, a
first-run onboarding path and a version-skew warning in the dashboard, TUI deprecation notices,
remaining Italian comments translated, dead code removed, and CI running the full gate
including the build-drift check and the browser test.

**M9 · Release 1.0.** Merge `feature/web-dashboard` to master, switch the dashboard's guided
install from cloning a branch to installing the published package, tag `v1.0.0`, publish
`crab-hpc`, then merge master into `sbatchman` and tag `v1.0.0+sbatchman`.

Still open, to settle when the matching milestone is planned: native variable sweeps in
standalone CRAB (today only SbatchMan's `variables:` expansion sweeps values across
experiments) and which milestone they belong to.

## After v1.0

Ordering after 1.1 is tentative and is revisited at each release.

- **1.1 · Wrappers and recipes in the dashboard**: browse, author and sync wrapper sources;
  install benchmarks from recipes with receipt validation before submit.
- **1.2 · Results and provenance**: per-run provenance (CRAB commit, wrapper hash, loaded
  modules, node list next to every result file); chart polish, series renaming and plot
  controls; a live view of running jobs; a dashboard mode that reads a plain local results
  directory; a standalone HTML export, after which `crab export` is deprecated.
- **1.3 · SbatchMan adapter**: the dashboard reads SbatchMan's job store; a documented combined
  workflow.
- **2.0**: TUI removal; a genericity audit of cluster-specific names in tests and examples; the
  general artifact pipeline, input-deck management and first-class multi-version apps; further
  schedulers (PBS, Flux) on the M2 interface; topology-aware placement; checkpointing,
  prolog/epilog hooks and live log streaming; per-experiment partition override and
  single-node reuse for sequential apps.
- The full intentionally-not-now list stays in [deferred.md](dashboard/deferred.md).

## Research track (parallel, does not gate v1.0)

Wrapper coverage for partner benchmarks (OpenCarp next), uniform cross-cluster metrics for the
pilot comparisons, validation runs on the project's pilot cluster, and bringing CRAB's copy of
BLINK in line with upstream (including its newer microbenchmarks).
