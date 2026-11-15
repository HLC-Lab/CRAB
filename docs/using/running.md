# Running an experiment

Run an experiment from the CLI (for scripting) or from the web dashboard (`crab web`, for
interactive configuration). Both submit a job. You need:

- CRAB [installed](installation.md), your benchmarks [set up](installation.md#set-up-benchmarks-crab-setup)
  and a [preset](presets.md) for your cluster.
- With a Slurm preset, a system where `sbatch` is available and an allocation to draw from. A local
  preset (`"scheduler": "local"`) needs neither: it runs the job on your machine as a detached local
  job. See [Running without Slurm](running-without-slurm.md).

## CLI

```bash
crab run <config.json> -p <preset>
```

| Argument | Meaning |
|----------|---------|
| `<config.json>` | Path to the [experiment config](../reference/configuration.md). |
| `-p`, `--preset` | Preset name from the shared presets or your own override files. Optional. See [preset resolution](presets.md#selecting-your-preset). |
| `--log-level` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` (default `INFO`). |

For example:

```bash
crab run examples/leonardo/congestion/noise_heatmap.json -p leonardo --log-level DEBUG
```

To check a config without submitting it, run `crab validate <config.json> -p <preset>`. The
[examples](https://github.com/HLC-Lab/CRAB/tree/master/examples) hold configs you can run as they
are or adapt, starting with one that runs on a laptop. The [orchestrator](../glossary.md#orchestrator)
submits a job that runs the [worker](../glossary.md#worker); see
[how a run works](../concepts/architecture.md#the-two-phase-execution-model).

## Monitor a run

Track the job with:

- `squeue`: the Slurm queue.
- `crab status <id>`: the state of a Slurm or `local-<n>` job.
- `crab cancel <id>`: stops a job.
- `slurm_output.log`, `slurm_error.log` and the CRAB logs in the run's output directory: the job's
  output. See [Reading results](results.md).
