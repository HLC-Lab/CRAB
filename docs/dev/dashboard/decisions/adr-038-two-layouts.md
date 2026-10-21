# ADR-038 · Two layouts: in-repo state for checkouts, user dirs for installs

- **Date:** 2026-10-21
- **Status:** accepted

## Context

CRAB found its files by walking up from the source file to the git checkout: eleven modules did it,
each with its own copy of the walk. Results (`data/`), benchmarks, receipts, private wrappers and
the preset files were all assumed to sit in that checkout. That breaks a `pip` or `pipx` install,
where the package lives in `site-packages` and the walk lands in the wrong place. The shipped
presets and the setup recipes were not in the wheel either. At the same time, partners, `make`,
the docs and the dashboard's install on each cluster all rely on the checkout layout, and
ADR-030 froze `CRAB_ROOT`, `__CWD__` and `CRAB_PATH_WRAPPERS`. A clean install had to work
without moving anything for the people already on a checkout.

## Decision

There are two layouts, and one module, `crab.paths`, decides which one applies. No other module
looks at `__file__` to find state.

**Detection.** The package is a checkout when the grandparent of its own directory holds
`pyproject.toml` and `src/crab/paths.py` there is this very file. Otherwise it is an install. This
is structural: it works for a source archive with no `.git`, it is never true for a wheel (there
is no `src/` above `site-packages/crab`), and it ignores an unrelated repository higher up, such
as a dotfiles repo in `$HOME`.

**Checkout.** Nothing changes. The home is the repo root, with `data/`, `local/`, `benchmarks/`
and `wrappers/` inside it. Per-machine files stay in the git-ignored `local/` folder.

**Install.** The home is the user data directory (`~/.local/share/crab`), holding `data/`,
`benchmarks/` and a `wrappers/` clone. Per-machine files (receipts, private wrappers, the user's
presets file) live in the user config directory (`~/.config/crab`).

**Overrides, most specific first:** an explicit config or CLI value (`datapath`, `--binary`);
`CRAB_PATH_WRAPPERS` and `CRAB_PATH_PRESETS`; `CRAB_HOME`; the layout default. In an install,
`CRAB_HOME` moves the home and puts the config under `$CRAB_HOME/config`. In a checkout the repo
root is always the home. `CRAB_HOME` is read once at import, since it is a setting for a whole
process and its children.

**Names kept.** `CRAB_ROOT` and `__CWD__` keep their names and now mean the home of whichever
layout is active. In a checkout that is the value they always had.

**Presets.** The shipped presets file moves inside the package (`src/crab/presets.json`) so a wheel
carries it. Presets merge in three layers: shipped, then the user's file
(`~/.config/crab/presets.json`, or `CRAB_PATH_PRESETS`), then the checkout's `local/presets.json`.
A preset with the same name replaces the earlier one whole.

**Wrappers.** `pip` cannot run code at install time, so an install clones crab-wrappers into
`<home>/wrappers` on first need, with one printed line. It never does this inside a `--json` call
or in `crab worker`, which get a hint instead. A checkout still gets its clone from `make`.

**`crab update`.** In a checkout it behaves as ADR-030 describes. In an install it updates only
the wrappers and points to `pipx upgrade crab-hpc` for the program itself. A checkout that has
local edits to the moved presets file is refused, with a message to move them to
`local/presets.json`.

**Contract.** `crab info --json` gains a `layout` field (`checkout` or `installed`); `crab_root`
keeps its key and reports the home. Both changes are additive, so `CONTRACT_SCHEMA` does not change.

## Alternatives considered

- One layout everywhere, in the repo: a pip install would write into `site-packages` or the
  current directory, and user dirs are what `pipx` users expect.
- One layout everywhere, in user dirs: it moves `data/`, `local/` and the receipts of every
  partner and cluster checkout, and breaks `make`, the docs and the dashboard's `<base>/CRAB`.
- Support checkouts only: it blocks the PyPI release and every user who does not want a clone.
- Detect a checkout by a `.git` folder: false for a source archive, and true inside someone
  else's repository.
- Detect it by the project name in `pyproject.toml`: it breaks on the distribution rename and on
  forks.
- Detect it by the editable flag in the package's `direct_url` metadata: false when running
  `python -m crab` from a source tree.

## Consequences

A checkout behaves exactly as before: same paths, same preset result, same output. Existing
configs, results and partner setups need no change.

There are now two layouts to test and document, and a bug can show in only one of them. A wheel
installed into a clean environment is checked in CI for this reason.

Each module keeps its own path constant (`CRAB_ROOT`, `ENV_DIR` and so on), assigned from
`crab.paths`, because tests patch those names. The one decision still lives in one module.

Docs, tooling and partner notes that name `config/presets.json` have to point to the new location.
The first run of an install needs git and network for the wrappers, and offline it only gets the
hint. ADR-030's per-machine-files and `crab update` paragraphs describe the checkout layout; this
record adds the installed one.
