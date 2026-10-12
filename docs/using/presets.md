# Configuring your cluster

A **preset** is the [system-dependent](../concepts/system-dependent-vs-independent.md) half of a
run: it tells CRAB how *this* machine launches work — which workload manager, which launcher and
flags, CPU pinning, modules to load, and the Slurm directives every job needs. CRAB ships with
presets for several systems; this page is about adding one for a cluster it doesn't know yet.

Presets live in `config/presets.json`, and your own additions and changes go in
`local/presets.json` (see [Keep your presets out of git](#keep-your-presets-out-of-git)). For the
exhaustive field/merge reference, see
[presets.json format](../reference/presets.md); this page is the practical how-to.

## Start by copying an existing preset

Don't start from `example_preset` — it's an empty schema skeleton (and is deliberately hidden from
`-p` tab-completion), so a run with it would fail. Copy a **complete, real** preset and adapt it:

- **Slurm cluster (launcher `srun`)** → copy **`leonardo`**.
- **Cluster where you launch with `mpirun`** → copy **`leonardo`** and set `CRAB_MPIRUN` to `mpirun`
  (see [the launcher](#the-launcher-which-variables-matter)).

The `local` preset is different: it runs a single-node job on your own machine without Slurm, for
trying CRAB out. Don't use it as a template for a cluster.

## Anatomy of a working Slurm preset

Here is `leonardo`, annotated — it's the recommended starting point for a Slurm system:

```json
"leonardo": {
    "description": "Leonardo Cluster @ CINECA",
    "env": {
        "CRAB_WL_MANAGER": "slurm",          // use the Slurm workload manager (srun)
        "CRAB_MPIRUN": "srun",               // the launcher binary
        "CRAB_MPIRUN_MAP_BY_NODE_FLAG": "",  // extra mapping flag (empty = none)
        "CRAB_MPIRUN_ADDITIONAL_FLAGS": "",  // any extra launcher flags
        "CRAB_PINNING_FLAGS": "--cpu-bind=socket",  // CPU pinning for srun
        "LD_LIBRARY_PATH": "${LD_LIBRARY_PATH}:/leonardo/.../lib"  // site libraries
    },
    "sbatch": [                              // #SBATCH directives added to every job
        "--account=<your_account>",
        "--partition=boost_usr_prod",
        "--gres=tmpfs:0"
    ],
    "header": [                              // shell lines run at the top of the job script
        "module purge"
    ]
}
```

To adapt it to your cluster, change the values you already know for your system:

- **`sbatch`** — your Slurm `--account` and `--partition` (plus any site requirements; some sites
  also want `--exclusive` to guarantee whole-node allocations for interference measurements).
- **`CRAB_PINNING_FLAGS`** — your preferred binding (`--cpu-bind=socket`, `--cpu-bind=core`, a
  `map_cpu` list, etc.).
- **`header`** — the `module load` lines your benchmarks need at run time.
- **`env`** — any environment variables your system or libraries require.

!!! note
    This guide assumes you already know your cluster's account, partition, and the right CPU
    pinning for your hardware — those are site-specific values your HPC support or local
    documentation provides.

## A minimal preset for a new cluster

For a cluster CRAB does not know, this is enough to start. Put it in `local/presets.json` (see
[Keep your presets out of git](#keep-your-presets-out-of-git)), and replace the account, the
partition and the module lines with your site's:

```json
{
  "mycluster": {
    "description": "My cluster",
    "env": {
      "CRAB_WL_MANAGER": "slurm",
      "CRAB_MPIRUN": "srun",
      "CRAB_PINNING_FLAGS": ""
    },
    "sbatch": [
      "--account=MY_PROJECT",
      "--partition=compute",
      "--exclusive"
    ],
    "header": [
      "module purge",
      "module load gcc openmpi"
    ]
  }
}
```

Run it with `crab run <config.json> -p mycluster`. When you copy `leonardo` instead, remove its
Leonardo-specific settings (the paths under `/leonardo/`, the `UCX_*` and `NCCL_*` variables and
`--gres=tmpfs:0`) unless your site needs them.

## The launcher: which variables matter

Jobs are always submitted with `sbatch`. Inside the job, `CRAB_WL_MANAGER` chooses how each
application is launched. Use `slurm`, which is the default. `CRAB_MPIRUN` then names the launcher:

=== "`srun` (the default)"

    | Variable | Default | Purpose |
    |----------|---------|---------|
    | `CRAB_MPIRUN` | `srun` | Launcher. Extra `srun` options go into this string, for example `"srun --mpi=pmix"`. |
    | `CRAB_PINNING_FLAGS` | `""` | CPU binding flags, added to the `srun` command. |

    `CRAB_MPIRUN_MAP_BY_NODE_FLAG` and `CRAB_MPIRUN_ADDITIONAL_FLAGS` are ignored with `srun`.

=== "`mpirun` (Open MPI or MPICH inside a Slurm job)"

    Set `CRAB_MPIRUN` to `mpirun` (or the full path to it). CRAB adds `-np` with the total number
    of tasks, and reads two optional variables:

    | Variable | Default | Purpose |
    |----------|---------|---------|
    | `CRAB_MPIRUN` | `srun` | Set it to `mpirun`. |
    | `CRAB_MPIRUN_ADDITIONAL_FLAGS` | `""` | Extra `mpirun` flags. |
    | `CRAB_MPIRUN_MAP_BY_NODE_FLAG` | `""` | Mapping flag, for example `--map-by node`. |

    `CRAB_PINNING_FLAGS` is ignored with `mpirun`: put binding options in
    `CRAB_MPIRUN_ADDITIONAL_FLAGS`.

!!! note "The `mpi` workload manager does not work"
    `CRAB_WL_MANAGER=mpi` is accepted by the name check but its code is not ported: a run raises
    `NotImplementedError`. To use `mpirun`, keep `CRAB_WL_MANAGER` at `slurm` and set `CRAB_MPIRUN`.

## Selecting your preset

At run time CRAB resolves which preset to use in this order:

1. The `-p`/`--preset` flag: `crab run myconfig.json -p mycluster`
2. The `CRAB_PRESET` environment variable
3. A `.env` file in the working directory containing just the preset name

If none of these names a preset, `crab run` stops with an error. It never picks one for you;
to run without Slurm, ask for it explicitly with `-p local`.

The `_common` block in `config/presets.json` is merged underneath every preset — put truly
universal settings there (it already defines `CRAB_ROOT` and `CRAB_PATH_WRAPPERS`). The special
token `__CWD__` is replaced with the repository root. If a preset doesn't set `CRAB_SYSTEM`, it
defaults to the preset's name and is used in the output directory path.

## Keep your presets out of git

`config/presets.json` is tracked, so editing it (for example to put in your project account)
turns every `git pull` or `crab update` into a conflict. Put your presets in
`local/presets.json` instead: same format, git-ignored, and merged over the shipped file.

`local/` is a folder at the root of the CRAB checkout, next to `config/`. It may not exist yet.
Create it from the checkout and start the file:

```bash
mkdir -p local
$EDITOR local/presets.json
```

- A preset in `local/presets.json` **replaces** the shipped preset of the same name as a whole,
  so copy the entire block before changing it.
- A preset that exists only in `local/presets.json` is simply added.
- For `_common`, the `env` entries are merged key by key (yours win); any other `_common` key
  you set replaces the shipped one.

For example, to run on `leonardo` with your project account, copy the whole `leonardo` block from
`config/presets.json` into `local/presets.json` and change only the `--account` line:

```json
{
  "leonardo": {
    "description": "Leonardo Cluster @ CINECA",
    "env": {
      "UCX_IB_SL": "1",
      "LD_LIBRARY_PATH": "${LD_LIBRARY_PATH}:/leonardo/smcx_home/bench/software/lib/ucx/nvhpc-22.2_pgi_cuda/lib/",
      "CRAB_MPIRUN": "srun",
      "CRAB_MPIRUN_MAP_BY_NODE_FLAG": "",
      "CRAB_PINNING_FLAGS": "--cpu-bind=socket",
      "CRAB_MPIRUN_ADDITIONAL_FLAGS": "",
      "CRAB_WL_MANAGER": "slurm",
      "CRAB_IB_DEVICES": "mlx5_0#mlx5_1#mlx5_2#mlx5_3",
      "NCCL_IB_SL": "${UCX_IB_SL}"
    },
    "sbatch": [
      "--account=MY_PROJECT",
      "--partition=boost_usr_prod",
      "--gres=tmpfs:0"
    ],
    "header": [
      "module purge"
    ]
  }
}
```

The shipped presets carry `--account=YOUR_PROJECT_ACCOUNT` as a placeholder where a cluster
needs an account.

Once your preset exists, [set up your benchmarks](installation.md#set-up-benchmarks-crab-setup) on
the cluster and you're ready to [write an experiment](../reference/configuration.md).
