# Installation & benchmark setup

Getting CRAB running is two steps: install the framework, then use the setup wizard to make the
benchmarks you want available on this machine.

## Prerequisites

- **Python 3.8+** (the installer hard-fails below this; on a cluster you may need `module load python` first).
- **Git**.
- **A Slurm cluster.** `crab run` submits the job with `sbatch`, so Slurm must be available — see
  the note below.
- **An MPI toolchain / compilers** (`mpicc`, `make`, sometimes `cmake`) if you intend to *build*
  benchmarks from source with the setup wizard.

!!! warning "CRAB requires Slurm to run experiments"
    Every run is submitted through `sbatch`, including with the `local` preset. There is no
    pure-laptop execution path — the `local`/MPI presets only change how individual applications
    are *launched* on the allocated nodes (`mpirun` instead of `srun`), not how the job is
    submitted. You need access to a Slurm-managed system to actually run experiments.

## Install the framework

The recommended path is the Makefile, which creates an isolated virtual environment, installs
CRAB in editable mode, and launches the setup wizard:

```bash
git clone https://github.com/HLC-Lab/CRAB
cd CRAB
make
```

`make` is idempotent — if CRAB is already installed it tells you so. Use `make clean` to remove
the virtual environment and build artifacts, and `make setup` to re-run just the wizard.

Once installed, **activate the environment** before using the `crab` command (this also enables
shell tab-completion, registered during install):

```bash
source .venv/bin/activate
```

### Manual install

If you prefer not to use the Makefile:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .          # core framework
pip install -e .[tui]     # optional: adds the Textual UI dependencies
```

The TUI extra can also be installed on demand — `crab tui` will offer to install it the first time
if it's missing.

## Set up benchmarks (`crab setup`)

CRAB ships with **recipes** that know how to obtain and build a set of supported benchmarks. The
wizard turns a recipe into a **receipt** — a record of where the built binary lives on this
machine — so wrappers can find it at run time.

```bash
crab setup
```

The wizard walks through:

1. **Select suites.** Choose which benchmark suites to install
   (see [Supported benchmarks](../reference/supported-benchmarks.md)).
2. **Select versions.** For suites with multiple versions (Quantum ESPRESSO offers v6 and v7),
   pick which to build.
3. **Choose a strategy** per benchmark:

    | Strategy | What it does |
    |----------|--------------|
    | **Auto-detect** | Looks for an existing build under `benchmarks/`, optionally doing a deep search of your home directory. |
    | **Manual path** | You supply the absolute path to an existing executable/directory; the recipe verifies it. |
    | **Environment module** | You give a `module load …` command and the binary name; the module load is recorded as a pre-run hook. |
    | **Build from source** | Clones and compiles the benchmark (optionally with module loads and build parameters such as `cpu`/`gpu` arch for QE), streaming the build log live. |

On success the wizard writes a receipt to `config/environments/<benchmark_id>.json`. Source builds
are placed under `benchmarks/<benchmark_id>/` in the repo (this directory is git-ignored).

Re-running `crab setup` lets you reconfigure an already-configured benchmark (it asks before
overwriting an existing receipt).

!!! info "What a receipt records"
    Each receipt stores the benchmark's `binary_path`, its `type` (`binary` / `module` / `source`),
    any `pre_run` hooks (e.g. a module load), and an optional `launcher_override`. At run time the
    orchestrator loads every receipt and also exports each binary path as a `CRAB_PATH_<ID>`
    environment variable for wrappers to read. See
    [Architecture → wrapper / recipe / receipt](../concepts/architecture.md#the-wrapper-recipe-receipt-model).

## Next steps

- On a cluster CRAB doesn't already know, define a preset: [Configuring your cluster](presets.md).
- Then write an experiment: [Configuration schema](../reference/configuration.md).
