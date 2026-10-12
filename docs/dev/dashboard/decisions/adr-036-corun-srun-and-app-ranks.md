# ADR-036 · Co-runs under srun, and ranks per node per app

- **Date:** 2026-10-12
- **Status:** accepted

## Context

Apps placed on the same nodes (`allocation.mode: "shared"`, [ADR-033](adr-033-execution-settings.md))
ran concurrently only with `mpirun`. Under `srun`, each step asked for the job's whole task
budget, so Slurm ran the steps one after the other, with no error and no warning. Separately,
`ppn` was one job-wide value, so a victim with one rank per node and an aggressor with several
could not share a job.

Measured on a production Slurm 25.05 system: `--exact --mem=0` gives concurrent steps disjoint
cores, `--overlap` lets steps share cores, and `--overlap --cpu-bind=map_cpu:<list>` binds
each rank to exactly the listed cores.

## Decision

1. **Co-located apps under `srun` get explicit step flags.** An app that shares nodes with
   another app, and runs at the same time, is launched with:

   | Core setting | Flags |
   |---|---|
   | disjoint cores, no `cpus` | `--exact --mem=0 --cpu-bind=verbose,cores` |
   | `cpus` given | `--overlap --cpu-bind=verbose,map_cpu:<cpus>` |
   | `cores: "shared"` | `--overlap --cpu-bind=verbose,none` |

   All three also get `--ntasks-per-node=<the app's ppn>`. Apps that do not share nodes keep
   the plain `srun` line. `verbose` makes `srun` print each rank's CPU mask into the app's log.
2. **CRAB's binding replaces the user's.** When CRAB binds an app, any `--cpu-bind` in the
   preset's or the config's `srun` flags is dropped for that app. The pre-submit check warns
   about it, so the user sees it when submitting, not only in the Slurm logs.
3. **`ppn` is set per app, per experiment (`local_options`) and globally; the most specific
   wins.** The batch script's `--ntasks-per-node` is the largest per-node load over the job. On
   each node, apps that share it add up; apps that start after another one on its nodes
   ([ADR-032](adr-032-chained-apps-reuse-nodes.md)) count once per chain, at the chain's largest
   `ppn`.
4. **Records.** `metadata.csv` keeps its single `ppn` column, holding the experiment's value.
   Each app's `ppn` and `env` are recorded in `crab_run.json`, so the results show what each
   app ran with.
5. **The network interface is chosen per app, not per rank,** through the app's `env` (for
   example `UCX_NET_DEVICES`).

## Alternatives considered

- `--overlap` for every co-run: cores are shared silently, and the experiment then measures the
  operating system's scheduling instead of the interference between apps.
- Refuse shared mode under `srun`: it forces `mpirun` on clusters where `srun` is the supported
  launcher.
- Keep the user's `--cpu-bind`: two apps can end up pinned to the same cores.
- Size the header to the global `ppn` and add `--overcommit`: it hides oversubscription instead
  of reporting it.
- A per-rank wrapper that picks a network interface by local rank: more moving parts, and one
  interface per app covers the experiments in view. It can be added later without changing
  existing configs.

## Consequences

SbatchMan writes its own batch script, so there `--ntasks-per-node` has to cover the largest
per-node load by hand.

`cpus` under Slurm still needs `--exclusive` among the directives (ADR-033).

This amends ADR-033, which treats `ppn` as one value for the whole job: `ppn` can now differ per
app and per experiment, and the checks that compare `ppn` with `cpus` or with a host's cores use
each app's own value.
