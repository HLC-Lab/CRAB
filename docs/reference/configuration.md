# Configuration schema

This is the complete reference for a CRAB experiment config — the JSON file you pass to
`crab run`. It is [system-independent](../concepts/system-dependent-vs-independent.md): the same
file runs on any cluster by selecting a different preset.

For the conceptual walkthrough of writing one, see
[Writing experiment configs](../using/writing-configs.md). This page is the exhaustive field list.

## The experiment hierarchy

A run config describes a small nesting of levels. From outermost to innermost:

```text
Job — one `crab run`: one config file → one Slurm job → one output directory
│     (named by global_options.name)
├─ Experiment "baseline"          an entry in `experiments`; experiments run sequentially
│    ├─ Run 1, Run 2, …           repetitions (minruns…maxruns), for statistical convergence
│    └─ apps 0, 1, 2, …           entries in `apps`; all run concurrently within each run
└─ Experiment "with_aggressor"
     └─ …
```

| Level | Where it lives | Notes |
|-------|----------------|-------|
| **[Job](../glossary.md#job)** | the whole config file | One `crab run` = one Slurm job = one `data/<system>/<name>_<timestamp>/` directory. Named by `global_options.name`. |
| **[Experiment](../glossary.md#experiment)** | a key under `experiments` | Run one after another, in sorted key order. Has its own `apps`; may override options via `local_options`. |
| **[Run](../glossary.md#run)** | runtime only (not in the JSON) | One repetition of an experiment. Repeated from `minruns` to `maxruns`, stopping early on convergence. |
| **[Application](../glossary.md#application)** | a key under an experiment's `apps` | The processes launched **concurrently** within each run. |

!!! note "Runs and apps are orthogonal"
    Each *run* executes all of the experiment's *apps* once, together; runs are repetitions for
    statistics, apps are the concurrent workload measured in every run. A 3-app experiment with
    10 runs launches those 3 apps together, ten times.

## Top-level shape

A config has two main top-level keys, `global_options` and `experiments`, and an optional
`schema_version`.

```json
{
  "schema_version": 2,
  "global_options": { ... },
  "experiments": {
    "experiment_name": {
      "description": "...",
      "local_options": { ... },
      "apps": { ... }
    }
  }
}
```

`schema_version` names the format of the file. The dashboard writes `2`. A config without it, or
with `1`, is read the same way. A config with a version newer than the installed CRAB understands
is refused before anything runs, with a hint to run `crab update`.

!!! note "Checked before anything runs"
    Values CRAB would otherwise misread are errors before the job starts: `convergeall`,
    `retain_files` and `collect` must be `true` or `false` (the strings `"true"` and `"false"` are
    accepted too), `outformat` must be `csv`, allocation `mode`s must be one of `linear`,
    `interleaved`, `random` or `shared`, and every app needs a `path`. The allocation keys used before
    the nested `allocation` block (`allocationmode`, `partitionsplit`, `partitionlayout`,
    `allocationsplit`) are refused with the equivalent form; see
    [Allocation fields](#allocation-fields).

    The launcher options and the preset are checked too. These are errors:

    - `launcher` is not `srun`, `mpirun` or `direct`, or `launcher_flags` is not a list of strings;
    - `sbatch_directives`, or the preset's `sbatch`, set `nodes`, `N`, `ntasks-per-node`, `ntasks`
      or `n`, which CRAB sets itself from `numnodes` and the apps' `ppn`;
    - a `ppn` (in `global_options`, `local_options` or an app) that is not a positive integer;
    - the `srun` launcher on a preset whose scheduler is `local` ("the srun launcher needs a Slurm
      preset");
    - the `direct` launcher with `numnodes`, or the `ppn` of any app, above 1 ("the direct
      launcher runs a single process");
    - `numnodes` above the number of hosts of a `local` preset.

    `sbatch_directives` on a `local` preset only log a warning: the local scheduler ignores them.

    Some other patterns only get a warning in the log: an allocation split that sums to less than
    100, an app with `collect: true` and an `end` of `"f"` or a number (see
    [`end`](#the-apps-block)), `numnodes`, `walltime` or `sbatch_directives` inside an
    experiment's `local_options`, and any experiment key other than `description`,
    `local_options` and `apps`.

!!! note "Legacy single-experiment form"
    A config may instead use a top-level `applications` block in place of `experiments`. CRAB
    wraps it automatically into a single experiment named `default_ex`. New configs should use the
    explicit `experiments` form.

## `global_options`

Settings applied to the whole run. An experiment can override most of these in its own
`local_options` (see [below](#per-experiment-overrides-local_options)).

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `numnodes` | int | **required** | Total nodes: allocated for the Slurm job, or the first `numnodes` hosts of a local preset. |
| `ppn` | int | `1` | Ranks per node, for every app that does not set its own. Drives the launcher's total task count. See [Ranks per node](#ranks-per-node-ppn). |
| `name` | string | `""` | Human-readable run name; prefixes the output directory (`<name>_<timestamp>`). |
| `datapath` | string | `<CRAB_ROOT>/data` | Root directory for results. |
| `allocation` | object | `{"mode":"linear"}` | Node-to-app mapping strategy. See [Allocation](#allocation-fields). |
| `minruns` | int | `10` | Minimum runs before convergence is checked. |
| `maxruns` | int | `20` | Hard cap on runs. |
| `timeout` | float | `1200.0` | Wall-clock budget for the experiment, in seconds. |
| `convergeall` | bool | `false` | If true, every metric must converge; otherwise only metrics flagged `conv` in the wrapper. |
| `alpha` | float | `0.05` | Confidence-interval significance level. |
| `beta` | float | `0.05` | Convergence threshold: CI width must fall below `beta × mean`. |
| `outformat` | `csv` | `csv` | Output file format. `csv` is the only format. |
| `retain_files` | bool | `true` | Keep per-run working directories. If `false`, successful runs' scratch dirs are deleted. |
| `tags` | string | `none` | Free-form label recorded in the run registry (`metadata.csv`). |
| `launcher` | string | the preset's `launcher` | How each application starts: `"srun"`, `"mpirun"` or `"direct"`. See [Presets](presets.md). |
| `launcher_flags` | list of strings | the preset's flags for that launcher | Replaces the preset's flags for the chosen launcher; `[]` means no extra flags. |
| `walltime` | string | `00:10:00` | The job's time limit, in the formats of Slurm's `--time`. Under Slurm it is the base `--time` value (overridable via `sbatch_directives`). Under a local preset CRAB enforces it itself and ends the job `TIMEOUT`; see [Running without Slurm](../using/running-without-slurm.md#walltime). |
| `extrainfo` | string | `job` | Short token used to build the Slurm job name. |
| `sbatch_directives` | list \| dict | `[]` | User Slurm directives. See [sbatch directives](#sbatch-directives). |

!!! info "Launcher options"
    `launcher` and `launcher_flags` can also be set per experiment in `local_options`. A
    benchmark's [receipt](../extending/receipts.md) `launcher_override` still decides that
    benchmark's launcher kind; in that case its flags come from the preset, not from
    `launcher_flags`.

### Ranks per node (`ppn`)

`ppn` is the number of ranks an app starts on each of its nodes. It is a positive integer and can
be set in three places; the most specific one wins, and without any the app gets `1`:

1. the app's own `ppn` (in its entry of `apps`);
2. the experiment's `local_options.ppn`;
3. `global_options.ppn`.

So a victim with one rank per node can share its nodes with an aggressor that runs several:

```json
"global_options": { "numnodes": "4", "ppn": "1", "allocation": { "mode": "shared" } },
"experiments": {
  "pair": {
    "apps": {
      "0": { "path": "blink/alltoall_comm_only.py", "collect": true },
      "1": { "path": "blink/alltoall_comm_only.py", "end": "f", "ppn": "8" }
    }
  }
}
```

An app's total ranks are its `ppn` times the number of nodes it runs on.

**The batch job's `--ntasks-per-node`** is the largest per-node load over the experiments, which
CRAB computes: on each node, apps that share it add up, and apps that start after another one on
its nodes (`start: "sN"`, see [Scheduling](#scheduling-start-and-end)) count once per chain, at
the chain's largest `ppn`. Experiments run one after the other, so the job takes the largest of
their loads. It cannot be set by hand: `--ntasks-per-node` in `sbatch_directives` is an error (see
[sbatch directives](#sbatch-directives)). When the load cannot be worked out (for example
`numnodes` is missing), CRAB uses `global_options.ppn`.

**What is recorded.** The `ppn` column of `metadata.csv` holds the experiment's value
(`local_options.ppn` if set, else `global_options.ppn`). Each app's own `ppn` and its `env` are in
the job's `crab_run.json`, under `launches`, so the results show what each app ran with; see
[What a run records about its launches](../using/running-without-slurm.md#what-a-run-records-about-its-launches).

### Allocation fields

All allocation config lives under a single `allocation` key. The minimal form is:

```json
"allocation": { "mode": "linear" }
```

**`mode`** — how nodes are distributed:

| Value | Behaviour |
|-------|-----------|
| `"linear"` (default) | Each app (or partition) gets a contiguous block of nodes. |
| `"interleaved"` | Nodes are dealt round-robin; supports an optional `stride` (default `1`). |
| `"random"` | Node list is shuffled (optionally with `seed`), then split linearly. |
| `"shared"` | Every app gets all the nodes, so the apps run together on the same hosts. See [Shared nodes](#shared-nodes). |

**`split`** — percentage share per app (omit for an equal split). An app that runs on another
app's nodes (a chained app, see [Scheduling](#scheduling-start-and-end)) takes no share: give one
value per remaining app, in app order.

```json
"allocation": { "mode": "linear", "split": [60, 40] }
```

**Partitioned allocation** — add a `partitions` key to split nodes into named groups. Each app then declares which group it belongs to via its `partition` field:

```json
"allocation": {
  "mode": "interleaved",
  "partitions": {
    "victim":    { "share": 50 },
    "aggressor": { "share": 50 }
  }
}
```

- `share` is a percentage. Either all partitions specify `share` (values should sum to 100) or none do (equal split).
- The top-level `mode` controls how the partition node-blocks are laid out relative to each other.
- If a partition contains multiple apps, an inner `mode` and `split` can be added inside the partition entry for intra-partition placement.
- A chained app with no `partition`, or with the same one as the app it waits for, runs on that app's nodes and is not counted as a member of the partition.

**Mode extras:**

| Extra key | Used with | Meaning |
|-----------|-----------|---------|
| `stride` | `"interleaved"` | Nodes assigned per turn (default `1`). |
| `seed` | `"random"` | Integer seed for the shuffle; omit for non-deterministic. |

### Shared nodes

By default each app gets nodes of its own. With `mode: "shared"` every app gets **all** the nodes
of its scope instead, so the apps run at the same time on the same hosts. That measures
interference inside a node: the apps compete for memory bandwidth, caches and the network card.
The `cores` key says how they share the cores:

```json
"allocation": { "mode": "shared", "cores": "disjoint" }
```

| `cores` | Behaviour |
|---------|-----------|
| `"disjoint"` (default) | Apps on the same nodes run on different cores. |
| `"shared"` | Apps may run on the same cores, so the result measures how the operating system schedules them as much as the hardware. CRAB warns about it. |

**Where it applies.** `mode: "shared"` goes at the top level of `allocation` when there are no
`partitions`, or inside one partition, which then holds two or more apps (a partition with one
app gives it all of its nodes whatever its `mode`). `cores` sits next to it, in the same scope. A
chained app (`start: "sN"`) already runs on its head's nodes and its cores.

These are errors, reported before anything is submitted:

- `split`, `stride` or `seed` next to `mode: "shared"` (every app gets all the nodes);
- `mode: "shared"` at the top level together with `partitions` (the top-level mode lays out the
  partition blocks); put `mode: "shared"` inside a partition instead;
- `cores` with any `mode` other than `"shared"`;
- a `cores` value other than `"disjoint"` or `"shared"`.

**Which cores each app gets.** CRAB decides this before the job starts, from the apps' [`cpus`
lists](#core-lists-and-environment-per-app-cpus-and-env) and the allocation's `cores`:

- An app with `cpus` is bound to exactly those cores, on every host it runs on.
- With `cores: "disjoint"` on a **local** preset, apps that have no `cpus` are split
  automatically. In app order they get consecutive blocks of `ppn` cores that no other app of the
  group uses, counted from core 0 of the host and skipping the cores that some `cpus` list
  names. A host's core count is the one in the preset's `hosts`, or this machine's CPU count for a
  bare `localhost`; with several hosts the smallest count is used. CRAB warns that this split
  ignores NUMA and NIC locality: set `cpus` per app to control it.
- With `cores: "shared"`, nothing is split. An app with `cpus` is still bound to them, and the
  lists may overlap. An app without `cpus` is left unbound (`mpirun` is told not to bind it), so
  that two launches do not both pile onto the first cores of the node.
- Under Slurm there is no automatic split: the `srun` launcher gives each app its own cores with
  the flags in [Shared nodes under `srun`](#shared-nodes-under-srun).

Binding to a `cpus` list is done by the `mpirun` and `srun` launchers (the exact flags are in the
[presets reference](presets.md#the-mpirun-launcher)). The `direct` launcher does not bind.

#### Shared nodes under `srun`

An app that shares nodes with another app and runs at the same time is started with explicit step
flags. Without them, each `srun` step would ask for the job's whole task budget and Slurm would
run the steps one after the other, with no error. The flags depend on the cores setting:

| Core setting | Flags added to the app's `srun` line |
|---|---|
| `cores: "disjoint"`, no `cpus` | `--exact --mem=0 --cpu-bind=verbose,cores` |
| `cpus` given | `--overlap --cpu-bind=verbose,map_cpu:<cpus>` |
| `cores: "shared"`, no `cpus` | `--overlap --cpu-bind=verbose,none` |

All three also get `--ntasks-per-node=<the app's ppn>`. Notes:

- `verbose` makes `srun` print each rank's CPU mask into the app's log, so you can check where
  the ranks ran.
- Apps that do not share nodes keep the plain `srun` line, with two exceptions: an app with
  `cpus` is bound to them (`--cpu-bind=verbose,map_cpu:<cpus>`, without `--overlap`), and an app
  whose `ppn` differs from the job's `--ntasks-per-node` gets `--ntasks-per-node=<its ppn>`.
- CRAB's binding replaces any `--cpu-bind` in `launchers.srun.flags` or `launcher_flags` for the
  apps it binds. The pre-submit check warns about it, so it shows when you submit.
- `cpus` under Slurm needs `--exclusive` among the `sbatch_directives` or the preset's `sbatch`;
  see the errors above.
- `mpirun` is unchanged: it binds as described in the
  [presets reference](presets.md#the-mpirun-launcher).

These are errors, reported before anything is submitted:

- two apps that run on the same nodes at the same time, with `cores: "disjoint"`, whose `cpus`
  lists share a core (the message names the core); give each its own cores or set `cores` to
  `"shared"`;
- apps without `cpus` that need more cores than the host has left, with `cores: "disjoint"` on a
  local preset (for example three apps with `ppn` 8 on a host of 16 cores): lower `ppn`, use
  fewer apps, or set `cpus`;
- `cpus` under Slurm when `--exclusive` is not among the `sbatch_directives` or the preset's
  `sbatch` (without it the cores are not yours alone);
- under Slurm, apps that share nodes with `cores: "disjoint"` where some set `cpus` and others do
  not (the message names the apps without): an `--overlap` step does not reserve its cores, so a
  step without `cpus` could be given them; set `cpus` on every app of the group, or on none.

## `experiments`

A dictionary mapping an experiment name to its definition. Experiments run **sequentially**, in
sorted key order.

| Key | Type | Meaning |
|-----|------|---------|
| `description` | string | Optional free-text note (stored in the run's `config.json`). |
| `local_options` | object | Per-experiment overrides of `global_options` (see below). |
| `apps` | object | The applications to run concurrently in this experiment. |

### Per-experiment overrides (`local_options`)

Any `global_options` key may be repeated in an experiment's `local_options`; the experiment's
value wins for that experiment (the framework merges `{**global, **local}`). Use this to vary,
say, `allocation` or `timeout` between experiments in the same run.

!!! warning "`numnodes`, `walltime` and `sbatch_directives` are job-level"
    They are written once into the batch script when the job is submitted, so putting them in
    `local_options` has no effect (CRAB warns). `ppn` is the exception: an experiment may set its
    own, and the job's `--ntasks-per-node` is sized to the largest load; see
    [Ranks per node](#ranks-per-node-ppn).

### The `apps` block

A dictionary keyed by **numeric string IDs** (`"0"`, `"1"`, …). CRAB takes the apps in numeric
key order, and `start: "sN"` counts positions in that order from 0, so number the apps from
`"0"` without gaps: then N is also the key of the app being waited for. Each value describes one
application:

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `path` | string | **required** | Path to the [wrapper](../glossary.md#wrapper) module. A relative path is looked up in `local/wrappers/`, then in each folder of `CRAB_PATH_WRAPPERS` (or `wrappers/`); see [Where CRAB looks for wrappers](../extending/wrappers.md#where-crab-looks-for-wrappers). |
| `binary` | string | — | Path to the application's executable for this app. Takes precedence over the wrapper's receipt and `PATH` lookup. |
| `args` | string | `""` | Command-line arguments passed to the executable. |
| `collect` | bool | `false` | Whether to parse and store this app's metrics. |
| `start` | string | `"0"` | When to start the app. See [Scheduling](#scheduling-start-and-end). |
| `end` | string | `""` | When to stop the app. See [Scheduling](#scheduling-start-and-end). |
| `partition` | string | — | (partitioned allocation) The named partition this app belongs to, e.g. `"victim"` or `"aggressor"`. Must match a key in `allocation.partitions`. |
| `ppn` | int | the experiment's, else the global one | Ranks per node for this app. See [Ranks per node](#ranks-per-node-ppn). |
| `cpus` | string | — | The cores this app's ranks are bound to, such as `"0-15,32"`: one core per rank. See [below](#core-lists-and-environment-per-app-cpus-and-env). |
| `env` | object | `{}` | Environment variables set for this app's ranks only. See [below](#core-lists-and-environment-per-app-cpus-and-env). |

!!! tip "Extra keys become wrapper attributes"
    Any key in an app entry that is **not** one of `path`, `args`, `collect`, `start`, `end`,
    `partition`, `cpus`, `env` or `ppn` (including `binary`) is injected as an attribute onto the
    wrapper instance. This lets a wrapper accept custom configuration straight from the JSON
    without framework changes.

### Core lists and environment per app (`cpus` and `env`)

**`cpus`** is a string of comma-separated items, each a core number `N` or a range `A-B` with
`A` not above `B`: `"0-15,32"`, `"4"`, `"0-3, 8-11"`. The cores are sorted, and a core listed
twice is an error. It must list at least the app's own `ppn` cores (one per rank), and the same
list is used on every host the app runs on. The `mpirun` and `srun` launchers bind the ranks to
it; see [Shared nodes](#shared-nodes) for how it combines with `allocation`, for the automatic
split of apps without one, and for the flags `srun` gets. The `direct` launcher does not bind.
Other errors:

- `cpus` on an app with `start: "sN"`: it runs on the cores of the app it waits for, so set
  `cpus` there (its `ppn` may not exceed the number of those cores);
- `cpus` under Slurm without `--exclusive` among the `sbatch_directives` or the preset's `sbatch`.

**`env`** is an object of strings. Each key has to be a valid shell variable name (letters, digits
and underscores, not starting with a digit), and may not be one of the removed `CRAB_*` launch
keys listed in the [presets reference](presets.md#removed-keys). The app's launch script exports
the variables just before the launch line, so they reach that app's ranks and no other app's, and
they win over a variable of the same name in the preset's `env`. A value is exported as written,
without shell expansion. With `mpirun` on Open MPI, CRAB also names these variables in `-x` so
that remote ranks receive them; Hydra passes the whole environment. Each app's `env` is recorded
in `crab_run.json` with its launch.

```json
"0": {
  "path": "blink/alltoall_comm_only.py",
  "collect": true,
  "cpus": "0-3",
  "env": { "OMP_NUM_THREADS": "1" }
}
```

To give each app its own network interface, see
[One network interface per app](../using/writing-configs.md#one-network-interface-per-app).

## Scheduling (`start` and `end`)

The `start` and `end` strings encode the victim/aggressor model and timed/sequential execution.

**`start` — when the app launches:**

| Value | Meaning |
|-------|---------|
| `"0"` or a number | Delay in seconds from the start of the run before launching. |
| `"sN"` | Start only **after application N has finished** (a dependency, enabling sequential chains). The app runs on application N's nodes; see below. |

**`end` — when the app is stopped:**

| Value | Meaning | Role |
|-------|---------|------|
| `""` (empty) | Wait for the app to finish on its own. | [Victim](../glossary.md#victim) |
| `"f"` | Force-terminate once all non-`f` apps have finished. | [Aggressor](../glossary.md#aggressor) |
| a number | Terminate after that many seconds. | Timed |

An app is stopped by killing it, and the output of a killed app is not collected. An app with
`collect: true` that is still running when `f` or a timed `end` stops it has no data for that
run; CRAB warns about this combination before the run starts.

**Which nodes a chained app uses.** An app with `start: "sN"` runs on the nodes of application N,
which have just been released. This carries along a chain: in `0` → `s0` → `s1` all three apps
run on app 0's nodes, and only app 0 gets a share of the allocation. A chained app that names a
**different** `partition` than the app it waits for instead gets a share of its own partition:
use this to start an app when another one ends, but on other nodes.

These are errors, reported before anything is submitted:

- two apps that would both run on the same app's nodes after it (for example two apps with
  `start: "s0"` and no partition change): they would run at the same time on the same nodes;
  give one of them a different partition;
- `"sN"` naming an application that does not exist;
- start values that wait on each other in a cycle.

## sbatch directives

`global_options.sbatch_directives` lets you add or override Slurm `#SBATCH` lines. The preferred
form is a **list** of complete directives:

```json
"sbatch_directives": ["--account=IscrC_FOO", "--qos=normal", "--exclusive"]
```

A legacy **dict** form is also accepted (`true` → bare flag, `false` → omitted, value → `--key=value`):

```json
"sbatch_directives": { "time": "00:20:00", "exclusive": true }
```

!!! warning "CRAB protects some directives"
    `--nodes` (`-N`), `--ntasks-per-node` and `--ntasks` (`-n`) are computed by the framework:
    `--nodes` from `numnodes`, and `--ntasks-per-node` as the largest per-node load of the apps'
    `ppn` (see [Ranks per node](#ranks-per-node-ppn)). Setting one in `sbatch_directives`, or in
    the preset's `sbatch`, stops the run before anything is submitted, with a message that names
    `numnodes` or `ppn` instead.
    Overriding `--output`/`--error` is allowed but warned about, since it redirects CRAB's
    standard logs. Directives containing newlines are rejected. System-level directives from the
    preset are merged in with lower priority than your config's directives.

## Worked example: partitioned victim vs aggressor

```json
{
  "global_options": {
    "name": "congestion_study",
    "numnodes": "8",
    "ppn": "1",
    "allocation": {
      "mode": "interleaved",
      "partitions": {
        "victim":    { "share": 50 },
        "aggressor": { "share": 50 }
      }
    },
    "minruns": "10",
    "maxruns": "30",
    "timeout": "1200.0",
    "outformat": "csv",
    "sbatch_directives": ["--exclusive", "--time=00:20:00"]
  },
  "experiments": {
    "a2a_vs_graph500": {
      "description": "All-to-all victim against a Graph500 aggressor, interleaved nodes.",
      "apps": {
        "0": {
          "path": "blink/alltoall_comm_only.py",
          "args": "-msgsize 8192 -iter 1000",
          "collect": true,
          "start": "0",
          "end": "",
          "partition": "victim"
        },
        "1": {
          "path": "graph500/g500_wrapper.py",
          "args": "",
          "collect": false,
          "start": "0",
          "end": "f",
          "partition": "aggressor"
        }
      }
    }
  }
}
```

This allocates 8 nodes, splits them 50/50 into two interleaved partitions, runs an all-to-all
*victim* (collected) on the `"victim"` partition against a Graph500 *aggressor* (force-killed when
the victim finishes) on the `"aggressor"` partition, and repeats between 10 and 30 runs until the
victim's convergence-target metric stabilizes.

For where the resulting files land, see [Architecture → Output layout](../concepts/architecture.md#output-layout).
