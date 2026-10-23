# ADR-041 · Cluster presets live in crab-wrappers; users patch them with per-user overrides

- **Date:** 2026-10-23
- **Status:** accepted (supersedes the preset parts of ADR-038 and ADR-039)

## Context

All presets ship in one package file, `src/crab/presets.json`. `src/crab/cli/presets.py` merges
three layers: the shipped file, the user's file (`~/.config/crab/presets.json` or
`CRAB_PATH_PRESETS`) and the checkout's `local/presets.json`. A preset with the same name replaces
the earlier one whole (ADR-038). A `_common` entry holds the one setting every preset shares,
`CRAB_ROOT=__CWD__`.

This hurts in three ways:

- To set an account, a user copies a whole preset into a personal file and keeps that copy in step
  with the shipped one by hand.
- Presets change only with a CRAB release, although they are cluster facts, not engine code.
- A partner's preset, such as the NPLB campaign's, lives on a branch and cannot be used elsewhere.

The SbatchMan call asked for the opposite: anyone can write a preset and use it on any cluster.
Wrappers already work this way. They live in the crab-wrappers repository, which CRAB clones,
pins to its own release tag and updates with `crab update` (ADR-030, ADR-038).

## Decision

**Everything moves.** Every preset, `local` included, is a file in crab-wrappers, and the package
ships none. `_common` becomes an engine default: `CRAB_ROOT=__CWD__` is set in
`load_environment_config` in `src/crab/cli/orchestrator.py`. A `_common` key in any presets file is
an error that says it was removed and to put the env in each preset.

**Layout.** crab-wrappers holds `presets/<cluster>.json`. Each file is a JSON object of presets in
the format of the [presets reference](../../../reference/presets.md), and one file may hold several
variants (a cluster and its GPU partition, say).

**Discovery.** Presets are found through the shared wrapper folders, the same ones wrappers use:
the `CRAB_PATH_WRAPPERS` entries, else `<home>/wrappers`. `local/wrappers` is not searched. Presets
ride the same release-tag pin and `crab update` as wrappers. There is no new setting.

**Overrides patch, they do not replace.** A user preset with the same name as a shared one patches
it. `"extends": "<name>"` makes a new variant of an existing preset. The failure cases are errors
that name the file:

- An entry with no shared base, and no `extends`, must be a complete preset. The message says to
  run `crab update`, since a missing clone looks the same.
- An `extends` target that does not exist, or a cycle of `extends`, is an error.
- The same name twice in one layer is an error naming both files.
- Across several shared folders the first folder wins, as in wrapper lookup.

A variant inherits the effective parent, so a user's patch of `leonardo` also reaches a shared
`leonardo_dcgp` that extends it. Jobs never re-read preset files: the resolved preset is already
snapshotted as `execution.json` at submit time (`src/crab/core/engine.py`).

**Merge rules, per field.**

| Field | Rule |
|---|---|
| `env` | merged by key; a value of `null` removes the key |
| `sbatch`, launcher `flags` | only an item written as `--name=value` matches by name: it replaces the shared item with the same `--name=` in place. Any other item is appended: a bare option such as `--exclusive`, a short form, or an option and its value written as two items (as mpirun flags often are) |
| mpirun `export` | names are added to the shared list, with no duplicates |
| `header` lines | the override's lines run after the shared ones |
| `notes` | the override's notes are added after the shared ones |
| scheduler, launcher, `exclusive`, hosts, hostfile, launcher command and dialect, `description` | replaced |

Two consequences are accepted. Because only `--name=value` items match, a short form such as `-A`
does not match `--account`, so shared presets use long forms only, and the crab-wrappers CI checks
it. A patch cannot remove a shared flag or header line: the way to drop one is a complete preset
under a new name.

**Override location.** The layout's per-machine directory, `paths.local_path(root, "presets")`:
`<repo>/local/presets/*.json` in a checkout, `~/.config/crab/presets/*.json` in an install (or
`$CRAB_HOME/config/presets/`). Files are read in name order. `CRAB_PATH_PRESETS` now names this
directory, and `crab info` prints it.

