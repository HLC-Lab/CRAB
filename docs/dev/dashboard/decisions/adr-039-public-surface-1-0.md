# ADR-039 · The public surface CRAB 1.0 keeps stable: the CLI, its data contracts and the wrapper base class

- **Date:** 2026-10-21
- **Status:** accepted; the preset parts are superseded by [ADR-041](adr-041-presets-in-crab-wrappers.md) (2026-10-23); the "from 1.0" promise is superseded by [ADR-042](adr-042-stable-from-2-0.md) (2026-10-27): the surface list below is what 2.0 promises, and 1.x releases may change it

## Context

ADR-030 froze the surfaces partners build on and ADR-031 froze the wrapper contract, but both were
written for the 0.x releases, and neither says what the 1.0 version number promises. Without that
statement "semver" has no content: a partner cannot tell whether a renamed Python function, a
reworded log line or a new `--json` key breaks them. The roadmap's milestone M6 proposed a small
Python API (`crab.run()` and friends) as part of the promise.

There is no such API today. `src/crab/__init__.py` holds only `__version__`. Everything else a
person can import is an internal module that the CLI, the engine and the dashboard happen to share,
and several of them are still being reshaped. The things people do depend on are all outside
Python: the command line, the JSON and CSV that CRAB writes, the environment variables, the files
in `local/`, and the one class that wrappers inherit from.

## Decision

