# Presets format

A preset defines the [system-dependent](../concepts/system-dependent-vs-independent.md)
environment CRAB can run in. CRAB ships none: the shared presets are files of the
[crab-wrappers](https://github.com/HLC-Lab/crab-wrappers) repository, and a user's own files patch
them. This is the field-level reference; for the practical walkthrough of adding a cluster, see
[Configuring your cluster](../using/presets.md). The decision behind it is
[ADR-041](../dev/dashboard/decisions/adr-041-presets-in-crab-wrappers.md).

## Where presets are read from

CRAB reads two kinds of folder and merges them:

1. The shared presets: `presets/*.json` in each shared wrapper folder. The shared wrapper folders
   are the entries of `CRAB_PATH_WRAPPERS` (separated by `:`), or `wrappers/` in CRAB's home when
   the variable is not set. `local/wrappers/` is not searched for presets. The variable is read
   from the environment of the `crab` command, not from a preset's `env`. When two folders define
   the same preset name, the first folder wins and the other definition is ignored.
2. The user's overrides: every `*.json` file of the overrides folder, in name order. The folder is
   `local/presets/` in a git checkout, `~/.config/crab/presets/` in a pip install
   (`$CRAB_HOME/config/presets/` when `CRAB_HOME` is set), or the folder that `CRAB_PATH_PRESETS`
   names. The variable names a folder, not a file. `crab info` prints the folder.

A missing folder is an empty layer. A file that is not valid JSON, or not a JSON object, stops CRAB
with an error that names the file. When no shared presets folder exists, the error for an unknown
preset says to run `crab update` to fetch crab-wrappers.

The two single files CRAB read before 1.2 are not read any more: `local/presets.json` in CRAB's
home, and `presets.json` in the per-user config folder (`~/.config/crab/presets.json`, or
`$CRAB_HOME/config/presets.json`). While one exists, CRAB stops with a message that names it and
the new folder. A `CRAB_PATH_PRESETS` that still names a file is refused the same way: the message
names it and says to move it into a folder. The package no longer contains `src/crab/presets.json`.

## Structure

A file is a single JSON object. Each top-level key is a preset name, and one file may hold several
presets. A preset name appears once per layer: the same name in two files of one folder is an
error that names both files.

```json
{
    "leonardo": { "description": "...", "scheduler": "slurm", "launcher": "srun", ... },
    "leonardo_short": { "extends": "leonardo", "sbatch": [ ... ] }
}
```

A preset has the fields below. A `_common` key is an error: it held the one setting every preset
shares, which is now an engine default (see [Special tokens](#special-tokens)).

| Field | Type | Meaning |
|-------|------|---------|
| `description` | string | Human-readable label. |
| `env` | object | Environment variables exported to the applications. They carry no launch settings. A value of `null` removes the key, in an override. |
| `sbatch` | array of strings | `#SBATCH` directives added to the generated job script. Slurm only. |
| `header` | array of strings | Shell lines emitted at the top of the job script (e.g. `module load …`). Slurm only; see [Module flow](../using/presets.md#module-flow). The script starts in the job's data folder, not in the folder you ran `crab run` from, so use absolute paths for anything a line sources or loads. |
| `extends` | string | The name of a preset to start from: this preset is that preset's resolved form, changed by the fields written here. See [`extends`](#extends). |
| `notes` | array of strings | Short remarks about the cluster, printed by `crab presets show` and carried by `crab info --json`. The crab-wrappers guide asks for at most 3 one-line entries in a shared preset. |

The execution fields, which say how this machine schedules and launches work, are listed next. A
key that appears in neither table stops CRAB with an error that lists the allowed keys. The resolved
preset never holds `extends`.

### `extends`

`"extends": "<name>"` makes a new preset out of an existing one, shared or yours. The new preset
starts as the parent's resolved form, so a patch of the parent in your overrides also reaches it,
and then the fields written beside `extends` are merged on top with the
[rules below](#merge-semantics). Chains are allowed (a preset may extend a preset that extends
another). An entry that has the name of a shared preset patches that preset and cannot also set
`extends`.

A preset that is not a patch and not a variant must be complete: it needs a `scheduler`. An entry
with neither a shared preset of that name nor `extends` nor a `scheduler` is an error.

## Execution fields

| Field | Meaning | Default | Applies to |
|-------|---------|---------|------------|
| `scheduler` | `"slurm"` (submit with `sbatch`) or `"local"` (run a detached process on this machine). | required | both |
| `launcher` | How each application starts: `"srun"`, `"mpirun"` or `"direct"` (the command as is, one process). | `"srun"` under Slurm, `"direct"` under Local | both; `"srun"` needs `slurm` |
| `launchers.srun.flags` | Extra `srun` flags, e.g. `["--cpu-bind=socket"]`. CRAB's own binding replaces a `--cpu-bind` here for the apps it binds; see [The srun launcher](#the-srun-launcher). | `[]` | `srun` |
| `launchers.mpirun.command` | The `mpirun` executable, or its full path. | `"mpirun"` | `mpirun` |
| `launchers.mpirun.flags` | Extra `mpirun` flags (`--map-by`, MCA parameters, …). | `[]` | `mpirun` |
| `launchers.mpirun.dialect` | `"auto"`, `"openmpi4"`, `"openmpi5"` or `"hydra"`. Intel MPI is not supported. | `"auto"` | `mpirun` |
| `launchers.mpirun.export` | Names of environment variables to forward to the remote ranks, in addition to the ones CRAB forwards itself; see [The mpirun launcher](#the-mpirun-launcher). | `[]` | `mpirun` |
| `hosts` | List of `"name"` or `"name:cores"`. Only `localhost` may omit the core count. | `["localhost"]` | `local` only |
| `hostfile` | Path to a file instead of `hosts`, absolute or starting with `~`. A line is `h`, `h:N` or `h slots=N`; `#` starts a comment. | none | `local` only |
| `exclusive` | `true` or `false`. | `true` | `local` only |

A few details:

- Under `slurm`, `hosts`, `hostfile` and `exclusive` are errors: Slurm picks the nodes.
- Give `hosts` or `hostfile`, not both. Host names must be distinct.
- Under `local`, a job runs on the first `numnodes` hosts of `hosts` (or of `hostfile`). A config
  that asks for more nodes than the preset has hosts is refused. Without either field the only
  host is `localhost`.
- The launch lines are:
    - `srun`: `srun --export=ALL --nodelist <hosts> <flags> [<binding>] [--ntasks-per-node=<ppn>] -n <ranks> -N <hosts>`;
      see [The srun launcher](#the-srun-launcher) for `<binding>`
    - `mpirun`: `<command> -n <ranks> <hosts> [<binding>] [-x NAME ...] <report> <flags>`; see
      [The mpirun launcher](#the-mpirun-launcher) for each part
    - `direct`: the application command, unchanged
- A receipt's `launcher_override` can switch one benchmark to `srun` or `mpirun`; see
  [Receipts](../extending/receipts.md).

A Slurm preset and a local one, from the shared presets (shortened):

```json
"leonardo": {
    "description": "Leonardo Cluster @ CINECA",
    "scheduler": "slurm",
    "launcher": "srun",
    "launchers": { "srun": { "flags": ["--cpu-bind=verbose,socket"] } },
    "env": { "CRAB_IB_DEVICES": "mlx5_0#mlx5_1#mlx5_2#mlx5_3" },
    "sbatch": ["--account=<YOUR_ACCOUNT>", "--partition=boost_usr_prod"],
    "header": ["module purge"]
},
"local": {
    "description": "Local (no Slurm, dev/testing only)",
    "scheduler": "local",
    "launcher": "mpirun"
}
```

## The srun launcher

`<ranks>` is the app's `ppn` times the number of hosts. `<flags>` are the preset's
`launchers.srun.flags`, or the config's `launcher_flags` when it sets them. CRAB adds a
`<binding>` and `--ntasks-per-node=<ppn>` for an app that shares its nodes with another app
running at the same time (see [Shared nodes](configuration.md#shared-nodes)), and also for an app
that sets `cpus`:

| App | `<binding>` |
|---|---|
| shares nodes, `cores: "disjoint"`, no `cpus` | `--exact --mem=0 --cpu-bind=verbose,cores` |
| shares nodes, `cpus` given | `--overlap --cpu-bind=verbose,map_cpu:<cpus>` |
| shares nodes, `cores: "shared"`, no `cpus` | `--overlap --cpu-bind=verbose,none` |
| alone on its nodes, `cpus` given | `--cpu-bind=verbose,map_cpu:<cpus>` |

An app that does not share its nodes and has no `cpus` keeps the plain line. The exception is an
app whose `ppn` differs from the job's `--ntasks-per-node`: it gets `--ntasks-per-node=<ppn>`, so
its steps are not sized to the job.

**CRAB's binding replaces yours.** When CRAB adds a `<binding>`, any `--cpu-bind` in `<flags>`
(`--cpu-bind=socket`, or `--cpu-bind map_cpu:...`) is dropped for that app, because two apps
pinned by their own flags can end up on the same cores. The pre-submit check warns about it. The
other flags stay.

`--exact --mem=0` gives each such app its cores and the node's memory, so it overrides a
`--mem` or `--mem-per-cpu` in `<flags>`.

`verbose` makes `srun` print each rank's CPU mask to the app's standard error, which CRAB keeps in
`<experiment dir>/bindings_app_<id>.log`; see
[What a run records about its launches](../using/running-without-slurm.md#what-a-run-records-about-its-launches).

## The mpirun launcher

`launchers.mpirun.dialect` says which MPI the `mpirun` command belongs to, because the three
supported ones spell their options differently. `"auto"` (the default) runs
`<command> --version` once and reads the answer: Open MPI 4 and 5 and MPICH's Hydra are
supported. Intel MPI, an Open MPI older than 4, and an `mpirun` that is only a stub asking for
`srun` are refused with a message that says what to set instead. A probe that fails or does not
answer within 10 seconds is an error too. The other values (`"openmpi4"`, `"openmpi5"`,
`"hydra"`) skip the probe.

`<ranks>` is the app's `ppn` times the number of hosts, and `N` below is that `ppn`, the ranks per
host:

| | `openmpi4` | `openmpi5` | `hydra` |
|---|---|---|---|
| `<hosts>` | `-H h,h,...` (a host repeated once per rank slot) | `--host h:N,...` | `-hosts h:N,...` |
| `<binding>` for an application with `cpus` | `--cpu-list <cores> --bind-to cpu-list:ordered` | `--map-by pe-list=<cores>:ordered` | `-bind-to user:<cores>` |
| `<binding>` for an application on [shared cores](configuration.md#shared-nodes) without `cpus` | `--bind-to none` | `--bind-to none` | `-bind-to none` |
| `-x NAME` for each forwarded variable | yes | yes | no: Hydra passes the whole environment |
| `<report>` | `--report-bindings` | `--display bindings` | none |

Hydra gets no report: MPICH 4.2 rejects `-report-bindings`. Setting `HYDRA_TOPO_DEBUG=1` by hand
makes it print `process <n> binding: <one 0/1 per core>` for each bound rank, but those lines land
in the app's standard output.

An application with neither `cpus` nor shared cores gets no binding flag, and `mpirun` uses its
own default. `<flags>` are the preset's `launchers.mpirun.flags`, or the config's
`launcher_flags` when it sets them, and come last. Inside a Slurm job the host list stays within
the allocation.

Forwarded variables. Open MPI only sends the variables it is told to. For each launch CRAB
names, as far as they are set in the environment the application starts in:

- the names in `launchers.mpirun.export`, then `PATH` and `LD_LIBRARY_PATH`, then the keys of the
  preset's `env`;
- every `CRAB_*` variable (not the internal `CRAB_SUPERVISOR_FD`);
- the names in the application's own [`env`](configuration.md#the-apps-block), which its launch
  script exports just before `mpirun` runs.

A multi-host local run starts the remote ranks over `ssh`, so the hosts need passwordless `ssh`
between them and the same software at the same paths.

Binding report. CRAB always asks `mpirun` for the `<report>` option above. The report goes to
`mpirun`'s standard error, which is also copied to `<experiment dir>/bindings_app_<id>.log` (with
whatever the ranks write to their own standard error); see
[What a run records about its launches](../using/running-without-slurm.md#what-a-run-records-about-its-launches).


## Merge semantics

A preset is built in layers. The shared preset (the first folder that has the name) is the base.
Your override of the same name is merged onto it. A preset with `extends` is its parent's
resolved form with the preset's own fields merged onto it. The same rules apply in each step, per
field:

| Field | Rule |
|---|---|
| `env` | Merged by key: the patch's value wins, and a value of `null` removes the key. |
| `sbatch`, launcher `flags` | An item written as `--name=value` replaces, in place, the first base item that starts with `--name=`, or is appended when there is none. Any other item is appended: a bare option such as `--exclusive`, a short form such as `-A`, or an option and its value written as two items (as mpirun flags often are). |
| `launchers.mpirun.export` | The names are added to the base list, without duplicates. |
| `header` | The patch's lines run after the base lines. |
| `notes` | The patch's notes are added after the base ones (a note already there is not repeated). |
| `scheduler`, `launcher`, `exclusive`, `hosts`, `hostfile`, `launchers.<kind>.command`, `launchers.<kind>.dialect`, `description` | Replaced by the patch's value. |

A `launchers` object is merged per launcher kind and then per field, so a patch that sets only
`launchers.mpirun.command` keeps the shared `flags`. A launcher kind the base does not have is
added as written.

Two consequences follow. Only `--name=value` items match by name, so a short form such as `-A`
does not match `--account`; shared presets use long options only. And a patch cannot remove a
shared flag or header line: to drop one, write a complete preset under a new name that no shared
preset has, or use `null` for an `env` key.

A preset with no `extends` and no shared base is taken as written (its `env` keys with a `null`
value are dropped).

The preset a job runs with is resolved once, when the job is submitted: it is saved in the run
directory as `execution.json`, so editing a preset file never changes a job that was already
submitted. The final steps then apply to the resolved preset:

- `CRAB_ROOT=__CWD__` is the default of `env`. A preset's own `CRAB_ROOT` wins; it must be an
  absolute path (`~` and `__CWD__` are fine), and a relative one is refused.
- `sbatch`: your experiment config's own `sbatch_directives` are applied later with higher
  priority; see [Configuration schema → sbatch directives](configuration.md#sbatch-directives).
- If the preset does not define `CRAB_SYSTEM`, it defaults to the preset's name. It is used in the
  results path (`data/<CRAB_SYSTEM>/…`).

## Required marks

A value that only the user knows is written as a mark, `<YOUR_NAME>`: `<YOUR_`, then capital
letters, digits and underscores, then `>`. For example `--account=<YOUR_ACCOUNT>`, or
`<YOUR_OPENMPI_DIR>/bin/mpirun`.

- Where a mark may be written: in an `env` value, in a `sbatch` or launcher `flags` item written as
  `--name=<YOUR_NAME>`, and in a launcher's `command`. It may be part of a longer value.
- Where it may not: in a `header` line, which a patch cannot address by key, and in a flag item that
  is not `--name=value`. A file that does this stops CRAB with an error that names the file and the
  preset.
- A mark is always required. `crab run`, `crab validate -p` and `crab nodes -p` refuse a preset
  that still holds one after the merge, and print the override to write, in the shape of an override
  file:

  ```text
  Preset 'leonardo' needs your own value for --account. Add this to a .json file in <your
  overrides folder>/ (merge it into your entry for 'leonardo' if you already have one), replacing
  each <YOUR_...>:
  {
    "leonardo": {
      "sbatch": [
        "--account=<YOUR_ACCOUNT>"
      ]
    }
  }
  ```

  The names after "value for" are what is unset: an `env` key, an option name such as `--account`,
  or `launchers.<kind>.command`.
- Listing is not refused: `crab info` shows such a preset with its unset names in `needs`, and
  `crab presets show` explains it.

## Errors

Each of these stops the command and names the file where it can:

| Message (shortened) | Cause | Fix |
|---|---|---|
| `single-file preset overrides were removed in CRAB 1.2` | `local/presets.json` or the config folder's `presets.json` exists. | Move the presets into the overrides folder; see [Moving from the old override files](../using/presets.md#moving-from-the-old-override-files). |
| `"_common" was removed in CRAB 1.2` | A presets file has a `_common` key. | Put its `env` in each preset. |
| `preset '<name>' is defined twice: <file> and <file>` | Two files of one folder define the name. | Keep one definition. |
| `preset '<name>' extends '<other>', which does not exist` | `extends` names an unknown preset. | Fix the name, or run `crab update` if the base is a shared preset. |
| `extends cycle <a> -> <b> -> <a>` | Presets extend each other in a loop. | Break the loop. |
| `preset '<name>' has no shared preset to patch` | An override with an unknown name and no `scheduler` or `extends`. It is also what a missing or outdated crab-wrappers clone looks like. | Run `crab update`, or make it a complete preset with a `scheduler`. |
| `cannot set "extends"` | An entry that patches a shared name also sets `extends`. | Write the variant under a new name. |
| `header lines cannot be overridden, so they cannot hold a <YOUR_...> mark` | A mark in `header`. | Put the value in an `env` value or a `--name=<YOUR_...>` flag. |
| `holds a <YOUR_...> mark but is not a --name=value item` | A mark in a flag item that is not keyed. | Write it as `--name=<YOUR_...>`. |
| `The preset '<name>' was not found in the shared presets (...) or your overrides (...)` | No layer has the name. It lists the folders searched, and says to run `crab update` when no shared presets folder exists. | Check the spelling against `crab info`, or run `crab update`. |
| `unknown key '<key>'; allowed keys: ...`, `scheduler: must be one of ...` and the other field messages | A field is invalid; see [Execution fields](#execution-fields). | Correct the field. |

## Special tokens

- `__CWD__`: replaced with CRAB's home (`CRAB_ROOT`: the repository root in a checkout, the
  data directory of a pip install) wherever it appears in an `env` value. CRAB itself sets
  `CRAB_ROOT=__CWD__` for every preset, so a preset needs no entry for it.

## Recognized environment variables

These are the variables CRAB itself reads. You can also define any other variables your benchmarks
or libraries require (e.g. `LD_LIBRARY_PATH`, `UCX_*`, `NCCL_*`).

An `env` value is expanded when the job starts, with `$NAME` and `${NAME}` read from the
environment the job was given (after the `header` lines ran). A name that is not set stays as
written, `~` is not expanded, and a value cannot refer to another key of the same `env`.

| Variable | Read by | Notes |
|----------|---------|-------|
| `CRAB_ROOT` | framework | CRAB's home: the repository root in a checkout, `~/.local/share/crab` (or `CRAB_HOME`) in a pip install. Set by CRAB for every preset via `__CWD__`. |
| `CRAB_PATH_WRAPPERS` | runner, presets | Folders searched for relative wrapper `path`s, separated by `:`, after `local/wrappers/` (`~/.config/crab/wrappers` in a pip install). Each entry must be an absolute path (`~` is fine; a relative one is refused). Default: `wrappers/` in CRAB's home. The same folders, with `presets/` added, are searched for shared presets (see [Where presets are read from](#where-presets-are-read-from)). The shared presets do not set it, so a value you export reaches the jobs; set it in a preset's `env` to override that for the wrappers (presets are not found that way). |
| `CRAB_PATH_PRESETS` | presets | The folder of your override files. Default: `local/presets/` in a checkout, `~/.config/crab/presets/` in a pip install. |
| `CRAB_SYSTEM` | engine | System label for the output path. Defaults to the preset name. |
| `CRAB_PRESET` | CLI | If set, selects the preset (overridden by `-p`). |
| `CRAB_PATH_<ID>` | wrappers | Injected automatically from each receipt's `binary_path` at run time, not set by hand. |

## Removed keys

Launch settings used to be `CRAB_*` variables in a preset's `env`. They are now preset fields and
the old keys are no longer read. If one is still in a preset's `env`, or in the environment a
worker starts with, CRAB stops with an error that names the replacement.

| Old key | Use instead |
|---------|-------------|
| `CRAB_WL_MANAGER` | `launcher` |
| `CRAB_SCHEDULER` | `scheduler` |
| `CRAB_MPIRUN` | `launchers.mpirun.command` |
| `CRAB_MPIRUN_ADDITIONAL_FLAGS` | `launchers.mpirun.flags` |
| `CRAB_MPIRUN_MAP_BY_NODE_FLAG` | `launchers.mpirun.flags` |
| `CRAB_MPIRUN_HOSTNAMES_FLAG` | nothing: delete it |
| `CRAB_PINNING_FLAGS` | `launchers.srun.flags` |

To migrate a preset, add `scheduler` (`"slurm"` for a cluster), set `launcher`, move the flags into
`launchers` and delete the old keys from `env`.

## Preset selection precedence

1. `-p` / `--preset` flag
2. `CRAB_PRESET` environment variable
3. A `.env` file in the working directory (its contents = the preset name)

If none of these names a preset, `crab run` stops with an error; it never picks one for you.
