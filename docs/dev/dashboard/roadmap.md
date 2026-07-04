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

## v1.0 gates (remaining)

1. **Submit & monitor** — push a config to a cluster, `crab run --json`, job registry, status
   polling (refresh + 10 s auto-poll of active jobs), cancel, open logs on demand, reconcile
   on reconnect.
2. **Results** — fetch result CSVs to a local cache, history across clusters, a Chart.js
   results dashboard (port of the `crab export` charts), per-experiment detail, **and a
   standalone self-contained HTML export** (data embedded, shareable offline).
3. **Hardening before submit ships**: localhost API authentication (session token + origin
   checks) and server-side config shape validation.
4. **Polish & packaging** — first-run onboarding, empty/error states, version-skew warning,
   clean-machine `pip install crab[web]` test, user docs.
5. **Merge to master**; switch the guided bootstrap's clone off the feature branch; set up CI
   to run `make verify` on PRs.

## After v1

- **Wrappers section** — browse/inspect wrapper sources (metadata, path finder, `read_data`),
  author wrappers locally and sync them to clusters. (Nav stub exists.)
- Deprecations: the Textual TUI once the web UI reaches parity; `crab export` once the
  dashboard's standalone HTML export replaces it.
- See [deferred.md](deferred.md) for the full intentionally-not-now list.
