# Installation & benchmark setup

Getting CRAB running is two steps: install the framework, then use the setup wizard to make the
benchmarks you want available on this machine.

## Prerequisites

- **Python 3.10+** (the installer hard-fails below this; on a cluster you may need `module load python` first).
- **Git**.
- **A Slurm cluster** to run experiments. `crab run` submits the job with `sbatch`; see the note
  below for the one exception.
- **An MPI toolchain / compilers** (`mpicc`, `make`, sometimes `cmake`) if you intend to *build*
  benchmarks from source with the setup wizard.

!!! warning "CRAB needs Slurm to run experiments"
    Every run is submitted through `sbatch`, except with the `local` preset: it runs a single-node
    job directly on the machine you are on, without Slurm. That is meant for trying CRAB out and
    for testing; real co-running experiments need a Slurm cluster.

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

For the web dashboard, which runs on your laptop and drives clusters over SSH, install the `web`
extra (`pip install -e ".[web]"`) and start it with `crab web`.

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

On success the wizard writes a receipt to `local/receipts/<benchmark_id>.json` (receipts written
by older versions in `config/environments/` are still read). Source builds
are placed under `benchmarks/<benchmark_id>/` in the repo (this directory is git-ignored).

Re-running `crab setup` lets you reconfigure an already-configured benchmark (it asks before
overwriting an existing receipt).

!!! info "What a receipt records"
    Each receipt stores the benchmark's `binary_path`, its `type` (`binary` / `module` / `source`),
    any `pre_run` hooks (e.g. a module load), and an optional `launcher_override`. At run time the
    orchestrator loads every receipt and also exports each binary path as a `CRAB_PATH_<ID>`
    environment variable for wrappers to read. See
    [Architecture → wrapper / recipe / receipt](../concepts/architecture.md#the-wrapper-recipe-receipt-model).

### Applications you installed yourself

An application that is already installed does not need the wizard:

- If its wrapper declares an `executable` and that program is on `PATH` when the job runs (for
  example after a `module load` in your preset), CRAB finds it with no setup at all.
- Otherwise record where it is: `crab receipts set <benchmark_id> --binary /path/to/program`,
  optionally with `--pre-run "module load ..."` (repeatable) and `--launcher srun|mpirun`.
- A config can also name the binary for one app directly, with a `binary` key next to `path`.

`crab wrappers list` shows every wrapper CRAB can see and where each one's binary comes from.

## Keep it up to date

Run `crab update` in the install. On a branch checkout it fast-forwards the branch; on an install
that sits on a release tag it moves to the newest release of the same line. It reinstalls
dependencies only when they changed, and it stops without changing anything if tracked files have
local edits.

Keep per-machine changes out of the tracked files so updates never conflict: the git-ignored
`local/` folder holds `local/presets.json` (merged over the shipped presets, see
[Configuring your cluster](presets.md)), `local/receipts/`, and `local/wrappers/` for private
wrappers.

## Next steps

- On a cluster CRAB already knows (such as `leonardo`), set your project account before the first
  run: the shipped presets carry the placeholder `--account=YOUR_PROJECT_ACCOUNT`. Copy the
  preset's block into `local/presets.json` and change the account
  ([how](presets.md#keep-your-presets-out-of-git)).
- On a cluster CRAB doesn't already know, define a preset: [Configuring your cluster](presets.md).
- Then write an experiment: [Configuration schema](../reference/configuration.md).
