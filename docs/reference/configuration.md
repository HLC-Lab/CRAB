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

A config has two top-level keys: `global_options` and `experiments`.

```json
{
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

!!! note "Legacy single-experiment form"
    A config may instead use a top-level `applications` block in place of `experiments`. CRAB
    wraps it automatically into a single experiment named `default_ex`. New configs should use the
    explicit `experiments` form.

## `global_options`

Settings applied to the whole run. An experiment can override most of these in its own
`local_options` (see [below](#per-experiment-overrides-local_options)).

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `numnodes` | int | **required** | Total nodes to allocate for the Slurm job. |
| `ppn` | int | `1` | Processes per node. Drives the launcher's total task count. |
| `name` | string | `""` | Human-readable run name; prefixes the output directory (`<name>_<timestamp>`). |
| `datapath` | string | `<CRAB_ROOT>/data` | Root directory for results. |
| `allocationmode` | `l` \| `i` \| `p` | `l` | Node-to-app mapping: **l**inear, **i**nterleaved, **p**artitioned. See [Allocation](#allocation-fields). |
| `allocationsplit` | string | `e` | How nodes are divided among apps. See [Allocation](#allocation-fields). |
| `partitionsplit` | string | `100` | (mode `p`) Size split between partitions, e.g. `50:50`. |
| `partitionlayout` | `l` \| `i` | `l` | (mode `p`) Whether partitions take contiguous (`l`) or interleaved (`i`) nodes. |
| `minruns` | int | `10` | Minimum runs before convergence is checked. |
| `maxruns` | int | `20` | Hard cap on runs. |
| `timeout` | float | `1200.0` | Wall-clock budget for the experiment, in seconds. |
| `convergeall` | bool | `false` | If true, every metric must converge; otherwise only metrics flagged `conv` in the wrapper. |
| `alpha` | float | `0.05` | Confidence-interval significance level. |
| `beta` | float | `0.05` | Convergence threshold: CI width must fall below `beta × mean`. |
| `outformat` | `csv` \| `hdf` | `csv` | Output file format. |
| `retain_files` | bool | `true` | Keep per-run working directories. If `false`, successful runs' scratch dirs are deleted. |
| `tags` | string | `none` | Free-form label recorded in the run registry (`metadata.csv`). |
| `walltime` | string | `00:10:00` | Base Slurm `--time` value (overridable via `sbatch_directives`). |
| `extrainfo` | string | `job` | Short token used to build the Slurm job name. |
| `sbatch_directives` | list \| dict | `[]` | User Slurm directives. See [sbatch directives](#sbatch-directives). |

!!! info "`ppn` is global"
    The processes-per-node value is read from `global_options` and applied uniformly; it reflects
    the physical allocation and is not overridden per experiment.

### Allocation fields

`allocationmode` selects the strategy that maps the job's nodes onto the applications:

- **`l` — linear:** each application gets a contiguous block of nodes.
- **`i` — interleaved:** nodes are dealt round-robin across applications.
- **`p` — partitioned:** nodes are first split into *partitions* (typically victims vs
  aggressors, by each app's [`partition`](#the-apps-block)), then apps within each partition are
  placed by a per-partition sub-rule.

`allocationsplit` controls the division among apps (modes `l`/`i`) or within partitions (mode `p`):

- `e` — equal split across applications.
- `50:50`, `70:30`, … — explicit percentages per application (must not exceed 100).
- For mode `p`, the value uses `-` to separate per-partition rules, e.g. `100-100` (each partition
  shares all its nodes among its apps) — see the partitioned example below.

`partitionsplit` (mode `p` only) sizes the partitions themselves: `50:50`, or `e` to auto-size by
the number of distinct partition IDs in use.

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
say, `allocationmode` or `timeout` between experiments in the same run.

### The `apps` block

A dictionary keyed by **numeric string IDs** (`"0"`, `"1"`, …). Each value describes one
application:

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `path` | string | **required** | Path to the [wrapper](../glossary.md#wrapper) module. If relative, resolved against `CRAB_PATH_WRAPPERS`. |
| `args` | string | `""` | Command-line arguments passed to the executable. |
| `collect` | bool | `false` | Whether to parse and store this app's metrics. |
| `start` | string | `"0"` | When to start the app. See [Scheduling](#scheduling-start-and-end). |
| `end` | string | `""` | When to stop the app. See [Scheduling](#scheduling-start-and-end). |
| `partition` | int | auto | (mode `p`) Which allocation partition this app belongs to. Defaults to `0` if `collect` is true, else `1`. |

!!! tip "Extra keys become wrapper attributes"
    Any key in an app entry that is **not** one of the reserved keys above
    (`path`, `args`, `collect`, `start`, `end`, `partition`) is injected as an attribute onto the
    wrapper instance. This lets a wrapper accept custom configuration straight from the JSON
    without framework changes.

## Scheduling (`start` and `end`)

The `start` and `end` strings encode the victim/aggressor model and timed/sequential execution.

**`start` — when the app launches:**

| Value | Meaning |
|-------|---------|
| `"0"` or a number | Delay in seconds from the start of the run before launching. |
| `"sN"` | Start only **after application N has finished** (a dependency, enabling sequential chains). |

**`end` — when the app is stopped:**

| Value | Meaning | Role |
|-------|---------|------|
| `""` (empty) | Wait for the app to finish on its own. | [Victim](../glossary.md#victim) |
| `"f"` | Force-terminate once all non-`f` apps have finished. | [Aggressor](../glossary.md#aggressor) |
| a number | Terminate after that many seconds. | Timed |

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
    `--nodes` and `--ntasks-per-node` are computed by the framework from `numnodes`/`ppn` and
    **cannot** be overridden — user attempts are ignored with a warning. Overriding `--output`/
    `--error` is allowed but warned about, since it redirects CRAB's standard logs. Directives
    containing newlines are rejected. System-level directives from the preset are merged in with
    lower priority than your config's directives.

## Worked example: partitioned victim vs aggressor

```json
{
  "global_options": {
    "name": "congestion_study",
    "numnodes": "8",
    "ppn": "1",
    "allocationmode": "p",
    "partitionsplit": "50:50",
    "allocationsplit": "100-100",
    "partitionlayout": "i",
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
          "path": "a2a_comm_only.py",
          "args": "-msgsize 8192 -iter 1000",
          "collect": true,
          "start": "0",
          "end": "",
          "partition": 0
        },
        "1": {
          "path": "others/g500.py",
          "args": "",
          "collect": false,
          "start": "0",
          "end": "f",
          "partition": 1
        }
      }
    }
  }
}
```

This allocates 8 nodes, splits them 50/50 into two interleaved partitions, runs an all-to-all
*victim* (collected) in partition 0 against a Graph500 *aggressor* (force-killed when the victim
finishes) in partition 1, and repeats between 10 and 30 runs until the victim's convergence-target
metric stabilizes.

For where the resulting files land, see [Architecture → Output layout](../concepts/architecture.md#output-layout).
