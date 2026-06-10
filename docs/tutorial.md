# Tutorial: your first co-run experiment

This walkthrough takes you end to end: you'll measure an all-to-all communication benchmark
**running alone**, then **under interference** from a bursty "noise" aggressor, and finally
**visualize** the difference. It uses the bundled **Blink** suite (CRAB's fully-supported
benchmark), so everything here is runnable — not pseudocode.

By the end you'll have touched every part of the framework: `crab setup`, an experiment config
with a victim and an aggressor, `crab run`, the results layout, and `crab export`.

!!! note "Prerequisites"
    - CRAB [installed](using/installation.md) and its environment activated (`source .venv/bin/activate`).
    - Access to a **Slurm** cluster with an allocation you can submit to — `crab run` submits via
      `sbatch`, so this tutorial can't run on a plain laptop.
    - A [preset](using/presets.md) for your cluster. The commands below use `leonardo` as the
      example; **substitute your own preset name**. If your cluster isn't defined yet, create a
      preset first.

It helps to skim [System-dependent vs system-independent](concepts/system-dependent-vs-independent.md)
and the [victim/aggressor](glossary.md#victim) idea first, but you can follow along without them.

## Step 1 — Build the benchmark

CRAB needs the Blink binaries on your cluster. Run the setup wizard:

```bash
crab setup
```

Select the **Blink Suite**, then choose **Build from source** (the wizard clones and compiles it,
streaming the build log). When it finishes it writes a [receipt](glossary.md#receipt) to
`config/environments/blink.json` recording where the binaries live — the wrappers read this at run
time. See [Installation & benchmark setup](using/installation.md#set-up-benchmarks-crab-setup) for
the other strategies (e.g. pointing at an existing build).

## Step 2 — Write the experiment config

Create `tutorial.json`. It defines **two experiments** that run back-to-back: a `baseline` (the
victim alone) and `with_noise` (the same victim plus a bursty aggressor). They share one job, so
the comparison is apples-to-apples.

```json
{
  "global_options": {
    "name": "tutorial_a2a",
    "numnodes": "8",
    "ppn": "1",
    "allocationmode": "p",
    "partitionsplit": "50:50",
    "partitionlayout": "l",
    "minruns": "5",
    "maxruns": "10",
    "timeout": "1200.0",
    "outformat": "csv",
    "sbatch_directives": { "time": "00:20:00" }
  },
  "experiments": {
    "baseline": {
      "description": "All-to-all victim alone on half the nodes — the reference.",
      "apps": {
        "0": { "path": "blink/a2a_comm_only.py", "args": "-msgsize 64 -iter 2000",
               "collect": true, "start": "0", "end": "", "partition": 0 }
      }
    },
    "with_noise": {
      "description": "Same victim, now with a bursty all-to-all aggressor on the other half.",
      "apps": {
        "0": { "path": "blink/a2a_comm_only.py", "args": "-msgsize 64 -iter 2000",
               "collect": true, "start": "0", "end": "", "partition": 0 },
        "1": { "path": "blink/bursty_noise_a2a.py", "args": "0.001 0.01",
               "collect": false, "start": "0", "end": "f", "partition": 1 }
      }
    }
  }
}
```

What the key fields mean (full list in the [Configuration schema](reference/configuration.md)):

- **`numnodes: 8`, `allocationmode: "p"`, `partitionsplit: "50:50"`** — allocate 8 nodes, split into
  two equal [partitions](glossary.md#partition). The victim lives in `partition 0`, the aggressor in
  `partition 1`, so each gets 4 dedicated nodes.
- **App `0` — the [victim](glossary.md#victim).** `collect: true` (its metrics are recorded) and
  `end: ""` (CRAB waits for it to finish naturally). In `baseline` it's the only app, so its half
  runs undisturbed; the other half sits idle. That's deliberate — the victim runs on the *same*
  4 nodes in both experiments.
- **App `1` — the [aggressor](glossary.md#aggressor).** `collect: false` (not measured) and
  `end: "f"` (force-killed as soon as the victim finishes). Its two arguments are the noise timing
  (roughly: quiet interval, then burst length) — smaller quiet / longer burst = heavier
  interference.
- **`minruns`/`maxruns`** — repeat each experiment 5–10 times, stopping early once the victim's
  metrics [converge](glossary.md#convergence).

For more shapes (sequential sweeps, timed aggressors, multiple victims) see
[Writing experiment configs](using/writing-configs.md).

## Step 3 — Run it

```bash
crab run tutorial.json -p leonardo      # ← substitute your preset
```

CRAB's [orchestrator](glossary.md#orchestrator) prepares a timestamped output directory, writes the
resolved config there, generates a Slurm batch script, and submits it with `sbatch`. The job itself
runs the [worker](glossary.md#worker), which executes `baseline` then `with_noise` on the allocated
nodes. (See [Architecture](concepts/architecture.md#the-two-phase-execution-model) for the full
two-phase flow.)

Track it with the usual Slurm tools while it's queued/running:

```bash
squeue --me
```

and, once it starts, watch the logs inside the run directory (`slurm_output.log` / `slurm_error.log`).

## Step 4 — Read the results

Results land under `data/<system>/tutorial_a2a_<timestamp>/`:

```
data/leonardo/tutorial_a2a_2026-06-10_14-30-05-123456/
├── config.json              # exactly what ran (reproducible)
├── crab_job.sh              # the generated batch script
├── slurm_output.log
├── baseline/
│   └── data_app_0.csv        # victim metrics, no interference
└── with_noise/
    └── data_app_0.csv        # victim metrics, under interference
```

Each `data_app_0.csv` has a `run_id` column plus one column per metric the wrapper reported (Blink
records duration statistics). Compare the two with a couple of lines of pandas:

```python
import pandas as pd
base = pd.read_csv(".../baseline/data_app_0.csv")
noisy = pd.read_csv(".../with_noise/data_app_0.csv")
print("baseline  mean:", base.filter(like="Avg-Duration").mean().item())
print("with noise mean:", noisy.filter(like="Avg-Duration").mean().item())
```

If the aggressor is interfering, the victim's average duration under `with_noise` will be higher
than `baseline`. See [Reading results](using/results.md) for the full layout and CSV format.

## Step 5 — Visualize

Turn the run into a self-contained HTML dashboard:

```bash
crab export data/leonardo/tutorial_a2a_<timestamp>/ -o tutorial.html
```

Open `tutorial.html` in any browser — no server needed. Use the **Compare** tab to overlay the
`baseline` and `with_noise` distributions on one chart; the shift between them *is* the interference
you just measured.

## What you did, and where to go next

You built a benchmark, expressed a victim-vs-aggressor study, ran it through Slurm, and quantified
interference. From here:

- **Scale the study up** — the bundled `examples/leonardo/blink_noise_study.json` runs this idea
  across 16 experiments (message-size sweeps, different collectives, light/heavy noise, delayed
  starts, linear vs interleaved layouts). It's the natural next read.
- **Vary placement** — try `allocationmode: "i"` (interleaved) to make victim and aggressor share
  the fabric more aggressively. See [Writing experiment configs](using/writing-configs.md#choosing-an-allocation-mode).
- **Benchmark your own application** — teach CRAB to run something new in
  [Extending CRAB](extending/overview.md).