**The old override files are refused.** `~/.config/crab/presets.json` and `local/presets.json` are
no longer read. If one exists, CRAB stops with a message that says to move the presets to the new
directory and points to the docs. This ships in release 1.2.0.

**Required marks for per-user values.** A value only the user knows is written as a mark,
`<YOUR_[A-Z0-9_]+>`, for example `--account=<YOUR_ACCOUNT>`. Marks are allowed only in keyed items
(env values, sbatch and launcher flags, the launcher command) and never in header lines, since
a header line cannot be patched by key. `crab run`, `crab validate` and `crab nodes` refuse a
preset with an unset mark and print the override snippet to paste. Marks are always required: an
optional mark that is silently dropped would hide documentation.

**Four kinds of per-user value.** They decide where a value goes, so that notes stay rare:

1. Required per user (the account): a mark.
2. A choice the cluster offers (partitions, GPU or CPU nodes): shared variant presets with
   `extends`, picked from the list like any preset.
3. The user's own environment (their UCX or OpenMPI build, a venv, modules, a QoS): generic, and
   documented once on the overrides page, not in each preset.
4. A cluster gotcha (`--gres=tmpfs:0`, an InfiniBand service level): baked into the preset.

An optional `notes` list holds what is left. The crab-wrappers CONTRIBUTING caps it at 3 one-line
entries, and its CI enforces the cap.

**A missing clone is loud.** When no presets are found, the error says to run `crab update` to
fetch crab-wrappers. `crab info --json` reports where it looked, and the dashboard's Remotes card
shows the hint. A checkout always has the clone (`make` makes it). An install clones on first need,
except under `--json` and in `crab worker` (ADR-038).

**Contract.** `crab info --json` gains, per preset, `needs` (the unset marks) and `notes`, and at
the top level `presets_sources` (the shared folders searched, found or not) and `presets_overrides`
(the override directory). All four are additive keys, so they fit the 1.x rule of ADR-039 and
`CONTRACT_SCHEMA` does not change.

**This breaks ADR-039 item 5 inside 1.x.** That item froze the presets file format, the three
merge layers and the replace rule. The change of layers, the refusal of the old files, the removal
of `_common` and the patch rule are all breaking by that item's own terms. It is accepted because
nobody outside the lab uses CRAB yet. The release is 1.2.0, and the changelog says so under
"Breaking". The preset fields themselves still only grow: `extends` and `notes` are added.

## Alternatives considered

- A separate presets repository: a second clone with its own pin and update machinery, for files
  that fit beside the wrappers.
- Explicit merge operations (`header_append` and the like): more syntax to learn, where merging by
  key covers the cases we have seen.
- Named values or variable expansion in presets: a larger design, kept for later.
- Optional marks: a value that is silently dropped is hidden documentation.
- Keeping the old override files with a warning: two places for the same setting, and a stale
  whole-preset copy would keep shadowing the shared one.
- Sweeps over presets: not a goal. One preset is one allocation (ADR-010), and a sweep is one submit
  per preset.
- Recording which layers made a preset in `crab_run.json`: `execution.json` already holds the
  resolved result.

## Consequences

The release order matters. crab-wrappers must have the tag `v1.2.0` before CRAB 1.2.0 is released,
because CRAB checks out the tag of its own version. CRAB's CI clones crab-wrappers `main`, so
crab-wrappers is pushed first.

A clone left on an old tag, from before presets moved, has no `presets/` folder and so no presets
at all. `crab update` fixes it, and the missing-clone message says so.

A same-name patch surprises someone who expects a replace. `crab presets show <name>` answers
"where does this value come from": it prints the resolved preset, the file each field came from,
the unset marks and an override skeleton.

The NPLB campaign's `nplb_h100` is not public. It lives on the `nplb` branch as an example file
under `examples/nplb/presets/`, and users copy it into their overrides directory.
