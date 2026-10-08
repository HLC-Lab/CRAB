# ADR-033 · Execution settings live in structured preset fields, with a small set of config overrides

- **Date:** 2026-10-08
- **Status:** accepted

## Context

Today the way a job is launched is set through `CRAB_*` environment variables placed in a
preset's `env` next to the variables the apps need: `CRAB_WL_MANAGER` picks the launch mode,
`CRAB_MPIRUN`, `CRAB_MPIRUN_ADDITIONAL_FLAGS` and `CRAB_MPIRUN_MAP_BY_NODE_FLAG` shape an
`mpirun` command, and `CRAB_PINNING_FLAGS` adds flags to `srun`. Nothing validates them, they
reach the apps' own environment, and the `mpi` mode can be selected (the shipped `slimfly` preset
does) although it cannot run. A local scheduler has no place to say which hosts it may use.
ADR-029 asked for the scheduler and the launcher to be separate choices; this decision gives
them a home in the preset and in the config, and fixes the config format that carries the
overrides.

## Decision

**Machine facts live in the preset.** A preset describes the machine, so it holds the scheduler,
the hosts and the launcher defaults. Fields:

| Field | Meaning | Default |
|---|---|---|
| `scheduler` | `"slurm"` or `"local"`. | required |
| `exclusive` | Local only. When true, a job locks every host it uses, so two local jobs never share a host; a waiting job shows `PENDING`. Jobs on disjoint hosts still run in parallel. | `true` |
| `hosts` | Local only. A list of `"name"` or `"name:cores"`. The core count may be omitted for `localhost`. | `["localhost"]` |
| `hostfile` | Local only. A path to a file instead of `hosts`. Each line is `h`, `h:N` or `h slots=N`; `#` starts a comment. | none |
| `launcher` | The launcher kind used unless a config or a receipt says otherwise: `"srun"`, `"mpirun"` or `"direct"`. | `"srun"` under Slurm, `"direct"` under Local |
| `launchers.srun.flags` | Extra `srun` flags, such as `--cpu-bind=socket`. | `[]` |
| `launchers.mpirun.command` | The `mpirun` executable or its full path. | `"mpirun"` |
| `launchers.mpirun.dialect` | `"auto"` (detected from `mpirun --version`), or `"openmpi4"`, `"openmpi5"`, `"hydra"`. | `"auto"` |
| `launchers.mpirun.flags` | Extra `mpirun` flags (MCA parameters, `--map-by`, and so on). | `[]` |
| `launchers.mpirun.export` | Names of environment variables forwarded to the remote ranks. | `[]` |
| `env` | Environment variables exported to the apps. Only apps see them; they carry no launch settings. | `{}` |
| `sbatch` | Slurm directives for the batch script (Slurm only). | `[]` |
| `header` | Shell lines placed once at the top of the batch script, such as `module load` lines. | `[]` |

An example:

```json
{
  "lab": {
    "description": "Two workstations",
    "scheduler": "local",
    "exclusive": true,
    "hosts": ["ws1:32", "ws2:32"],
    "launcher": "mpirun",
    "launchers": {
      "srun": { "flags": ["--cpu-bind=socket"] },
      "mpirun": { "command": "mpirun", "dialect": "auto", "flags": [], "export": ["UCX_TLS"] }
    },
    "env": {},
    "sbatch": [],
    "header": []
  }
}
```

A local job uses the first `numnodes` entries of `hosts`, in order, and the chosen hosts are
recorded in the run's provenance. A `numnodes` larger than the number of hosts is an error.

**The `CRAB_*` launch keys are dropped.** `CRAB_WL_MANAGER`, `CRAB_SCHEDULER`, `CRAB_MPIRUN`,
`CRAB_MPIRUN_ADDITIONAL_FLAGS`, `CRAB_MPIRUN_MAP_BY_NODE_FLAG`, `CRAB_MPIRUN_HOSTNAMES_FLAG` and
`CRAB_PINNING_FLAGS` no longer mean anything. If one is still present in a preset's `env` or in
the environment a worker starts with, CRAB stops with an error that names the field to use
instead. It is never ignored silently.

