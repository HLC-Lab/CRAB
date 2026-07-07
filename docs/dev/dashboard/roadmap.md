# Web dashboard roadmap

Where the dashboard is and what remains for v1.0. History of completed phases lives in git;
decisions live in the [decision records](decisions/index.md).

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

## v1.0 gates (remaining)

1. **Results dashboard** — fetch result CSVs to a local cache; a top-level Results section
   (Plotly charts with a print/paper look, a sortable table, and a cross-job/cross-cluster
   Compare workbench) covering every job `crab history` reports, whether submitted through the
   dashboard or run directly on the cluster — **and a standalone self-contained HTML export**
   (data embedded, shareable offline) still outstanding. History-across-clusters and
   per-experiment detail already shipped (see Done).
2. **Hardening before submit ships**: localhost API authentication (session token + origin
   checks) and server-side config shape validation.
3. **Polish & packaging** — first-run onboarding, empty/error states, version-skew warning,
   clean-machine `pip install crab[web]` test, user docs.
4. **Merge to master**; switch the guided bootstrap's clone off the feature branch; set up CI
   to run `make verify` on PRs.

## After v1

- **Wrappers section** — browse/inspect wrapper sources (metadata, path finder, `read_data`),
  author wrappers locally and sync them to clusters. (Nav stub exists.)
- **Genericity audit** — the dashboard and its tests grew against one real cluster
  (Leonardo @ CINECA) as the running example, and it shows in places beyond just docs: test
  fixtures are literally named and shaped after it (e.g. `tests/test_web_remotes.py`'s
  `_leonardo()` profile fixture), rather than a neutral placeholder cluster. Before calling the
  dashboard done, sweep the codebase (tests, fixtures, example data, comments) for this kind of
  cluster-specific naming and replace it with generic placeholders, so nothing reads as built
  for one specific HPC center rather than for HPC clusters in general.
- Deprecations: the Textual TUI once the web UI reaches parity; `crab export` once the
  dashboard's standalone HTML export replaces it.
- See [deferred.md](deferred.md) for the full intentionally-not-now list.
