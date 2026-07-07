# ADR-021 · Results dashboard: recursive SFTP, per-job cache, one chart implementation

- **Date:** 2026-07-07
- **Status:** accepted

## Context

The legacy `crab export` command builds a standalone Chart.js dashboard (`crab_dashboard.html`)
from a CSV tree already sitting on disk. The web dashboard has no equivalent: viewing results
from a job meant SSHing in by hand. Bringing that experience into the dashboard as a live,
on-demand view meant deciding how to move the CSV tree off the cluster, where to scope the
resulting local copy, and whether the live view and the offline export should share one chart
implementation or two.

## Decision

- **Recursive SFTP, not `rsync`.** `Transport.fetch_tree()` uses asyncssh's own recursive
  `get(..., recurse=True)` — the same library `write_file` already depends on. Shelling out to a
  system `rsync` binary would add a dependency not guaranteed to exist on a cluster login node,
  and this codebase has no other pattern of invoking external transfer tools.
- **Per-job cache scope, manual eviction only.** The cache is keyed by `(cluster, data_dir
  basename)` — one submission's result tree, not a whole use case's history across jobs. There
  is no automatic eviction; a "clear cache" action and a visible total size are the only
  controls. This matches the already-accepted unbounded-cache stance for the ADR-018 fallback
  cache — both are laptop-local, both are cheap enough that manual clearing is enough for v1.
- **One Vue chart implementation, two build targets.** The scatter/line/bar/violin/table/compare
  components that render the live "Results" tab also compile, unmodified, into a single
  self-contained HTML file (`vite-plugin-singlefile`) for the standalone export — replacing
  `crab export`'s use of the legacy plain-JS `crab_dashboard.html`. One implementation to
  maintain instead of two chart engines drifting apart.
- **Cross-cluster compare deferred.** Overlaying the same use case run on two different clusters
  is out of scope for v1; compare stays within one job's own CSV tree, matching what
  `crab_dashboard.html` already does today. Tracked in `deferred.md` so it isn't silently
  dropped.

## Alternatives considered

- Reuse `crab_dashboard.html` as-is for both the live view and the export — less initial work,
  but means maintaining a plain-JS chart engine alongside the app's Vue design system
  indefinitely, and the live view would look and behave differently from the export.
- Shell out to `rsync` for the tree transfer — faster for very large trees with many unchanged
  files, but adds an external binary dependency with no fallback if it's missing on a login node.
- Automatic cache eviction (LRU or size cap) — more polished, but this dashboard's other caches
  (ADR-018) already accept manual-only clearing at this scale; adding a second eviction policy
  for a second cache wasn't judged worth the complexity yet.
- Per-use-case (multi-job) fetch/cache scope — would let the compare tab span a use case's whole
  history in one fetch, but conflicts with the plan's per-job cache key and was deferred
  alongside cross-cluster compare.

## Consequences

Easier: one code path renders charts everywhere they appear (in-app and offline), so a chart fix
or new chart type lands in both places at once; fetching stays simple (no rsync install/path
detection needed on the remote). Harder: a very large result tree could make the initial SFTP
fetch slow compared to a real delta transfer — accepted for v1, revisit only if a real cluster
fetch turns out unacceptably slow in practice. The cache has no size limit, so a laptop that
fetches many large jobs and never clears the cache will accumulate disk usage indefinitely,
same accepted trade-off as ADR-018's fallback cache.