Preset fields and these keys are not frozen surfaces under ADR-030, which freezes the worker
command, the SbatchMan environment variables, the result files and the `--json` contract, so
they are dropped without a deprecation release. The worker's fallback to its process
environment (ADR-027, frozen) stays: only a dropped key found in that environment is refused.

**Config format version 2.** Configs are written with `schema_version: 2`, always. The engine
reads versions 1 and 2 the same way; version 2 only adds optional keys, so a version 1 file is
also a valid version 2 file. The additions:

- `global_options` and `local_options` accept `launcher` and `launcher_flags`. `launcher_flags`
  replaces the preset's flags for that launcher kind; an empty list means no pinning at all.
  The two option blocks merge shallowly as before (`{**global, **local}`). The launcher is
  chosen per experiment, never per app.
- An allocation accepts `mode: "shared"`, at the top level when there are no `partitions` or
  inside a partition. Every chain head of that scope then receives all of its nodes, and
  `split`, `stride` and `seed` next to it are errors. `cores: "disjoint" | "shared"` goes with
  it (default `disjoint`): disjoint gives concurrent apps separate cores, shared lets them
  overlap and measures OS scheduling as much as the hardware.
- An app accepts `env` (an object of strings, exported to that app's ranks only) and `cpus` (an
  explicit core list such as `"0-15,32"`, one core per rank). Both are reserved keys: they are
  never read as wrapper attributes.

**Configs stay free of presets** (ADR-010). Machine facts such as hosts, paths and the `mpirun`
command never appear in a config. A config may only pick the launcher kind and its flags, which
are properties of the experiment's intent, and which the preset's defaults cover when absent.

**The receipt's `launcher_override` stays**, as a validated kind (`srun` or `mpirun`). It covers
per-install needs; the executable and the dialect still come from the preset's
`launchers.mpirun`, or from `mpirun` on `PATH` plus detection.

**How the worker finds its settings.** The submit step writes `execution.json` in the job's
working directory, and the worker reads it. Without that file the worker falls back to the
preset named by `CRAB_PRESET`. Under SbatchMan, whose presets are SbatchMan's, neither exists:
the worker uses Slurm with `srun` and the environment of the job script, and pinning there goes
through Slurm's own input variables (`SLURM_CPU_BIND`). The worker logs which source it used.

**Checks before submitting.** Errors:

- managed Slurm directives (`nodes`, `ntasks-per-node`, `ntasks`, `N`, `n`) in `sbatch` or
  `sbatch_directives`, naming the CRAB field to use instead;
- `srun` with a local preset;
- `direct` with more than one host or `ppn` above 1;
- `numnodes` above the number of local hosts;
- `cpus` shorter than `ppn`, or overlapping between concurrent apps on shared nodes in
  `disjoint` mode;
- `ppn` times the number of apps above a host's cores, in `disjoint` mode on a local preset;
- `cpus` under Slurm without `--exclusive` among the directives;
- a dropped `CRAB_*` launch key.

Warnings: `sbatch_directives` under the Local scheduler (they are ignored); an automatic
disjoint split, which ignores NUMA and NIC locality; `cores: "shared"`.

## Alternatives considered

- Keep the `CRAB_*` env keys: launch settings stay mixed with the apps' environment, and there
  is nothing to validate them against.
- Let each app choose its launcher in the config: more freedom, but a launcher path or dialect
  is a fact about one installation, and the receipt override already covers that.
- Write version 1 when a config uses none of the new keys, so an older CRAB can still read it:
  no older CRAB is in use, so always writing version 2 is simpler. The engine still reads both.

## Consequences

Partners' presets must be converted; the shipped ones are converted in the same release. The
typed config schema planned for milestone M5 has to include these fields, and the editor gains
inputs for the new keys. A config written for this release (version 2) is refused by an older
CRAB, which asks for `crab update`. Placement, locking and job identity for the Local scheduler
are a separate decision (ADR-034).
