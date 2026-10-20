# ADR-018 · Local cache as a fallback, never the source of truth

- **Date:** 2026-07-06
- **Status:** accepted

## Context

The dashboard reads job logs, per-app error logs, and `crab history` rows fresh over SSH on
every view, by design (ADR-002: the cluster's `crab … --json` output is authoritative, never
re-derived or assumed). In practice, a login node hiccup or a laptop briefly losing network
mid-session blanks out a view the user had already loaded successfully — logs disappear, a
report goes empty — even though nothing about the underlying job actually changed. There was no
way to tell "the cluster is unreachable right now" apart from "there is genuinely nothing here."

## Decision

Add a small on-disk cache (`store/cache.py`, one JSON file per fetched item under
`Settings.cache_dir`) written on every successful fetch of read-only cluster data: job logs,
per-experiment error logs, and `crab history` rows. It is consulted only when a live fetch
fails (SSH disconnected or the remote command itself failed) — never checked first. A
successful live fetch always wins and overwrites the cached copy. When a fallback is served,
the response carries `stale: true` and the timestamp of the cached fetch, and the UI shows a
banner naming the unreachable cluster and how old the data is, instead of either blanking the
view or silently showing a result the user can't tell is out of date. A cache miss with no live
data behaves exactly as before (an error), since there is nothing to fall back to.

## Alternatives considered

- Cache-first, refresh in the background — faster perceived loads, but reintroduces exactly the
  staleness-without-warning problem this exists to avoid; rejected outright given ADR-002.
- No cache at all, just a clearer "cluster unreachable" error — simpler, but throws away data
  the user already successfully saw once for no reason.
- A shared cache keyed only by cluster name — cheaper to reason about, but a job-scoped log
  fetch and a system-scoped history fetch return different data for the same cluster; a shared
  key would silently serve the wrong shape back. Keys are scoped to exactly what was fetched
  (record id, record+experiment pair, or system name) instead.

## Consequences

Easier: a flaky connection no longer costs the user data they already saw; the fallback is
opt-in per read path, so adding it to a new route is a small, isolated change. Harder: the
cache can grow without bound (no eviction in v1, consistent with the existing job registry and
library) and can go stale for a long time if a cluster stays unreachable — mitigated only by
always showing the fetch timestamp, never a bare "showing cached data" with no way to judge
how old it is.
