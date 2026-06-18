# Running an experiment

Once CRAB is [installed](installation.md), your benchmarks are [set up](installation.md#set-up-benchmarks-crab-setup),
and you have a [preset](presets.md) for your cluster, you can run experiments two ways: the CLI
(for automation and scripting) or the TUI (for interactive configuration).

Both submit a Slurm job — make sure you're on a system where `sbatch` is available and you have an
allocation to draw from.

## CLI

```bash
crab run <config.json> -p <preset>
```

| Argument | Meaning |
|----------|---------|
| `<config.json>` | Path to the [experiment config](../reference/configuration.md). |
| `-p`, `--preset` | Preset name from `config/presets.json`. Optional — see [preset resolution](presets.md#selecting-your-preset). |
| `--log-level` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` (default `INFO`). |

Example:

```bash
crab run examples/leonardo/congestion/noise_heatmap.json -p leonardo --log-level DEBUG
```

**What happens:** the [orchestrator](../glossary.md#orchestrator) prepares a timestamped output
directory, writes the resolved `config.json` and `environment.json` into it, generates a
`crab_job.sh` batch script, and submits it with `sbatch`. The job itself runs the
[worker](../glossary.md#worker), which executes your experiments on the allocated nodes. See
[Architecture](../concepts/architecture.md#the-two-phase-execution-model) for the full flow.

**Monitoring a run:** because the work happens inside a Slurm job, track it with the usual tools —
`squeue` for queue status, and the job's `slurm_output.log` / `slurm_error.log` plus the CRAB logs
inside the run's output directory (see [Reading results](results.md)).

## TUI

```bash
crab tui
```

The Textual interface organizes a run into four tabs:

| Tab | Purpose |
|-----|---------|
| **Applications** | Add and configure the applications to run (wrapper path, args, collect, start/end). |
| **Options** | Global options (nodes, ppn, allocation, convergence, …). |
| **Environment** | Select the preset / environment settings. |
| **Log** | Live output once a run is launched. |

Key bindings:

| Key | Action |
|-----|--------|
| `s` | Save the current configuration to a JSON file |
| `l` | Load a configuration from a JSON file |
| `space` | Launch the benchmark |
| `q` | Quit |

The TUI requires the optional `textual` dependencies; if they're missing, `crab tui` offers to
install them on first launch (see [Installation](installation.md#manual-install)).

Configurations you build in the TUI are saved as ordinary config files — interchangeable with
hand-written ones and runnable with `crab run`.
