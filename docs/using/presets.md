# Configuring your cluster

A preset is the [system-dependent](../concepts/system-dependent-vs-independent.md) half of a
run: it tells CRAB how *this* machine runs work: which scheduler, which launcher and
flags, CPU pinning, modules to load, and the Slurm directives every job needs. CRAB ships with
presets for several systems; this page is about adding one for a cluster it doesn't know yet.

The shipped presets live inside the package (`src/crab/presets.json` in a checkout), and your own
additions and changes go in a file of yours (see
[Keep your presets out of git](#keep-your-presets-out-of-git)). For the
exhaustive field/merge reference, see
[presets.json format](../reference/presets.md); this page is the practical how-to.

## Start by copying an existing preset

Don't start from `example_preset`: it's an empty schema skeleton (and is deliberately hidden from
`-p` tab-completion), so a run with it would fail. Copy a complete, real preset and adapt it:

- Slurm cluster (launcher `srun`) → copy `leonardo`.
- Slurm cluster where you launch with `mpirun` → copy `slimfly`.

The `local` preset is different: it runs a single-node job on your own machine without Slurm, for
trying CRAB out (see [Running without Slurm](#running-without-slurm)). Don't use it as a template
for a cluster.

## Anatomy of a working Slurm preset

Here is `leonardo`, annotated. It's the recommended starting point for a Slurm system:

```json
"leonardo": {
    "description": "Leonardo Cluster @ CINECA",
    "scheduler": "slurm",                    // submit the job with sbatch
    "launcher": "srun",                      // start each application with srun
    "launchers": {
        "srun": { "flags": ["--cpu-bind=socket"] }  // CPU pinning for srun
    },
    "env": {                                 // exported to the applications only
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

- `sbatch`: your Slurm `--account` and `--partition` (plus any site requirements; some sites
  also want `--exclusive` to guarantee whole-node allocations for interference measurements).
- `launchers.srun.flags`: your preferred binding (`--cpu-bind=socket`, `--cpu-bind=core`, a
  `map_cpu` list, etc.).
  For apps that share nodes or set `cpus`, CRAB binds the ranks itself and drops a `--cpu-bind`
  from these flags; see [The srun launcher](../reference/presets.md#the-srun-launcher).
- `header`: the `module load` lines your benchmarks need at run time.
- `env`: any environment variables your system or libraries require. They reach the
  applications; launch settings do not belong here.

!!! note
    This guide assumes you already know your cluster's account, partition, and the right CPU
    pinning for your hardware. Those are site-specific values your HPC support or local
    documentation provides.

## A minimal preset for a new cluster

For a cluster CRAB does not know, this is enough to start. Put it in your presets file (see
[Keep your presets out of git](#keep-your-presets-out-of-git)), and replace the account, the
partition and the module lines with your site's:

```json
{
  "mycluster": {
    "description": "My cluster",
    "scheduler": "slurm",
    "launcher": "srun",
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

## Choosing the launcher

The preset's `scheduler` decides how the job itself is run: `"slurm"` submits it with `sbatch`.
Its `launcher` decides how each application starts inside the job:

=== "`srun`"

    The default under Slurm, so a minimal Slurm preset is short:

    ```json
    "mycluster": {
        "scheduler": "slurm",
        "launcher": "srun",
        "launchers": { "srun": { "flags": ["--cpu-bind=socket"] } },
        "sbatch": ["--account=<your_account>", "--partition=<partition>"]
    }
    ```

    `launchers.srun.flags` is optional (default: none).

=== "`mpirun`"

    Use this when the cluster wants `mpirun` inside the Slurm job, as `slimfly` does:

    ```json
    "mycluster": {
        "scheduler": "slurm",
        "launcher": "mpirun",
        "launchers": {
            "mpirun": {
                "command": "/path/to/openmpi/bin/mpirun",
                "flags": ["--map-by", "node"]
            }
        },
        "sbatch": ["--account=<your_account>", "--partition=<partition>"]
    }
    ```

    `command` defaults to `mpirun` on the `PATH`, and `flags` to none. Inside a Slurm allocation
    CRAB gives `mpirun` the allocated host names itself, so there is no host list to write.

The full list of fields, with defaults, is in the
[presets.json format](../reference/presets.md#execution-fields). A benchmark can also ask for a
launcher of its own through its [receipt](../extending/receipts.md).

!!! warning "Presets from older versions"
    Launch settings used to be `CRAB_*` variables in `env`. CRAB no longer reads them and stops
    with an error that names the field to use instead. To convert an existing preset, follow the
    [removed keys table](../reference/presets.md#removed-keys).

## Running without Slurm

For trying CRAB on a laptop or workstation, or on a few machines reached over `ssh`, a preset can
skip Slurm. This is the shipped `local` preset:

```json
"local": {
    "description": "Local (no Slurm, dev/testing only)",
    "scheduler": "local",
    "launcher": "mpirun"
}
```

`"scheduler": "local"` starts the job as a detached process on this machine, and `"launcher":
"mpirun"` starts each application with `mpirun`. Request the preset with `-p local`; CRAB never
falls back to it on its own. Hosts, job ids, walltime, exclusive hosts and the caveat about
logging out are on the page [Running without Slurm](running-without-slurm.md).

## Module flow

Three places can load modules or set variables, and they run at different moments:

1. The preset's `header`. Under Slurm its lines are written once, at the top of the batch
   script `crab_job.sh`, after the `#SBATCH` directives. They run before `crab worker` starts, so
   the worker and every application it starts inherit what they set.
2. The receipt's `pre_run` hooks. CRAB writes a small script for each application launch,
   `<run dir>/.wrappers/app_<id>.sh`. It sources `/etc/profile.d/modules.sh` when that file exists,
   runs the receipt's `pre_run` hooks, then exports the application's own
   [`env`](../reference/configuration.md#the-apps-block) variables.
3. The launcher. The last line of that script is the launch line, which starts the application
   with `srun`, `mpirun` or as is.

With `mpirun` on several hosts, the remote ranks are started by `mpirun` over `ssh` and do not run
that script. They get the environment variables `mpirun` forwards (see
[the mpirun launcher](../reference/presets.md#the-mpirun-launcher)), so a module that sets other
variables has to be named in `launchers.mpirun.export`.

Under the local scheduler there is no batch script, so `header` does not apply (and neither do
`sbatch` and `sbatch_directives`). The job's worker inherits the environment of the shell that ran
`crab run`: load modules there, or in the receipt's `pre_run` hooks, which apply under every
scheduler.

## Selecting your preset

At run time CRAB resolves which preset to use in this order:

1. The `-p`/`--preset` flag: `crab run myconfig.json -p mycluster`
2. The `CRAB_PRESET` environment variable
3. A `.env` file in the working directory containing just the preset name

If none of these names a preset, `crab run` stops with an error. It never picks one for you;
to run without Slurm, ask for it explicitly with `-p local`.

The `_common` block of the shipped presets is merged underneath every preset. Put truly
universal settings there (it already defines `CRAB_ROOT`). It may hold
only `description`, `env`, `sbatch` and `header`; `scheduler`, `launcher` and the other execution
fields belong in each preset. The special
token `__CWD__` is replaced with CRAB's home: the repository root in a checkout, the data
directory of a pip install. If a preset doesn't set `CRAB_SYSTEM`, it
defaults to the preset's name and is used in the output directory path.

## Keep your presets out of git

The shipped presets are part of CRAB's code, so editing them (for example to put in your project
account) would turn every `crab update` into a conflict. Put your presets in a file of your own
instead: same format, merged over the shipped file. CRAB reads three files, in this order, each
merged over the one before:

1. The shipped presets, `src/crab/presets.json` (installed as `crab/presets.json`).
2. Your user file: `~/.config/crab/presets.json`, or the path in `CRAB_PATH_PRESETS` when that
   variable is set. With `CRAB_HOME` set on a pip install it is `$CRAB_HOME/config/presets.json`.
3. In a git checkout only, `local/presets.json`: a folder at the root of the checkout, next to
   `src/`. It is git-ignored and may not exist yet.

On a checkout you can use either of the last two; `local/` keeps everything with the checkout. On
a pip install use the user file. To start the checkout's file:

```bash
mkdir -p local
$EDITOR local/presets.json
```

The merge rules apply to every layer:

- A preset in your file replaces the earlier preset of the same name as a whole, so copy the
  entire block before changing it.
- A preset that exists only in your file is added.
- For `_common`, the `env` entries are merged key by key (yours win); any other `_common` key
  you set replaces the earlier one.

The shipped `_common.env` sets only `CRAB_ROOT`. It does not set `CRAB_PATH_WRAPPERS`, so a value
you export in your shell reaches the jobs.

!!! note "Edited `config/presets.json` on an older checkout?"
    The shipped file used to be `config/presets.json`. If you edited it, `crab update` refuses to
    go on and prints the steps: copy your edits to `local/presets.json`, run `git checkout
    config/presets.json`, then run `crab update` again.

For example, to run on `leonardo` with your project account, copy the whole `leonardo` block from
the shipped `src/crab/presets.json` into your file (`local/presets.json` here) and change only the
`--account` line:

```json
{
  "leonardo": {
    "description": "Leonardo Cluster @ CINECA",
    "scheduler": "slurm",
    "launcher": "srun",
    "launchers": { "srun": { "flags": ["--cpu-bind=socket"] } },
    "env": {
      "UCX_IB_SL": "1",
      "LD_LIBRARY_PATH": "${LD_LIBRARY_PATH}:/leonardo/smcx_home/bench/software/lib/ucx/nvhpc-22.2_pgi_cuda/lib/",
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
