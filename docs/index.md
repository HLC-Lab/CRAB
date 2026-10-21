# CRAB

CRAB (Co-Running Applications Benchmarking framework) runs several HPC applications at the same
time on one machine and measures how much they interfere with each other: network congestion and
contention for shared resources. You mark applications as victims (measured) or aggressors (load
generators). CRAB launches them together, repeats the run until the victims' metrics converge, and
stores the results as CSV.

It runs on Slurm clusters, and on a laptop or a few workstations through the local scheduler. Each
application is described by a small Python wrapper that says how to start it and how to parse its
output, so any program can take part.

## Three ways to use it

| Interface | Command | Use it for |
|-----------|---------|------------|
| Command line | `crab run` | Submitting and following experiments, scripting, batch studies |
| JSON configs | `crab validate`, `crab run` | Experiments written by hand and kept in git |
| Web dashboard | `crab web` | Authoring, submitting and comparing experiments from your laptop, over SSH |

## Where to go next

- To try CRAB, [install it](using/installation.md), then follow the [tutorial](tutorial.md) or
  [run without Slurm](using/running-without-slurm.md). The
  [examples guide](https://github.com/HLC-Lab/CRAB/blob/master/examples/README.md) says which
  example to start with.
- To run experiments on a cluster, [configure your cluster](using/presets.md),
  [write a config](using/writing-configs.md), [run it](using/running.md) and
  [read the results](using/results.md). The [web dashboard](using/dashboard.md) does the same
  from a browser.
- To understand the model, read
  [System-dependent vs system-independent](concepts/system-dependent-vs-independent.md), the
  [architecture](concepts/architecture.md) and the [methodology](concepts/methodology.md), which
  explains what a run measures and how convergence is decided.
- To support a new application, see [Extending CRAB](extending/overview.md): a
  [wrapper](extending/wrappers.md) and, optionally, a [build recipe](extending/recipes.md). The
  stock wrappers live in [crab-wrappers](https://github.com/HLC-Lab/crab-wrappers).
- To look something up, use the [CLI reference](reference/cli.md), the
  [configuration schema](reference/configuration.md), the [presets format](reference/presets.md),
  the [glossary](glossary.md) and the [changelog](changelog.md).
- To work on CRAB itself, read [Contributing](contributing.md).

The SbatchMan integration lives on the `sbatchman` branch of the repository. Its guide is
[Using CRAB with SbatchMan](https://github.com/HLC-Lab/CRAB/blob/sbatchman/docs/using/sbatchman-integration.md).

CRAB is released under the MIT License.
