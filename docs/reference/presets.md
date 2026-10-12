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
    "local":    { "description": "...", "scheduler": "local", "launcher": "mpirun", ... }
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
| `header` | array of strings | Shell lines emitted at the top of the job script (e.g. `module load …`). Slurm only; see [Module flow](../using/presets.md#module-flow). |

The execution fields, which say how this machine schedules and launches work, are listed next. A
key that appears in neither table stops CRAB with an error that lists the allowed keys.

## Execution fields

| Field | Meaning | Default | Applies to |
|-------|---------|---------|------------|
| `scheduler` | `"slurm"` (submit with `sbatch`) or `"local"` (run a detached process on this machine). | required | both |
| `launcher` | How each application starts: `"srun"`, `"mpirun"` or `"direct"` (the command as is, one process). | `"srun"` under Slurm, `"direct"` under Local | both; `"srun"` needs `slurm` |
| `launchers.srun.flags` | Extra `srun` flags, e.g. `["--cpu-bind=socket"]`. CRAB's own binding replaces a `--cpu-bind` here for the apps it binds; see [The srun launcher](#the-srun-launcher). | `[]` | `srun` |
| `launchers.mpirun.command` | The `mpirun` executable, or its full path. | `"mpirun"` | `mpirun` |
| `launchers.mpirun.flags` | Extra `mpirun` flags (`--map-by`, MCA parameters, …). | `[]` | `mpirun` |
| `launchers.mpirun.dialect` | `"auto"`, `"openmpi4"`, `"openmpi5"` or `"hydra"`. Intel MPI is not supported. | `"auto"` | `mpirun` |
| `launchers.mpirun.export` | Names of environment variables to forward to the remote ranks, in addition to the ones CRAB forwards itself; see [The mpirun launcher](#the-mpirun-launcher). | `[]` | `mpirun` |
| `hosts` | List of `"name"` or `"name:cores"`. Only `localhost` may omit the core count. | `["localhost"]` | `local` only |
| `hostfile` | Path to a file instead of `hosts`, absolute or starting with `~`. A line is `h`, `h:N` or `h slots=N`; `#` starts a comment. | none | `local` only |
| `exclusive` | `true` or `false`. | `true` | `local` only |

A few details:

- Under `slurm`, `hosts`, `hostfile` and `exclusive` are errors: Slurm picks the nodes.
- Give `hosts` or `hostfile`, not both. Host names must be distinct.
- Under `local`, a job runs on the first `numnodes` hosts of `hosts` (or of `hostfile`). A config
  that asks for more nodes than the preset has hosts is refused. Without either field the only
  host is `localhost`.
- The launch lines are:
    - `srun`: `srun --export=ALL --nodelist <hosts> <flags> [<binding>] [--ntasks-per-node=<ppn>] -n <ranks> -N <hosts>`;
      see [The srun launcher](#the-srun-launcher) for `<binding>`
    - `mpirun`: `<command> -n <ranks> <hosts> [<binding>] [-x NAME ...] <report> <flags>`; see
      [The mpirun launcher](#the-mpirun-launcher) for each part
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
    "launcher": "mpirun"
}
```

## The srun launcher

`<ranks>` is the app's `ppn` times the number of hosts. `<flags>` are the preset's
`launchers.srun.flags`, or the config's `launcher_flags` when it sets them. CRAB adds a
`<binding>` and `--ntasks-per-node=<ppn>` for an app that shares its nodes with another app
running at the same time (see [Shared nodes](configuration.md#shared-nodes)), and also for an app
that sets `cpus`:

| App | `<binding>` |
|---|---|
| shares nodes, `cores: "disjoint"`, no `cpus` | `--exact --mem=0 --cpu-bind=verbose,cores` |
| shares nodes, `cpus` given | `--overlap --cpu-bind=verbose,map_cpu:<cpus>` |
| shares nodes, `cores: "shared"`, no `cpus` | `--overlap --cpu-bind=verbose,none` |
| alone on its nodes, `cpus` given | `--cpu-bind=verbose,map_cpu:<cpus>` |

An app that does not share its nodes and has no `cpus` keeps the plain line. The exception is an
app whose `ppn` differs from the job's `--ntasks-per-node`: it gets `--ntasks-per-node=<ppn>`, so
its steps are not sized to the job.

**CRAB's binding replaces yours.** When CRAB adds a `<binding>`, any `--cpu-bind` in `<flags>`
(`--cpu-bind=socket`, or `--cpu-bind map_cpu:...`) is dropped for that app, because two apps
pinned by their own flags can end up on the same cores. The pre-submit check warns about it. The
other flags stay.

`verbose` makes `srun` print each rank's CPU mask into the app's log.

## The mpirun launcher

`launchers.mpirun.dialect` says which MPI the `mpirun` command belongs to, because the three
supported ones spell their options differently. `"auto"` (the default) runs
`<command> --version` once and reads the answer: Open MPI 4 and 5 and MPICH's Hydra are
supported. Intel MPI, an Open MPI older than 4, and an `mpirun` that is only a stub asking for
`srun` are refused with a message that says what to set instead. A probe that fails or does not
answer within 10 seconds is an error too. The other values (`"openmpi4"`, `"openmpi5"`,
`"hydra"`) skip the probe.

`<ranks>` is the app's `ppn` times the number of hosts, and `N` below is that `ppn`, the ranks per
host:

| | `openmpi4` | `openmpi5` | `hydra` |
|---|---|---|---|
| `<hosts>` | `-H h,h,...` (a host repeated once per rank slot) | `--host h:N,...` | `-hosts h:N,...` |
| `<binding>` for an application with `cpus` | `--cpu-set <cores> --bind-to core` | `--map-by pe-list=<cores>:ordered` | `-bind-to user:<cores>` |
| `<binding>` for an application on [shared cores](configuration.md#shared-nodes) without `cpus` | `--bind-to none` | `--bind-to none` | `-bind-to none` |
| `-x NAME` for each forwarded variable | yes | yes | no: Hydra passes the whole environment |
| `<report>` | `--report-bindings` | `--display bindings` | `-report-bindings` |

An application with neither `cpus` nor shared cores gets no binding flag, and `mpirun` uses its
own default. `<flags>` are the preset's `launchers.mpirun.flags`, or the config's
`launcher_flags` when it sets them, and come last. Inside a Slurm job the host list stays within
the allocation.

**Forwarded variables.** Open MPI only sends the variables it is told to. For each launch CRAB
names, as far as they are set in the environment the application starts in:

- the names in `launchers.mpirun.export`, then `PATH` and `LD_LIBRARY_PATH`, then the keys of the
  preset's `env`;
- every `CRAB_*` variable (not the internal `CRAB_SUPERVISOR_FD`);
- the names in the application's own [`env`](configuration.md#the-apps-block), which its launch
  script exports just before `mpirun` runs.

A multi-host local run starts the remote ranks over `ssh`, so the hosts need passwordless `ssh`
between them and the same software at the same paths.

**Binding report.** CRAB always asks `mpirun` for the `<report>` option above. The report goes to
`mpirun`'s standard error, which is also copied to `<experiment dir>/bindings_app_<id>.log` (with
whatever the ranks write to their own standard error); see
[What a run records about its launches](../using/running-without-slurm.md#what-a-run-records-about-its-launches).

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
