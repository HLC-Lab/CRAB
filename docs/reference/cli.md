# CLI commands

CRAB installs a single console script, `crab`, with four subcommands. Activate the virtual
environment first (`source .venv/bin/activate`); shell tab-completion is registered during install.

## `crab setup`

Launches the interactive setup wizard to obtain/build benchmarks and write their
[receipts](../extending/receipts.md).

```bash
crab setup
```

No arguments. See [Installation → benchmark setup](../using/installation.md#set-up-benchmarks-crab-setup).

## `crab run`

Runs an experiment: prepares the output directory and submits the Slurm job.

```bash
crab run <config.json> [-p PRESET] [--log-level LEVEL]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `config.json` | ✅ | Path to the [experiment config](configuration.md). Tab-completes `.json` files. |
| `-p`, `--preset` | — | Preset name from `config/presets.json` or `local/presets.json`. Tab-completes known presets. See [resolution order](../using/presets.md#selecting-your-preset). |
| `--log-level` | — | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`. Default `INFO`. |

See [Running an experiment](../using/running.md).

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
crab wrappers test [APP ...] [--strict]
crab wrappers new APP [--name NAME] [--local | --dir DIR]
```

| Subcommand | What it does |
|---|---|
| `list` | Every wrapper on the search path, and where its binary comes from: `config`, `receipt`, `path`, `missing`, or `error` when the lookup itself failed. Wrappers that cannot be loaded are listed with the error. |
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
crab receipts set ID --binary PATH [--pre-run CMD ...] [--launcher LAUNCHER] [--allow-missing] [--json]
```

Writes `local/receipts/ID.json`. `--launcher` sets this benchmark's launcher kind, `srun` or `mpirun`, which overrides the preset's `launcher` for this benchmark. Any other value makes runs of this benchmark fail at setup. It refuses a `PATH` that does not exist unless
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
