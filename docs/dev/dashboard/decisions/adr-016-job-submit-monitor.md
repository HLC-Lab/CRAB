# ADR-016 · Job submit/monitor: staging, polling, and the sacct-purge fallback

- **Date:** 2026-07-04
- **Status:** accepted

## Context

Phase 4 (submit an authored config, watch it run, cancel it, read its logs) needed answers to
four coupled questions with real alternatives: how to get a config file onto the cluster before
`crab run` can see it, who drives the 10-second job-status refresh, whether cancel/logs go
through the CLI contract or raw SSH, and what to do when Slurm's own accounting has forgotten a
job (`squeue`/`sacct` both miss). `Transport` (`connections/transport.py`) exposed only
`run(command)`, no file transfer.

## Decision

**Staging.** `Transport` gained `write_file(path, content)` — `SSHTransport` opens an
asyncssh SFTP client per call; `LocalTransport` writes directly (off the event loop via
`asyncio.to_thread`). `remoteops/transfer.py::stage_config()` resolves the (possibly
`~`-relative) staging directory to an **absolute path in one shell round-trip**
(`mkdir -p <dir> && cd <dir> && pwd`) before writing: SFTP has no shell and does not expand a
literal `~` itself, so mkdir (shell) and the write (SFTP) would otherwise risk targeting two
different directories. SSH profiles stage under `<crab_dir>/.web_staging`; the `local` transport
(no checkout-directory concept) stages under `<settings.data_dir>/web_staging`. The resolved
absolute path is also what `crab run` receives as an argument.

**Polling.** A single frontend-owned timer (`stores/jobs.ts`, 10s, in-flight guard) refreshes
`GET /api/jobs`. The backend stays stateless per request: it groups the registry's non-terminal
jobs by cluster and issues **one `crab status --json <ids...>` call per connected cluster**
(`gather_status` already batches all ids into one `squeue` call), not one call per job. No
backend background task or WebSocket — the codebase has no persistent-process infrastructure
yet, and introducing it for job status alone (Slurm has no push mechanism; "push" would still
mean the backend polls) was judged a disproportionate first step. See `deferred.md` for the
live-log-tail follow-up this rules out for now.

**Cancel and logs stay behind the CLI contract**, not raw SSH: `crab cancel <job_id> --json`
(wraps `scancel`) and `crab logs --data-dir <dir> --json` (reads `slurm_output.log`/
`slurm_error.log`, which the engine already writes into a job's data_dir, size-capped) — same
seam as `status`/`history` (ADR-002), so the backend never encodes Slurm-specific behavior
itself.

**Sacct-purge fallback.** When `crab status` reports `UNKNOWN` for a job (squeue and sacct both
miss it — accounting retention purged), the backend cross-checks `crab history --json -s
<system>`: a history row belongs to the job if its `relative_path` starts with
`./<basename(data_dir)>/`. A `crab run` submission executes every experiment in the config's
`experiments` dict, so a purged job's data_dir can match **several** rows; the job resolves to
the worst status among them (`FAILED` > `TIMEOUT` > `COMPLETED`) rather than the first match, so
one failed experiment among several is never hidden by an optimistic guess. No match at all ⇒
stays `UNKNOWN` ("stale"), never guessed.

**Update (2026-07-04):** the same cross-check also runs the first time a job transitions to a
fresh `COMPLETED` from squeue/sacct, not only on `UNKNOWN`. `engine.py`'s `_run_worker` catches
and logs each experiment's exception rather than aborting the allocation, so the Slurm job can
exit 0 while an experiment inside it genuinely failed — found on the first real-cluster submit.
Since a job only ever appears in the active-polling set while its stored state is non-terminal,
this fires exactly once, at the transition, never on a re-poll. A no-match COMPLETED (e.g.
metadata not written yet) is left as COMPLETED, same never-guess rule as UNKNOWN.

## Alternatives considered

- Base64/heredoc the config through `run()` instead of `write_file()` — avoids extending
  `Transport`, but risks shell-escaping bugs and size limits; SFTP is the standard tool for this.
- Trust `write_file()` with a literal `~` and rely on the remote SFTP server to expand it —
  unverified (asyncssh does no client-side expansion; server-side support isn't guaranteed) and
  the mkdir/write would use two different expansion mechanisms regardless. Resolving to an
  absolute path once removes the assumption entirely.
- Backend-owned polling (background task, cached results) — saves redundant `crab status` calls
  across multiple open tabs, but is the first persistent-process piece in a stateless-per-request
  codebase; deferred until it's needed for more than status refresh (see live-log-tail).
- Raw `scancel`/`cat` over SSH instead of contract commands — fewer moving parts short-term, but
  puts Slurm-specific behavior (state parsing, log file naming) in the backend instead of behind
  the versioned, testable CLI seam.
- Treat a purged job's `UNKNOWN` as terminal-by-default (assume COMPLETED) — simpler, but can
  mislabel a genuinely failed or still-running-but-purged job as successful; rejected outright.

## Consequences

Easier: submit/monitor/cancel/logs all reuse the same fake-transport-testable, contract-based
pattern as the rest of the backend; no new infrastructure class introduced. Harder: the preset
free-text fallback in the submit modal only shows real presets when the current browser session
has called `connect()` itself (a page reload loses that client-side cache even though the
backend connection persists) — a known, minor UX rough edge, not a correctness issue. Live log
tail remains future work once a persistent-process design is worth taking on.

Owner idea from the first real-cluster run (2026-07-04, not scoped here): a way to resubmit only
the failed experiments from a job rather than the whole config again. Would need per-experiment
status exposed somewhere (the history cross-check above only surfaces a worst-case job-level
state), which points at the Phase 5 Results per-experiment detail view as the natural home for
it — noted for that plan's grilling pass, not designed here.
