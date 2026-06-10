# Glossary

CRAB reuses a few everyday words with precise, framework-specific meanings — and a couple of them
collide with Slurm terminology. This page is the authoritative reference for what each term means
here.

### Aggressor

An application whose purpose is to **generate interference** rather than be measured. In a config,
an aggressor is marked with `end: "f"` — CRAB force-terminates it as soon as all the
[victims](#victim) have finished. Contrast with [victim](#victim).

### Application (app)

A single executable CRAB launches within an [experiment](#experiment), described by one entry in
the config's `apps` block and adapted by a [wrapper](#wrapper). Multiple applications run
concurrently in one experiment — that concurrency is the whole point of CRAB.

### Collection (`collect`)

Whether CRAB parses an application's output and stores its metrics. Set per application with
`collect: true`. Applications that only generate interference typically set `collect: false`.

### Convergence

The condition that stops an experiment's repeated runs early. After `minruns`, CRAB checks whether
the [confidence interval](concepts/architecture.md#per-experiment-lifecycle) of each
convergence-target metric has tightened below `beta × mean`; once all targets converge (or
`maxruns`/`timeout` is hit), the experiment ends. Which metrics are targets is set by the
`conv` flag in a wrapper's `metadata`, or by `convergeall`.

### Engine

The control core (`src/crab/core/engine.py`) that runs in one of two modes —
[orchestrator](#orchestrator) or [worker](#worker).

### Environment

Overloaded; two senses:

1. The **preset's `env` block** — environment variables exported for a run (e.g. `CRAB_MPIRUN`,
   `LD_LIBRARY_PATH`). This is [system-dependent](concepts/system-dependent-vs-independent.md).
2. The serialized `environment.json` the [orchestrator](#orchestrator) writes into the data
   directory and the [worker](#worker) reads back.

Both derive from the selected [preset](#preset).

### Experiment

A named unit of work within a run: a set of [applications](#application) launched together under
one node allocation and schedule, repeated until [convergence](#convergence). A single config can
define multiple experiments (the `experiments` block), executed one after another by the
[worker](#worker). Distinct from a [run](#run).

### Launcher

The command that actually starts an application's processes on the allocated nodes — `srun` (Slurm)
or `mpirun` (MPI), selected by the preset. A [receipt](#receipt) may specify a `launcher_override`
for a benchmark that needs a non-default launcher.

### Orchestrator

The first execution phase (`Engine._run_orchestrator`), running on the login node. It prepares the
data directory, generates the Slurm batch script, and submits it with `sbatch`. It does **not** run
benchmarks itself — it hands off to the [worker](#worker). See [Architecture](concepts/architecture.md#the-two-phase-execution-model).

### Partition

⚠️ **Two unrelated meanings — do not confuse them:**

1. **CRAB allocation partition** — a group of nodes within an experiment under the partitioned
   allocation mode (`allocationmode: p`), typically one for victims and one for aggressors. Set per
   app via `partition` (→ `partition_id`).
2. **Slurm partition** — a queue/resource pool on the cluster, requested via an `sbatch` directive
   like `--partition=boost_usr_prod`.

When this documentation says "partition" unqualified in a CRAB allocation context, it means the
first. In `sbatch_directives` or preset Slurm flags, it means the second.

### ppn

**Processes per node.** Combined with the node count to compute total task count for the launcher
(`-n`/`-np`). Taken from `global_options.ppn`.

### Preset

A named, [system-dependent](concepts/system-dependent-vs-independent.md) environment definition in
`config/presets.json`: workload manager, launcher flags, CPU pinning, modules (`header`), and Slurm
directives (`sbatch`). Selected at run time with `-p <name>`. The `_common` block is merged under
every preset.

### Receipt

A JSON file in `config/environments/<benchmark_id>.json`, produced by `crab setup` building a
[recipe](#recipe). It records the built `binary_path`, optional `pre_run` hooks, a possible
`launcher_override`, and `target_arch`. It is the bridge between *building* a benchmark and
*running* it: [wrappers](#wrapper) read it at run time. System-dependent.

### Recipe

A Python class (`src/crab/setup/recipes/*.py`, subclassing `BenchmarkRecipe`) describing how to
**download and build** a benchmark. Used only by `crab setup`, which turns a recipe into a
[receipt](#receipt). Distinct from a [wrapper](#wrapper).

### Run

A single execution of all an [experiment's](#experiment) applications. An experiment performs many
runs (from `minruns` up to `maxruns`) to gather enough samples for [convergence](#convergence).
"Run" (one repetition) and "experiment" (the whole repeated study) are not interchangeable.

### Victim

An application that **is being measured**. Marked with `end: ""` — CRAB waits for it to finish
naturally. The metrics of interest come from victims; [aggressors](#aggressor) exist only to
perturb them.

### Workload manager (WL manager)

The framework module that translates "run this command on these nodes" into a concrete launcher
invocation: `slurm` (`wl_manager/slurm.py`, uses `srun`) or `mpi` (`wl_manager/mpi.py`, uses
`mpirun`). Chosen by the preset's `CRAB_WL_MANAGER`.

### Wrapper

A Python module in `wrappers/` with a class named `app` (subclassing `base`) that teaches CRAB how
to **launch** a specific application and **parse** its output into metrics. The
[system-independent](concepts/system-dependent-vs-independent.md) counterpart to a
[recipe](#recipe). See [Extending CRAB](extending/wrappers.md).

### Worker

The second execution phase (`Engine._run_worker`), running on the allocated compute nodes inside
the Slurm job. It reads back the config the [orchestrator](#orchestrator) wrote and runs each
experiment. Invoked as the hidden `crab worker` subcommand — never called by hand.
