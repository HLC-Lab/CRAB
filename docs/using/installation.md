# Installation & benchmark setup

## Prerequisites

- Python 3.10 or newer (the installer hard-fails on an older Python). On a cluster, run `module load python`.
- Git.
- A Slurm cluster to run experiments. `crab run` submits the job with `sbatch`.
- Network access to PyPI and github.com: `make` downloads Python packages and clones the wrappers,
  and `crab setup` clones benchmark sources. A compute node might not have it.
- Optional: build tools, to build benchmarks from source with `crab setup`. Blink needs CMake 3.16
  or newer, a C and a C++ compiler, and the MPI compiler wrappers `mpicc` and `mpicxx`. On a
  cluster, load them through the module line the wizard asks for.

!!! warning "CRAB needs Slurm to run experiments"
    Every run is submitted through `sbatch`, except with the `local` preset: it runs a single-node
    job directly on the machine you are on, without Slurm. That is for trying CRAB out and for
    testing. Real co-running experiments need a Slurm cluster.

## Install the framework

The Makefile creates an isolated virtual environment, installs CRAB in editable mode, clones the
wrappers into `wrappers/` and starts the setup wizard. The clone below takes the `sbatchman`
branch, the line of CRAB that launches jobs through SbatchMan
(see [Using CRAB with SbatchMan](sbatchman-integration.md)):

```bash
git clone --branch sbatchman https://github.com/HLC-Lab/CRAB
cd CRAB
make
```

