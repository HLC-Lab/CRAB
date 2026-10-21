# Writing experiment configs

This page shows the common *patterns* for expressing a study: measuring an application, adding
interference, chaining runs. For the exhaustive field list (every `global_options` key, defaults,
allocation/scheduling rules), see the [Configuration schema](../reference/configuration.md).

Recall the [victim / aggressor](../glossary.md#victim) model: a victim is measured and runs to
completion (`end: ""`); an aggressor generates interference and is force-killed once the
victims finish (`end: "f"`).

## Pattern 1: a single measured application (baseline)

The simplest experiment: one victim, collected, run on all nodes. Use this to establish a baseline
before introducing interference.

```json
{
  "global_options": { "name": "baseline", "numnodes": "4", "ppn": "1" },
  "experiments": {
    "a2a_alone": {
      "apps": {
        "0": { "path": "blink/alltoall/alltoall_comm_only.py", "args": "-msgsize 8192 -iter 1000",
               "collect": true, "start": "0", "end": "" }
      }
    }
  }
}
```

## Pattern 2: victim vs aggressor (the core use case)

Run a measured victim alongside an aggressor to quantify interference. Use partitioned
allocation so each gets its own nodes, and mark the aggressor `end: "f"` so it's killed as soon as
the victim finishes.

```json
{
  "global_options": {
    "name": "interference",
    "numnodes": "8", "ppn": "1",
    "allocation": {
      "mode": "interleaved",
      "partitions": {
        "victim":    { "share": 50 },
        "aggressor": { "share": 50 }
      }
    }
  },
  "experiments": {
    "a2a_vs_g500": {
      "apps": {
        "0": { "path": "blink/alltoall/alltoall_comm_only.py", "args": "-msgsize 8192 -iter 1000",
               "collect": true, "start": "0", "end": "", "partition": "victim" },
        "1": { "path": "graph500/g500_wrapper.py", "args": "",
               "collect": false, "start": "0", "end": "f", "partition": "aggressor" }
      }
    }
  }
}
```

`mode: "interleaved"` interleaves the two partitions' nodes so the victim and aggressor share the
network fabric, which is exactly the contention you usually want to measure.

## Pattern 3: sequential / dependent runs

Use `start: "sN"` to launch an application only after application N has finished. This chains
applications in sequence within one experiment.

```json
"apps": {
  "0": { "path": "blink/alltoall/alltoall_comm_only.py", "args": "-msgsize 8 -iter 1000",    "collect": true, "start": "0",  "end": "" },
  "1": { "path": "blink/alltoall/alltoall_comm_only.py", "args": "-msgsize 1024 -iter 1000", "collect": true, "start": "s0", "end": "" },
  "2": { "path": "blink/alltoall/alltoall_comm_only.py", "args": "-msgsize 65536 -iter 1000","collect": true, "start": "s1", "end": "" }
}
```

Here app 1 starts when app 0 ends, and app 2 when app 1 ends, a message-size sweep run one after
another. (This is the shape used in `examples/blink-congestion-analysis/incast_burst_grid.json`.)

All three run on the same nodes: a chained app reuses the nodes of the app it waits for, so only
app 0 takes a share of the allocation. To start an app when another one ends but on other
nodes, give it a different `partition`. Two apps cannot both start after the same app on its
nodes (they would share them at the same time); CRAB reports that before submitting. Details:
[Scheduling](../reference/configuration.md#scheduling-start-and-end).

## Pattern 4: timed termination

Give `end` a number to stop an application after a fixed number of seconds, which is useful for a
long-running aggressor you want bounded regardless of the victim:

```json
"1": { "path": "graph500/g500_wrapper.py", "args": "", "collect": false, "start": "5", "end": "60" }
```

This app starts 5 seconds in and is terminated after 60 seconds.

## Pattern 5: several experiments in one run

Put multiple named experiments in one config to run a baseline and a co-run back-to-back; they
execute sequentially and each writes its own results. Use `local_options` to vary settings per
experiment.

```json
"experiments": {
  "baseline":     { "description": "victim alone",        "apps": { "0": { "path": "blink/alltoall/alltoall_comm_only.py", "args": "-msgsize 8192 -iter 1000", "collect": true, "start": "0", "end": "" } } },
  "with_aggressor": {
    "description": "victim + aggressor",
    "local_options": {
      "allocation": {
        "mode": "linear",
        "partitions": { "victim": { "share": 50 }, "aggressor": { "share": 50 } }
      }
    },
    "apps": {
      "0": { "path": "blink/alltoall/alltoall_comm_only.py", "args": "-msgsize 8192 -iter 1000", "collect": true,  "start": "0", "end": "",  "partition": "victim" },
      "1": { "path": "graph500/g500_wrapper.py",   "args": "",               "collect": false, "start": "0", "end": "f", "partition": "aggressor" }
    }
  }
}
```

## Pattern 6: apps sharing nodes, with different ranks per node

Set `allocation.mode` to `"shared"` and every app runs on all the nodes at the same time, so they
compete inside each node. Each app can have its own `ppn`: here the victim runs one rank per node
and the aggressor eight, on cores of their own.

```json
{
  "global_options": {
    "name": "intra_node",
    "numnodes": "2", "ppn": "1",
    "allocation": { "mode": "shared", "cores": "disjoint" },
    "sbatch_directives": ["--exclusive"]
  },
  "experiments": {
    "a2a_vs_a2a": {
      "apps": {
        "0": { "path": "blink/alltoall/alltoall_comm_only.py", "args": "-msgsize 8192 -iter 1000",
               "collect": true, "end": "" },
        "1": { "path": "blink/alltoall/alltoall_comm_only.py", "args": "-msgsize 8192 -iter 1000",
               "end": "f", "ppn": "8" }
      }
    }
  }
}
```

The batch job asks Slurm for 9 tasks per node (1 + 8), and under `srun` each app is started with
flags that keep it on its own cores; see
[Shared nodes under `srun`](../reference/configuration.md#shared-nodes-under-srun).

## One network interface per app

To make each app use a different network card, set the interface in the app's `env` and pin the
app to cores close to that card. With Open MPI or any other UCX-based MPI, `UCX_NET_DEVICES`
takes `<hca>:<port>`:

```json
"apps": {
  "0": { "path": "blink/alltoall/alltoall_comm_only.py", "collect": true, "end": "",
         "cpus": "0-3",   "env": { "UCX_NET_DEVICES": "mlx5_0:1" } },
  "1": { "path": "blink/alltoall/alltoall_comm_only.py", "end": "f",
         "cpus": "16-19", "env": { "UCX_NET_DEVICES": "mlx5_1:1" } }
}
```

Choose `cpus` from the cores that sit on the same NUMA node as the card:

- the list is in `/sys/class/infiniband/<hca>/device/local_cpulist` (for example
  `cat /sys/class/infiniband/mlx5_0/device/local_cpulist`);
- `lstopo` shows the same layout as a diagram, and on GPU nodes `nvidia-smi topo -m` shows which
  cores are close to which GPU and card.

Under Slurm, `cpus` needs `--exclusive` among the `sbatch_directives`. The `env` of each app is
recorded in the job's `crab_run.json` next to its launch, so the results show which interface
each app used. A complete config is `examples/leonardo/congestion/corun_nics.json`.

## One network interface per rank

To spread the ranks of one app over several cards, list the cards in the app's `nics` instead of
setting `UCX_NET_DEVICES` in `env`. Rank `i` on a node uses `nics[i % len(nics)]`. For an app with
4 ranks per node on a node with 4 cards, where cores 0-3 sit next to `mlx5_0`, 4-7 next to
`mlx5_1`, and so on, bind one core per rank:

```json
"0": {
  "path": "blink/alltoall/alltoall_comm_only.py", "collect": true, "end": "",
  "ppn": 4,
  "cpus": "0,4,8,12",
  "nics": ["mlx5_0:1", "mlx5_1:1", "mlx5_2:1", "mlx5_3:1"]
}
```

Both lists are in rank order, so rank 0 runs on core 0 and uses `mlx5_0:1`, rank 1 on core 4 with
`mlx5_1:1`, and so on. Pick each core from the cards' `local_cpulist`, as above. Under Slurm,
`cpus` needs `--exclusive` among the `sbatch_directives`.

To check the result, open `bindings_app_<id>.log` in the experiment directory. Next to the
launcher's binding report, it has one line per rank:

```text
crab: node01 local rank 0 -> UCX_NET_DEVICES=mlx5_0:1
crab: node01 local rank 1 -> UCX_NET_DEVICES=mlx5_1:1
```

`nics` and `env.UCX_NET_DEVICES` on the same app are refused. The data directory must be on a
shared filesystem; see [NICs per rank](../reference/configuration.md#nics-per-rank-nics).

## Choosing an allocation mode

| Mode | `allocation.mode` | Use when |
|------|--------------------|----------|
| Linear | `"linear"` (default) | Each app should get a contiguous block of nodes. |
| Interleaved | `"interleaved"` | Apps should be spread round-robin across nodes (more fabric sharing). Add `"stride"` to assign multiple nodes per turn. |
| Random | `"random"` | Shuffle the node list before splitting (add `"seed"` for reproducibility). |
| Partitioned | any mode + `"partitions"` | You want named victim/aggressor groups, each with their own `share`. |

For partitioned allocation, each app's `partition` field must match a key in `allocation.partitions`.
See [Allocation fields](../reference/configuration.md#allocation-fields).

## Tuning convergence

CRAB repeats each experiment until its convergence-target metrics stabilize. The relevant knobs:

- `minruns` / `maxruns`: the floor and ceiling on repetitions.
- `alpha` / `beta`: the confidence level and the relative width threshold (CI width `< beta × mean`).
- `convergeall`: require *every* metric to converge, not just those flagged `conv` in the wrapper.
- `timeout`: a wall-clock cap that ends the experiment regardless of convergence.

Start with the defaults (`minruns: 10`, `maxruns: 20`, `alpha`/`beta`: `0.05`) and tighten `beta`
if you need narrower intervals. See [Convergence](../glossary.md#convergence), and
[Methodology](../concepts/methodology.md#how-many-runs-the-convergence-check) for what the check
computes.

## Browse real examples

The `examples/` directory has working configs per system (`examples/local/`, `examples/cluster_di/`,
`examples/leonardo/`, `examples/blink-congestion-analysis/`) covering baselines, interleaved tests,
sequential sweeps, and aggressor mixes. Copy one and adapt it. The
[examples guide](https://github.com/HLC-Lab/CRAB/tree/master/examples) says in which order to
read them, which one runs on a laptop, and how to adapt one to your cluster. Representative examples:

- `examples/leonardo/congestion/noise_heatmap.json`: A2A victim-vs-aggressor sweep across message
  sizes and noise intensities (partitioned allocation).
- `examples/leonardo/congestion/layout_effect.json`: same workload with four different placement
  strategies (`local_options.allocation` replaced per experiment).
- `examples/leonardo/congestion/co_scheduling.json`: flat interleaved co-runs without partitions.
