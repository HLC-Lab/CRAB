# ADR-030 · Version what partners keep, tag each partner release, update with one command

- **Date:** 2026-09-30
- **Status:** accepted

## Context

Partners are about to install CRAB from the `sbatchman` branch and keep files produced by it on
their own machines: experiment configs, saved SbatchMan campaigns, SbatchMan jobs files that run
`crab worker`, result folders, receipts, their own presets. The roadmap still changes a lot before
v1.0 (packaging, a typed config schema, a new wrapper base class, execution backends). Until now
none of those files carried a format version. The dashboard read the cluster's contract number
from `crab info --json` but never compared it. There were no tags, so a partner was always on
whatever the branch held that day. A partner who upgrades must not find that old files are
misread or refused without a clear reason.

## Decision

**Frozen v1 surfaces.** These are the surfaces partners build on. A later release accepts or
migrates them, and removes one only after at least one release in which it gives a deprecation
warning:

- `crab worker --workdir <dir> [--log-level <level>]`, including the fallback to the process
  environment when the work directory has no `environment.json` (ADR-027).
- The environment variables the SbatchMan guide (on `sbatchman`) tells partners to set (`CRAB_ROOT`,
  `CRAB_SYSTEM`, `CRAB_PATH_WRAPPERS`, `SBATCHMAN_JOB_DIR`).
- Result files: `data_app_<id>.csv` keeps `run_id` and `msg_size` as its first two columns,
  followed by one `<app>_<metric>_<unit>` column per metric. The per-system `metadata.csv`
  registry keeps its column names; new columns are only added at the end.
- The `crab <command> --json` contract, numbered by `CONTRACT_SCHEMA`.

**Format versions in the files themselves.**

- `config.json` carries `schema_version` (currently 1). A config without it is version 1. The
  engine refuses a newer or malformed version with a message pointing at `crab update`.
- Saved SbatchMan campaigns and wrappers get their own version fields when those formats are
  next touched (the campaign spec and the wrapper contract).
- The package version has a single source, `crab.__version__`.

**Skew is visible.** On connect, the dashboard compares the cluster's `CONTRACT_SCHEMA` with its
own. When they differ, it shows which side to update instead of failing later on a field it
cannot read.

**Per-machine files stay out of git.** Preset overrides (`local/presets.json`), receipts
(`local/receipts/`) and private wrappers live in the git-ignored `local/` folder, so `git pull`
never conflicts with them. Receipts in the old `config/environments/` folder are still read.

**Tags and updates.** Each partner release is tagged `vX.Y.Z` on the product line and
`vX.Y.Z+sbatchman` on `sbatchman` (the ADR-028 scheme, now used before 1.0 as well). The
installer will clone the newest tag of its line instead of the branch tip. A `crab update`
command, added before the first tag, will move an installation to the newest tag of its line,
reinstall dependencies only when they changed, update the wrappers checkout, and refuse to run
over local edits to tracked files.

## Alternatives considered

- Follow the branch with no tags, relying on only merging tested work: less release work, but
  a partner cannot say which version produced a result or stay on a known one.
- Tag only at v1.0: simpler history, but every partner upgrade before then is a jump to an
  unnamed state.
- Keep per-machine files in the tracked `config/` folder: fewer places to look, but every
  local edit turns into a merge conflict on the next pull.
- Detect skew by comparing package versions instead of the contract number: stricter, but
  it would flag every harmless patch release as an incompatibility.

## Consequences

Old configs and result folders keep working without changes. Every future change to a frozen
surface needs either a migration or a deprecation period, which slows down some cleanups
planned for v1.0 (the wrapper base class, the typed config schema). Releases now include a
tagging step. `CONTRACT_SCHEMA` and `schema_version` must be bumped deliberately, with the old
value still handled.
