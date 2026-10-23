# Glossary

CRAB reuses a few everyday words with precise, framework-specific meanings, and a couple of them
collide with Slurm terminology. This page is the authoritative reference for what each term means
here.

### Aggressor

An application whose purpose is to generate interference rather than be measured. In a config,
an aggressor is marked with `end: "f"`: CRAB force-terminates it as soon as all the
[victims](#victim) have finished. Contrast with [victim](#victim).

### Application (app) { #application }

A single executable CRAB launches within an [experiment](#experiment), described by one entry in
the config's `apps` block and adapted by a [wrapper](#wrapper). Multiple applications run
concurrently in one experiment. That concurrency is the whole point of CRAB.

### Behind / Modified { #behind-modified }

How a [target](#target)'s crab-wrappers clone compares with GitHub. Behind means GitHub has
commits the clone lacks (Pull fetches them). Modified means tracked files in the clone were
edited, which blocks Pull until the edits are committed, stashed or moved to `local/wrappers`.

### Collection (`collect`)

Whether CRAB parses an application's output and stores its metrics. Set per application with
`collect: true`. Applications that only generate interference typically set `collect: false`.

### Convergence

The condition that stops an experiment's repeated runs early. After `minruns` successful runs, CRAB checks whether
the [confidence interval](concepts/architecture.md#per-experiment-lifecycle) of each
convergence-target metric has tightened below `beta × mean`; once all targets converge (or
`maxruns`/`timeout` is hit), the experiment ends. Which metrics are targets is set by the
`conv` flag in a wrapper's `metadata`, or by `convergeall`.

### Engine

The control core (`src/crab/core/engine.py`) that runs in one of two modes:
[orchestrator](#orchestrator) or [worker](#worker).

### Environment

Overloaded; two senses:

1. The preset's `env` block: environment variables exported for a run (e.g. `LD_LIBRARY_PATH`,
   `UCX_*`). This is [system-dependent](concepts/system-dependent-vs-independent.md).
2. The serialized `environment.json` the [orchestrator](#orchestrator) writes into the data
   directory and the [worker](#worker) reads back.

Both derive from the selected [preset](#preset).

### Experiment

A named unit of work within a [job](#job): a set of [applications](#application) launched together
under one node allocation and schedule, repeated across [runs](#run) until
[convergence](#convergence). A single config can define multiple experiments (the `experiments`
block), executed one after another by the [worker](#worker).

### Home

Where a checkout or an install keeps the results (`data/`), benchmarks and wrappers it works
with. For a git checkout it is the repo root; for a `pip` or `pipx` install it is
`~/.local/share/crab`, or the folder named by `CRAB_HOME`. `CRAB_ROOT` and `__CWD__` hold it.
Contrast with the install location of the package itself (`site-packages` for an install), which
holds code and is never written to, and with the per-user config folder (`~/.config/crab`), which
holds only the user's presets, receipts and private wrappers. See
[ADR-038](dev/dashboard/decisions/adr-038-two-layouts.md).

### Host list

The machines a [local](#scheduler) preset runs on, written in the preset's `hosts` field (or read
from a `hostfile`) as `"name"` or `"name:cores"`. A job uses the first `numnodes` of them, in
order. Under Slurm there is no host list: Slurm picks the nodes. See
[Running without Slurm](using/running-without-slurm.md).

### Job

The top-level container: a single `crab run` invocation: one config file, submitted as one
job (a Slurm job on a cluster), producing one output directory under `data/<system>/<name>_<timestamp>/`. Named by
`global_options.name` (recorded as `job_name` in the registry). A job contains one or more
[experiments](#experiment).

### Launcher

The command that actually starts an application's processes on the allocated nodes: `srun`,
`mpirun`, or `direct` (the command as is, with no wrapper). The preset's `launcher` field picks the
kind, and `launchers.srun` and `launchers.mpirun` hold its command and flags. A
[receipt](#receipt) may set `launcher_override` to `srun` or `mpirun` for a benchmark that needs a
different launcher than the preset's.

### Orchestrator

The first execution phase (`Engine._run_orchestrator`), running on the login node. It prepares the
data directory, generates the Slurm batch script, and submits it (with `sbatch` under the `slurm` scheduler). It does **not** run
benchmarks itself. It hands off to the [worker](#worker). See [Architecture](concepts/architecture.md#the-two-phase-execution-model).

### Partition

**Two unrelated meanings.** Do not confuse them:

1. CRAB allocation partition: a named group of nodes defined in `allocation.partitions`, typically
   `"victim"` and `"aggressor"`. Each app references its partition by name via the `partition` field
   (→ `partition_id`).
2. Slurm partition: a queue/resource pool on the cluster, requested via an `sbatch` directive
   like `--partition=boost_usr_prod`.

When this documentation says "partition" unqualified in a CRAB allocation context, it means the
first. In `sbatch_directives` or preset Slurm flags, it means the second.

### ppn

Ranks per node. Combined with the node count to compute an app's total task count for the
launcher (`-n`/`-np`). Each app's `ppn` is its own, else its experiment's `local_options.ppn`,
else `global_options.ppn`; see [Ranks per node](reference/configuration.md#ranks-per-node-ppn).

### Preset

A named, [system-dependent](concepts/system-dependent-vs-independent.md) environment definition in
a file of the crab-wrappers clone (`presets/<cluster>.json`) or in your own [override](#override)
files: scheduler, launcher and its flags, CPU pinning, modules (`header`), and Slurm directives
(`sbatch`). Selected at run time with `-p <name>`. CRAB itself sets `CRAB_ROOT` in every preset's
environment, so a preset does not write it.

### Override

A JSON file in your own presets folder (`local/presets/` in a checkout, `~/.config/crab/presets/`
in an install) that patches a shared [preset](#preset) instead of replacing it, or adds a new
preset with `"extends"`. It is the place for values only you know, such as your account. See
[Your overrides](using/presets.md#your-overrides).

### Mark

A value only you can supply, written in a shared preset as `<YOUR_NAME>` (for example
`--account=<YOUR_ACCOUNT>`). `crab run`, `crab validate` and `crab nodes` refuse a preset while a
mark is unset, and print the [override](#override) to paste. `crab presets show <name>` lists the
marks that are unset.

### Receipt

A JSON file in `local/receipts/<benchmark_id>.json`, produced by `crab setup` building a
[recipe](#recipe). It records the built `binary_path`, optional `pre_run` hooks, a possible
`launcher_override` (`srun` or `mpirun`), and `target_arch`. It is the bridge between *building* a benchmark and
*running* it: [wrappers](#wrapper) read it at run time. System-dependent.

### Recipe

A Python class (`src/crab/setup/recipes/*.py`, subclassing `BenchmarkRecipe`) describing how to
download and build a benchmark. Used only by `crab setup`, which turns a recipe into a
[receipt](#receipt). Distinct from a [wrapper](#wrapper).

### Run

A single execution of all an [experiment's](#experiment) applications. An experiment performs many
runs (at least `minruns` successful ones, at most `maxruns` in all) to gather enough samples for [convergence](#convergence).
"Run" (one repetition) and "experiment" (the whole repeated study) are not interchangeable.

### Scheduler

What runs the [job](#job) itself, chosen by the preset's `scheduler` field. `slurm` submits it with
`sbatch`; `local` starts it as a detached process on this machine, for development and testing
(no Slurm needed). It is separate from the [launcher](#launcher), which starts each application
inside the job.

### Shared mode

An [allocation](reference/configuration.md#shared-nodes) with `mode: "shared"`, in which every
[application](#application) of a scope gets all of its nodes, so the apps run at the same time on
the same hosts. Its `cores` key, `"disjoint"` (the default) or `"shared"`, says whether they run
on separate cores or may compete for the same ones. Apps that run this way are said to
co-run.

### Target

Where the dashboard looks at [wrappers](#wrapper): This laptop (the machine running `crab web`) or
a connected cluster. Each target has its own crab-wrappers clone and [receipts](#receipt).

### Victim

An application that is being measured. Marked with `end: ""`: CRAB waits for it to finish
naturally. The metrics of interest come from victims; [aggressors](#aggressor) exist only to
perturb them.

### Wrapper

A Python module in `wrappers/` with a class named `app` (subclassing `base`) that teaches CRAB how
to launch a specific application and parse its output into metrics. The
[system-independent](concepts/system-dependent-vs-independent.md) counterpart to a
[recipe](#recipe). See [Extending CRAB](extending/wrappers.md).

### Worker

The second execution phase (`Engine._run_worker`), running on the allocated compute nodes inside
the Slurm job (under the local scheduler, as a background process on this machine). It reads
back the config the [orchestrator](#orchestrator) wrote and runs each experiment. Invoked as the hidden `crab worker` subcommand, never called by hand.
