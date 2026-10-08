# `presets.json` format

`config/presets.json` defines the [system-dependent](../concepts/system-dependent-vs-independent.md)
environments CRAB can run in. This is the field-level reference; for the practical walkthrough of
adding a cluster, see [Configuring your cluster](../using/presets.md).

## Structure

A single JSON object. Each top-level key is a preset name, plus the special `_common` block:

```json
{
    "_common":  { "description": "...", "env": { ... }, "sbatch": [ ... ], "header": [ ... ] },
    "leonardo": { "description": "...", "scheduler": "slurm", "launcher": "srun", ... },
    "local":    { "description": "...", "scheduler": "local", "launcher": "direct", ... }
}
```

A preset has the fields below. `_common` is not a preset: it holds only `description`, `env`,
`sbatch` and `header`, which every preset inherits. An execution field (`scheduler`, `launcher`
and so on) in `_common` stops CRAB with an error, because each machine states its own.

| Field | Type | Meaning |
|-------|------|---------|
| `description` | string | Human-readable label. |
| `env` | object | Environment variables exported to the applications. They carry no launch settings. |
| `sbatch` | array of strings | `#SBATCH` directives added to the generated job script. Slurm only. |
| `header` | array of strings | Shell lines emitted at the top of the job script (e.g. `module load …`). Slurm only. |

The execution fields, which say how this machine schedules and launches work, are listed next. A
key that appears in neither table stops CRAB with an error that lists the allowed keys.

## Execution fields

| Field | Meaning | Default | Applies to |
|-------|---------|---------|------------|
| `scheduler` | `"slurm"` (submit with `sbatch`) or `"local"` (run a detached process on this machine). | required | both |
| `launcher` | How each application starts: `"srun"`, `"mpirun"` or `"direct"` (the command as is, one process). | `"srun"` under Slurm, `"direct"` under Local | both; `"srun"` needs `slurm` |
| `launchers.srun.flags` | Extra `srun` flags, e.g. `["--cpu-bind=socket"]`. | `[]` | `srun` |
| `launchers.mpirun.command` | The `mpirun` executable, or its full path. | `"mpirun"` | `mpirun` |
| `launchers.mpirun.flags` | Extra `mpirun` flags (`--map-by`, MCA parameters, …). | `[]` | `mpirun` |
| `launchers.mpirun.dialect` | `"auto"`, `"openmpi4"`, `"openmpi5"` or `"hydra"`. Intel MPI is not supported. | `"auto"` | `mpirun` |
| `launchers.mpirun.export` | Names of environment variables to forward to the remote ranks. | `[]` | `mpirun` |
| `hosts` | List of `"name"` or `"name:cores"`. Only `localhost` may omit the core count. | `["localhost"]` | `local` only |
| `hostfile` | Path to a file instead of `hosts`, absolute or starting with `~`. A line is `h`, `h:N` or `h slots=N`; `#` starts a comment. | none | `local` only |
| `exclusive` | `true` or `false`. | `true` | `local` only |

A few details:

- Under `slurm`, `hosts`, `hostfile` and `exclusive` are errors: Slurm picks the nodes.
- Give `hosts` or `hostfile`, not both. Host names must be distinct.
- CRAB reads and validates `hosts`, `hostfile` and `exclusive`, but the Local scheduler runs
  every job on this machine (`localhost`) today, whatever hosts are listed.
- `launchers.mpirun.dialect` and `launchers.mpirun.export` are validated and recorded with the
  job, but the `mpirun` line is not built from them yet.
- The launch lines are:
    - `srun`: `srun --export=ALL --nodelist <hosts> <flags> -n <ranks> -N <hosts>`
    - `mpirun`: `<command> <flags> -np <ranks>` (inside a Slurm job `mpirun` finds the nodes itself)
    - `direct`: the application command, unchanged
- A receipt's `launcher_override` can switch one benchmark to `srun` or `mpirun`; see
  [Receipts](../extending/receipts.md).

A Slurm preset and a local one, from the shipped file:

```json
"leonardo": {
    "description": "Leonardo Cluster @ CINECA",
    "scheduler": "slurm",
    "launcher": "srun",
    "launchers": { "srun": { "flags": ["--cpu-bind=socket"] } },
    "env": { "CRAB_IB_DEVICES": "mlx5_0#mlx5_1#mlx5_2#mlx5_3" },
    "sbatch": ["--account=YOUR_PROJECT_ACCOUNT", "--partition=boost_usr_prod"],
    "header": ["module purge"]
},
"local": {
    "description": "Local (no Slurm, dev/testing only)",
    "scheduler": "local",
    "launcher": "direct"
}
```

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
| `CRAB_PATH_WRAPPERS` | runner | Folders searched for relative wrapper `path`s, separated by `:`, after `local/wrappers/`. Default: the checkout's `wrappers/`. |
| `CRAB_SYSTEM` | engine | System label for the output path. Defaults to the preset name. |
| `CRAB_PRESET` | CLI | If set, selects the preset (overridden by `-p`). |
| `CRAB_PATH_<ID>` | wrappers | Injected automatically from each receipt's `binary_path` at run time — not set by hand. |

## Removed keys

Launch settings used to be `CRAB_*` variables in a preset's `env`. They are now preset fields and
the old keys are no longer read. If one is still in a preset's `env`, in `_common`'s `env`, or in
the environment a worker starts with, CRAB stops with an error that names the replacement.

| Old key | Use instead |
|---------|-------------|
| `CRAB_WL_MANAGER` | `launcher` |
| `CRAB_SCHEDULER` | `scheduler` |
| `CRAB_MPIRUN` | `launchers.mpirun.command` |
| `CRAB_MPIRUN_ADDITIONAL_FLAGS` | `launchers.mpirun.flags` |
| `CRAB_MPIRUN_MAP_BY_NODE_FLAG` | `launchers.mpirun.flags` |
| `CRAB_MPIRUN_HOSTNAMES_FLAG` | nothing: delete it |
| `CRAB_PINNING_FLAGS` | `launchers.srun.flags` |

To migrate a preset, add `scheduler` (`"slurm"` for a cluster), set `launcher`, move the flags into
`launchers` and delete the old keys from `env`.

## Preset selection precedence

1. `-p` / `--preset` flag
2. `CRAB_PRESET` environment variable
3. A `.env` file in the working directory (its contents = the preset name)
If none of these names a preset, `crab run` stops with an error; it never picks one for you.

## A note on `example_preset`

`config/presets.json` contains an `example_preset` entry. It is a **structural placeholder only**
— it has no `scheduler`, so CRAB refuses it, and it is excluded from
`-p` tab-completion. Use it as a shape reference if you like, but build real presets by copying a
working one (see [Configuring your cluster](../using/presets.md)).
