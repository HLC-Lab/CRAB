# `presets.json` format

`config/presets.json` defines the [system-dependent](../concepts/system-dependent-vs-independent.md)
environments CRAB can run in. This is the field-level reference; for the practical walkthrough of
adding a cluster, see [Configuring your cluster](../using/presets.md).

## Structure

A single JSON object. Each top-level key is a preset name, plus the special `_common` block:

```json
{
    "_common":  { "description": "...", "env": { ... }, "sbatch": [ ... ], "header": [ ... ] },
    "leonardo": { "description": "...", "env": { ... }, "sbatch": [ ... ], "header": [ ... ] },
    "local":    { ... }
}
```

Each preset (and `_common`) has the same shape:

| Field | Type | Meaning |
|-------|------|---------|
| `description` | string | Human-readable label. |
| `env` | object | Environment variables exported for the run. |
| `sbatch` | array of strings | `#SBATCH` directives added to the generated job script. |
| `header` | array of strings | Shell lines emitted at the top of the job script (e.g. `module load …`). |

## Merge semantics

When a preset is selected, it is combined with `_common`:

- **`env`** — `_common.env` is the base; the selected preset's `env` is merged on top (preset wins
  on conflicts).
- **`sbatch`** — `_common.sbatch` **+** preset `sbatch` (concatenated). Your experiment config's
  own `sbatch_directives` are applied later with higher priority — see
  [Configuration schema → sbatch directives](configuration.md#sbatch-directives).
- **`header`** — `_common.header` **+** preset `header` (concatenated).

If the selected preset does not define `CRAB_SYSTEM`, it defaults to the preset's name. It is used
in the results path (`data/<CRAB_SYSTEM>/…`).

## Special tokens

- **`__CWD__`** — replaced with the repository root (`CRAB_ROOT`) wherever it appears in an `env`
  value. Used in `_common` for `CRAB_ROOT` and `CRAB_PATH_WRAPPERS`.

## Recognized environment variables

These are the variables CRAB itself reads. You can also define any other variables your benchmarks
or libraries require (e.g. `LD_LIBRARY_PATH`, `UCX_*`, `NCCL_*`).

| Variable | Read by | Notes |
|----------|---------|-------|
| `CRAB_ROOT` | framework | Repository root. Set in `_common` via `__CWD__`. |
| `CRAB_PATH_WRAPPERS` | runner | Base directory for resolving relative wrapper `path`s. |
| `CRAB_WL_MANAGER` | runner | `slurm` (default) or `mpi` — selects the launch binding. |
| `CRAB_MPIRUN` | wl_manager | Launcher binary. `slurm` defaults to `srun`; `mpi` requires it. |
| `CRAB_PINNING_FLAGS` | wl_manager | CPU binding flags. Optional under `slurm`, required (may be empty) under `mpi`. |
| `CRAB_MPIRUN_MAP_BY_NODE_FLAG` | wl_manager | Mapping flag (e.g. `--map-by node`). Required under `mpi`. |
| `CRAB_MPIRUN_ADDITIONAL_FLAGS` | wl_manager | Extra launcher flags. Required (may be empty) under `mpi`. |
| `CRAB_MPIRUN_HOSTNAMES_FLAG` | wl_manager (`mpi`) | Host-list flag, e.g. `-H`. Required under `mpi`. |
| `CRAB_SYSTEM` | engine | System label for the output path. Defaults to the preset name. |
| `CRAB_PRESET` | CLI | If set, selects the preset (overridden by `-p`). |
| `CRAB_PATH_<ID>` | wrappers | Injected automatically from each receipt's `binary_path` at run time — not set by hand. |

## Preset selection precedence

1. `-p` / `--preset` flag
2. `CRAB_PRESET` environment variable
3. A `.env` file in the working directory (its contents = the preset name)
4. Default: `local`

## A note on `example_preset`

`config/presets.json` contains an `example_preset` entry. It is a **structural placeholder only**
— it does not define a workload manager or launcher, so it cannot run, and it is excluded from
`-p` tab-completion. Use it as a shape reference if you like, but build real presets by copying a
working one (see [Configuring your cluster](../using/presets.md)).
