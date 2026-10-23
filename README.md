<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/svg/crab-outlined-dark.svg">
    <img alt="CRAB" src="docs/assets/svg/crab-outlined.svg" width="480">
  </picture>
</p>

<p align="center"><strong>Co-Running Applications Benchmarking framework</strong></p>

<p align="center">
  <a href="https://www.python.org/"><img alt="Python Version" src="https://img.shields.io/badge/python-3.10+-blue.svg"></a>
  <a href="https://opensource.org/licenses/MIT"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg"></a>
  <a href="https://hlc-crab.readthedocs.io/en/latest/?badge=latest"><img alt="Documentation Status" src="https://readthedocs.org/projects/hlc-crab/badge/?version=latest"></a>
  <a href="https://github.com/HLC-Lab/CRAB/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/HLC-Lab/CRAB/actions/workflows/ci.yml/badge.svg"></a>
</p>

CRAB runs several HPC applications at the same time on one machine and measures how much they
slow each other down. It is built for studying interference between co-running applications:
network congestion and contention for shared resources. You mark some applications as victims (the
ones you measure) and others as aggressors (the ones that create load). CRAB launches them
together, repeats the run until the victims' metrics converge, and writes the results as CSV.

CRAB runs on Slurm clusters, and on a laptop or a few workstations through a local scheduler. It
treats applications as black boxes: a small Python wrapper tells CRAB how to start an application
and how to parse its output, so any program can be a victim or an aggressor.

## Three ways to use it

- The command line. `crab run` submits an experiment. `crab status`, `crab logs`, `crab history`
  and `crab export` follow it and read the results.
- JSON configs. An experiment is a JSON file that lists the applications, when each starts and
  ends, the node layout and the convergence rule. You can write it by hand and keep it in git. The
  same file runs on any system that has a preset.
- The web dashboard. `crab web` starts a page on your laptop that connects to your clusters over
  SSH and runs each cluster's own `crab` command. You author experiments, submit jobs and compare
  results from the browser.

## Install

From a git checkout, which is the main path today:

```bash
git clone https://github.com/HLC-Lab/CRAB
cd CRAB
make                        # creates .venv, installs CRAB, fetches the wrappers, runs the setup wizard
source .venv/bin/activate
```

From the first PyPI release, `pipx install "crab-hpc[web]"` installs CRAB as a package. The
distribution is called `crab-hpc`; the command and the import name stay `crab`. Without the web
dashboard, `pipx install crab-hpc` is enough. See [Installation](docs/using/installation.md) for
the prerequisites, the `local/` folder and `crab update`.

CRAB needs Python 3.10 or newer and Git. Running experiments needs a Slurm cluster, or an MPI
installation for the local scheduler.

## A first run on a laptop

The `local` preset runs a job on your own machine, without Slurm. The example below chains four
Barrier runs from the Blink suite, so Blink has to be set up first (`crab setup` does that).

```bash
crab validate -p local examples/local/sequential_barrier.json
crab run -p local examples/local/sequential_barrier.json
```

`crab validate` checks the config the way `crab run` does, without running it. `crab run` starts
the job in the background and returns. Follow it and read what it produced:

```bash
crab status                 # state of local and Slurm jobs
crab history                # past experiments
crab logs --data-dir data/local/<name>_<timestamp>   # the job's captured stdout and stderr
```

Results are written to `data/local/<name>_<timestamp>/`. The [examples guide](examples/README.md)
says which example to start with and how to adapt one to your cluster. For an experiment with a
victim and an aggressor on a Slurm cluster, follow the [tutorial](docs/tutorial.md).

To start the dashboard, install the `web` extra and run:

```bash
pip install -e ".[web]"
crab web                    # serves http://127.0.0.1:8765
```

## Documentation

The documentation is at [hlc-crab.readthedocs.io](https://hlc-crab.readthedocs.io/). The same
pages are in `docs/` in this repository.

- [Tutorial](docs/tutorial.md): a victim and an aggressor on a Slurm cluster, end to end.
- Using CRAB: [installation](docs/using/installation.md),
  [configuring a cluster](docs/using/presets.md),
  [running without Slurm](docs/using/running-without-slurm.md),
  [writing experiment configs](docs/using/writing-configs.md),
  [reading results](docs/using/results.md),
  [the web dashboard](docs/using/dashboard.md).
- Concepts: [system-dependent and system-independent parts](docs/concepts/system-dependent-vs-independent.md),
  [architecture](docs/concepts/architecture.md) and [methodology](docs/concepts/methodology.md)
  (what a run measures and how convergence is decided).
- Extending CRAB: [writing a wrapper](docs/extending/wrappers.md) and
  [adding a build recipe](docs/extending/recipes.md).
- Reference: [CLI commands](docs/reference/cli.md),
  [configuration schema](docs/reference/configuration.md),
  [presets format](docs/reference/presets.md),
  [supported benchmarks](docs/reference/supported-benchmarks.md).
- [Changelog](CHANGELOG.md) and [contributing](docs/contributing.md).

The wrappers live in their own repository,
[HLC-Lab/crab-wrappers](https://github.com/HLC-Lab/crab-wrappers). `make` and `crab update` clone
them into `wrappers/`.

To build the docs locally:

```bash
pip install -r docs/requirements.txt
mkdocs serve                # serves http://127.0.0.1:8000
```

## SbatchMan

The integration with SbatchMan lives on the `sbatchman` branch. Install that branch instead of the
default one and follow
[Using CRAB with SbatchMan](https://github.com/HLC-Lab/CRAB/blob/sbatchman/docs/using/sbatchman-integration.md).

## License

CRAB is released under the MIT License. See [`LICENSE`](LICENSE).
