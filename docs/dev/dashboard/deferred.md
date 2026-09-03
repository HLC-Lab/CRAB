# Deferred — explicitly not now

Recorded so nothing is silently dropped or silently added. To pull an item into scope, move it
into the [project roadmap](../roadmap.md) (or a work plan) with a date and reason.

## Closed: cross-cluster compare

ADR-021 deferred overlaying the same use case across clusters and said it would be tracked here
as a deferred feature — it never actually landed as a row below. Noted here only to close that
loop, not because anything is still outstanding: plan 077 built the cross-cluster/cross-job
Compare workbench (ADR-022), so this item is done, not deferred.

## Closed: per-run (not just per-experiment) failure status

Was tracked below as deferred, with a revisit trigger. The trigger fired (2026-07-08, see
git history around that date) and the gap was closed the same day: plan
`081-per-run-failure-visibility.md` shipped a `failed_runs` counter alongside the existing
`runs` counter (engine + CLI contract + web backend + frontend), so a FAILED experiment now
shows e.g. "3/10 runs failed" instead of just "FAILED" with no indication most runs actually
succeeded. See ADR-023. Noted here only to close the loop the entry below originally opened.

## Deferred features

| Feature | Why deferred | Revisit |
|---|---|---|
| Standalone self-contained HTML export (data embedded, shareable offline) | Moved out of the v1.0 scope (2026-07-10): the original spec predates the Plotly/Compare redesign and needs re-scoping; not worth blocking the release | First post-v1 follow-up; `crab export` stays until it lands |
| Live-updating job detail view (auto-refresh, RUNNING chips, growing experiment list) | Captured as an idea during v1.0 work; the main Jobs list already auto-refreshes, the detail view fetches once on mount | Post-v1.0, with the other engine/dashboard live features |
| Live log streaming (`tail -f` over WebSocket) | v1 opens/refreshes log files on demand; streaming is plumbing-heavy | First post-v1 follow-up |
| Remote `crab setup` from the UI (build/register benchmarks) | Interactive builds and module systems are risky remotely; v1 uses what is installed | With the Wrappers section |
| Pre-submit validation against installed receipts, plus install-from-UI (recipes) and a manual "point at this binary" fallback for apps with no recipe (mirrors `crab setup`'s manual path entry) | Found via a real submit that silently produced an empty `srun` command for an uninstalled app (base class swallows the missing-binary case instead of erroring clearly) — a real gap, but the UI half needs the Wrappers section to exist first | With the Wrappers section |
| Engine-side stopgap: `crab.wrappers.base.run_app()` raises a clear "benchmark not installed" error instead of silently returning `""` (which currently surfaces as Slurm's opaque `srun: fatal: No command given to execute.`) | Real fix is the pre-submit validation above (catches it before burning cluster allocation at all); this would only improve the error for whoever hits it before that lands. Also not a drop-in change: the launch call at `runner.py:280` has no local try/except, so raising here would abort the whole experiment immediately instead of the current per-run retry-and-log behavior — needs a deliberate look at that flow, not just a one-line fix | Worth doing, owner flagged it explicitly (2026-07-04) — pick up whenever the Wrappers/receipts work is being scoped, even as a smaller lead-in step |
| Wrapper authoring/editing/upload in the browser | Large and risky; select-existing only in v1 | Wrappers section (post-v1) |
| Background monitoring / notifications while the laptop is closed | Needs a persistent process the HPC will not host; against the no-daemon design | Low priority |
| Editable presets from the UI | v1 reads presets; editing is a nice-to-have | Post-v1 |
| Parameter sweeps / experiment matrices | Arrive with the planned SbatchMan integration | With that merge |
| Multi-user / shared server / central instance | Explicitly a personal tool | Not planned |
| Gantt-style scheduling visualizations, drag-and-drop reordering | Cosmetic | Later |
| Interactive cluster map of real nodes (pick nodes visually) | Overkill for now; the placement strip is the stepping stone | Later |

## v1 simplifications (smaller versions we do ship)

- Env/sbatch/header shown read-only in authoring (full editing later).
- Results fetched on demand; no background sync; no cache eviction policy yet.
- Auto-poll interval is user-selectable (5/10/30/60 s, active jobs only) but still a fixed
  choice — no adaptive backoff.
- Job progress is coarse (state + runs done), not a live convergence curve — per-experiment
  status and per-app error logs are browsable (ADR-017), but not a live per-run metric feed.
- Guided bootstrap confirms before running; not fully silent. Its install output is captured
  per step rather than streamed live — live progress streaming stays out of v1.0 (the
  per-step capture is workable) and is revisited after the bootstrap switches to installing
  the published package.

## Known debt (tracked, not forgotten)

- The guided bootstrap currently clones the `feature/web-dashboard` branch (the JSON seam is
  not on master yet); switch to the default branch at the v1 merge — a `TODO(pre-v1)` comment
  in `src/crab/web/remoteops/bootstrap.py` marks the spot.
- `webui/src/api/types.ts`: generated types (`npm run gen:api`, landed with the
  config-validation work) are now the default for every backend-owned shape — most of the file
  is `type X = components["schemas"]["X"]` re-exports. Only cluster-contract passthrough shapes
  (forwarded verbatim from `crab ... --json` and so never appearing in the OpenAPI schema —
  `CrabInfo`, `BootstrapPlan`/`StepResult`, `Wrapper`/`BenchmarksResult`, `NodesResult`,
  `JobLogs`/`ExperimentLogs`) stay intentionally hand-written; that's a permanent design choice,
  not a pending migration.
- Cache/library growth is unbounded; revisit with the results cache. The local fallback cache
  for job logs/history (ADR-018) has the same unbounded-growth trade-off, for the same reason.
- A real bug in `Transport.fetch_tree()` (asyncssh not creating the local destination's missing
  parent directories) shipped past unit tests because `SSHTransport`'s asyncssh boundary only
  ever had fake-sftp-client coverage, only surfacing on a real cluster fetch (plan 065). Fixed,
  and a reusable real local loopback SSH+SFTP server (`tests/ssh_server.py`) now backs
  `SSHTransport.run()`/`write_file()`/`fetch_tree()`/`connect_ssh()` with genuine-connection
  tests instead of only fakes. Not yet retrofitted onto the higher `remoteops/` layer
  (`stage_config`, `run_crab_json`) — those currently only have fake-transport coverage too, and
  the same class of "the fake never touches a real filesystem/socket" bug could in principle hide
  there. Revisit if another real-cluster-only bug surfaces in that layer.
- **`sbatchman` branch only** (plan 085): this branch was narrowed to SbatchMan campaign
  authoring only, since it is scoped to one project rather than the product direction (see
  ADR-026). Author (classic single-experiment direct-submit), Jobs, and Results were unhooked
  from nav/routing, not deleted — their view/store/component files
  (`views/AuthorView.vue`, `views/JobsView.vue`, `views/ResultsView.vue`,
  `views/ResultsCompareView.vue`, `views/ResultsJobView.vue`, `views/ReportView.vue`, and their
  supporting stores/components) sit unregistered in the tree. Delete them for real if this
  branch is ever picked up for further work. The `--sbatchman` CLI flag, `Settings.sbatchman`,
  and the `CRAB_WEB_SBATCHMAN` env var are similarly dead — `get_settings()` now hardcodes
  `sbatchman=True` — and should go with the same cleanup. `webui/tests/e2e/authoring.spec.ts` is
  `test.skip`ped rather than deleted or rewritten; the real replacement is a campaign-editor e2e
  spec, not written yet.