From 1.0, the semver promise covers the nine surfaces below and nothing else. For each one, "stable"
means the same thing: a change that breaks an existing user needs a new major version, and before
that a minor release that warns about it (ADR-030's deprecation rule). Additive changes are minor
releases. The releases before 1.0 follow ADR-030 as written.

1. **CLI commands and flags.** The commands are `setup`, `run`, `validate`, `web`, `export`,
   `info`, `list-benchmarks`, `nodes`, `status`, `history`, `cancel`, `logs`, `parse`, `wrappers`,
   `receipts` and `update` (`src/crab/cli/main.py:275`), with their flags, positional
   arguments and exit statuses. A command or flag is not removed or given a different meaning in a
   minor release. New commands and flags are additive. The human-readable output (tables, log
   lines, wording, colors) is **not** stable and must not be parsed; scripts use `--json`.
2. **`--json` outputs.** Every `crab <command> --json` answer carries `schema`, the number
   `CONTRACT_SCHEMA` (`src/crab/cli/contract.py:40`, currently 2). Adding a key is additive.
   Removing a key, renaming it, or changing its type or meaning bumps `CONTRACT_SCHEMA` and is a
   major change. The dashboard compares this number on connect (ADR-030).
3. **The `crab worker` seam.** `crab worker --workdir <dir> [--log-level <level>]`, including the
   fallback to the process environment when the work directory has no `environment.json`. The
   command is hidden from `--help` (`cli/main.py:397`) because people do not run it by hand, but
   SbatchMan jobs files and the generated Slurm script do.
4. **The config file schema.** The experiment config `crab run` and `crab validate` read. It
   carries `schema_version`, `CONFIG_SCHEMA_VERSION` (`src/crab/core/engine.py:23`, currently 2); a
   config without it is version 1. A new CRAB reads every earlier version, migrating where needed.
   An older CRAB refuses a newer config with a message pointing to `crab update`
   (`engine.py:26-41`). New optional fields are additive; making a field mandatory, removing one or
   changing its meaning bumps the version.
5. **The presets file format.** The shape of `presets.json`: the fields documented in
   [presets.json format](../../../reference/presets.md), the three merge layers and the replace
   rule of ADR-038, and the `__CWD__` token. The file has no version number. Fields are added, not
   changed. A field that must go is first read with a warning; after that it is refused with an
   error that names its replacement, as the removed `CRAB_WL_MANAGER` family is today.
6. **Result files.** `data_app_<id>.csv` keeps `run_id` and `msg_size` as its first two columns
   (`src/crab/core/data/utils.py:82`), then the key columns, then one `<app>_<metric>_<unit>` column
   per metric (ADR-031). The per-system `metadata.csv` keeps the column names and order in
   `_HISTORY_COLUMNS` (`cli/contract.py:116`); new columns are only added at the end. The
   `crab_run.json` keys written by `core/provenance.py` (`results_schema`, `crab_version`,
   `crab_commit`, `wrapper_api`, `started`, `nodes`, `wrappers`, `apps`, `execution`) keep their
   meaning. `RESULTS_SCHEMA` (`provenance.py:24`, currently 1) is bumped on a breaking change.
7. **The wrapper base class.** `crab.wrappers.base.base` and the helpers exported beside it
   (`sizeof_fmt`, `MissingBinaryError`), as documented in the
   [wrapper reference](../../../reference/api.md). The version is `wrapper_api`
   (`src/crab/wrappers/base.py:20`, currently 1), and every job records it in `crab_run.json`. A
   wrapper written for `wrapper_api = 1` keeps working for the whole 1.x line. The one exception is
   the old one-list-per-metric return of `read_data()`, which ADR-031 already schedules to stop at
   1.0.
8. **Receipts.** The JSON file `<benchmark_id>.json` in `local/receipts/` (or the user config
   directory in an install), with the fields `id`, `type`, `binary_path`, `launcher_override`,
   `hooks` and the optional `target_arch`. A receipt written by any 1.x release is read by every
   later 1.x release, and the older `config/environments/` location keeps being read. New fields
   are additive.
9. **Environment variables.** The names CRAB reads and sets: `CRAB_ROOT`, `CRAB_SYSTEM`,
   `CRAB_PATH_WRAPPERS`, `CRAB_PRESET`, `CRAB_HOME` and `CRAB_PATH_PRESETS` (`src/crab/paths.py`,
   `cli/orchestrator.py`), and the `CRAB_PATH_<ID>` family injected from receipts. Their names and
   meanings do not change; `CRAB_ROOT` and `__CWD__` keep meaning the home of the active layout
   (ADR-038). `SBATCHMAN_JOB_DIR` belongs to the SbatchMan integration on the `sbatchman` branch
   and is not part of this list.

**Not covered.** These can change in any release, and the release notes mention them only when it
helps:

- Python imports other than `crab.wrappers.base`: the recipe base class
  (`crab.setup.recipes.base`), the engine, the schedulers, `crab.paths`, `crab.cli.*` and every
  other module, and the module layout itself. There is no `crab.run()` or other programmatic entry point.
- The dashboard's HTTP API (`/api/...`). It is internal between the dashboard's own frontend and
  backend, which ship together. The dashboard reads the cluster only through item 2.
- The dashboard's own state files (profiles, the library, caches) and its static assets.
- Human-readable CLI output, log wording and the contents of log files.

## Alternatives considered

- A Python API in 1.0 (`crab.run()`, `crab.validate()`): nothing is exported today, and any function we picked would freeze the engine's internals
  (settings objects, scheduler classes, the logger) along with it. Scripts that need CRAB from
  Python can call the CLI with `--json`. This is deferred to a 1.x minor release, where it is
  additive and can be designed against real users. The roadmap's M6 moves there.
- No written promise: partners keep SbatchMan jobs files, configs, receipts and result folders on
  their own machines, and a dashboard talks to clusters running other versions. They need to know
  which parts will hold, and ADR-030 only covers part of the list.
- Make the dashboard's HTTP API public: it would turn every frontend change into an API change, and
  the same data is already available through `--json`.
- Promise the human output as well: it would freeze wording and layout, and `--json` already exists
  for scripts.

## Consequences

A 1.x minor release may add commands, flags, `--json` keys, config fields, preset fields, CSV columns
at the end of a file, `crab_run.json` keys, receipt fields and environment variables, and may change
anything in the "not covered" list. It may not remove or rename anything in the nine surfaces,
change a column's position or meaning, or reuse a flag for something else.

A removal follows ADR-030: at least one minor release warns first (a message on stderr, never in
`--json` output), then the next major removes it. A format version (`CONTRACT_SCHEMA`,
`CONFIG_SCHEMA_VERSION`, `wrapper_api`, `RESULTS_SCHEMA`) is bumped only in a major release, except
that a bump which only adds a reader for the new form and still accepts the old one is allowed in a
minor.

The cost is that cleanups of these surfaces now wait for 2.0. The benefit is that the internals can
still be rewritten freely, which is where the planned work is. When the Python API arrives, it gets
its own ADR and its own entry in this list.
