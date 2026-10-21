# Congestion

Five templates that put a victim next to an aggressor, or two apps next to each other, and
measure what the neighbour costs. Run [`../baselines/collectives.json`](../baselines/README.md)
first: it gives the time of each collective when nothing else runs.

All five need Blink and the wrappers. Except for `corun_nics.json`, they need 8 nodes and name no
network card, so they run on any Slurm cluster once `numnodes`, the time limit and your preset
fit. Where a file has a victim and an aggressor, the victim is collected and ends by itself, and
the aggressor has `"collect": false` and `"end": "f"`, so CRAB stops it when the victim finishes.
Unless the text below says otherwise, the victim starts 3 seconds after the aggressor
(`"start": "3"`), so the aggressor is already running when the victim starts. The aggressors send
2 MiB messages in bursts. See [Aggressors that burst](../../README.md#aggressors-that-burst) for
the flags.

## Files

`noise_heatmap.json` sweeps the victim's message size against the noise intensity. The victim is
a communication-only all-to-all with 8 B, 64 B, 4 KiB or 1 MiB messages. For each size there are
four experiments: `baseline` (no aggressor), `light` (bursts of 1 ms, pauses of 50 ms), `medium`
(10 ms and 1 ms) and `heavy` (50 ms and 0.1 ms). That makes 16 experiments. The aggressor is
`alltoall_man`. Plot the slowdown over the two axes to get a heatmap. The nodes are split 50/50
into a victim and an aggressor partition, laid out in contiguous blocks.

`victim_sweep.json` changes the victim and the kind of noise, at one intensity (10 ms bursts, 1 ms
pauses). The victims are an all-to-all, an incast and an allgather. Each runs alone, against an
all-to-all aggressor and against an incast aggressor: nine experiments. Two more use the
all-to-all victim to look at timing. In `04_a2a_vs_noise_delayed` the aggressor starts 2 seconds
after the victim, which runs 2,000,000 iterations. Blink keeps the last 1000 iterations, so the
samples all come after the aggressor started. In `05_a2a_vs_noise_timed` the aggressor stops after
3 seconds and the samples are the victim after the noise ended.

`layout_effect.json` keeps the workload and changes where the nodes sit. A victim (all-to-all with
64 B messages) runs against an all-to-all aggressor on 4 and 4 nodes. `01_victim_alone` is the
reference. Then the same pair runs with a linear layout (blocks), an interleaved layout with
stride 1 and with stride 2, and a random layout with a fixed seed. Each experiment replaces the
allocation with `local_options`. Differences between the layouts show how much placement matters.

`co_scheduling.json` runs two collectives at the same time on interleaved nodes, 4 each, with no
aggressor: both are collected and both are measured. `01` and `02` are references (the
all-to-all alone on 8 nodes and on 4 interleaved nodes). Experiments 03 to 05 run two
all-to-alls with 64 B messages, with 1 MiB messages and with one of each. 06 runs an all-to-all
next to an incast, 07 next to an allgather, and 08 repeats 03 on randomly placed nodes (seed 7).

`corun_nics.json` studies the network card. It is the most machine specific file. Two nodes run a
ping-pong (8 B messages, 1 rank per node, core 0, card `mlx5_0`) next to an all-to-all aggressor
(4 ranks per node, cores 16 to 19). Experiment 01 is the ping-pong alone. In 02 the aggressor
uses another card, `mlx5_1`. In 03 it uses `mlx5_0`, the victim's card. Compare 01 with 02 to see
what the aggressor costs when the cards differ, and 02 with 03 to see what sharing a card adds. The card names
(`UCX_NET_DEVICES`) and the core lists (`cpus`) fit a Leonardo node. On another cluster, list your
cards and the cores close to each one before you edit them (`/sys/class/infiniband/<card>/device/local_cpulist`).
Slurm needs `--exclusive` for `cpus`, and the config sets it.

## Warnings

`crab validate -p leonardo examples/leonardo/congestion/corun_nics.json` gives three warnings,
one for each experiment: the launcher flag `--cpu-bind=socket` is replaced by CRAB's core
binding for apps that set `cpus`. This is intended. The `leonardo` preset passes that flag to
every `srun`, and in this file CRAB binds each app to its own cores instead. The other four
files give no warning.

## Run

```bash
crab validate -p leonardo examples/leonardo/congestion/noise_heatmap.json
crab run -p leonardo examples/leonardo/congestion/noise_heatmap.json
```
