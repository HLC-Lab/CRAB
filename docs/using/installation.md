# Installation & benchmark setup

Getting CRAB running is two steps: install the framework, then use the setup wizard to make the
benchmarks you want available on this machine.

## Prerequisites

- **Python 3.10+** (the installer hard-fails below this; on a cluster you may need `module load python` first).
- **Git**.
- **A Slurm cluster** to run experiments. `crab run` submits the job with `sbatch`; see the note
  below for the one exception.
- **Network access.** `make` downloads Python packages from PyPI and clones the wrappers from
  github.com. `crab setup` clones the benchmark sources from github.com when you build from source.
  A cluster login node usually has this; a compute node may not.
- **Build tools** if you intend to *build* benchmarks from source with the setup wizard. Blink
  needs CMake 3.16 or newer, a C and a C++ compiler, and the MPI compiler wrappers `mpicc` and
  `mpicxx`. On a cluster these usually come from modules (see the build step below).

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

`make` also clones the wrappers into `wrappers/`. They live in their own repository,
[HLC-Lab/crab-wrappers](https://github.com/HLC-Lab/crab-wrappers), and CRAB does not ship them in
its own tree.

`make` is idempotent — if CRAB is already installed it tells you so. Use `make clean` to remove
the virtual environment and build artifacts, and `make setup` to re-run just the wizard.
`make clean` leaves `wrappers/` alone.

### The `local/` folder

`local/` sits at the root of the checkout (next to `config/` and `wrappers/`). Git ignores it, so
it holds the files that belong to this machine and never conflict with an update:

| Path | What it is | Who creates it |
|------|------------|----------------|
| `local/presets.json` | Your presets, merged over the shipped `config/presets.json` (see [Configuring your cluster](presets.md#keep-your-presets-out-of-git)). | You: run `mkdir -p local` in the checkout, then create the file. |
| `local/receipts/` | One receipt per configured benchmark. | `crab setup` |
| `local/wrappers/` | Private or in-progress wrappers, searched before `wrappers/`. | You, if you need it. |

Before the first `crab run` on a cluster that needs a Slurm account, create
`local/presets.json` with your account. The shipped presets carry the placeholder
`--account=YOUR_PROJECT_ACCOUNT`, which `sbatch` refuses. The
[presets page](presets.md#keep-your-presets-out-of-git) has a complete example.

Once installed, **activate the environment** before using the `crab` command (this also enables
shell tab-completion, registered during install):

```bash
source .venv/bin/activate
```

### Manual install

If you prefer not to use the Makefile:

```bash
git clone https://github.com/HLC-Lab/crab-wrappers wrappers   # the wrappers, next to CRAB's code
python -m venv .venv
source .venv/bin/activate
pip install -e .          # core framework
pip install -e .[tui]     # optional: adds the Textual UI dependencies
```

Run the `git clone` from the CRAB checkout, so that the folder is `wrappers/` there. A command that
needs a wrapper stops with `wrappers/ is missing: run crab update` when the folder is not there;
`crab update` clones it.

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

`make` already runs this wizard once at the end of the install. Run `crab setup` yourself to add
or reconfigure benchmarks later.

The wizard walks through:

1. **Select suites.** Choose which benchmark suites to install
   (see [Supported benchmarks](../reference/supported-benchmarks.md)).
2. **Select versions.** For suites with multiple versions (Quantum ESPRESSO offers v6 and v7),
   pick which to build.
3. **Choose a strategy** per benchmark:

    | Strategy | What it does |
    |----------|--------------|
    | **Auto-detect** | Looks for an existing build under `benchmarks/`, optionally doing a deep search of your home directory. For Blink it only finds a build that `crab setup` made (`benchmarks/blink/build/bin`). |
    | **Manual path** | You supply the absolute path to an existing executable/directory; the recipe verifies it. For Blink, give the `build/bin` folder of your build. |
    | **Environment module** | You give a `module load …` command and the binary name; the module load is recorded as a pre-run hook. |
    | **Build from source** | Asks for a module line (default `module load gcc openmpi`), then clones and compiles the benchmark (with build parameters such as `cpu`/`gpu` arch for QE), streaming the build log live. |

For a source build, the module line is run in a non-interactive `bash`, so it must work there. If
CMake is a module on your cluster, add it to the line (for example
`module load gcc openmpi cmake`). The same line is saved in the receipt as a pre-run hook, so jobs
load those modules at run time. If the line fails, the wizard prints the shell's error and skips
that benchmark without building anything.

#### Example: build Blink from source

1. Run `crab setup` and choose **Install or configure a supported benchmark**.
2. The suite list pre-selects every suite that is not configured yet. Press Space to untick the
   ones you do not want, so that only **Blink Suite** is ticked, then press Enter.
3. At **Select Configuration Strategy**, type `4` (Download and compile from source layout).
4. At **Enter necessary pre-build cluster module loads**, press Enter to accept
   `module load gcc openmpi`, or type your own line (add CMake if it is a module).
5. A build panel shows the steps (clone, configure with CMake, compile) with the last lines of the
   log. Blink is built under `benchmarks/blink/`.
6. When it ends, the wizard saves the receipt `local/receipts/blink.json`.

If an earlier build failed, it left a `benchmarks/blink` folder behind. The wizard then asks
**Found a leftover build at ... Remove it and start again?** Answer yes to remove the folder and
build again. If you answer no, Blink is skipped.

On success the wizard writes a receipt to `local/receipts/<benchmark_id>.json` (receipts written
by older versions in `config/environments/` are still read). Source builds
are placed under `benchmarks/<benchmark_id>/` in the repo (this directory is git-ignored).

Re-running `crab setup` lets you reconfigure an already-configured benchmark (it asks before
overwriting an existing receipt).

!!! info "What a receipt records"
    Each receipt stores the benchmark's `binary_path`, its `type` (`binary` / `module` / `source`),
    any `pre_run` hooks (e.g. a module load), and an optional `launcher_override` (`""` to use the
    preset's launcher, or `"srun"` or `"mpirun"`; any other value makes the experiment fail at
    setup). At run time the orchestrator loads every receipt and also exports each binary path as
    a `CRAB_PATH_<ID>` environment variable for wrappers to read. See
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

Two cases need a little more:

- **It stops with "Local edits to tracked files would be overwritten".** The message lists the
  files. The usual one is `config/presets.json`, where you put your account. Copy your preset's
  block into `local/presets.json` (see [Configuring your cluster](presets.md#keep-your-presets-out-of-git)),
  restore the shipped file with `git checkout -- config/presets.json`, and run `crab update` again.
- **You come from v0.4.0 or older.** Run `crab update` twice. The first run still executes the old
  update code, which does not know how to fetch the wrappers. The second run uses the new code and
  clones them.

It updates the wrappers too: it pulls `wrappers/` when that folder is its own git clone, and
clones [crab-wrappers](https://github.com/HLC-Lab/crab-wrappers) when the folder is missing or
holds only leftovers of the old copy that CRAB used to track (such as `__pycache__` folders). A
`wrappers/` folder with anything else in it is left alone. The wrappers follow the `main` branch
of crab-wrappers, not the CRAB release you are on. Running `make` again also clones them when
`wrappers/` is missing.

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
