# Web dashboard roadmap

**Superseded (2026-07-10):** planning now happens in the [project-wide roadmap](../roadmap.md),
which covers the whole package (engine, wrappers, packaging, docs, dashboard). This page is
kept as the record of what the dashboard shipped; its former "remaining" sections were folded
into the project roadmap.

Decisions live in the [decision records](decisions/index.md).

## Done

- **Foundations** — `crab web` (FastAPI + built Vue SPA shipped in the wheel), settings,
  error taxonomy.
- **CLI JSON seam** — `crab info / list-benchmarks / nodes / run / status / history --json`
  with a schema version (the cluster-side contract the backend drives).
- **Remotes** — profile CRUD, connect/disconnect over asyncssh (agent/key/password),
  guided CRAB bootstrap for clusters without CRAB.
- **Authoring** — full experiment editor: basics, node-allocation editor (division bar,
  linear/interleaved/random placement), run settings, Slurm directives, per-experiment
  overrides, apps with wrapper picker (local + remote catalogs merged), flow diagram,
  local library with import/export, live JSON view, shape validation.
- **Submit & monitor** — push a config to a cluster, `crab run --json`, job registry, status
  polling (refresh + selectable auto-poll interval of active jobs), cancel, open logs on
  demand, reconcile on reconnect.
- **Jobs & experiment reporting** (ADR-017) — filter/search the Jobs view by cluster, use case,
  and status; a per-use-case report sourced from `crab history --json` showing every
  experiment's state across connected clusters; per-app error log browsing
  (`crab logs --experiment`); whole-config and partial (`crab run --only`) rerun.
- **Per-job detail, cache fallback, async submit** (ADR-018, ADR-019) — a per-job detail view
  scoped to one exact submission, with the use-case report now reachable from it as the
  secondary cross-time view; job logs, per-app logs, and history fall back to a local cache
  with a visible staleness banner when a cluster is unreachable; submit and rerun return
  immediately and resolve through a pending card instead of blocking the request; a redesigned
  Jobs card (clickable, toolbar actions, a read-only config-snapshot viewer) and clearer
  refresh/filter controls.
- **Rerun lineage and clearer history UX** (ADR-020) — a rerun (whole-job or a subset) links
  back to its parent job and shows what was included; a job's detail view lists every rerun
  descended from it; the use-case history view groups experiments by submission instead of one
  flat list; a one-click "rerun failed experiments" action, with manual multi-select now an
  explicit opt-in mode instead of always-visible checkboxes.
- **Hardening** (ADR-013, ADR-015) — localhost API authentication (per-session token plus
  origin/host checks) and server-side config shape validation (saves warn, never reject).
- **Results dashboard** (ADR-021, ADR-022, ADR-024) — fetch result CSVs to a local cache; a
  top-level Results section (Plotly charts with a print/paper look, a sortable table, and a
  cross-job/cross-cluster Compare workbench) covering every job `crab history` reports, whether
  submitted through the dashboard or run directly on the cluster. Performance passes (result
  caching, deduped `crab history` round-trips, WebGL-rendered scatter/line charts with an
  automatic SVG fallback where WebGL is unavailable) and UX fixes (picker card redesign,
  small-multiples shared axis range, sidebar text truncation, chart axis ordering) landed across
  several follow-up rounds after the initial ship — see `078-results-perf-and-ux.md` for the
  itemized backlog.
- **Per-run failure visibility** (ADR-023) — an experiment's run-failure count (e.g. "3/10 runs
  failed") is now visible on job cards and in the Results sidebar, closing the gap where a
  FAILED experiment could still show Results data with no explanation.

## What remains

See the [project-wide roadmap](../roadmap.md). The dashboard items formerly listed here
(Results backlog, onboarding, version-skew warning, user docs, CI, the merge itself, the
standalone HTML export, the Wrappers section, the genericity audit, the deprecations) are
all placed there, before or after v1.0. [deferred.md](deferred.md) still holds the full
intentionally-not-now list.
