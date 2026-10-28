# CRAB roadmap

The plan of record for the whole project: what v1.0 means, the milestones that get there in
order, and what comes after. The cut for 1.0 was set on 2026-10-20: the items it moved out of
1.0 are listed under [Moved to 1.x](#moved-to-1x). It supersedes the stream-based
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
  `make`, the same on clusters. A git checkout keeps working as before: there are two layouts
  and one module decides which applies ([ADR-038](dashboard/decisions/adr-038-two-layouts.md)).
- The measurements it reports can be trusted: known silent-wrong-data paths in the engine,
  config handling, and benchmark wrappers are fixed, and the launch and parse code is tested.
- It runs on Slurm and without Slurm: a local backend, on one machine or several, is documented
  and supported like the Slurm one, with `srun` or `mpirun` as the launcher
  ([ADR-029](dashboard/decisions/adr-029-scheduler-and-launcher.md)).
- A documented public surface: the CLI and its `--json` outputs, the `crab worker` seam, the
  config and presets formats, the result files, and the wrapper base class
  ([ADR-039](dashboard/decisions/adr-039-public-surface-1-0.md)). The promise that it stays
  stable starts at 2.0, not at 1.0
  ([ADR-042](dashboard/decisions/adr-042-stable-from-2-0.md)). There is no Python API in
  1.0: `crab.run()` moved to 1.x.
- A `crab validate` command that runs the pre-submit checks of `crab run` without submitting.
- The wrapper contract is finished: the old `read_data()` return shape is an error and the stock
  wrappers are migrated to rows.
- Complete user documentation, including the web dashboard, an accurate CLI reference, and a
  statistical methodology page; a rewritten README; a LICENSE file.
- CI runs the verification gate (Python and frontend), real MPI launches, a strict docs build,
  a package check and a wheel smoke test on every push and pull request
  ([Testing & verification](dashboard/testing.md)).
- `develop`, the product line, is merged to `master`, the release is tagged, and the package is
  published to PyPI as `crab-hpc` (the name `crab` is taken; the import name stays `crab`).

Interface direction at v1.0: the CLI and the web dashboard are the two supported interfaces.
The Textual TUI is removed in 1.0 (done: `crab tui` and the `tui` extra are gone). The legacy
`crab export` static viewer remains until the dashboard gains its own standalone export (1.2).

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
minute; NCCL, amg and miniFE fallbacks); ph.x no longer runs pw.x through a shared
`benchmark_id`; the ember and amg wrappers load and collect; config coercions become strict (the
string `"false"` is not true, an unknown `outformat` or allocation mode is an error); a missing
benchmark binary is an error, not an empty launch; interrupting during `sbatch` cancels the job;
the convergence check is tested to add runs when needed. One engine-side shape check compares
parsed columns with each wrapper's declared metrics, and a golden-fixture parser suite covers
every wrapper family. The unused HDF output is fixed or dropped. Two items are left for 1.x: the
g500 real sample, and the `msgsize` and result-params columns (see
[Moved to 1.x](#moved-to-1x)).

**M2 · Execution backends.** Slurm and Local are both real, tested backends behind one scheduler
interface, with the launcher chosen separately (`srun` or `mpirun`, OpenMPI and MPICH), a host
list or hostfile in the preset, co-runs of several apps on local machines, and a dashboard
profile that drives a machine without Slurm like a cluster. The worker pool, the leftover
worker node-list file, the allocation logic and `module purge` behavior on execution nodes are
reviewed in the same pass ([ADR-029](dashboard/decisions/adr-029-scheduler-and-launcher.md)).
Exit includes one manual multi-node run on machines without Slurm, run by the owner on real
hardware.

**M3 · Dashboard correctness and input hygiene.** Re-fetching results does not nest the cached
tree; one unreachable cluster does not fail the whole jobs list; system-scoped history no longer
shares a cache key across clusters; the `~/` rewrite applies only to path arguments; profile
names and results paths are validated; the session token is not logged.

**M4 · Packaging and relocatability.** One path-resolution module replaces the seven
checkout-relative `CRAB_ROOT` computations; wrappers, default config and examples ship in the
wheel, with a user directory for user-authored wrappers and a user presets file that overrides
the shipped one; shipped presets carry placeholders instead of real project accounts; the
dependency list matches what is imported; the version is single-sourced; a LICENSE file exists;
a clean-machine `pip` install of the built wheel is tested in CI. The paths follow two layouts,
a checkout and an install ([ADR-038](dashboard/decisions/adr-038-two-layouts.md)). The
non-interactive `crab setup` is not part of 1.0.

**M5 · Validation.** Cut for 1.0 to `crab validate`: one command that runs the pre-submit checks
of `crab run` without submitting, with an optional preset to check the config against what that
cluster can run, plus the same config-only checks as warnings when the dashboard saves an
experiment. The typed schema in core, which would make every producer (hand-written files, the
dashboard, SbatchMan-generated configs) pass one typed gate, moved to 1.x. The config format
stays JSON and carries `schema_version`.

**M6 · Public surface.** Cut for 1.0 to a decision record,
[ADR-039](dashboard/decisions/adr-039-public-surface-1-0.md), that names the surfaces the
public promise covers: the CLI, its `--json` outputs, the `crab worker` seam, the config and
presets formats, the result files, the wrapper base class, receipts and the environment
variables. The promise itself starts at 2.0
([ADR-042](dashboard/decisions/adr-042-stable-from-2-0.md)), and 1.x releases may still change
these surfaces. A Python `crab.run()` API is not in 1.0 and moved to 1.x.

**M7 · Wrapper contract.** An explicit, documented wrapper base class that all shipped wrappers
migrate to; an isolated working directory per app run; a hook that copies the files a benchmark
produces into its results; one binary-lookup scheme (receipts), with missing receipts and
missing `benchmark_id`s as loud errors. This is what makes wrappers practical for simulation
codes that write files rather than print results. The tail of the milestone is the end of the
legacy `read_data()` shape: it becomes an error, and the stock wrappers in crab-wrappers are
migrated to rows.

**M8 · Documentation and polish.** A `crab web` user guide, a rewritten README and install
guide, a complete CLI reference, a statistical methodology page (convergence criterion, run
counts, failure semantics), backend and launcher documentation, the reference for the public
surface, a first-run onboarding path and a version-skew warning in the dashboard, the TUI
removed, remaining Italian comments translated, and dead code removed.

**M9 · Release 1.0.** Merge `develop`, the product line, to `master` (releases go to `master`),
switch the dashboard's guided install from cloning a branch to installing the published package,
tag `v1.0.0`, publish `crab-hpc`, then merge `master` into `sbatchman` and tag
`v1.0.0+sbatchman`.

Still open, to settle when the matching release is planned: native variable sweeps in
standalone CRAB (today only SbatchMan's `variables:` expansion sweeps values across
experiments) and which release they belong to.

## Moved to 1.x

The 2026-10-20 cut took these out of 1.0. None of them blocks the release, and each is a
feature on top of a 1.0 that already works end to end. They have no release assigned yet.

- **Typed config schema in core.** One typed, versioned schema for experiment JSON that the
  engine reads through, so a hand-written file, the dashboard and SbatchMan-generated configs
  share one gate. 1.0 has `crab validate` and `schema_version` instead.
- **A Python `crab.run()` API.** Programmatic run and result parsing as a library. 1.0 promises
  the CLI and its data contracts ([ADR-039](dashboard/decisions/adr-039-public-surface-1-0.md)),
  not Python imports.
- **The g500 real sample.** The g500 wrapper's golden sample and content parsing need a real
  install on a cluster to capture.
- **`msgsize` and result-params columns.** Recording the message size and the result parameters
  as columns of the data files needs its own design pass.
- **A non-interactive `crab setup`.** Postponed: the recipes may be dropped, and `setup` and
  `receipts` may merge into one command, so the shape is not settled.
- **Shared cluster presets.** Generic presets stay in CRAB, and presets for specific clusters
  live in a shared repository.
- **A terminal UI**, only if users ask for one.

## After v1.0

Ordering after 1.1 is tentative and is revisited at each release.

- **1.1 · Recipes and receipt validation in the dashboard**: install benchmarks from recipes and
  validate receipts before submit. Browsing and syncing wrappers already ships with the
  dashboard's Wrappers section; writing a wrapper in the browser is not planned (it stays in
  the user's editor).
- **1.2 · Results and provenance**: per-run provenance (CRAB commit, wrapper hash, loaded
  modules, node list next to every result file); chart polish, series renaming and plot
  controls; a live view of running jobs; a dashboard mode that reads a plain local results
  directory; a standalone HTML export, after which `crab export` is deprecated.
- **1.3 · SbatchMan adapter**: the dashboard reads SbatchMan's job store; a documented combined
  workflow.
- **2.0**: a genericity audit of cluster-specific names in tests and examples; the
  general artifact pipeline, input-deck management and first-class multi-version apps; further
  schedulers (PBS, Flux) on the M2 interface; topology-aware placement; checkpointing,
  prolog/epilog hooks and live log streaming; per-experiment partition override and
  single-node reuse for sequential apps.
- **2.0 is the first stable release.** The surfaces listed in
  [ADR-039](dashboard/decisions/adr-039-public-surface-1-0.md) are promised from then on: a
  breaking change to them needs a new major version. Until then 1.x releases may change them,
  and the changelog lists each change ([ADR-042](dashboard/decisions/adr-042-stable-from-2-0.md)).
- A terminal UI. The Textual TUI was removed in 1.0 (the web dashboard replaced it); bring one
  back only if users ask for it (see [Moved to 1.x](#moved-to-1x)).
- The full intentionally-not-now list stays in [deferred.md](dashboard/deferred.md).

## Research track (parallel, does not gate v1.0)

Wrapper coverage for partner benchmarks (OpenCarp next), uniform cross-cluster metrics for the
pilot comparisons, validation runs on the project's pilot cluster, and bringing CRAB's copy of
BLINK in line with upstream (including its newer microbenchmarks).
