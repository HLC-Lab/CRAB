# CRAB roadmap

The plan of record for the whole project: what v1.0 means, the work remaining before it,
and what deliberately comes after. It supersedes the earlier
[web dashboard roadmap](dashboard/roadmap.md), which now covers only the dashboard's own
completed history; the dashboard's remaining work is folded in here. Decisions with
alternatives live in the [decision records](dashboard/decisions/index.md); work explicitly
not planned is in [deferred.md](dashboard/deferred.md).

## What v1.0 means

CRAB 1.0 is a mature, installable product, not a feature milestone:

- Installs cleanly on any machine with `pip install` or `pipx install` — no git checkout,
  no `make`, and the same on clusters.
- The measurements it reports can be trusted: known silent-wrong-data paths in the engine,
  config handling, and benchmark wrappers are fixed, and the launch/parse code is tested.
- A small, documented, semver-stable Python API: run an experiment configuration
  programmatically, parse results as a library, and extend CRAB with a wrapper or recipe.
- Complete user documentation, including the web dashboard, an accurate CLI reference, and
  a statistical methodology page; a rewritten README; a LICENSE file.
- CI runs the full verification gate (Python and frontend) on every push and pull request.
- The `feature/web-dashboard` branch is merged to master, the release is tagged, and the
  package is published to PyPI. The name `crab` is taken on PyPI, so the distribution will
  be published under `crab-hpc` (the import name stays `crab`).

Interface direction at v1.0: the CLI and the web dashboard are the two supported
interfaces. The Textual TUI is frozen now, marked deprecated at v1.0 with a pointer to
`crab web`, and removed in a later release. The legacy `crab export` static viewer remains
but is superseded by the dashboard and will be deprecated once the dashboard gains its own
standalone export (after v1.0).

## Relationship with SbatchMan

