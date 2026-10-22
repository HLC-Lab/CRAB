# CLI commands

CRAB installs a single console script, `crab`. Activate the virtual
environment first (`source .venv/bin/activate`); shell tab-completion is registered during install.

The sections follow the order of `crab --help`. Every command takes `-h` and `--help`. The `--json`
answers carry a `schema` field, the version of the `--json` contract, which is `2`. The [web
dashboard](../using/dashboard.md) reads these answers on each cluster.

## `crab setup`

Launches the interactive setup wizard to obtain/build benchmarks and write their
[receipts](../extending/receipts.md).

```bash
crab setup
```

No arguments. See [Installation: benchmark setup](../using/installation.md#set-up-benchmarks-crab-setup).

## `crab run`

Runs an experiment: prepares the output directory and submits the job, to Slurm or to the local
scheduler, as the preset says.

```bash
crab run [-p PRESET] [--log-level LEVEL] [--json] [--only KEYS] <config.json>
```

| Argument | Description | Default |
|----------|-------------|---------|
| `config.json` | Path to the [experiment config](configuration.md). Required. Tab-completes `.json` files. | none |
| `-p`, `--preset` | Preset name from the shipped presets or your own presets file. Tab-completes known presets. See [resolution order](../using/presets.md#selecting-your-preset). | the `CRAB_PRESET` environment variable; with neither, `crab run` stops with an error |
| `--log-level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. Any other value counts as `INFO`. | `INFO` |
| `--json` | Print the submit result as JSON on stdout: `job_id`, `data_dir` and `system`. Logs go to stderr. | off |
| `--only` | Comma-separated experiment keys of the config to run. The other experiments are skipped. A key the config does not have is an error. | the whole config |

```bash
crab run examples/local/sequential_barrier.json -p local
crab run examples/local/sequential_barrier.json -p local --only 01_sequential_barrier_chain --json
```

Before it submits, `crab run` checks that every app's wrapper `path` names a file on the wrapper
search path (the preset's `CRAB_PATH_WRAPPERS` included), with the same messages as
[`crab validate`](#crab-validate). With `--only`, only the chosen experiments are checked.

See [Running an experiment](../using/running.md).

## `crab validate`

Runs the checks `crab run` makes before it submits, without submitting and without touching the
scheduler: the config's `schema_version` and the engine's config checks (option values, allocation,
ppn, launcher, wrapper paths). With `-p`, the config is also checked against what that preset can
run (the scheduler, the launcher and the number of hosts).

```bash
crab validate [-p PRESET] [--json] <config.json>
```

| Argument | Description | Default |
|----------|-------------|---------|
| `config.json` | Path to the [experiment config](configuration.md). Required. | none |
| `-p`, `--preset` | Also check the config against this preset. | none: config-only checks |
| `--json` | Print `{"ok": bool, "errors": [...], "warnings": [...]}` on stdout instead of text. The exit status is the same. | off |

Every app's wrapper `path` must name a file on this machine's wrapper search path. Otherwise the
check is an error, and for a Blink wrapper renamed in 1.0 it names the new path. A
`CRAB_PATH_WRAPPERS` set in the preset's `env` is honored, but only when you pass `-p`. See
[the troubleshooting entry](../using/troubleshooting.md#wrapper-not-found-on-the-wrapper-search-path).

The output is `ok: <file>` followed by one line per warning, with exit status 0. When a check fails,
the message goes to stderr and the exit status is 1. A missing file, a file that is not JSON and an
unknown preset are one-line errors too.

```bash
crab validate examples/local/sequential_barrier.json
crab validate -p local examples/local/sequential_barrier.json --json
```

The web dashboard runs the same config-only checks (no preset) when it saves a config. Their errors
and warnings join the save's warnings, and a save is never rejected.

## `crab web`

Starts the web dashboard on this machine and opens it in the browser. It needs the `web` extra;
when the extra is missing, `crab web` offers to install it and waits for a `y`.

```bash
crab web [--host HOST] [--port PORT] [--no-browser] [-v]
```

| Flag | Meaning | Default |
|------|---------|---------|
| `--host` | Address to bind. | `127.0.0.1` |
| `--port` | Port to bind. | `8765`; the `CRAB_WEB_PORT` environment variable sets the default |
| `--no-browser` | Do not open a browser on start. | opens one |
| `-v`, `--verbose` | Log startup and every request. | warnings and errors only |

```bash
crab web
crab web --port 8899 --no-browser
```

See [The web dashboard](../using/dashboard.md#start-it) for the session token, the cluster
connections and what the dashboard does.

## `crab export`

Generates a self-contained HTML dashboard from a results directory. The CSV data is embedded
into the file, so the result can be emailed, committed to a repo, or opened directly anywhere
without any server or import step.

```bash
crab export [-o OUTPUT] <data_dir>
```

| Argument | Description | Default |
|----------|-------------|---------|
| `data_dir` | Path to a results directory containing experiment CSVs. CRAB scans both flat (`<dir>/*.csv`) and nested (`<dir>/<exp>/data_app_*.csv`) layouts. Required. | none |
| `-o`, `--output` | Output HTML path. | `crab_export.html` in the current directory |

Example:

```bash
crab export data/leonardo/interference_2026-06-09_14-30-05-123456/ -o results.html
```

The exported file is a self-contained snapshot built from the dashboard template bundled in the
package (`src/crab/crab_dashboard.html`) with the data pre-loaded and the "drop folder" UI hidden.
The output file should not be committed to version control. See [Share results with `crab
export`](../using/results.md#share-results-with-crab-export) for what the file holds and when to
use it instead of the dashboard.

## `crab info`

Shows the installed version, the CRAB home, the install layout and the presets CRAB can use.

```bash
crab info [--json]
```

| Flag | Meaning | Default |
|------|---------|---------|
| `--json` | Print JSON instead of text. | off |

With `--json` the answer carries `schema`, `crab_version`, `crab_root`, `layout` and `presets`.
`layout` is `"checkout"` when CRAB runs from a git checkout and `"installed"` for a pip or pipx
install. Each entry of `presets` has the preset's `name` and `description`, plus its `scheduler`
(`"slurm"` or `"local"`), its `launcher` and its `hosts` (the host names; an empty list under
Slurm). A preset that does not parse keeps its `name` and `description` and gets an `error`
message in place of those three fields. `_common` and `example_preset` are not listed.

```bash
crab info
crab info --json
```

## `crab list-benchmarks`

Lists the installed benchmarks and the wrappers CRAB can find.

```bash
crab list-benchmarks [--json]
```

| Flag | Meaning | Default |
|------|---------|---------|
| `--json` | Print JSON instead of text. | off |

An installed benchmark is one with a [receipt](../extending/receipts.md), read from
`config/environments/` and then `local/receipts/`; a local receipt wins over an older one with the
same id. The wrappers are the `.py` files on the wrappers search path, and the first file with a
given path wins. A wrapper that cannot be loaded is still listed, marked `(unloadable)` in the text.

With `--json` the answer has two lists:

| Field | Meaning |
|-------|---------|
| `benchmarks` | One entry per receipt, sorted by id: `id`, `type`, `target_arch`, `binary_path`, `launcher_override` and `hooks`. |
| `wrappers` | One entry per wrapper: `file`, `relpath`, `path`, `group`, `loadable`, `benchmark_id`, `bench_name` and `metadata` (a list of `name`, `unit` and `conv` for each declared metric). A wrapper that failed to load has `loadable: false` and an `error`. |

```bash
crab list-benchmarks
crab list-benchmarks --json
```

For a fuller description of one wrapper, use [`crab wrappers show`](#crab-wrappers).

## `crab nodes`

Shows what the cluster, or a preset, offers.

```bash
crab nodes [-p PRESET] [--json]
```

| Flag | Meaning | Default |
|------|---------|---------|
| `-p`, `--preset` | Show this preset's nodes (for a local preset, its hosts). | none: ask Slurm |
| `--json` | Print JSON instead of text. | off |

- Without `-p`, or with a Slurm preset, CRAB runs `sinfo` and lists the partitions and the node
  names. When `sinfo` is missing or fails, the answer is `available: false` with a `note`. With a
  Slurm preset the JSON also has `scheduler: "slurm"`.
- With a local preset it never runs `sinfo`. It lists the preset's hosts and their core counts
  (`?` for a bare `localhost`, which has none). In JSON these are `hosts`, a list of `{name, cores}`
  with `cores` `null` for such a host, and `nodes`, their names; `scheduler` is `"local"`.
- A preset that does not exist or does not parse is an error: the command exits 1, and `--json`
  gives `{"schema": 2, "error": "..."}`.

```bash
crab nodes
crab nodes -p local --json
```

## `crab status`

Shows the state of jobs by id.

```bash
crab status [--json] [JOB_ID ...]
```

| Argument | Meaning | Default |
|----------|---------|---------|
| `JOB_ID ...` | Job ids to query: `local-<n>` or all digits. | none |
| `--json` | Print JSON instead of text. | off |

A job id is `local-<n>` for a job of the [local scheduler](../using/running-without-slurm.md) or
all digits for a Slurm job. Local ids are answered from this machine's state, so a local job can
only be queried on the host that submitted it. Slurm ids are asked of `squeue` first and then of
`sacct`.

```text
  local-3      RUNNING      (local)
  4711         COMPLETED    (sacct)
```

The columns are the id, the state and where the state came from. The states are Slurm's
(`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`, `TIMEOUT`, and so on), and a local job
can also be `LOST`; see [the local states](../using/running-without-slurm.md#states). An id that
no scheduler knows is `UNKNOWN`.

With `--json` the answer is `{"schema": 2, "jobs": [...]}`, one entry per id in the order given.
An entry has:

| Field | Meaning |
|-------|---------|
| `job_id` | The id as given. |
| `state` | The state. |
| `source` | Where it came from: `squeue`, `sacct`, `local`, `none` (Slurm does not know the id) or `invalid`. |
| `exit_code` | The exit code, when the scheduler reports one (`sacct`'s `ExitCode` column as it prints it, or a local job's worker exit code). |
| `error` | Only when the id could not be resolved: why. The state is then `UNKNOWN`. |

`error` appears for an id of any other shape (`source` is `invalid`) and for a local record that
cannot be used, such as one written on another host or damaged (`source` is `local`). One bad id
does not fail the rest. The text output does not show `error`.

```bash
crab status local-3 4711
```

## `crab history`

Lists the past experiments recorded in the `metadata.csv` registries.

```bash
crab history [-s SYSTEM] [--json]
```

| Flag | Meaning | Default |
|------|---------|---------|
| `-s`, `--system` | Read only this system's registry. | every system under `data/` |
| `--json` | Print JSON instead of text. | off |

It reads the registries under the `data/` folder of the CRAB checkout and does not read a custom
`datapath`. A system with no registry adds nothing, and the command still exits 0. With `--json`
the answer is `{"schema": 2, "experiments": [...]}`. Each experiment has the registry's columns
(`job_name`, `experiment_name`, `timestamp`, `numnodes`, `ppn`, `apps_list`, `status`, `tags`,
`relative_path`, `total_runs`, `failed_runs` and `seed`) plus `system` and `absolute_path`, the
experiment's folder. `seed` is the experiment's random-layout seed, or an empty string when there
is none (also for a registry written by an older CRAB, which has no `seed` column).

```text
$ crab history
1 experiment(s):
  [COMPLETED] leonardo/interference/a2a_vs_g500  2026-06-09_14-30-05  apps=alltoall_b graph500
```

```bash
crab history -s leonardo --json
```

See [Reading results](../using/results.md#read-from-the-command-line) for the registry and what to
do with the folders it lists.

## `crab cancel`

Cancels one job.

```bash
crab cancel [--json] JOB_ID
```

| Argument | Meaning | Default |
|----------|---------|---------|
| `JOB_ID` | The job to cancel: `local-<n>` or all digits, as for `crab status`. Required. | none |
| `--json` | Print JSON instead of text. | off |

A Slurm job is cancelled with `scancel`. A local job is sent a termination signal, and its
applications are stopped some seconds later (see [Checking and cancelling a
job](../using/running-without-slurm.md#checking-and-cancelling-a-job)).

With `--json` the answer is `{"schema": 2, "job_id": ..., "cancelled": true|false, "detail": ...}`.
A job that is already finished or does not exist, an id of any other shape, and a local record that
cannot be used all answer `cancelled: false` with a `detail` that says why, instead of failing.
`scancel` is never run for an id that is not all digits.

```bash
crab cancel 4711
crab cancel local-3 --json
```

## `crab logs`

Prints the end of a job's captured logs.

```bash
crab logs --data-dir DIR [--experiment NAME] [--json]
```

| Flag | Meaning | Default |
|------|---------|---------|
| `--data-dir` | The job's data directory. Required. | none |
| `--experiment` | Read this experiment's per-app error logs (`error_app_<i>.log`) instead of the job's Slurm logs. | the job's `slurm_output.log` and `slurm_error.log` |
| `--json` | Print JSON instead of text. | off |

Each file is cut to its last 200,000 bytes, and the text output marks a cut. A job log that is not
written yet is reported as such, not as an error. With `--experiment`, an experiment folder with no
`error_app_<i>.log` files prints that nothing failed, and a folder that does not exist under `DIR`
is an error (exit 1).

With `--json`:

| Mode | Fields |
|------|--------|
| job logs | `data_dir`, and `stdout` and `stderr`, each with `path`, `exists`, `content` and `truncated`. |
| `--experiment` | `data_dir` (the experiment's folder) and `files`, one entry per error log: `app_id`, `path`, `exists`, `content` and `truncated`. |

```bash
crab logs --data-dir data/leonardo/interference_2026-06-09_14-30-05-123456
crab logs --data-dir data/leonardo/interference_2026-06-09_14-30-05-123456 --experiment a2a_vs_g500
```

See [Reading results](../using/results.md#read-from-the-command-line).

## `crab parse`

Runs a wrapper's parser on saved output, with the same checks a run uses.

```bash
crab parse [--stderr FILE] [--dir DIR] [--args ARGS] [--set KEY=VALUE ...] [--json | --check EXPECTED_JSON] WRAPPER OUTPUT
```

| Argument | Meaning | Default |
|----------|---------|---------|
| `WRAPPER` | The wrapper file. A relative path is looked up under the wrappers folder (`CRAB_PATH_WRAPPERS`). Required. | none |
| `OUTPUT` | A file holding the application's stdout. Required. | none |
| `--stderr` | A file holding its stderr. | none |
| `--dir` | The folder holding files the application wrote. | the folder of `OUTPUT` |
| `--args` | The app's `args` string from the config. | none |
| `--set` | An extra app config key, as `KEY=VALUE`. Repeatable. | none |
| `--json` | Print the rows as JSON. `--check` takes precedence. | CSV |
| `--check` | Compare the rows with this JSON list and exit 1 on a difference. | no comparison |

It prints the rows as CSV, or with `--json` as `{"rows": [...], "legacy_shape": false}`. With
`--check` it prints `ok: N rows match FILE` for a match, and lists the differences on stderr and
exits 1 otherwise. A parse error exits 2 with the reason.

```bash
crab parse myapp/myapp.py output.txt --args "-n 4" --json
crab parse myapp/myapp.py samples/example/stdout.txt --check samples/example/expected.json
```

See [Writing a wrapper](../extending/wrappers.md).

## `crab wrappers`

Lists, inspects, updates, tests and creates wrappers.

```bash
crab wrappers list [--json]
crab wrappers show [--json] RELPATH
crab wrappers status [--json]
crab wrappers pull [--json]
crab wrappers test [--strict] [APP ...]
crab wrappers new [--name NAME] [--local | --dir DIR] APP
```

| Subcommand | What it does |
|---|---|
| `list` | Every wrapper on the search path, and where its binary comes from: `config`, `receipt`, `path`, `missing`, or `error` when the lookup itself failed. Wrappers that cannot be loaded are listed with the error. |
| `show` | Describes one wrapper by its path under the wrappers folders (the first folder holding it wins; an old `suite/name.py` path resolves to the file's new group folder). Only files `list` shows qualify. Reports what it is (the module docstring, else the first paragraph of the nearest `README.md` below the wrappers folder, with its source), the declared metrics (name, unit, role, convergence flag), the executable and where its binary comes from, and, for a file committed in a git clone, that clone's commit and a GitHub URL to the file at it (null for the URL when `origin` is not on GitHub). It prints neither source text nor sample cases. Exits 2 for an unknown wrapper, an absolute path, or a path with `..`. |
| `status` | Reports the shared wrappers clone (the folder `crab update` manages): its commit (with the commit's subject line) and branch, the tracked files with local edits (staged or not; untracked files are not listed), the commit GitHub's `origin` has for that branch (`git ls-remote`, 20 s timeout, never prompting for credentials), and `state`: `up to date`, `behind`, `ahead`, `diverged`, or `unknown` when the folder is not a clone, has no commits, or GitHub cannot be reached (`upstream.error` says why). It reads without writing and never fetches, so `behind` without counts means GitHub has commits this clone has not fetched; counts appear once the commit is local. `upstream.url` is the `origin` URL without any `user:password@` part. A detached HEAD is compared with `origin`'s HEAD. Exits 0 even when the folder is missing or GitHub is unreachable. |
| `pull` | Fast-forwards the shared wrappers clone with `git pull --ff-only`, or clones it when the folder is missing (the same code as the wrappers part of `crab update`). Reports `action` (`updated`, `up to date`, `cloned`) with the old and new commit; for a folder that is not a clone it reports `action: "skipped"` with a `detail` and exits 0. Exits 1, changing nothing, when tracked files have local edits or the pull fails (for example a diverged clone, or a detached HEAD, which is not on a branch). |
| `test` | Runs the sample cases (`<app>/samples/<case>/`) of the named apps, or of all apps, and compares each result with its `expected.json`. With `--strict`, a wrapper that no case covers fails unless `unverified.txt` in its folder lists it. Exits 1 on any failure. |
| `new` | Creates `APP/NAME.py` (default name: the app), a README and a passing example sample, in the shared wrappers folder, in `local/wrappers/` with `--local`, or in `DIR`. |

| Flag | Subcommands | Meaning | Default |
|------|-------------|---------|---------|
| `--json` | `list`, `show`, `status`, `pull` | Print JSON instead of text. | off |
| `--strict` | `test` | Fail on wrappers with no sample unless `unverified.txt` lists them. | off |
| `--name` | `new` | The wrapper's file name. | the app name |
| `--local` | `new` | Create it in `local/wrappers/` (private, untracked). | the shared wrappers folder |
| `--dir` | `new` | Create it in this wrappers folder instead. | the shared wrappers folder |

`RELPATH` is a wrapper path relative to the wrappers folders, such as `blink/alltoall/a.py`. `APP`
is an app folder name, such as `hpl`; for `test`, name none to test every app.

```bash
crab wrappers list
crab wrappers new myapp --local
crab wrappers test myapp --strict
```

See [Writing a wrapper](../extending/wrappers.md).

## `crab receipts`

Manages benchmark [receipts](../extending/receipts.md). It has one subcommand, `set`.

### `crab receipts set`

Records where a benchmark's binary lives, without the interactive wizard.

```bash
crab receipts set --binary PATH [--pre-run CMD ...] [--launcher srun|mpirun] [--allow-missing] [--json] ID
```

| Argument | Meaning | Default |
|----------|---------|---------|
| `ID` | The wrapper's `benchmark_id`. Required. | none |
| `--binary` | Path to the executable. Required. | none |
| `--pre-run` | A command to run before each launch. Repeatable. | none |
| `--launcher` | `srun` or `mpirun`. Sets this benchmark's launcher kind, which overrides the preset's `launcher` for this benchmark. Any other value is refused. | the preset's launcher |
| `--allow-missing` | Record the path even if it does not exist here. Without it, a `PATH` that does not exist is refused. | off |
| `--json` | Print the saved receipt as JSON. | off |

It writes `local/receipts/ID.json`.

```bash
crab receipts set mybench --binary /scratch/me/mybench/bin/mybench
crab receipts set mybench --binary /scratch/me/mybench/bin/mybench --launcher mpirun --json
```

See [Receipts](../extending/receipts.md#registering-an-installed-binary-with-crab-receipts-set).

## `crab update`

Updates this install and its wrappers checkout. By default it goes to the release; `--head`
follows the branches instead.

```bash
crab update [--head | --to-release] [--json]
```

| Flag | Meaning | Default |
|------|---------|---------|
| `--head` | Follow the branch HEAD of both CRAB and the wrappers, not the release. For developers. | off |
| `--to-release` | Same as the default, kept so older scripts keep working. Cannot be combined with `--head`. | on |
| `--json` | Print the result as JSON. Both the `crab` and the `wrappers` block carry `ref`, `"release"` or `"head"`. | off |

- Release (the default). A checkout moves to the newest release tag of its line (`vX.Y.Z`, or
  `vX.Y.Z+sbatchman` on the SbatchMan line); with no tag at all it stays on its branch and
  fast-forwards it. The wrappers clone moves to the tag `vX.Y.Z` of the CRAB version the update
  ends on, so the two always match. The dashboard's installer runs `crab update --to-release`
  after cloning.
- Wrappers tag missing. If the wrappers repository has no tag `vX.Y.Z` yet, an existing
  wrappers clone stays where it is, the command prints `no wrappers tag vX.Y.Z yet, left at
  <commit>`, and it still succeeds (action `kept`). A wrappers clone made by this run reports
  `cloned` with the same line in its detail and stays on the default branch.
- `--head`. CRAB fast-forwards its branch (`git pull --ff-only`) and the wrappers clone runs
  `git pull --ff-only` on its checked-out branch. A wrappers clone a release update left on a tag
  goes back to the default branch first. A CRAB checkout left on a release tag by a release
  update is refused, before anything changes: run `git checkout <branch>` in the checkout, then
  `crab update --head`.
- Pip install. When CRAB was installed with pip or pipx there is no git checkout to move, so
  CRAB itself is left alone and the output says to run `pipx upgrade crab-hpc`. The wrappers
  clone under the install's home is still cloned or moved, to the tag of the installed version
  (or to the branch with `--head`). The `crab` block reads `"mode": "pip"` with a `hint`.
- Dependencies are reinstalled only when `pyproject.toml` changed. An old `crab` distribution
  (the name before `crab-hpc`) is uninstalled first.
- It stops without changing anything if tracked files have local edits, in the CRAB checkout or
  in the wrappers clone. Keep per-machine changes
  in `local/`. If the edited file is `config/presets.json`, which moved to
  `src/crab/presets.json`, the message gives the exact steps to migrate it.
- The wrappers folder is cloned if it is missing, and left alone if it is part of the CRAB
  checkout or holds files of its own.

```bash
crab update
crab update --head
crab update --json
```

## `crab worker` (internal)

Hidden subcommand executed *by the generated Slurm job* on the compute nodes: the second phase of
the [two-phase model](../concepts/architecture.md#the-two-phase-execution-model). It reads the
config the orchestrator wrote into the work directory and runs the experiments.

```bash
crab worker --workdir <dir> [--log-level LEVEL]
```

| Flag | Meaning | Default |
|------|---------|---------|
| `--workdir` | The prepared work directory. Required. | none |
| `--log-level` | Log verbosity, as for `crab run`. | `INFO` |

!!! danger "Do not run `crab worker` by hand"
    It is invoked automatically inside the batch job and expects a fully prepared work directory
    (`config.json` + `environment.json`) and an allocation. It is hidden from the command list and
    is not a user-facing command.
