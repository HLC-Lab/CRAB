# ADR-019 · Async submit via an in-memory tracker, not a persisted queue

- **Date:** 2026-07-06
- **Status:** accepted

## Context

Submitting or rerunning a job means staging a config over SFTP and running `crab run` over
SSH, which can take anywhere from under a second to tens of seconds depending on the cluster
and login node load. The submit endpoint used to block for the whole round-trip, so a slow
cluster made the UI look hung with no feedback until it either finished or timed out.

## Decision

`POST /api/jobs/submit` now does only the work that can fail instantly (the profile exists, is
connected, the config resolves, a preset is chosen), then hands the actual staging/run work to
a background `asyncio` task and returns `202` with a `submission_id` immediately. The frontend
polls `GET /api/jobs/submissions/{id}` once a second until it resolves to a real job record or
an error, and shows a pending placeholder card in the meantime.

The tracker is a plain in-memory dictionary on the app process, not a persisted queue. This
follows from how a job is identified today: a `JobRecord`'s id is derived from the cluster's own
job id, which does not exist until `crab run` has already succeeded — so there is nothing to
persist as "pending" without changing that id scheme everywhere it's used, which is a larger,
separate change. The consequence is accepted directly: if the backend process restarts while a
submission is in flight, that submission's outcome is lost — the pending card in the browser
becomes permanently stuck until the page is reloaded, which drops the client-side pending entry
too. For a laptop-local, single-user tool this is judged an acceptable, rare edge case rather
than something worth a persisted-queue redesign.

## Alternatives considered

- Persist a "pending" placeholder record in the job registry before the SSH round-trip starts —
  would survive a backend restart, but needs a new id scheme (today's id needs a real cluster
  job id) and a reconciliation step for orphaned placeholders; deferred as a larger change with
  no immediate need.
- A fixed-duration timer that just waits N seconds before checking status — simpler than
  polling, but either makes fast submissions feel artificially slow or leaves slow ones looking
  hung again; rejected for the same reason the blocking request was.
- WebSocket push instead of polling — avoids the one-second poll latency, but adds a persistent
  connection and its own reconnect/backoff logic for a case where a one-second delay is not
  noticeable.

## Consequences

Easier: submissions and reruns never block the UI regardless of how slow a login node is, and
the same mechanism covers whole-job and per-experiment reruns for free, since both already went
through the same submit endpoint. Harder: a submission's progress exists only in the backend
process's memory — restarting the backend loses any in-flight submission, and there is no way
for the user to recover except retrying. If this tool ever needs to survive backend restarts
mid-submit, the job-id scheme is the thing that has to change first.
