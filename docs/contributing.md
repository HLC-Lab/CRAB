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

Requires **Python 3.10+**. The package is installed in editable mode, so changes to `src/crab/` take
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
│   └── wl_manager/       # srun (slurm.py) / mpirun (mpi.py) bindings + template
├── setup/        # `crab setup`: wizard, recipe registry, recipes/, receipt storage (memory.py)
├── tui/          # Textual UI (optional dependency)
├── log/          # CrabLogger
└── wrappers/base.py   # the wrapper base class

wrappers/         # application wrappers (blink/, others/) — referenced from configs
config/           # presets.json (+ environments/ receipts, generated)
examples/         # sample experiment configs per system
crab_dashboard.html  # standalone results viewer
```

The most important thing to understand before changing the core is the **two-phase orchestrator /
worker execution model** and the **wrapper / recipe / receipt** separation — both covered in
[Architecture](concepts/architecture.md).

## Where things go

| To add… | Put it in… | Mechanism |
|---------|-----------|-----------|
| Support for a new application | `wrappers/` (+ optionally a recipe) | Loaded by file path; see [Extending CRAB](extending/overview.md). |
| A buildable benchmark | `src/crab/setup/recipes/` | Auto-discovered subclass of `BenchmarkRecipe`. |
| A new cluster environment | `config/presets.json` | See [Configuring your cluster](using/presets.md). |
| A new launch mechanism | `src/crab/core/wl_manager/` | A module exposing a `wl_manager` class; selected by `CRAB_WL_MANAGER`. |
