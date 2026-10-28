# Configuring your cluster

A preset is the [system-dependent](../concepts/system-dependent-vs-independent.md) half of a
run: it tells CRAB how *this* machine runs work: which scheduler, which launcher and
flags, CPU pinning, modules to load, and the Slurm directives every job needs. CRAB itself ships no
presets. The presets for the clusters it knows live in the
[crab-wrappers](https://github.com/HLC-Lab/crab-wrappers) repository, and you adjust them to your
account and your software with small override files of your own.

This page is the practical how-to. For the exhaustive field and merge reference, see
[Presets format](../reference/presets.md).

## Where presets come from

The shared presets are files named `presets/<cluster>.json` in crab-wrappers. CRAB uses the clone
that already holds the stock wrappers: `wrappers/` in a checkout, `~/.local/share/crab/wrappers/`
in a pip install (`$CRAB_HOME/wrappers/` when `CRAB_HOME` is set). A pip install clones it the
first time a command needs it.

The presets move with CRAB, like the wrappers do:

- `crab update` moves the clone to the tag that matches your CRAB version, so the presets and the
  engine that reads them agree.
- `crab update --head` follows the branch of both, for the newest presets. It is meant for
  developers.

A clone left on a tag from before presets moved there has no `presets/` folder, so it offers no
presets at all. When CRAB finds no shared presets folder, the error says to run `crab update`.

`crab info` lists the presets CRAB can use, and marks the ones that still need a value from you
(see [Marks](#values-only-you-know-marks)). Add `--json` for the full answer; see
[`crab info`](../reference/cli.md#crab-info).

## Selecting your preset

At run time CRAB resolves which preset to use in this order:

1. The `-p`/`--preset` flag: `crab run myconfig.json -p mycluster`
2. The `CRAB_PRESET` environment variable
3. A `.env` file in the working directory containing just the preset name

If none of these names a preset, `crab run` stops with an error. It never picks one for you;
to run without Slurm, ask for it explicitly with `-p local`.

CRAB sets `CRAB_ROOT` for every preset: the special token `__CWD__` in an `env` value is replaced
with CRAB's home, the repository root in a checkout and the data directory of a pip install. A
preset that sets its own `CRAB_ROOT` wins. If a preset doesn't set `CRAB_SYSTEM`, it defaults to
the preset's name and is used in the output directory path.

## Your overrides

Never edit the shared presets: they are part of the crab-wrappers clone, and an edit there would
turn every `crab update` into a conflict. Put your changes in override files instead. CRAB reads
every `*.json` file of one folder, in name order, and a file may hold several presets:

| Where CRAB runs | Your overrides folder |
|---|---|
| a git checkout | `local/presets/` at the root of the checkout, next to `src/` (git-ignored) |
| a pip install | `~/.config/crab/presets/` |
| a pip install with `CRAB_HOME` set | `$CRAB_HOME/config/presets/` |
| either, with `CRAB_PATH_PRESETS` set | the folder that variable names |

`crab info` prints the folder in use on its `overrides:` line. `CRAB_PATH_PRESETS` names a
folder, as an absolute path (`~` is fine): a path to a single file is refused with a message that
names it. The folder does not exist until you make it.

An entry with the name of a shared preset **patches** it: only the lines you write change, and
everything else stays as crab-wrappers has it. So the account of a cluster is two lines of yours:

```bash
mkdir -p local/presets            # make the overrides folder (use the one `crab info` prints)
$EDITOR local/presets/mine.json   # any file name ending in .json works
```

```json
{
  "leonardo": {
    "sbatch": ["--account=MY_PROJECT"]
  }
}
```

`"leonardo"` is the shared preset to patch. `sbatch` lists the `#SBATCH` directives to add or
change, and `--account=MY_PROJECT` replaces the shared `--account=` line with your value. When the
shared preset improves, you get it with the next `crab update`, because your file holds only your
own lines.

How each field combines with the shared one (environment variables by key, flags by name, header
lines appended) is the table of [merge semantics](../reference/presets.md#merge-semantics).

### Values only you know: marks

A value only you can supply, such as your account, is written in the shared preset as a **mark**:
`<YOUR_ACCOUNT>`, in capital letters. `crab run`, `crab validate -p` and `crab nodes -p` refuse a
preset while a mark is unset, and print what to write:

```text
Preset 'leonardo' needs your own value for --account. Add this to a .json file in
<your overrides folder>/ (merge it into your entry for 'leonardo' if you already have one),
replacing each <YOUR_...>:
{
  "leonardo": {
    "sbatch": [
      "--account=<YOUR_ACCOUNT>"
    ]
  }
}
```

Paste that snippet into a file of your overrides folder and replace `<YOUR_ACCOUNT>` with your
value, as in the example above. Run the command again and the refusal is gone.

### See where each value comes from

`crab presets show <name>` prints the preset as CRAB will use it, the file each value came from,
what is still unset and, when something is unset, the snippet to paste:

```bash
crab presets show leonardo        # the resolved preset, its sources and the override skeleton
```

```text
leonardo
  description: Leonardo Cluster @ CINECA  (from .../wrappers/presets/leonardo.json)
  scheduler: slurm  (from .../wrappers/presets/leonardo.json)
  ...
  sbatch:
    --account=MY_PROJECT  (from .../local/presets/mine.json)
    --partition=boost_usr_prod  (from .../wrappers/presets/leonardo.json)
    --gres=tmpfs:0  (from .../wrappers/presets/leonardo.json)
  header:  (from .../wrappers/presets/leonardo.json)
    module purge
needs setup: nothing
Personal tweaks go in .../local/presets/leonardo.json (a preset with this name patches the one above).
```

Use it whenever a preset behaves unexpectedly: it answers "is this my value or the shared one?".
The paths above are shortened. The options of the command are in
[`crab presets show`](../reference/cli.md#crab-presets-show).

### Variants with `extends`

To keep a second version of a preset beside the first, write a new name with `"extends"` and only
what differs:

```json
{
  "leonardo_short": {
    "extends": "leonardo",
    "description": "Leonardo, short queue",
    "sbatch": ["--partition=short"]
  }
}
```

`extends` names the preset to start from. `leonardo_short` is that preset, including any patch of
yours, with the lines above changed. Select it like any other preset: `-p leonardo_short`. The
shared presets use the same mechanism for the choices a cluster offers, such as a GPU and a CPU
partition. An entry that patches a shared name cannot also set `extends`.

### A clean slate under a new name

A patch can add and change, but it cannot remove a shared flag or a shared header line. To drop
one, write a complete preset under a name that no shared preset has. It needs at least a
`scheduler`, and CRAB takes it as it is, with nothing underneath. To start from an existing one,
copy the `preset` key of `crab presets show <name> --json`, the resolved preset, into a new file
and rename it.

### Moving from the old override files

Before CRAB 1.2 your changes lived in one file, `local/presets.json` in CRAB's home (the checkout)
or `~/.config/crab/presets.json` (`$CRAB_HOME/config/presets.json` with `CRAB_HOME` set), and an
entry replaced the shared preset of the same name. Those files are no longer read. While one
exists, every command that loads presets stops with a message that names the file and the new
folder. `CRAB_PATH_PRESETS` used to name a file too: it names a folder now, so point it at one.
While it names a file, CRAB stops with a message that says so.

To move to the new folder:

```bash
mkdir -p local/presets                              # the new overrides folder
mv local/presets.json local/presets/mine.json       # the old file becomes one of its files
$EDITOR local/presets/mine.json                     # shrink whole copies to patches (below)
```

Then check the file:

- A whole copy of a shared preset still loads, but it now patches the shared one. Its `header`
  lines are added after the shared header lines, and so is any flag not written as
  `--name=value` (an mpirun flag and its value as two items, for example), so a copy that only
  changes the account would repeat all of them. Cut each copy down to the lines you changed.
- A `_common` entry is an error that says it was removed. Put its `env` in each preset instead;
  CRAB sets `CRAB_ROOT` itself.
- A preset for a cluster that crab-wrappers does not have is a complete preset and works as it is.

If you edited the shipped presets file of a checkout, `crab update` stops before it changes
anything and prints the steps: put your changes as patches in `local/presets/<name>.json`, run
`git checkout` on the edited file, then run `crab update` again.

## The four kinds of per-user values

Where a value goes depends on who knows it:

| Kind | Example | Where it goes |
|---|---|---|
| Required from each user | your account | A mark in the shared preset, and your value in an override (above). |
| A choice the cluster offers | GPU or CPU nodes, a partition | A shared variant preset: pick it with `-p`. `crab info` lists the variants. |
| Your own environment | your UCX or OpenMPI build, a venv, modules, a QoS | Your override file. The recipes below cover each. |
| A gotcha of the cluster | `--gres=tmpfs:0`, an InfiniBand service level | Already in the shared preset. If one is missing, send it to crab-wrappers. |

A shared preset may also carry a short `notes` list for what the other kinds cannot say. `crab
presets show` prints it, and `crab info --json` carries it.

### Your own UCX or OpenMPI build

Point the libraries and the programs at your build with `env`:

```json
{
  "leonardo": {
    "env": {
      "LD_LIBRARY_PATH": "$HOME/ucx/lib:${LD_LIBRARY_PATH}",
      "PATH": "$HOME/ompi/bin:${PATH}"
    }
  }
}
```

- `env` merges key by key, so your two keys are added to the shared ones, and a key the shared
  preset also sets takes your value. If the shared preset already sets `LD_LIBRARY_PATH`, find its
  value with `crab presets show` and keep that part in yours.
- CRAB expands `$NAME` and `${NAME}` when the job starts, against the environment the job was
  given (after the `header` lines ran), so `${LD_LIBRARY_PATH}` keeps what is there. A name that is
  not set stays as written, and `~` is not expanded: write `$HOME`. A value cannot refer to another
  key of the same `env`.
- To remove a key a shared preset sets, give it the value `null`: `"UCX_IB_SL": null`.
- Under the `mpirun` launcher, `PATH` and `LD_LIBRARY_PATH` and every key of `env` are forwarded to
  the remote ranks. To forward more variables add their names to `launchers.mpirun.export`; see
  [the mpirun launcher](../reference/presets.md#the-mpirun-launcher).
- A preset that launches with `mpirun` often marks the path of the command, as `slimfly` does with
  `<YOUR_OPENMPI_DIR>/bin/mpirun`. Your value goes in the launcher:

```json
{
  "slimfly": {
    "launchers": {
      "mpirun": { "command": "/path/to/openmpi/bin/mpirun" }
    }
  }
}
```

### A virtual environment or modules

Put the lines in `header`:

```json
{
  "leonardo": {
    "header": [
      "module load python/3.11",
      "source $HOME/venvs/crab/bin/activate"
    ]
  }
}
```

Your header lines run after the shared ones, at the top of the job script and before `crab worker`
starts, so the worker and the applications inherit what they set. This is the order of the
[module flow](#module-flow). Header lines are shell lines, so `~` and `$HOME` work in them. Under
the `local` scheduler there is no job script and `header` does not apply.

### A QoS

Add the Slurm option to `sbatch`:

```json
{
  "leonardo": {
    "sbatch": ["--qos=normal"]
  }
}
```

Write it as `--qos=<name>`, with `--` and `=`. An item in that form replaces a shared item of the
same name, so a shared `--qos=` is replaced and nothing doubles up. A short form such as `-q`
would only be added next to it. To set an option for one experiment only, use the config's
[`sbatch_directives`](../reference/configuration.md#sbatch-directives).

## Writing a preset for a new cluster

For a cluster crab-wrappers does not have yet, write a complete preset in your overrides folder. It
is the same format as the shared ones. Start from the smallest preset that can work and replace the
account, the partition and the module lines with your site's:

```json
{
  "mycluster": {
    "description": "My cluster",
    "scheduler": "slurm",
    "launcher": "srun",
    "sbatch": [
      "--account=MY_PROJECT",
      "--partition=compute",
      "--exclusive"
    ],
    "header": [
      "module purge",
      "module load gcc openmpi"
    ]
  }
}
```

Run it with `crab run <config.json> -p mycluster`. A new name with a `scheduler` is a complete
preset, so there is no shared preset underneath it. The `local` preset is different: it runs a
single-node job on your own machine without Slurm, for trying CRAB out (see [Running without
Slurm](#running-without-slurm)). Don't use it as a template for a cluster.

To adapt an existing preset, run `crab presets show <name>` first to see its parts. Here is
`leonardo`, shortened and annotated:

```json
"leonardo": {
    "description": "Leonardo Cluster @ CINECA",
    "scheduler": "slurm",                    // submit the job with sbatch
    "launcher": "srun",                      // start each application with srun
    "launchers": {
        "srun": { "flags": ["--cpu-bind=verbose,socket"] }  // CPU pinning for srun
    },
    "env": {                                 // exported to the applications only
        "UCX_IB_SL": "1"                     // a setting of the site's network
    },
    "sbatch": [                              // #SBATCH directives added to every job
        "--account=<YOUR_ACCOUNT>",          // a mark: the user's own account
        "--partition=boost_usr_prod",
        "--gres=tmpfs:0"                     // a gotcha of the cluster
    ],
    "header": [                              // shell lines run at the top of the job script
        "module purge"
    ]
}
```

The `//` comments are for reading: a JSON file cannot hold them. To adapt a preset to your
cluster, change the values you already know for your system:

- `sbatch`: your Slurm `--account` and `--partition` (plus any site requirements; some sites
  also want `--exclusive` to guarantee whole-node allocations for interference measurements).
- `launchers.srun.flags`: your preferred binding (`--cpu-bind=socket`, `--cpu-bind=core`, a
  `map_cpu` list, etc.).
  For apps that share nodes or set `cpus`, CRAB binds the ranks itself and drops a `--cpu-bind`
  from these flags; see [The srun launcher](../reference/presets.md#the-srun-launcher).
- `header`: the `module load` lines your benchmarks need at run time.
- `env`: any environment variables your system or libraries require. They reach the
  applications; launch settings do not belong here.

!!! note
    This guide assumes you already know your cluster's account, partition, and the right CPU
    pinning for your hardware. Those are site-specific values your HPC support or local
    documentation provides.

Once the preset works for you, share it: a preset describes a cluster, so everyone on that cluster
can use it. Send it as a pull request to crab-wrappers, in `presets/<cluster>.json`. Write the
account as a mark (`--account=<YOUR_ACCOUNT>`), use long `sbatch` options, and make the choices of
the cluster variants with `extends`. The
[contributing guide](https://github.com/HLC-Lab/crab-wrappers/blob/main/CONTRIBUTING.md#presets)
has the rules and the check to run.

## Choosing the launcher

The preset's `scheduler` decides how the job itself is run: `"slurm"` submits it with `sbatch`.
Its `launcher` decides how each application starts inside the job:

=== "`srun`"

    The default under Slurm, so a minimal Slurm preset is short:

    ```json
    "mycluster": {
        "scheduler": "slurm",
        "launcher": "srun",
        "launchers": { "srun": { "flags": ["--cpu-bind=socket"] } },
        "sbatch": ["--account=<your_account>", "--partition=<partition>"]
    }
    ```

    `launchers.srun.flags` is optional (default: none).

=== "`mpirun`"

    Use this when the cluster wants `mpirun` inside the Slurm job, as `slimfly` does:

    ```json
    "mycluster": {
        "scheduler": "slurm",
        "launcher": "mpirun",
        "launchers": {
            "mpirun": {
                "command": "/path/to/openmpi/bin/mpirun",
                "flags": ["--map-by", "node"]
            }
        },
        "sbatch": ["--account=<your_account>", "--partition=<partition>"]
    }
    ```

    `command` defaults to `mpirun` on the `PATH`, and `flags` to none. Inside a Slurm allocation
    CRAB gives `mpirun` the allocated host names itself, so there is no host list to write.

The full list of fields, with defaults, is in the
[Presets format](../reference/presets.md#execution-fields). A benchmark can also ask for a
launcher of its own through its [receipt](../extending/receipts.md).

!!! warning "Presets from older versions"
    Launch settings used to be `CRAB_*` variables in `env`. CRAB no longer reads them and stops
    with an error that names the field to use instead. To convert an existing preset, follow the
    [removed keys table](../reference/presets.md#removed-keys).

## Running without Slurm

For trying CRAB on a laptop or workstation, or on a few machines reached over `ssh`, a preset can
skip Slurm. This is the shared `local` preset:

```json
"local": {
    "description": "Local (no Slurm, dev/testing only)",
    "scheduler": "local",
    "launcher": "mpirun"
}
```

`"scheduler": "local"` starts the job as a detached process on this machine, and `"launcher":
"mpirun"` starts each application with `mpirun`. Request the preset with `-p local`; CRAB never
falls back to it on its own. Hosts, job ids, walltime, exclusive hosts and the caveat about
logging out are on the page [Running without Slurm](running-without-slurm.md).

## Module flow

Three places can load modules or set variables, and they run at different moments:

1. The preset's `header`. Under Slurm its lines are written once, at the top of the batch
   script `crab_job.sh`, after the `#SBATCH` directives. They run before `crab worker` starts, so
   the worker and every application it starts inherit what they set.
2. The receipt's `pre_run` hooks. CRAB writes a small script for each application launch,
   `<run dir>/.wrappers/app_<id>.sh`. It sources `/etc/profile.d/modules.sh` when that file exists,
   runs the receipt's `pre_run` hooks, then exports the application's own
   [`env`](../reference/configuration.md#the-apps-block) variables.
3. The launcher. The last line of that script is the launch line, which starts the application
   with `srun`, `mpirun` or as is.

With `mpirun` on several hosts, the remote ranks are started by `mpirun` over `ssh` and do not run
that script. They get the environment variables `mpirun` forwards (see
[the mpirun launcher](../reference/presets.md#the-mpirun-launcher)), so a module that sets other
variables has to be named in `launchers.mpirun.export`.

Under the local scheduler there is no batch script, so `header` does not apply (and neither do
`sbatch` and `sbatch_directives`). The job's worker inherits the environment of the shell that ran
`crab run`: load modules there, or in the receipt's `pre_run` hooks, which apply under every
scheduler.

Once your preset exists, [set up your benchmarks](installation.md#set-up-benchmarks-crab-setup) on
the cluster and you're ready to [write an experiment](../reference/configuration.md).
