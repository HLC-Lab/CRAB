# Blink congestion analysis

Two large congestion studies built on Blink, and the script that plotted a campaign like them. The
configs are valid and complete, but they are templates: they come from a research campaign and
their sizes (8 and 16 nodes, a 20 minute limit, one run per experiment) fit that campaign. Copy
one and change `numnodes`, the time limit and the message sizes for your cluster, as
[Adapt an example to your cluster](../README.md#adapt-an-example-to-your-cluster) describes.

## What you need

- A Slurm cluster with 8 nodes (16 for `a2a_vs_a2a_16mib.json`), Blink built on it
  (`crab setup`) and the wrappers folder.
- A preset. The configs name no partition or account. Pass your own with `-p`.
- `--exclusive` is set in both configs, so your site must allow it.

Check a config with `crab validate -p <your preset> <file>` before you submit it.

## Files

`incast_burst_grid.json` asks how the rhythm of a bursty aggressor changes the slowdown of an
all-to-all. The 8 nodes are split in two interleaved halves. On one half a victim runs an
all-to-all (`alltoall_comm_only`) at 8 message sizes, from 8 B to 16 MiB, one size after the
other (each app starts when the previous one ends). On the other half an incast aggressor
(`incast_nb`) sends 2 MiB messages in bursts. The file has nine experiments, one for each
combination of burst length (1 ms, 10 ms, 100 ms) and pause between bursts (1 microsecond,
100 microseconds, 10 ms). An experiment name such as `cong_inc_0.0001_0.01` reads
`cong_inc_<pause>_<length>`, in seconds. Each experiment runs once, with 1000 iterations per size.
The victim starts 3 seconds after the aggressor.

`a2a_vs_a2a_16mib.json` puts a large all-to-all victim next to a steady all-to-all aggressor. It
uses 16 nodes with 4 ranks per node. The victim gets 35 percent of the nodes and the aggressor
65 percent, interleaved. Both send 16 MiB messages. The victim runs 1000 iterations and the
aggressor runs until CRAB stops it. There is one experiment and one run. It measures a congested
all-to-all with no bursts, and you can compare it with an all-to-all alone on the same nodes.

`blink_plotter.py` is the script that drew the heatmaps of a campaign like `incast_burst_grid.json`.
It is not ready to run on a current results folder. It reads a hand-built file `data/description.csv`
with the columns `path`, `system`, `extra` and `numnodes`, and it finds the experiments by label
strings such as `All-to-All Inc-Congested 0.01 0.1` in the `extra` column. CRAB now writes
`metadata.csv` with other columns (see [Reading results](../../docs/using/results.md#the-registry-metadatacsv)),
and `extrainfo` in these configs does not use those labels. The script also needs `pandas`,
`seaborn` and `matplotlib`, and a `plots/` folder. Keep it as a reference for how the figures
were made. To plot your own results, read the `data_app_<i>.csv` files with `pandas`, as the page
above shows, or open them with `crab export`.

## Run

```bash
crab validate -p <your preset> examples/blink-congestion-analysis/incast_burst_grid.json
crab run -p <your preset> examples/blink-congestion-analysis/incast_burst_grid.json
```
