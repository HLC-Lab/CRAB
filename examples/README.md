# CRAB examples

These are experiment configs you can read, copy and run. They go with the documentation:
[Writing experiment configs](../docs/using/writing-configs.md) explains the format and the
[Tutorial](../docs/tutorial.md) builds one study step by step. Here you run real ones.

The examples are not part of the pip package. Clone the repository to get them:

```bash
git clone https://github.com/HLC-Lab/CRAB
```

## Reading order

Start on your laptop, then run one application on a cluster, then co-run applications, then scale up, and last launch ready-made studies through SbatchMan.

| Step | Folder | What you learn | Runs where |
|---|---|---|---|
| 1 | [`local/`](local/README.md) | How a config, a run and its results look. | Your laptop, no Slurm. Ready to run. |
| 2 | [`cluster_di/`](cluster_di/README.md) | A single application alone, then the same one next to an aggressor, on a small Slurm cluster. | The computer science department cluster, preset `cluster_di`. Ready to run there, a template elsewhere. |
| 3 | [`leonardo/baselines/`](leonardo/baselines/README.md) | The reference time of each collective when it runs alone. | A Slurm cluster, 8 nodes. Template. |
| 4 | [`leonardo/congestion/`](leonardo/congestion/README.md) | A victim next to an aggressor: noise intensity, placement, network cards, co-scheduling. | A Slurm cluster, 2 to 8 nodes. Template. |
| 5 | [`leonardo/scale/`](leonardo/scale/README.md) | The same studies on 64 and 256 nodes. | A large Slurm cluster. Template. |
| 6 | [`blink-congestion-analysis/`](blink-congestion-analysis/README.md) | Two large congestion studies and a plotting script, from a research campaign. | A Slurm cluster, 8 to 16 nodes. Template. |
| 7 | [`sbatchman/`](sbatchman/README.md) | Ready-to-launch SbatchMan campaigns: a smoke test, and an all-to-all baseline against incast noise and against all-to-all noise. | A Slurm cluster with SbatchMan, 2 to 16 nodes. Ready to launch after `crab sbatchman-preset`. |

[`leonardo/`](leonardo/README.md) describes the three Leonardo folders together.

A ready-to-run example needs nothing but CRAB, Blink and the preset named in its README. A
template is a complete, valid config tuned to one machine. Copy it, then change the keys listed
below.

## Run one on your laptop

The only example that needs no cluster is `examples/local/sequential_barrier.json`. You need CRAB,
Open MPI (or MPICH) and Blink. If you have not built Blink yet, run the setup wizard, tick only
the Blink Suite, and choose the build-from-source strategy. [Installation](../docs/using/installation.md#set-up-benchmarks-crab-setup)
describes the wizard.

```bash
crab setup
crab validate -p local examples/local/sequential_barrier.json
crab run -p local examples/local/sequential_barrier.json
```

`crab run` prints the job id, such as `local-1`, and returns. The job keeps running in the
background. Follow it with `crab status local-1`, and list finished experiments with
`crab history -s local`. [Running without Slurm](../docs/using/running-without-slurm.md) describes
local jobs.

## Where the results go

A run writes a folder `<datapath>/<system>/<name>_<timestamp>/`. With the `local` preset and the
config above that is `data/local/sequential_barrier_<timestamp>/`, in the CRAB checkout. Each
experiment has one `data_app_<i>.csv` per collected app. [Reading results](../docs/using/results.md)
describes every file, and `crab export <folder> -o results.html` makes one HTML page you can send
to someone.

## Adapt an example to your cluster

A config says what to run. A preset says how your cluster runs it. Keep the two apart: the
examples hold no account, no partition and no module names.

1. Pick or write a preset for your cluster. [Configuring your cluster](../docs/using/presets.md)
   shows how. Your account and partition go in the preset's `sbatch` list, in `local/presets.json`.
   Run the example with `-p <your preset>`.
2. Change these keys in the config:

   | Key | Where | What to set |
   |---|---|---|
   | `numnodes` | `global_options` | The nodes you may use. A config with `partitions` splits them by `share`, so 8 nodes at 50/50 gives 4 and 4. |
   | `ppn` | `global_options`, or one app | Ranks per node. Keep it at 1 for network studies unless you want more ranks to compete. |
   | `sbatch_directives` | `global_options` | The time limit and job name. Add `--exclusive` if your site needs it. |
   | `allocation` | `global_options` | How nodes are laid out: `linear`, `interleaved`, `random` or `shared`. |
   | `path` | each app | The wrapper file. It is relative to the wrappers folder, such as `blink/alltoall/alltoall_comm_only.py`. |
   | `cpus`, `env` | an app, optional | Core lists and environment such as `UCX_NET_DEVICES`. The values in `corun_nics.json` fit one machine. |

3. Make sure every wrapper in the config exists in your wrappers folder (`crab wrappers list`)
   and that its benchmark is installed (`crab list-benchmarks`).

## Check a config before you submit it

`crab validate` runs the checks `crab run` makes before it submits, and submits nothing:

```bash
crab validate examples/cluster_di/ping_pong.json
crab validate -p <your preset> examples/leonardo/congestion/noise_heatmap.json
```

It prints `ok: <file>` and one line for each warning, and exits with 1 and a message when
something is wrong. With `-p`, it also checks the config against the preset: the scheduler, the
launcher and the number of hosts. Every config in this folder passes it. One example gives
warnings under its own preset, and its README explains them.

## Aggressors that burst

Several examples run an aggressor in bursts, to make the interference steady and repeatable. The
aggressor is Blink's `alltoall_man` or `incast_nb`, run with `-endl` (until it is stopped) and
two burst flags: `-blength` is how long a burst lasts and `-bpause` the pause between bursts, both
in seconds. `-msgsize 2097152` sends 2 MiB per message. In the config the aggressor has
`"collect": false` and `"end": "f"`, so CRAB stops it when the victim finishes. [The Blink
wrappers README](https://github.com/HLC-Lab/crab-wrappers/blob/main/blink/README.md) lists every
Blink flag.
