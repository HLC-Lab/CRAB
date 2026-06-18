# Writing experiment configs

This page shows the common *patterns* for expressing a study — measuring an application, adding
interference, chaining runs. For the exhaustive field list (every `global_options` key, defaults,
allocation/scheduling rules), see the [Configuration schema](../reference/configuration.md).

Recall the [victim / aggressor](../glossary.md#victim) model: a **victim** is measured and runs to
completion (`end: ""`); an **aggressor** generates interference and is force-killed once the
victims finish (`end: "f"`).

## Pattern 1 — a single measured application (baseline)

The simplest experiment: one victim, collected, run on all nodes. Use this to establish a baseline
before introducing interference.

```json
{
  "global_options": { "name": "baseline", "numnodes": "4", "ppn": "1" },
  "experiments": {
    "a2a_alone": {
      "apps": {
        "0": { "path": "blink/a2a_comm_only.py", "args": "-msgsize 8192 -iter 1000",
               "collect": true, "start": "0", "end": "" }
      }
    }
  }
}
```

## Pattern 2 — victim vs aggressor (the core use case)

Run a measured victim alongside an aggressor to quantify interference. Use **partitioned**
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
        "0": { "path": "blink/a2a_comm_only.py", "args": "-msgsize 8192 -iter 1000",
               "collect": true, "start": "0", "end": "", "partition": "victim" },
        "1": { "path": "graph500/g500_wrapper.py", "args": "",
               "collect": false, "start": "0", "end": "f", "partition": "aggressor" }
      }
    }
  }
}
```

`mode: "interleaved"` interleaves the two partitions' nodes so the victim and aggressor share the
network fabric — exactly the contention you usually want to measure.

## Pattern 3 — sequential / dependent runs

Use `start: "sN"` to launch an application only **after application N has finished**. This chains
applications in sequence within one experiment.

```json
"apps": {
  "0": { "path": "blink/a2a_comm_only.py", "args": "-msgsize 8 -iter 1000",    "collect": true, "start": "0",  "end": "" },
  "1": { "path": "blink/a2a_comm_only.py", "args": "-msgsize 1024 -iter 1000", "collect": true, "start": "s0", "end": "" },
  "2": { "path": "blink/a2a_comm_only.py", "args": "-msgsize 65536 -iter 1000","collect": true, "start": "s1", "end": "" }
}
```

Here app 1 starts when app 0 ends, and app 2 when app 1 ends — a message-size sweep run one after
another. (This is the shape used in `examples/lorenzo/cong_analysis.json`.)

## Pattern 4 — timed termination

Give `end` a number to stop an application after a fixed number of seconds — useful for a
long-running aggressor you want bounded regardless of the victim:

```json
"1": { "path": "graph500/g500_wrapper.py", "args": "", "collect": false, "start": "5", "end": "60" }
```

This app starts 5 seconds in and is terminated after 60 seconds.

## Pattern 5 — several experiments in one run

Put multiple named experiments in one config to run a baseline and a co-run back-to-back; they
execute sequentially and each writes its own results. Use `local_options` to vary settings per
experiment.

```json
"experiments": {
  "baseline":     { "description": "victim alone",        "apps": { "0": { "path": "blink/a2a_comm_only.py", "args": "-msgsize 8192 -iter 1000", "collect": true, "start": "0", "end": "" } } },
  "with_aggressor": {
    "description": "victim + aggressor",
    "local_options": {
      "allocation": {
        "mode": "linear",
        "partitions": { "victim": { "share": 50 }, "aggressor": { "share": 50 } }
      }
    },
    "apps": {
      "0": { "path": "blink/a2a_comm_only.py", "args": "-msgsize 8192 -iter 1000", "collect": true,  "start": "0", "end": "",  "partition": "victim" },
      "1": { "path": "graph500/g500_wrapper.py",   "args": "",               "collect": false, "start": "0", "end": "f", "partition": "aggressor" }
    }
  }
}
```

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

- `minruns` / `maxruns` — the floor and ceiling on repetitions.
- `alpha` / `beta` — the confidence level and the relative width threshold (CI width `< beta × mean`).
- `convergeall` — require *every* metric to converge, not just those flagged `conv` in the wrapper.
- `timeout` — a wall-clock cap that ends the experiment regardless of convergence.

Start with the defaults (`minruns: 10`, `maxruns: 20`, `alpha`/`beta`: `0.05`) and tighten `beta`
if you need narrower intervals. See [Convergence](../glossary.md#convergence).

## Browse real examples

The `examples/` directory has working configs per system (`examples/leonardo/`, `examples/local/`,
`examples/cluster_di/`, `examples/lorenzo/`) covering baselines, interleaved tests, sequential
sweeps, and aggressor mixes — a good starting point to copy and adapt. Representative examples:

- `examples/leonardo/congestion/noise_heatmap.json` — victim-vs-aggressor sweep across collective
  types and message sizes (partitioned allocation).
- `examples/leonardo/congestion/layout_effect.json` — same workload with four different placement
  strategies (`local_options.allocation` replaced per experiment).
- `examples/leonardo/congestion/co_scheduling.json` — flat interleaved co-runs without partitions.
