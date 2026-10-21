# Running an experiment

Once CRAB is [installed](installation.md), your benchmarks are [set up](installation.md#set-up-benchmarks-crab-setup),
and you have a [preset](presets.md) for your cluster, you can run experiments two ways: the CLI
(for automation and scripting) or the web dashboard (`crab web`, for interactive configuration).

Both submit a job. With a Slurm preset, make sure you're on a system where `sbatch` is available
and you have an allocation to draw from. With a local preset (`"scheduler": "local"`) the job runs
on your machine as a detached local job, with no Slurm involved; see
[Running without Slurm](running-without-slurm.md).

## CLI

```bash
crab run <config.json> -p <preset>
```

| Argument | Meaning |
|----------|---------|
| `<config.json>` | Path to the [experiment config](../reference/configuration.md). |
| `-p`, `--preset` | Preset name from the shipped presets or your own presets file. Optional. See [preset resolution](presets.md#selecting-your-preset). |
| `--log-level` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` (default `INFO`). |

Example:

```bash
crab run examples/leonardo/congestion/noise_heatmap.json -p leonardo --log-level DEBUG
```

**What happens:** the [orchestrator](../glossary.md#orchestrator) prepares a timestamped output
directory, writes the resolved `config.json` and `environment.json` into it, generates a
`crab_job.sh` batch script, and submits it: with `sbatch` under a Slurm preset, or as a detached
local job (id `local-<n>`) under a local preset. The job itself runs the
[worker](../glossary.md#worker), which executes your experiments on the allocated nodes. See
[Architecture](../concepts/architecture.md#the-two-phase-execution-model) for the full flow.

**Monitoring a run:** the work happens inside a job, so track it with the usual tools:
`squeue` for the queue under Slurm, or `crab status <id>` for a Slurm or `local-<n>` job id. Stop
a job with `crab cancel <id>`. The job's `slurm_output.log` / `slurm_error.log` plus the CRAB logs
inside the run's output directory (see [Reading results](results.md)) hold its output.
