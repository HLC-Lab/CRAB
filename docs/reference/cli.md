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
| `-p`, `--preset` | — | Preset name from `config/presets.json`. Tab-completes known presets. See [resolution order](../using/presets.md#selecting-your-preset). |
| `--log-level` | — | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`. Default `INFO`. |

See [Running an experiment](../using/running.md).

## `crab tui`

Launches the Textual interface. Requires the optional `tui` dependencies; if missing, the command
offers to install them.

```bash
crab tui
```

No arguments. See [Running an experiment → TUI](../using/running.md#tui).

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
