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
└── crab_dashboard.html  # standalone results viewer

wrappers/         # application wrappers and cluster presets (presets/): a separate git-ignored clone of HLC-Lab/crab-wrappers
local/            # git-ignored, per machine: presets/ (your overrides), receipts/, wrappers/
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
| A new cluster environment | `presets/<cluster>.json` in the crab-wrappers repository (shared with everyone); a file in `local/presets/` for one machine | See [Configuring your cluster](using/presets.md). |
| A new launch mechanism | `src/crab/core/execution/launcher/` | A class with a `prefix(placement)` method (the `Launcher` protocol in `base.py`), plus a kind in `LAUNCHER_KINDS` (`settings.py`) and a branch in `select.py`. |

## Writing docs

Write each page for a reader who wants to finish one task. The same rules apply to CRAB's docs and to
the partner documentation.

### Page types

Every page has one job. Internals go on a concepts page, linked once.

| Page | Job | Must not contain |
|---|---|---|
| Install | Get a working tool and prove it | Installer narration, flag lists, failure analysis, new terms |
| How-to | Reach one named goal | Why it works, every option, alternatives |
| Reference | Look something up | Instructions, opinion, rationale |
| Troubleshooting | Map a symptom to a fix | Install steps, concepts, anything that is not a failure |
| Tutorial | Teach by doing | Options, alternatives, explanation beyond the step |

### Rules

1. Open with the action or the fact. Do not announce the page or repeat its heading.
2. Write "you" and the imperative, in the present tense.
3. Give each numbered step one action. Unordered facts are bullets.
4. Say what the reader gets, not how the tool works inside.
5. Do not narrate installer steps or flags. The tool prints its own steps; link the reference.
6. State a fact once and link to the page that owns it.
7. Use the exact error message as the troubleshooting heading, then the cause, then the fix.
8. Write placeholders in angle brackets, such as `<umbrella-url>`, and explain each at first use.
9. Use code font for commands, paths, flags and values. Bold is for UI labels only.
10. Use no em or en dashes. Use a period, colon, comma or parentheses.
11. Cut filler: "simply", "just", "easy", "please", "note that", "usually", "may", "should", "in order to".
12. Keep sentences near 20 words and paragraphs to 4 sentences.
13. Name the thing instead of writing "above" or "below". Make link text name the destination, never "here".
14. Make headings say what the reader does or finds, in sentence case, with no trailing colon or period.
15. Add a callout only when it changes what the reader does: `!!! warning` for data loss, the wrong
    cluster or wasted node-hours, `!!! note` for a fact the next step depends on. One per section.

### Visual separation

- Put a blank line between every paragraph, list, table and code block.
- Put one command in each block. Several commands share a block only when they form one step and run in order.
- Leave out the `$` prompt.
- Put the command in a `bash` block and its output in a `text` block, after a sentence such as "The output ends with:".
- Show output only for a verification step or an error.
- Number the steps and indent the command block under its number. Keep prose around the steps outside the list.
- Put facts of one kind (flags, files, keys) in a table or bullets, not a paragraph.

### Patterns that read as machine-written

- Announcing the page ("This page covers") or closing with "In summary".
- Filler openers: "Additionally,", "Importantly,", "It is worth noting".
- Em dashes as connectors.
- Bold lead-in bullets (`**Term:** text`) and title-case headings.
- Reflexive triplets ("fast, safe and repeatable") and "not just X, but Y".
- Puffed words: "seamless", "robust", "leverage", "ensures", "serves as".
- Trailing "-ing" commentary: "..., ensuring that the job finds the binary".
- Hedges: "typically", "usually", "it is recommended".

### Example rewrite

````text
Before:
This section installs the framework. Note that the installer simply creates a virtual environment, installs the dependencies and runs a self-check, ensuring that everything works. Run it below.

After:
On a login node:

./install.sh --account <account>

The installer ends with a self-check.
````

### Sizes

An install page is 60 to 80 lines. A how-to is 100 lines or fewer. Move the rest to a reference or
troubleshooting page.
