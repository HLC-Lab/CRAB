# Leonardo examples

Configs written for Leonardo, the Slurm cluster at CINECA, and for the shipped `leonardo` preset.
They are templates: they are complete and valid, but the node counts, the time limits and (in one
file) the network card names fit Leonardo. To use one on your own cluster, follow [Adapt an example to your cluster](../README.md#adapt-an-example-to-your-cluster).

| Folder | What it holds | Nodes |
|---|---|---|
| [`baselines/`](baselines/README.md) | Each collective alone, for several message sizes. The reference for the other folders. | 8 |
| [`congestion/`](congestion/README.md) | A victim next to an aggressor. Noise intensity, node placement, network cards and co-scheduling. | 2 to 8 |
| [`scale/`](scale/README.md) | Collectives, timing patterns and interference on a large allocation. | 64 and 256 |

## What you need

- A Slurm cluster. Leonardo itself works after the two edits below.
- Blink built on the cluster (`crab setup`) and the wrappers folder.
- A preset. On Leonardo, use `-p leonardo`. The shipped preset holds the placeholders
  `YOUR_PROJECT_ACCOUNT` and `YOUR_UCX_LIB_DIR`. Put your values in `local/presets.json`, which
  Git ignores. [Configuring your cluster](../../docs/using/presets.md) shows how.
- For the large runs, an account that may use the nodes and the time. Check
  `crab nodes -p leonardo` first.

## Check and run

```bash
crab validate -p leonardo examples/leonardo/baselines/collectives.json
crab run -p leonardo examples/leonardo/baselines/collectives.json
```

Start with `baselines/collectives.json`. It is the cheapest run and it tells you whether Blink and
the preset work. Results go to `data/leonardo/`; see [Reading results](../../docs/using/results.md).
