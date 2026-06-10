# Configuring your cluster

A **preset** is the [system-dependent](../concepts/system-dependent-vs-independent.md) half of a
run: it tells CRAB how *this* machine launches work — which workload manager, which launcher and
flags, CPU pinning, modules to load, and the Slurm directives every job needs. CRAB ships with
presets for several systems; this page is about adding one for a cluster it doesn't know yet.

Presets live in `config/presets.json`. For the exhaustive field/merge reference, see
[presets.json format](../reference/presets.md); this page is the practical how-to.

## Start by copying an existing preset

Don't start from `example_preset` — it's an empty schema skeleton (and is deliberately hidden from
`-p` tab-completion), so a run with it would fail. Copy a **complete, real** preset and adapt it:

- **Slurm cluster (launcher `srun`)** → copy **`leonardo`**.
- **Cluster where you launch with `mpirun`** → copy **`local`** or **`cluster_di`**.

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
        "--account=IscrC_FOCAL",
        "--partition=boost_usr_prod",
        "--exclusive",
        "--gres=tmpfs:0"
    ],
    "header": [                              // shell lines run at the top of the job script
        "module purge"
    ]
}
```

To adapt it to your cluster, change the values you already know for your system:

- **`sbatch`** — your Slurm `--account` and `--partition` (and any site requirements like
  `--exclusive`).
- **`CRAB_PINNING_FLAGS`** — your preferred binding (`--cpu-bind=socket`, `--cpu-bind=core`, a
  `map_cpu` list, etc.).
- **`header`** — the `module load` lines your benchmarks need at run time.
- **`env`** — any environment variables your system or libraries require.

!!! note
    This guide assumes you already know your cluster's account, partition, and the right CPU
    pinning for your hardware — those are site-specific values your HPC support or local
    documentation provides.

## Slurm vs MPI launcher: which variables matter

The preset's `CRAB_WL_MANAGER` selects the binding used to launch each application:

=== "`slurm` (uses `srun`)"

    Sensible defaults are applied, so a minimal Slurm preset is short. Relevant variables
    (all optional, with defaults):

    | Variable | Default | Purpose |
    |----------|---------|---------|
    | `CRAB_MPIRUN` | `srun` | Launcher binary |
    | `CRAB_PINNING_FLAGS` | `""` | CPU binding flags |
    | `CRAB_MPIRUN_MAP_BY_NODE_FLAG` | `""` | Extra mapping flag |
    | `CRAB_MPIRUN_ADDITIONAL_FLAGS` | `""` | Any additional launcher flags |

=== "`mpi` (uses `mpirun`)"

    The MPI binding reads these variables **directly** — all of them must be present in the preset
    or the launch fails:

    | Variable | Required | Purpose |
    |----------|----------|---------|
    | `CRAB_MPIRUN` | ✅ | `mpirun` (or full path to it) |
    | `CRAB_MPIRUN_MAP_BY_NODE_FLAG` | ✅ | e.g. `--map-by node` |
    | `CRAB_MPIRUN_ADDITIONAL_FLAGS` | ✅ | extra flags (may be empty string) |
    | `CRAB_PINNING_FLAGS` | ✅ | CPU binding (may be empty string) |
    | `CRAB_MPIRUN_HOSTNAMES_FLAG` | ✅ | host list flag, e.g. `-H` |

    Set them to an empty string if you don't need them, but the keys must exist. The `local`
    preset is a complete minimal example.

## Selecting your preset

At run time CRAB resolves which preset to use in this order:

1. The `-p`/`--preset` flag: `crab run myconfig.json -p mycluster`
2. The `CRAB_PRESET` environment variable
3. A `.env` file in the working directory containing just the preset name
4. Falls back to `local`

The `_common` block in `config/presets.json` is merged underneath every preset — put truly
universal settings there (it already defines `CRAB_ROOT` and `CRAB_PATH_WRAPPERS`). The special
token `__CWD__` is replaced with the repository root. If a preset doesn't set `CRAB_SYSTEM`, it
defaults to the preset's name and is used in the output directory path.

Once your preset exists, [set up your benchmarks](installation.md#set-up-benchmarks-crab-setup) on
the cluster and you're ready to [write an experiment](../reference/configuration.md).