CRAB and [SbatchMan](https://github.com/LorenzoPichetti/SbatchMan) stay independent,
fully standalone tools, with an agreed division of labor for teams using both:
downloading and compiling benchmarks is CRAB's recipe system; scheduler configuration and
job submission are SbatchMan's; a SbatchMan job can generate a CRAB experiment JSON and
run the CRAB worker inside the allocation it obtained; parsing stays in CRAB's wrappers;
plotting and inspection happen in CRAB's web dashboard.

Before v1.0, CRAB ships its half of that boundary as ordinary standalone features (they
are useful without SbatchMan): a worker entrypoint that runs a config inside an existing
allocation, a versioned and validated experiment-JSON schema, result parsing callable as a
library, and a dashboard mode that reads a plain local results directory with no SSH or
cluster profile. The actual adapter work (reading SbatchMan's store, launching the
dashboard from SbatchMan) comes after v1.0.

## Work remaining before v1.0

In execution order. Each stream is sized to land independently.

**1. Measurement trustworthiness.** Fix the known paths where a run can record wrong data
without any error: wrapper parsers that fabricate values on parse failure (Quantum
Espresso timing for runs over a minute, NCCL and microbench fallbacks), the duplicated
`benchmark_id` that makes ph.x runs silently execute pw.x, collection-time crashes in the
ember/amg/miniFE family, and the config coercions where a string like `"false"` is
truthy, an unknown `outformat` writes nothing, or a misspelled allocation mode silently
falls back to linear placement. One engine-side shape check (parsed columns vs the
wrapper's declared metrics) guards all 78 wrappers at once; a golden-fixture test suite
for parsers keeps them honest. Cancelling an interrupted run now also cancels the Slurm
job.

**2. Results dashboard backlog.** The three open items from live use: chart visual
polish, renaming a data series' displayed label, and the plot-controls set (axis range,
theme, legend, per-series color) — the last two likely share one design. Plus the three
dashboard correctness fixes found in review: re-fetching results must not nest the cached
tree, one unreachable cluster must not fail the whole jobs list, and system-scoped history
must not poison the cluster-wide cache.

**3. Packaging and relocatability.** Replace the seven checkout-relative `CRAB_ROOT`
computations with one shared path-resolution module (platformdirs for user data, packaged
resources for shipped files, environment overrides kept); ship wrappers, default config,
and examples in the wheel with a seeded user directory for user-authored wrappers; trim
the dependency list to what is imported; single-source the version; add the missing
LICENSE file; verify a clean-machine `pip` and `pipx` install end to end.

**4. Config schema and validation.** One typed, versioned schema for experiment JSON
(the engine's raw-dict reads are the inventory), and one pre-submit validation gate that
every producer passes through — hand-written files, the dashboard, and SbatchMan-generated
configs alike. Cluster-specific settings move out of ambient environment variables into
configuration passed down explicitly; the config format stays JSON.

**5. Public API and integration seams.** Fix the worker entrypoint so it runs correctly
outside the orchestrator (today a placeholder path is never resolved); export the small
public surface from `crab/__init__.py` (programmatic run, result parsing — which already
exists internally — and the wrapper/recipe base classes); add the local-results-directory
dashboard mode; record the integration boundary in a decision record.

**6. Wrapper contract hardening.** Formalize today's folklore contract (a `class app`
found by filename convention, configured by attribute injection) into an explicit,
documented base class that existing wrappers migrate to mechanically; give each app run
an isolated working directory so file-writing benchmarks cannot collide; add the minimal
hook for collecting files a benchmark produces (the general artifact pipeline comes
later); make missing receipts and missing `benchmark_id`s loud errors instead of silent
no-ops. This is what makes wrappers viable for benchmarks from outside computer science —
simulation codes that write files rather than printing results.

**7. Documentation and polish.** The web dashboard user guide (none exists), a rewritten
README and pip/pipx install guide, an accurate CLI reference (it currently documents 4 of
13 subcommands), a statistical methodology page (convergence criterion, run counts,
failure semantics), the public API reference, and a first-run onboarding path plus a
version-skew warning in the dashboard. Cleanups ride along: broken shipped examples fixed
or marked as templates, remaining Italian comments translated, dead files removed, TUI
deprecation notices added. CI is extended to run the full `make verify` gate (about 15-20
minutes including the frontend build-drift check and the browser test).

**8. Release.** Merge to master, switch the guided bootstrap from cloning the feature
branch to installing the published package, tag, and publish `crab-hpc` to PyPI.

Open decisions to settle during stream planning: whether `crab setup` gets a minimal
non-interactive mode before v1.0 (the SbatchMan flow eventually needs one), whether the
promised-but-unimplemented `mpirun` launcher is removed or clearly errors at v1.0, and
whether the unused HDF output option is fixed or dropped (dropping is simpler).

## After v1.0

- **SbatchMan integration, adapter half**: dashboard reads SbatchMan's job store; a
  documented combined workflow; possibly a meta-package.
- **Standalone HTML export** from the dashboard (data embedded, shareable offline);
  `crab export` deprecated once it lands.
- **Wrappers section in the dashboard**: browse wrapper sources, author and sync them;
  install-from-recipes UI with pre-submit receipt validation.
- **Wrapper generality, round two**: general artifact collection, benchmark input-deck
  management, first-class multi-version apps.
- **Engine features**: per-experiment partition override, single-node sequential reuse,
  checkpointing, prolog/epilog hooks, live log streaming, live job detail view.
- **Topology awareness and provenance**: record placement and environment per run;
  placement strategies that use topology.
- **TUI removal**, after one release of deprecation.
- **Genericity audit**: replace cluster-specific fixture naming (grown against Leonardo @
  CINECA) with neutral placeholders across tests and examples.
- The full intentionally-not-now list stays in [deferred.md](dashboard/deferred.md).

## Research track (parallel, does not gate v1.0)

Wrapper coverage for partner benchmarks (OpenCarp next), uniform cross-cluster metrics for
the pilot comparisons, and validation runs on the project's pilot cluster.
