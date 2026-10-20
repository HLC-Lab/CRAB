# CLI commands

CRAB installs a single console script, `crab`. Activate the virtual
environment first (`source .venv/bin/activate`); shell tab-completion is registered during install.

## `crab setup`

Launches the interactive setup wizard to obtain/build benchmarks and write their
[receipts](../extending/receipts.md).

```bash
crab setup
```

No arguments. See [Installation → benchmark setup](../using/installation.md#set-up-benchmarks-crab-setup).

## `crab run`

Runs an experiment: prepares the output directory and submits the job, to Slurm or to the local
scheduler, as the preset says.

```bash
crab run <config.json> [-p PRESET] [--log-level LEVEL]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `config.json` | ✅ | Path to the [experiment config](configuration.md). Tab-completes `.json` files. |
| `-p`, `--preset` | — | Preset name from `config/presets.json` or `local/presets.json`. Tab-completes known presets. See [resolution order](../using/presets.md#selecting-your-preset). |
| `--log-level` | — | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`. Default `INFO`. |

See [Running an experiment](../using/running.md).

## `crab info`

Shows the installed version, the checkout root and the presets CRAB can use.

```bash
crab info [--json]
```

With `--json` the answer carries `schema` (the version of the `--json` contract, which is `2`),
`crab_version`, `crab_root` and `presets`. Each entry of `presets` has the preset's `name` and
`description`, plus its `scheduler` (`"slurm"` or `"local"`), its `launcher` and its `hosts` (the
host names; an empty list under Slurm). A preset that does not parse keeps its `name` and
`description` and gets an `error` message in place of those three fields. `_common` and
`example_preset` are not listed.

## `crab nodes`

Shows what the cluster, or a preset, offers.

```bash
crab nodes [-p PRESET] [--json]
```

| Argument | Description |
|----------|-------------|
| `-p`, `--preset` | Show this preset's nodes. Without it, `crab nodes` asks Slurm. |

- Without `-p`, or with a Slurm preset, CRAB runs `sinfo` and lists the partitions and the node
  names. When `sinfo` is missing or fails, the answer is `available: false` with a `note`. With a
  Slurm preset the JSON also has `scheduler: "slurm"`.
- With a local preset it never runs `sinfo`. It lists the preset's hosts and their core counts
  (`?` for a bare `localhost`, which has none). In JSON these are `hosts`, a list of `{name, cores}`
  with `cores` `null` for such a host, and `nodes`, their names; `scheduler` is `"local"`.
- A preset that does not exist or does not parse is an error: the command exits 1, and `--json`
  gives `{"schema": 2, "error": "..."}`.

## `crab status`

Shows the state of jobs by id.

```bash
crab status [JOB_ID ...] [--json]
```

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

## `crab cancel`

Cancels one job.

```bash
crab cancel JOB_ID [--json]
```

`JOB_ID` is `local-<n>` or all digits, as for `crab status`. A Slurm job is cancelled with
`scancel`. A local job is sent a termination signal, and its applications are stopped some
seconds later (see [Checking and cancelling a job](../using/running-without-slurm.md#checking-and-cancelling-a-job)).

With `--json` the answer is `{"schema": 2, "job_id": ..., "cancelled": true|false, "detail": ...}`.
A job that is already finished or does not exist, an id of any other shape, and a local record that
cannot be used all answer `cancelled: false` with a `detail` that says why, instead of failing.
`scancel` is never run for an id that is not all digits.

## `crab tui`

Launches the Textual interface. Requires the optional `tui` dependencies; if missing, the command
offers to install them.

```bash
crab tui
```

No arguments. See [Running an experiment → TUI](../using/running.md#tui).

## `crab export`

Generates a **self-contained HTML dashboard** from a results directory — the CSV data is embedded
into the file, so the result can be emailed, committed to a repo, or opened directly anywhere
without any server or import step.

```bash
crab export <data_dir> [-o OUTPUT]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `data_dir` | ✅ | Path to a results directory containing experiment CSVs. CRAB scans both flat (`<dir>/*.csv`) and nested (`<dir>/<exp>/data_app_*.csv`) layouts. |
| `-o`, `--output` | — | Output HTML path. Default: `crab_export.html` in the current directory. |

Example:

```bash
crab export data/leonardo/interference_2026-06-09_14-30-05-123456/ -o results.html
```

The exported file is a self-contained snapshot built from the dashboard template bundled in the package (`src/crab/crab_dashboard.html`) with the data pre-loaded and the "drop folder" UI hidden. The output file should not be committed to version control. For browsing **fresh** results from the cluster without exporting, open the template directly — see [Reading results → The dashboard](../using/results.md#the-dashboard).

## `crab update`

Updates this install and its wrappers checkout.

```bash
crab update [--to-release] [--json]
```

- A checkout on a branch is fast-forwarded (`git pull --ff-only`).
- A checkout on a release tag moves to the newest tag of its line (`vX.Y.Z`, or
  `vX.Y.Z+sbatchman` on the SbatchMan line).
- `--to-release` switches a branch checkout to the newest release tag of that branch's line, and
  stays on the branch if there is none. The dashboard's installer runs it after cloning.
- Dependencies are reinstalled only when `pyproject.toml` changed.
- It stops without changing anything if tracked files have local edits. Keep per-machine changes
  in `local/`.
- The wrappers folder is pulled if it is its own git clone, cloned if it is missing, and left
  alone if it is part of the CRAB checkout.

## `crab wrappers`

```bash
crab wrappers list [--json]
crab wrappers show RELPATH [--json]
crab wrappers status [--json]
crab wrappers pull [--json]
crab wrappers test [APP ...] [--strict]
crab wrappers new APP [--name NAME] [--local | --dir DIR]
```

| Subcommand | What it does |
|---|---|
| `list` | Every wrapper on the search path, and where its binary comes from: `config`, `receipt`, `path`, `missing`, or `error` when the lookup itself failed. Wrappers that cannot be loaded are listed with the error. |
| `show` | Describes one wrapper by its path under the wrappers folders (the first folder holding it wins; an old `suite/name.py` path resolves to the file's new group folder). Only files `list` shows qualify. Reports what it is (the module docstring, else the first paragraph of the nearest `README.md` below the wrappers folder, with its source), the declared metrics (name, unit, role, convergence flag), the executable and where its binary comes from, and, for a file committed in a git clone, that clone's commit and a GitHub URL to the file at it (null for the URL when `origin` is not on GitHub). It prints neither source text nor sample cases. Exits 2 for an unknown wrapper, an absolute path, or a path with `..`. |
| `status` | Reports the shared wrappers clone (the folder `crab update` manages): its commit (with the commit's subject line) and branch, the tracked files with local edits (staged or not; untracked files are not listed), the commit GitHub's `origin` has for that branch (`git ls-remote`, 20 s timeout, never prompting for credentials), and `state`: `up to date`, `behind`, `ahead`, `diverged`, or `unknown` when the folder is not a clone, has no commits, or GitHub cannot be reached (`upstream.error` says why). It reads without writing and never fetches, so `behind` without counts means GitHub has commits this clone has not fetched; counts appear once the commit is local. `upstream.url` is the `origin` URL without any `user:password@` part. A detached HEAD is compared with `origin`'s HEAD. Exits 0 even when the folder is missing or GitHub is unreachable. |
| `pull` | Fast-forwards the shared wrappers clone with `git pull --ff-only`, or clones it when the folder is missing (the same code as the wrappers part of `crab update`). Reports `action` (`updated`, `up to date`, `cloned`) with the old and new commit; for a folder that is not a clone it reports `action: "skipped"` with a `detail` and exits 0. Exits 1, changing nothing, when tracked files have local edits or the pull fails (for example a diverged clone, or a detached HEAD, which is not on a branch). |
| `test` | Runs the sample cases (`<app>/samples/<case>/`) of the named apps, or of all apps, and compares each result with its `expected.json`. With `--strict`, a wrapper that no case covers fails unless `unverified.txt` in its folder lists it. Exits 1 on any failure. |
| `new` | Creates `APP/NAME.py` (default name: the app), a README and a passing example sample, in the shared wrappers folder, in `local/wrappers/` with `--local`, or in `DIR`. |

See [Writing a wrapper](../extending/wrappers.md).

## `crab parse`

Runs a wrapper's parser on saved output, with the same checks a run uses.

```bash
crab parse WRAPPER OUTPUT [--stderr FILE] [--dir DIR] [--args ARGS] [--set KEY=VALUE ...] [--json | --check EXPECTED_JSON]
```

Prints the rows as CSV, or as JSON with `--json`. `--dir` is the folder holding files the
application wrote (default: the folder of `OUTPUT`); `--set` passes an extra app config key.
With `--check`, it compares the rows with a JSON list and exits 1 on a difference. A parse error
exits 2 with the reason.

## `crab receipts set`

Records where a benchmark's binary lives, without the interactive wizard.

```bash
crab receipts set ID --binary PATH [--pre-run CMD ...] [--launcher srun|mpirun] [--allow-missing] [--json]
```

Writes `local/receipts/ID.json`. `--launcher` sets this benchmark's launcher kind, `srun` or `mpirun`, which overrides the preset's `launcher` for this benchmark. Any other value is refused. Leave it out to use the preset's launcher. It refuses a `PATH` that does not exist unless
`--allow-missing` is given. See [Receipts](../extending/receipts.md).

## `crab worker` (internal)

Hidden subcommand executed *by the generated Slurm job* on the compute nodes — the second phase of
the [two-phase model](../concepts/architecture.md#the-two-phase-execution-model). It reads the
config the orchestrator wrote into the work directory and runs the experiments.

```bash
crab worker --workdir <dir> [--log-level LEVEL]
```

!!! danger "Do not run `crab worker` by hand"
    It is invoked automatically inside the batch job and expects a fully prepared work directory
    (`config.json` + `environment.json`) and an allocation. It is hidden from the command list and
    is not a user-facing command.
