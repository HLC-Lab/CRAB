# Installation & benchmark setup

Getting CRAB running is two steps: install the framework, then use the setup wizard to make the
benchmarks you want available on this machine.

## Prerequisites

- Python 3.10+ (the installer hard-fails below this; on a cluster you may need `module load python` first).
- Git.
- A Slurm cluster to run experiments. `crab run` submits the job with `sbatch`; see the note
  below for the one exception.
- Network access. `make` downloads Python packages from PyPI and clones the wrappers from
  github.com. `crab setup` clones the benchmark sources from github.com when you build from source.
  A cluster login node usually has this; a compute node may not.
- Build tools if you intend to *build* benchmarks from source with the setup wizard. Blink
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

`make` is idempotent: if CRAB is already installed it tells you so. Use `make clean` to remove
the virtual environment and build artifacts, and `make setup` to re-run just the wizard.
`make clean` leaves `wrappers/` alone.

### The `local/` folder

`local/` sits at the root of the checkout (next to `src/` and `wrappers/`). Git ignores it, so
it holds the files that belong to this machine and never conflict with an update:

| Path | What it is | Who creates it |
|------|------------|----------------|
| `local/presets/` | Your override files: `*.json` files that patch the shared presets from the crab-wrappers clone (see [Your overrides](presets.md#your-overrides)). | You: run `mkdir -p local/presets` in the checkout, then add a file. |
| `local/receipts/` | One receipt per configured benchmark. | `crab setup` |
| `local/wrappers/` | Private or in-progress wrappers, searched before `wrappers/`. | You, if you need it. |

Before the first `crab run` on a cluster that needs a Slurm account, add an override file in
`local/presets/` with your account. The shared presets carry the mark `--account=<YOUR_ACCOUNT>`,
and `crab run` refuses to start while a mark is unset. It prints the file to write, and
`crab presets show <name>` prints it too. The [presets page](presets.md#your-overrides) has a
complete example. The shared presets themselves are the files in `wrappers/presets/`.

Once installed, activate the environment before using the `crab` command (this also enables
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
```

Run the `git clone` from the CRAB checkout, so that the folder is `wrappers/` there. A command that
needs a wrapper stops with `wrappers/ is missing: run crab update` when the folder is not there;
`crab update` clones it.

For the web dashboard, which runs on your laptop and drives clusters over SSH, install the `web`
extra (`pip install -e ".[web]"`) and start it with `crab web`.

## Install with pip or pipx

!!! note "Available from the first PyPI release"
    The package `crab-hpc` is not on PyPI yet. These steps work from the first PyPI release of
    `crab-hpc`. Until then, the git checkout above is the documented way to install CRAB on a
    cluster, and it stays the main path.

For a machine where you do not want a checkout, install the package into its own environment
with pipx (the `web` extra adds the dashboard):

```bash
pipx install "crab-hpc[web]"
```

Without the dashboard, `pipx install crab-hpc` is enough. `pip install crab-hpc` into a virtual
environment works the same way.

A pip install has no checkout, so CRAB keeps its files in user folders instead (the two layouts
are described in [ADR-038](../dev/dashboard/decisions/adr-038-two-layouts.md); the code is
`src/crab/paths.py`). `crab info` prints the layout (`installed`) and the home.

| Folder | What it holds |
|--------|---------------|
| `~/.local/share/crab` (the home) | `data/` (results), `benchmarks/` (source builds) and `wrappers/` (the crab-wrappers clone: the wrappers and the shared presets in `presets/`). |
| `~/.config/crab` | `presets/` (your override files), `receipts/` and `wrappers/` (private or in-progress wrappers, searched before the shared ones). |

These are the places on Linux; other systems use their own user data and config folders. Two
variables move them:

- `CRAB_HOME` sets the home, and puts the config folder at `$CRAB_HOME/config`. It must be an
  absolute path (`~` is fine): a relative value stops the command with an error.
- `CRAB_PATH_PRESETS` sets the folder of your override files. It must be an absolute path
  (`~` is fine), like `CRAB_HOME`.

The package ships no presets: the shared ones come from the crab-wrappers clone, with the
wrappers. Your own override files go in `~/.config/crab/presets/` (see
[Your overrides](presets.md#your-overrides)).

The wrappers are not part of the package. The first time a command needs them (`crab run`,
`crab setup`, `crab wrappers ...`), CRAB prints one line and clones
[crab-wrappers](https://github.com/HLC-Lab/crab-wrappers) into `~/.local/share/crab/wrappers`.
This needs git and network access on that machine. It does not happen in a call with `--json`
(there a hint is printed instead) or when `CRAB_PATH_WRAPPERS` is set. If the clone fails, run
`crab wrappers pull` once the network is back.

`crab update` on a pip install does not touch CRAB itself: it updates the wrappers and prints
`pipx upgrade crab-hpc`, which is the command that upgrades the program. See
[Keep it up to date](#keep-it-up-to-date).

To drive a cluster that has CRAB installed this way from the dashboard, fill the remote's
"Crab command" field (see [Connect a cluster](dashboard.md#connect-a-cluster)).

## Set up benchmarks (`crab setup`)

CRAB ships with recipes that know how to obtain and build a set of supported benchmarks. The
wizard turns a recipe into a receipt, a record of where the built binary lives on this
machine, so wrappers can find it at run time.

```bash
crab setup
```

`make` already runs this wizard once at the end of the install. Run `crab setup` yourself to add
or reconfigure benchmarks later.

The wizard walks through:

1. Select suites. Choose which benchmark suites to install
   (see [Supported benchmarks](../reference/supported-benchmarks.md)).
2. Select versions. For suites with multiple versions (Quantum ESPRESSO offers v6 and v7),
   pick which to build.
3. Choose a strategy per benchmark:

    | Strategy | What it does |
    |----------|--------------|
    | Auto-detect | Looks for an existing build under `benchmarks/`, optionally doing a deep search of your home directory. For Blink it only finds a build that `crab setup` made (`benchmarks/blink/build/bin`). |
    | Manual path | You supply the absolute path to an existing executable/directory; the recipe verifies it. For Blink, give the `build/bin` folder of your build. |
    | Environment module | You give a `module load …` command and the binary name; the module load is recorded as a pre-run hook. |
    | Build from source | Asks for a module line (default `module load gcc openmpi`), then clones and compiles the benchmark (with build parameters such as `cpu`/`gpu` arch for QE), streaming the build log live. |

For a source build, the module line is run in a non-interactive `bash`, so it must work there. If
CMake is a module on your cluster, add it to the line (for example
`module load gcc openmpi cmake`). The same line is saved in the receipt as a pre-run hook, so jobs
load those modules at run time. If the line fails, the wizard prints the shell's error and skips
that benchmark without building anything.

#### Example: build Blink from source

1. Run `crab setup` and choose "Install or configure a supported benchmark".
2. The suite list pre-selects every suite that is not configured yet. Press Space to untick the
   ones you do not want, so that only Blink Suite is ticked, then press Enter.
3. At "Select Configuration Strategy", type `4` (Download and compile from source layout).
4. At "Enter necessary pre-build cluster module loads", press Enter to accept
   `module load gcc openmpi`, or type your own line (add CMake if it is a module).
5. A build panel shows the steps (clone, configure with CMake, compile) with the last lines of the
   log. Blink is built under `benchmarks/blink/`.
6. When it ends, the wizard saves the receipt `local/receipts/blink.json`.

If an earlier build failed, it left a `benchmarks/blink` folder behind. The wizard then asks
"Found a leftover build at ... Remove it and start again?" Answer yes to remove the folder and
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
  optionally with `--pre-run "module load ..."` (repeatable) and `--launcher srun|mpirun`. Give
  a command name on `PATH` (stored as typed, such as `xhpl`), or the full path: `~` is expanded,
  and a relative path is made absolute from the folder you run the command in, so the receipt
  works from any folder. A receipt that holds a relative path, for example one written by an
  older CRAB, is not used: `crab wrappers list` shows it as missing, so register it again.
- A config can also name the binary for one app directly, with a `binary` key next to `path`.

`crab wrappers list` shows every wrapper CRAB can see and where each one's binary comes from.

## Keep it up to date

Run `crab update` in the install. By default it goes to the release: a checkout moves to the
newest release tag of its line, and the wrappers clone moves to the matching wrappers tag
(`vX.Y.Z` of the CRAB version it ends on). `crab update --head` follows the branches of both
instead, which is for developers. [`crab update`](../reference/cli.md#crab-update) lists every
case. CRAB reinstalls dependencies only when they changed, and it stops without changing
anything if tracked files have local edits.

On a pip or pipx install, `crab update` updates only the wrappers (to the tag of the installed
version, or the branch with `--head`) and prints `pipx upgrade crab-hpc`. Run that command to
upgrade CRAB itself.

Two cases need a little more:

- It stops with "Local edits to tracked files would be overwritten". The message lists the
  files. Move per-machine changes to `local/`, or commit or stash them. One old case has its own
  message: the presets used to be tracked files in CRAB (`config/presets.json`, later
  `src/crab/presets.json`), and if you put your account there, the update tells you to move
  your changes into an override file in `local/presets/` (see
  [Your overrides](presets.md#your-overrides)), run `git checkout <that file>`, and run
  `crab update` again.
- You come from v0.4.0 or older. Run `crab update` twice. The first run still executes the old
  update code, which does not know how to fetch the wrappers. The second run uses the new code and
  clones them.

The wrappers are updated too: `crab update` pulls or moves `wrappers/` when that folder is its own
git clone, and clones [crab-wrappers](https://github.com/HLC-Lab/crab-wrappers) when the folder
is missing or holds only leftovers of the old copy that CRAB used to track (such as `__pycache__`
folders). A `wrappers/` folder with anything else in it is left alone. Running `make` again also
clones them when `wrappers/` is missing.

Keep per-machine changes out of the tracked files so updates never conflict: the git-ignored
`local/` folder holds `local/presets/` (your override files, see
[Your overrides](presets.md#your-overrides)), `local/receipts/`, and `local/wrappers/` for private
wrappers.

## Next steps

- On a cluster CRAB already knows (such as `leonardo`), set your project account before the first
  run: the shared presets carry the mark `--account=<YOUR_ACCOUNT>`. Write an override file in
  `local/presets/` that sets your account
  ([how](presets.md#your-overrides)).
- On a cluster CRAB doesn't already know, define a preset: [Configuring your cluster](presets.md).
- Then write an experiment: [Configuration schema](../reference/configuration.md).
