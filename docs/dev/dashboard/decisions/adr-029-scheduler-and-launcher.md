# ADR-029 · Support Slurm and a local backend before v1.0, with the launcher separate from the scheduler

- **Date:** 2026-09-24
- **Status:** accepted

## Context

CRAB's engine calls `sbatch`, `squeue`, `sacct`, `scancel` and `scontrol` directly from
`core/engine.py` and `cli/contract.py`. A `CRAB_SCHEDULER=local` path now exists for testing
without Slurm, but it is a set of special cases next to the Slurm code: single process, no MPI,
and co-runs of two or more apps cannot work because the worker assumes the node list
`["localhost"]`. The `mpi` workload manager raises `NotImplementedError`, yet presets can still
select it. The roadmap had placed a real local backend after v1.0 and left the `mpirun` question
open.

## Decision

Before v1.0 CRAB gets a real scheduler interface with two implementations, Slurm and Local, held
to the same bar (documented, tested, supported):

- The scheduler (Slurm or Local) owns submit, status, cancel, logs and the node list of an
  allocation. The hardcoded Slurm commands move behind it, and the `CRAB_SCHEDULER=local` special
  cases are folded into the Local implementation.
- The launcher (`srun` or `mpirun`) is a separate choice. `srun` is valid only under Slurm;
  `mpirun` works under both. OpenMPI and MPICH (Hydra) are supported, and the flag dialect is
  detected from `mpirun --version`.
- Local runs can span several machines: the preset gives a host list or a hostfile (default:
  `localhost`), and CRAB's existing allocation modes split it between co-running apps exactly as
  they split a Slurm node list. CRAB does not discover or reserve machines; the user owns them.
- A cluster profile in the dashboard can target a machine without Slurm (over SSH, or this
  laptop) and is driven through the same `crab --json` commands.
- The unported `mpi` workload manager is either ported to this interface or removed; no preset
  may select a launcher that raises at run time.

Automated tests cover a single host. Multi-node local runs are verified by a manual run on real
machines before release.

## Alternatives considered

- Keep the current local special cases and add MPI to them: faster, but it keeps the ad hoc
  shape and every new command has to be special-cased twice.
- Tie the launcher to the scheduler (Slurm always `srun`, Local always `mpirun`): simpler, but
  sites that run `mpirun` inside Slurm allocations lose that option.
- Single-host local only: much smaller, but it does not serve the non-Slurm clusters and
  workstations the local backend is meant for.

## Consequences

The experiment schema (roadmap milestone M5) must cover the scheduler, launcher and host fields.
Local submit, status and results become testable end to end without a cluster, which later
milestones can use for their own tests. Other schedulers (PBS, Flux) can be added on the same
interface later. Multi-node local behavior is only as well tested as the manual release check.
This supersedes the roadmap's earlier "real local execution backend after v1.0" item and settles
the open `mpirun` decision.
