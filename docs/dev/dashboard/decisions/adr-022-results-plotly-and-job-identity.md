# ADR-022 · Results redesign: Plotly over Chart.js, absolute paths, job identity off the registry

- **Date:** 2026-07-07
- **Status:** accepted

## Context

The Results dashboard shipped in ADR-021 rendered on Chart.js, defaulted its axes to the first
two numeric columns on a logarithmic scale, and could only show jobs the local `JobsStore`
registry knew about (i.e. submitted through the dashboard itself). Using it surfaced three
separate problems that this ADR addresses together, since all three touch how a job's result
tree is located and drawn: Chart.js needed a third-party plugin for box/violin charts and had no
built-in vector export; a CLI-submitted job (never touching the dashboard's submit path) had no
way into Results at all, contradicting the requirement that dashboard-submitted and
hand-run-on-the-cluster jobs behave identically; and resolving a CLI-only job's directory would
otherwise mean reconstructing a path client-side from `crab info --json`'s `crab_root`, which
silently breaks for any job run with a custom `datapath` global-option override.

## Decision

- **Plotly (`plotly.js-cartesian-dist-min`) replaces Chart.js.** Native violin/box traces (no
  third-party boxplot plugin) and built-in `toImage`/`downloadImage` export at print DPI, at the
  cost of a larger bundle (~600KB min vs. Chart.js + the boxplot plugin combined).
- **`gather_history` reports each row's resolved absolute path (`absolute_path`), additively.**
  The gatherer already scans the real filesystem to build `relative_path`; reporting the
  already-known absolute directory lets the web backend `fetch_tree()` a CLI-only job without
  guessing where it lives. No `CONTRACT_SCHEMA` bump — purely additive.
- **Results' job identity moves off the local registry to `(cluster, system, job_basename)`.**
  Every Results route now resolves registry-first (a `JobsStore` record gives `data_dir`
  directly) and falls back to a live `crab history` match otherwise, exactly mirroring the
  existing registry-optional join `use_case_report` already uses for the same interoperability
  reason.

## Alternatives considered

- Keep Chart.js and add a boxplot plugin plus a canvas-to-image export helper — less bundle
  weight, but keeps two dependencies (chart.js + plugin) for functionality Plotly provides
  natively, and canvas export is raster-only, not print-quality vector output.
- Reconstruct a CLI-only job's absolute path client-side from `crab info --json`'s `crab_root`
  plus the known `<crab_root>/data/<system>/` convention — no contract change needed, but this
  convention is undocumented and silently wrong for any cluster with a custom `datapath`
  override; the dashboard would produce a wrong fetch path with no way to detect it.
  Rejected per ADR-002 (never reimplement engine/CLI logic in the web layer) — the CLI already
  resolves this path correctly, so the contract should report it rather than have the web layer
  guess.
- A parallel "anonymous job" code path for CLI-only jobs, keeping the registry-keyed routes for
  dashboard-submitted jobs unchanged — less disruptive to existing call sites, but forks the
  implementation into two routes that must be kept behaviorally identical forever, directly
  contradicting the owner's explicit interoperability requirement (a CLI-submitted job must
  behave identically to a dashboard-submitted one). Rejected in favor of one code path keyed on
  the triple every job already has, registry membership being optional metadata rather than an
  identity requirement.

## Consequences

Easier: charts get native box/violin/vector-export for free, and any job `crab history --json`
reports is browsable in Results, closing the CLI-only gap without a second implementation to
maintain. Harder: the bundle is heavier (accepted for v1, revisit only if it proves a real
problem); the results cache's on-disk layout changes (system-scoped, ADR-021's 2-segment layout
is orphaned but harmlessly re-fetchable, no migration); and `gather_history`'s scan root is still
hardcoded to `<crab_root>/data`, so a job run with a genuinely custom `datapath` still won't
appear in `crab history --json` at all -- that gap is independent of this ADR's fix and remains
open, flagged in plan 077's Risks rather than silently assumed away.
