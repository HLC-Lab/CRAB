# CRAB

CRAB (Co-Running Applications Benchmarking framework) runs several HPC applications at the same
time on one machine. It measures how much they interfere with each other through network
congestion and contention for shared resources.

You mark each application as a victim (measured) or an aggressor (a load generator). CRAB launches
them together, repeats the run until the victims' metrics converge, and stores the results as CSV.

CRAB runs on Slurm clusters, and on a laptop or a few workstations through the local scheduler. A
small Python wrapper per application says how to start it and how to parse its output, so any
program can take part.

## Three ways to use it

| Interface | Command | Use it for |
|-----------|---------|------------|
| Command line | `crab run` | Submitting and following experiments, scripting, batch studies |
| JSON configs | `crab validate`, `crab run` | Experiments written by hand and kept in git |
| Web dashboard | `crab web` | Authoring, submitting and comparing experiments from your laptop, over SSH |

## Where to go next

- To try CRAB, [install it](using/installation.md), then follow the [tutorial](tutorial.md) or
  [run without Slurm](using/running-without-slurm.md).
- To pick a first config, use the
  [examples guide](https://github.com/HLC-Lab/CRAB/blob/master/examples/README.md).
- To run experiments on a cluster, [configure your cluster](using/presets.md),
  [write a config](using/writing-configs.md), [run it](using/running.md) and
  [read the results](using/results.md).
- To do the same from a browser, use the [web dashboard](using/dashboard.md).
- To understand the model, read
  [System-dependent vs system-independent](concepts/system-dependent-vs-independent.md), the
  [architecture](concepts/architecture.md) and the [methodology](concepts/methodology.md).
- To support a new application, see [Extending CRAB](extending/overview.md): a
  [wrapper](extending/wrappers.md) and an optional [build recipe](extending/recipes.md). The stock
  wrappers are in [crab-wrappers](https://github.com/HLC-Lab/crab-wrappers).
- To look something up, use the [CLI reference](reference/cli.md),
  the [configuration schema](reference/configuration.md), the [presets format](reference/presets.md),
  the [glossary](glossary.md) and the [changelog](changelog.md).
- To work on CRAB itself, read [Contributing](contributing.md).
- To use CRAB with SbatchMan, see
  [Using CRAB with SbatchMan](https://github.com/HLC-Lab/CRAB/blob/sbatchman/docs/using/sbatchman-integration.md)
  on the `sbatchman` branch.

CRAB is released under the MIT License.
