# Department cluster examples

Four configs for the computer science department's Slurm cluster, which the shipped `cluster_di`
preset describes. On that cluster they are ready to run. On another cluster they are templates:
edit `numnodes` and the time limit, and run them with your own preset (see
[Adapt an example to your cluster](../README.md#adapt-an-example-to-your-cluster)).

## What you need

- A Slurm cluster with at least 4 nodes (8 for `concurrent_collectives_stress.json`).
- Blink built on it (`crab setup`), and the wrappers folder.
- A preset. Use `-p cluster_di` on the department cluster.

None of the configs sets a partition or an account. Those belong to the preset.

## Files

`ping_pong.json` runs `pingpong_b` on one node with 2 ranks and 1 KiB messages. It measures the
latency between two ranks, with nothing else running. It is the smallest cluster example and a
good first job.

`pingpong_interference.json` allocates 4 nodes and splits them into a victim partition and an
aggressor partition of 2 nodes each. It has five experiments:

1. `01_baseline_pingpong_small`: ping-pong with 1 KiB messages on the victim nodes, alone.
2. `02_baseline_pingpong_large`: the same with 1 MiB messages, alone.
3. `03_interference_incast`: the 1 MiB ping-pong while an incast aggressor sends 64 KiB messages from the other 2 nodes.
4. `04_topology_ring`: a ring (`ring_bsnbr`) on all 4 nodes, to compare another traffic pattern.
5. `05_stress_a2a_vs_incast`: an all-to-all with 2 KiB messages against an incast aggressor.

Compare experiment 3 with experiment 2 to see what the aggressor costs the ping-pong.

`allreduce_interference.json` has the same shape with an allreduce (`allreduce_b`) as the victim.
Experiments 1 and 2 are the baselines (1 KiB and 1 MiB), experiment 3 adds the incast aggressor,
experiment 4 runs `ring_nb` on all 4 nodes, and experiment 5 is the all-to-all against incast.

`concurrent_collectives_stress.json` allocates 8 nodes and runs four apps on 2 nodes each, all at
the same time: two all-to-alls with 64 MiB messages, a ring with 32 MiB messages, and a barrier
loop that CRAB stops when the others finish. It loads the fabric with many large collectives at
once. `-grty 1` tells Blink to time each operation on its own.

The aggressors are not collected (`"collect": false`) and end with `"end": "f"`, which means CRAB
stops them when the victim finishes. The victims use `"end": ""`.

## Run

```bash
crab validate -p cluster_di examples/cluster_di/ping_pong.json
crab run -p cluster_di examples/cluster_di/ping_pong.json
```

`crab run` submits the job with `sbatch` and prints its id. `crab status <id>` shows where it is.
Results go to `data/cluster_di/`. See [Reading results](../../docs/using/results.md).
