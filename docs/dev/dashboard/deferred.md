# Deferred — explicitly not now

Recorded so nothing is silently dropped or silently added. To pull an item into scope, move it
into [roadmap.md](roadmap.md) (or a work plan) with a date and reason.

## Deferred features

| Feature | Why deferred | Revisit |
|---|---|---|
| Live log streaming (`tail -f` over WebSocket) | v1 opens/refreshes log files on demand; streaming is plumbing-heavy | First post-v1 follow-up |
| Remote `crab setup` from the UI (build/register benchmarks) | Interactive builds and module systems are risky remotely; v1 uses what is installed | With the Wrappers section |
| Pre-submit validation against installed receipts, plus install-from-UI (recipes) and a manual "point at this binary" fallback for apps with no recipe (mirrors `crab setup`'s manual path entry) | Found via a real submit that silently produced an empty `srun` command for an uninstalled app (base class swallows the missing-binary case instead of erroring clearly) — a real gap, but the UI half needs the Wrappers section to exist first | With the Wrappers section |
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
- Job progress is coarse (state + runs done), not a live convergence curve. Per-experiment and
  per-app log/result files (`error_app_N.log` and similar, one per app per experiment) exist on
  disk but are not individually browsable from the dashboard yet — natural fit for the Results
  per-experiment detail view.
- Guided bootstrap confirms before running; not fully silent. Its install output is captured
  per step rather than streamed live — live progress streaming should land before v1.0 ships
  (tracked in the roadmap's polish gate).

## Known debt (tracked, not forgotten)

- The guided bootstrap currently clones the `feature/web-dashboard` branch (the JSON seam is
  not on master yet); switch to the default branch at the v1 merge — a `TODO(pre-v1)` comment
  in `src/crab/web/remoteops/bootstrap.py` marks the spot.
- `webui/src/api/types.ts` is hand-maintained against the backend models until generated
  types land (see the config-validation work).
- Cache/library growth is unbounded; revisit with the results cache.