The wrappers live in [HLC-Lab/crab-wrappers](https://github.com/HLC-Lab/crab-wrappers), not in
CRAB's own tree. Git ignores the `local/` folder at the checkout root: it holds your override
presets, receipts and private wrappers, so updates never conflict with it (see
[Where CRAB keeps its files](../concepts/architecture.md#where-crab-keeps-its-files)).

- `make` is idempotent: it tells you when CRAB is already installed.
- `make setup` re-runs only the wizard.
- `make clean` removes the virtual environment and the build artifacts. It leaves `wrappers/`
  alone.

Activate the environment before you use `crab`. This also enables shell tab-completion, registered
during install:

```bash
source .venv/bin/activate
```

If `pip` or `crab` fails with an `ImportError` from a path outside `.venv`, see
[ImportError from a path outside .venv](troubleshooting.md#importerror-from-a-path-outside-venv).

### Manual install

Without the Makefile, run from the CRAB checkout:

```bash
git clone https://github.com/HLC-Lab/crab-wrappers wrappers   # the wrappers, next to CRAB's code
python -m venv .venv
source .venv/bin/activate
pip install -e .          # core framework
```

If `wrappers/` is missing, `crab update` clones it. For the web dashboard, which runs on your
laptop and drives clusters over SSH, install the `web` extra (`pip install -e ".[web]"`) and start
it with `crab web`.

### Using an existing virtual environment

By default `make` uses `.venv` in the checkout and reuses it when it exists. To install into
another environment, such as one shared with other tools, name it:

```bash
make VENV_DIR=<path-to-venv>
```

- If `VENV_DIR` holds `bin/python`, `make` installs CRAB into it. If not, `make` creates the
  environment there first.
- `make` does not pick up an active `VIRTUAL_ENV`: pass `VENV_DIR` to use a specific environment.
- `make clean` deletes only the default `.venv`. With another `VENV_DIR` it removes the build files
  and prints `[=] leaving the venv <path> in place`.
- The tab-completion lines are added to `activate` only when `make` creates the environment. If
  `argcomplete` is missing, see [A half-built environment](troubleshooting.md#a-half-built-environment).

### Installing without the wizard

`make core` installs CRAB and stops before the wizard, which suits scripts and installers. It
creates or reuses the virtual environment, runs `pip install -e .` and clones the wrappers into
`wrappers/` when the folder is missing. Plain `make` does the same, then starts the wizard:

```bash
make core
make core VENV_DIR=<path-to-venv>
```

## Install with pip or pipx

!!! note "Available from the first PyPI release"
    The package `crab-hpc` is not on PyPI yet. These steps work from its first release. Until then,
    the git checkout install is the documented way to install CRAB on a cluster.

On a machine where you do not want a checkout, install the package into its own environment with
pipx (the `web` extra adds the dashboard):

```bash
pipx install "crab-hpc[web]"
```

Without the dashboard, `pipx install crab-hpc` is enough. `pip install crab-hpc` into a virtual
environment works the same way.

A pip install keeps its files in user folders, and clones the wrappers on first use. See
[Where CRAB keeps its files](../concepts/architecture.md#where-crab-keeps-its-files). If the clone
fails, see [wrappers/ is missing](troubleshooting.md#wrappers-is-missing). `crab info` prints the
layout.

To drive a cluster that has CRAB installed this way from the dashboard, fill the remote's "Crab
command" field (see [Connect a cluster](dashboard.md#connect-a-cluster)).

## Set up benchmarks (`crab setup`)

CRAB ships recipes that know how to obtain and build the supported benchmarks. The wizard turns a
recipe into a receipt, a record of where the built binary lives on this machine, so wrappers can
find it at run time.

```bash
crab setup
```

`make` runs this wizard once at the end of the install. Run `crab setup` yourself to add or
reconfigure benchmarks later. It asks before it overwrites an existing receipt. The wizard asks:

1. Select the suites to install (see [Supported benchmarks](../reference/supported-benchmarks.md)).
2. Select versions, for suites with several (Quantum ESPRESSO offers v6 and v7).
3. Choose a strategy per benchmark:

    | Strategy | What it does |
    |----------|--------------|
    | Auto-detect | Looks for an existing build under `benchmarks/`, optionally doing a deep search of your home directory. For Blink it only finds a build that `crab setup` made (`benchmarks/blink/build/bin`). |
    | Manual path | You supply the absolute path to an existing executable or directory; the recipe verifies it. For Blink, give the `build/bin` folder of your build. |
    | Environment module | You give a `module load …` command and the binary name; the module load is recorded as a pre-run hook. |
    | Build from source | Asks for a module line (blank loads no modules; the prompt shows `module load gcc openmpi` as an example), then clones and compiles the benchmark (with build parameters such as `cpu`/`gpu` arch for QE or the `gpu` backend of Blink), streaming the build log live. |

For a source build, the module line runs in a non-interactive `bash`, so it must work there. If
CMake is a module on your cluster, add it to the line (for example
`module load gcc openmpi cmake`). The same line is saved in the receipt as a pre-run hook, so jobs
load those modules at run time. If the line fails, the wizard prints the shell's error and skips
that benchmark without building anything.

On success the wizard writes a receipt to `local/receipts/<benchmark_id>.json`. Receipts written by
older versions in `config/environments/` are still read. Source builds go under
`benchmarks/<benchmark_id>/`, which git ignores. [Architecture](../concepts/architecture.md#receipt)
lists what a receipt records.

The [tutorial](../tutorial.md#step-1-build-the-benchmark) builds Blink with the wizard. The GPU
backend question, its failures and a CUDA example are in
[Supported benchmarks](../reference/supported-benchmarks.md#build-blink-with-crab-setup).

### Applications you installed yourself

An installed application does not need the wizard:

- If its wrapper declares an `executable` that is on `PATH` when the job runs (for example after a
  `module load` in your preset), CRAB finds it with no setup.
- Otherwise record it with `crab receipts set <benchmark_id> --binary /path/to/program` (see
  [`crab receipts set`](../reference/cli.md#crab-receipts-set)), or name it with a `binary` key next
  to `path` in the config (see [Configuration schema](../reference/configuration.md)).

`crab wrappers list` shows every wrapper CRAB can see and where each one's binary comes from. If a
binary is not found, see [A missing binary](troubleshooting.md#a-missing-binary).

## Keep it up to date

Run `crab update` in the install. A checkout moves to the newest release tag of its line, and the
wrappers clone moves to the matching wrappers tag. `crab update --head` follows the branches of both
instead, which is for developers.

On a pip or pipx install, `crab update` updates only the wrappers and prints
`pipx upgrade crab-hpc`. Run that command to upgrade CRAB itself.

[`crab update`](../reference/cli.md#crab-update) lists every case, and
[Updating](troubleshooting.md#updating-crab-update-stops) covers each message it stops with. Keep
per-machine changes in the git-ignored `local/` folder so updates never conflict.

## Next steps

- If SbatchMan launches your jobs, your Slurm account goes in the SbatchMan configs file
  ([the partner umbrella](sbatchman-integration.md#partner-umbrella)), not in `local/presets/`:
  on this line the configs file owns the job environment.
- For a direct `crab run` on a cluster CRAB already knows (such as `leonardo`), set your project
  account before the first run. The shared presets (the files in `wrappers/presets/`) carry the
  mark `--account=<YOUR_ACCOUNT>`, and `crab run` refuses to start while a mark is unset. It
  prints the file to write, and `crab presets show <name>` prints it too. Write an override file in
  `local/presets/` that sets your account ([how](presets.md#your-overrides)).
- On a cluster CRAB does not know, define a preset: [Configuring your cluster](presets.md).
- Then write an experiment: [Configuration schema](../reference/configuration.md).
