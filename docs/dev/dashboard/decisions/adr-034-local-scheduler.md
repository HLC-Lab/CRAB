# ADR-034 · The local scheduler: prefixed job ids, per-user state, a liveness lock and a SIGTERM-first cancel

- **Date:** 2026-10-08
- **Status:** accepted

## Context

ADR-029 gives CRAB a second scheduler, Local, held to the same bar as Slurm. The code it replaces
is a throwaway. A local job is a detached `bash -c` running the worker; its id is the process id,
and its state is a JSON file in `<CRAB_ROOT>/.crab_local_jobs/<pid>.json`. That shape has three
problems, one of them reproduced:

- **Cancel leaves the job running.** `crab cancel` sends SIGTERM to the job's process group and
  answers `cancelled: true`, but each app runs in its own session (`process/manager.py`), and the
  worker has no SIGTERM handler, so its teardown never runs. The apps keep running, and the job
  then reads `UNKNOWN` forever. Teardown itself sends SIGKILL to the app's group, which reaches
  `srun` or `mpirun` but not the ranks, because MPI launchers move their ranks into another
  process group (Open MPI) or session (Hydra).
- **Ids and state are fragile.** A bare pid is reused by the operating system, and it cannot be
  told apart from a Slurm job id. State files sit in one checkout, are never cleaned (eleven
  stale ones were found), and are plain JSON that cancel trusts: a fabricated pid in one made
  CRAB signal every process of the user (`killpg(1, ...)` is `kill(-1, ...)`).
- **Nothing keeps local jobs apart.** Two jobs on one host disturb each other's measurements.

## Decision

**Job ids** are `local-<n>`, where `n` comes from a counter kept under a file lock, so two
concurrent submits never get the same id. `crab status` and `crab cancel` route by prefix:
`local-` goes to the Local scheduler, all digits go to Slurm, and any other shape is an error.

**Job state** lives under `$XDG_STATE_HOME/crab/jobs/` (default `~/.local/state/crab/jobs/`), not
in the checkout. The job record is `{id, host, data_dir, supervisor_pid, created}`. The exit file
and the state file live in the job's own data directory, next to its logs. The record stores the
submitting host: a status or cancel query from another host is a loud error, never a guess.

**Each job runs under a detached supervisor.** Submit starts a supervisor process in a new
session, with stdin on `/dev/null` and stdout and stderr on the job's two log files, so the job
outlives the submitting shell. The supervisor:

- holds the job's liveness lock for as long as it lives;
- writes `PENDING`, takes the host locks when the preset is `exclusive`, then writes `RUNNING`;
- starts the worker in its own process group;
- enforces the walltime, and ends the job as `TIMEOUT` when it runs out;
- writes the exit code and the final state atomically (a temporary file, then a rename), as
  `COMPLETED`, `FAILED`, `CANCELLED` or `TIMEOUT`.

**Liveness is a held lock.** The supervisor takes an exclusive `flock` on `jobs/<id>.lock` and
keeps it until it exits. A query that can take the lock knows the supervisor is gone. Unlike a
pid check, this is immune to pid reuse, and it works on macOS, where a process start time read
from `/proc` does not exist. A job whose lock is free and that has no final state reads `LOST`.

**Exclusive jobs lock their hosts.** With `exclusive` set (the preset field of ADR-033, default
true), a job locks each host it uses, in sorted order so two jobs cannot deadlock, and so two
local jobs never share a host. A job waiting for a lock shows `PENDING`. Jobs on disjoint hosts
run in parallel. The locks are per submitting machine: jobs submitted from different machines
are not coordinated.

**Cancel order.** Cancel sends SIGTERM to the supervisor. The supervisor forwards it to the
worker, whose SIGTERM handler tears the apps down: SIGTERM to each app's launcher and process
group, a wait of up to 10 seconds, then SIGKILL for whatever is left. The job ends `CANCELLED`.
SIGKILL first would orphan the MPI ranks, because the launcher is the process that knows how to
stop them. The same order applies when a run is stopped for any other reason, including the
walltime.

**Signal safety.** CRAB signals only a pid proven to be this job's live supervisor, proven by
its held liveness lock. It never signals a pid read from a file alone, never pid 0 or 1, and
never its own process group. A job that has an exit file, or whose lock is free, is never
signalled, because its pid may now belong to an unrelated process.

**States** are the Slurm state names (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`,
`TIMEOUT`) plus `LOST`, reported through the `--json` contract.

**Contract and ADR-030.** The `--json` contract is a frozen surface under ADR-030. The change
here is limited to local jobs, which were a testing-only path that no partner relies on: the
shipped `local` preset is described as "dev/testing only", and so is the old local scheduler's
own documentation. Slurm job ids and states are unchanged. `CONTRACT_SCHEMA` goes to 2, so the
dashboard's skew check (ADR-030) reports a cluster that still speaks 1. No deprecation release
is given for the old local id shape.

## Alternatives considered

- Keep bare-pid ids: the operating system reuses pids, and a pid cannot be told from a Slurm id.
- Keep state in the checkout (`.crab_local_jobs/`): it ties jobs to one checkout and collects
  stale files.
- Decide liveness from `/proc/<pid>` and its start time: it works only on Linux.
- Let local jobs share a host by default: they disturb each other's measurements. The preset
  can still opt out with `exclusive: false`.
- SIGKILL on cancel: it orphans the MPI ranks, which is the bug being fixed.

## Consequences

Local jobs survive the submitting shell, but not a logout on a machine where systemd has
`KillUserProcesses` enabled: the supervisor is killed with the rest of the session, and the job
then reads `LOST`. `loginctl enable-linger <user>` avoids it. `flock` is unreliable on NFS homes,
so a job's status is only queried on the host that submitted it, which is why the record stores
the host. The old `.crab_local_jobs/` files are not read any more and can be deleted. Running
jobs on several machines from one dashboard needs a different design for the host locks, and is
not provided.
