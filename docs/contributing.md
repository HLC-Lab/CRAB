# Contributing

Notes for working on the CRAB framework itself (as opposed to [adding benchmarks](extending/overview.md),
which needs no core changes). The repository is [HLC-Lab/CRAB](https://github.com/HLC-Lab/CRAB).

## Development setup

```bash
git clone https://github.com/HLC-Lab/CRAB
cd CRAB
python -m venv .venv
source .venv/bin/activate
pip install -e .[web,dev]    # editable install with the web dashboard + dev tools
```

Requires Python 3.10+. The package is installed in editable mode, so changes to `src/crab/` take
effect without reinstalling. Frontend work additionally needs node: `cd src/crab/webui && npm install`.

## Verification

Before considering any change done (and before every commit), run from the repo root:

```bash
make verify        # lint + format check + type check + all unit tests
make verify-full   # verify + frontend build/sync check + end-to-end tests
```

Use `make verify-full` whenever you touched `src/crab/webui/`. `make fmt` auto-formats both
languages. What each gate runs, and how to write tests, is documented in
[Testing & verification](dev/dashboard/testing.md); the dashboard's design is in
[Web dashboard architecture](dev/dashboard/architecture.md), and the reasoning behind
non-obvious choices in the [decision records](dev/dashboard/decisions/index.md).

## Commits and branches

Commits are one line with an area prefix (`web:` `core:` `cli:` `fix:` `test:` `docs:` `chore:`
`examples:`), one concern each: a refactor, a behavior change and a formatting pass are separate
commits.

Work lands on `develop`, the product branch, and `master` gets the releases. `sbatchman` and `nplb`
are partner branches that merge down from `develop` (a merge, never a rebase) and carry only
campaign-specific commits ([ADR-028](dev/dashboard/decisions/adr-028-branch-model.md)). The short
version of this page is
[CONTRIBUTING.md](https://github.com/HLC-Lab/CRAB/blob/develop/CONTRIBUTING.md) in the repository.

## Project layout

```
src/crab/
├── cli/          # `crab` entry point and argument parsing (main.py → cli_router)
├── core/         # the engine and execution machinery
│   ├── engine.py         # orchestrator/worker control core
│   ├── experiment/       # ExperimentRunner: per-experiment lifecycle
│   ├── allocation/       # node-to-app mapping strategies
│   ├── data/             # DataContainer, convergence (check_CI), CSV/HDF output
│   ├── process/          # launching/terminating app processes
│   └── execution/        # scheduler/ (job submission) and launcher/ (srun, mpirun, direct)
├── setup/        # `crab setup`: wizard, recipe registry, recipes/, receipt storage (memory.py)
├── log/          # CrabLogger
├── wrappers/base.py   # the wrapper base class
├── presets.json  # the shipped presets (they ship inside the package)
└── crab_dashboard.html  # standalone results viewer

wrappers/         # application wrappers: a separate git-ignored clone of HLC-Lab/crab-wrappers
local/            # git-ignored, per machine: presets.json, receipts/, wrappers/
examples/         # sample experiment configs per system, each folder with a README
```

The most important thing to understand before changing the core is the two-phase orchestrator /
worker execution model and the wrapper / recipe / receipt separation. Both are covered in
[Architecture](concepts/architecture.md).

## Where things go

| To add… | Put it in… | Mechanism |
|---------|-----------|-----------|
| Support for a new application | the crab-wrappers repository, or `local/wrappers/` while in progress (+ optionally a recipe) | Loaded by file path; see [Extending CRAB](extending/overview.md). |
| A buildable benchmark | `src/crab/setup/recipes/` | Auto-discovered subclass of `BenchmarkRecipe`. |
| A new cluster environment | `src/crab/presets.json` (shipped for everyone); your own presets file for one machine | See [Configuring your cluster](using/presets.md). |
| A new launch mechanism | `src/crab/core/execution/launcher/` | A class with a `prefix(placement)` method (the `Launcher` protocol in `base.py`), plus a kind in `LAUNCHER_KINDS` (`settings.py`) and a branch in `select.py`. |
