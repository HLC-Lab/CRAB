# Tutorial: your first co-run experiment

Measure an all-to-all communication benchmark running alone, then under interference from a bursty
"noise" aggressor, and visualize the difference. The tutorial uses the bundled Blink suite, CRAB's
fully supported benchmark, so every command runs as written.

!!! note "Prerequisites"
    - CRAB [installed](using/installation.md) and its environment activated (`source .venv/bin/activate`).
    - Access to a Slurm cluster with an allocation you can submit to. `crab run` submits via
      `sbatch`, so the tutorial cannot run on a plain laptop.
    - A [preset](using/presets.md) for your cluster. The commands use `leonardo` as the example:
      replace it with your own preset name. If your cluster has no preset yet, create one first.
    - Your project account in that preset. The shared presets carry the mark
      `--account=<YOUR_ACCOUNT>`, and `crab run` refuses to start until you set it: write an
      override file (`local/presets/<name>.json` in a checkout) with your account
      ([how](using/presets.md#your-overrides)).

Optional reading: [System-dependent vs system-independent](concepts/system-dependent-vs-independent.md)
and the [victim/aggressor](glossary.md#victim) idea.

## Step 1: Build the benchmark

CRAB needs the Blink binaries on your cluster. Run the setup wizard:

```bash
crab setup
```

Then answer the prompts:

1. Choose "Install or configure a supported benchmark".
2. Press Space to untick every suite except the Blink Suite. The list pre-selects each suite that
   is not configured yet.
3. At the strategy prompt, type `4` (build from source).
4. At the modules prompt, type the cluster modules to load before building, such as
   `module load gcc openmpi` (add CMake if it is a module on your cluster). Press Enter to load none.

The wizard clones and compiles Blink, streaming the build log. It then writes a
[receipt](glossary.md#receipt) to `local/receipts/blink.json` that records where the binaries live.
The wrappers read it at run time. For the other strategies, such as pointing at an existing build,
see [Installation & benchmark setup](using/installation.md#set-up-benchmarks-crab-setup).

## Step 2: Write the experiment config

Create `tutorial.json`. It defines two experiments that run back to back: `baseline` (the victim
alone) and `with_noise` (the same victim plus a bursty aggressor). They share one job, so the
comparison is like for like.

```json
{
  "global_options": {
    "name": "tutorial_a2a",
    "numnodes": "8",
    "ppn": "1",
    "allocation": {
      "mode": "linear",
      "partitions": {
        "victim":    { "share": 50 },
        "aggressor": { "share": 50 }
      }
    },
    "minruns": "5",
    "maxruns": "10",
    "timeout": "1200.0",
    "outformat": "csv",
    "sbatch_directives": { "time": "00:20:00" }
  },
  "experiments": {
    "baseline": {
      "description": "All-to-all victim alone on half the nodes, the reference.",
      "apps": {
        "0": { "path": "blink/alltoall/alltoall_comm_only.py", "args": "-msgsize 64 -iter 2000",
               "collect": true, "start": "0", "end": "", "partition": "victim" }
      }
    },
    "with_noise": {
      "description": "Same victim, now with a bursty all-to-all aggressor on the other half.",
      "apps": {
        "0": { "path": "blink/alltoall/alltoall_comm_only.py", "args": "-msgsize 64 -iter 2000",
               "collect": true, "start": "0", "end": "", "partition": "victim" },
        "1": { "path": "blink/alltoall/alltoall_man.py", "args": "-endl -msgsize 2097152 -blength 0.01 -bpause 0.001",
               "collect": false, "start": "0", "end": "f", "partition": "aggressor" }
      }
    }
  }
}
```

The key fields (the [Configuration schema](reference/configuration.md) lists all of them):

- `numnodes: 8` and the `allocation` block: 8 nodes, split into two equal
  [partitions](glossary.md#partition) named `"victim"` and `"aggressor"`, with 4 dedicated nodes
  each. `mode: "linear"` gives each partition a contiguous block. On a smaller cluster, lower
  `numnodes`: with 4 nodes the 50/50 split gives 2 and 2.
- App `0`, the [victim](glossary.md#victim): `collect: true` records its metrics and `end: ""` makes
  CRAB wait for it to finish. In `baseline` it is the only app and the other half stays idle. The
  victim runs on the same 4 nodes in both experiments.
- App `1`, the [aggressor](glossary.md#aggressor): `collect: false` (not measured) and `end: "f"`
  (force-killed as soon as the victim finishes). Its `args` make Blink's `alltoall_man` run until
  it is stopped (`-endl`), in bursts of 10 ms (`-blength 0.01`) separated by 1 ms pauses
  (`-bpause 0.001`). Each burst sends 2 MiB to every other rank (`-msgsize 2097152`). Longer bursts
  and shorter pauses mean heavier interference.
- `minruns` and `maxruns`: run each experiment until 5 runs have succeeded (at most 10 runs in
  all), and stop as soon as the victim's metrics [converge](glossary.md#convergence) after that.

!!! note "`path` points to the wrapper, not the binary"
    `blink/alltoall/alltoall_comm_only.py` is the [wrapper](glossary.md#wrapper) file, given
    relative: CRAB looks for it in `local/wrappers/` first, then in each folder of
    `CRAB_PATH_WRAPPERS`, or in `wrappers/` when that is unset. It is not an absolute path and not
    the benchmark executable. This keeps the config
    [system-independent](concepts/system-dependent-vs-independent.md): the wrapper looks up the
    binary location from the [receipt](glossary.md#receipt) at run time, so the same config file
    runs on any cluster where you ran `crab setup`.

For more shapes (sequential sweeps, timed aggressors, multiple victims) see
[Writing experiment configs](using/writing-configs.md).

!!! tip "Don't want to hand-write JSON?"
    The web dashboard (`crab web`) can build a config for you and run it. See
    [Installation](using/installation.md) for how to start it.

## Step 3: Run it

```bash
crab run tutorial.json -p leonardo      # ← substitute your preset
```

CRAB submits the job with `sbatch`. The job runs `baseline` then `with_noise` on the allocated
nodes. [How a run works](concepts/architecture.md#the-two-phase-execution-model) explains the two
phases.

While the job is queued or running, check it with:

```bash
squeue --me
```

Once it starts, watch the logs `slurm_output.log` and `slurm_error.log` inside the run directory.

## Step 4: Read the results

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

## Step 5: Visualize

Turn the run into a self-contained HTML dashboard:

```bash
crab export data/leonardo/tutorial_a2a_<timestamp>/ -o tutorial.html
```

Open `tutorial.html` in any browser. No server is needed. Use the Compare tab to overlay the
`baseline` and `with_noise` distributions on one chart; the shift between them is the interference
you measured.

## Next steps

- Scale the study up. `examples/leonardo/congestion/victim_sweep.json` runs this idea across
  11 experiments (different victim collectives against A2A and incast noise at a single intensity,
  plus delayed and timed aggressors). `examples/leonardo/congestion/noise_heatmap.json` sweeps
  message sizes against light, medium and heavy noise.
  `examples/leonardo/congestion/layout_effect.json` isolates the effect of node placement. The
  [examples guide](https://github.com/HLC-Lab/CRAB/tree/master/examples) lists every example in
  reading order.
- Vary placement. Try `mode: "interleaved"` in the `allocation` block to make victim and
  aggressor share the fabric more aggressively. See
  [Writing experiment configs](using/writing-configs.md#choosing-an-allocation-mode).
- Benchmark your own application. [Extending CRAB](extending/overview.md) shows how to teach CRAB to
  run something new.
