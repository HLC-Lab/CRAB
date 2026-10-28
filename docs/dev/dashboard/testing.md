# Testing and verification

Everything a change must pass before it is considered done, locally and in CI. The single entry
point is:

```bash
make verify        # after every change
make verify-full   # at task boundaries and whenever frontend source changed
```

Read the exit code of the make command itself. Piping it into `tail` or `grep` reports the
status of the last command in the pipe, so redirect to a file and check `$?` first.

## The gates

`make verify` runs, in this order:

| Step | Tool | Scope |
|---|---|---|
| 1 | `ruff check` | `src` and `tests` |
| 2 | `ruff format --check` | `src` and `tests` |
| 3 | `mypy` | the files listed in `[tool.mypy]` in `pyproject.toml`: `src/crab/web`, `cli/contract.py`, `cli/export.py`, `core/wrapper_paths.py`, `paths.py` and `core/execution` |
| 4 | `pytest -q` | the whole `tests/` folder |
| 5 | `prettier --check` | `src` and `tests` of `src/crab/webui` |
| 6 | `eslint` | `src` and `tests/unit` of the frontend |
| 7 | `vue-tsc --noEmit` | strict TypeScript across the SPA |
| 8 | `vitest run` | the frontend unit suite |

`make verify-full` runs all of that, then:

1. `npm run build`: type-check plus the production build into `src/crab/web/static/`.
2. A check that the committed `src/crab/web/static/` matches the build.
3. `npm run gen:api`, then a check that `src/crab/webui/src/api/generated.ts` (the API types
   generated from the backend's OpenAPI schema) is unchanged. A backend model or route docstring
   change regenerates it.
4. The Playwright end-to-end suite.

Both drift checks compare the working tree with git's index, so after a rebuild or a
regeneration they fail until the changed files are staged. Stage them and run `make verify-full`
again; a second failure is real.

Green means every tool exits 0 and pytest, vitest and playwright report no failures or errors.
Skips are fine only for optional extras that are not installed.

`make fmt` applies the fixes for the lint and format steps: `ruff check --fix` and `ruff format`
on the Python side, `prettier --write` on the frontend.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request, as five independent jobs:

| Job | Runs on | What it does |
|---|---|---|
| `verify` | ubuntu-latest, Python 3.12, Node 22 | Installs `.[web,dev]` and the frontend dependencies, clones crab-wrappers into `wrappers/`, runs `make verify`. It does not run `verify-full`: the build, static and generated-types checks and the e2e suite stay a local gate. |
| `mpi` | ubuntu-24.04 | Installs Open MPI and MPICH and runs `pytest -m mpi` with `CRAB_TEST_MPIRUNS` naming both `mpirun` binaries. The job fails if any test was skipped, because a skip means an MPI dialect lost coverage. It is pinned to 24.04 because the Open MPI package of newer Ubuntu images cannot print its version, which makes the tests skip. |
| `docs` | ubuntu-latest | `pip install -r docs/requirements.txt`, then `mkdocs build --strict`. A broken link or a warning fails it. |
| `package` | ubuntu-latest, Python 3.10 | The minimum supported version: `pip install -e .`, `compileall` over `src/`, and `crab --help`. It catches syntax newer than the declared floor. |
| `wheel` | ubuntu-latest, Python 3.10 and 3.12 | Builds the wheel and the sdist, installs the wheel in a clean venv and runs CRAB from it with `scripts/wheel_smoke.sh`, and checks that the sdist holds no `node_modules`. |

The docs are outside `make verify`. To build them locally, install `docs/requirements.txt` into a
separate virtual environment and run `mkdocs build --strict -d <output dir>` from the repository
root, so the build does not write a `site/` folder into the tree.

## The suites

| Suite | Command | What it covers |
|---|---|---|
| Backend | `.venv/bin/python -m pytest -q` | Engine and core units, the execution backends, the CLI, and all web API routes. Web tests use fake SSH transports: no network and no cluster are needed |
| Current-folder independence | `.venv/bin/python -m pytest -q tests/test_cwd_independence.py tests/test_cwd_tripwire.py` | Part of the backend suite. `test_cwd_independence.py` runs the installed `crab` console script from the repository root, from an unrelated folder and from a deleted folder: the read-only `--json` commands must exit 0 with equal output. `test_cwd_tripwire.py` parses `src/crab` and fails when a call is given `"."`, a `./` path or `.env`, or code reads or changes the current folder (`os.getcwd`, `Path.cwd`, `os.chdir`), outside a short allowlist with a reason per entry |
| Real MPI | `.venv/bin/python -m pytest -q -m mpi -rs` | Real `mpirun` launches through CRAB's launcher, against every MPI found on the machine (or the paths in `CRAB_TEST_MPIRUNS`, `:`-separated). A binary that is unusable skips its test |
| Frontend unit | `cd src/crab/webui && npx vitest run` | Pure logic, above all the **config round-trip suite**: engine JSON to UI draft to JSON must be lossless over handled fields, checked against synthetic fixtures and the real configs in `examples/` |
| Frontend types | `cd src/crab/webui && npm run type-check` | Strict TypeScript across the SPA |
| End-to-end | `cd src/crab/webui && npx playwright test` | The critical flows against a real `crab web` instance started on temporary data dirs (your real library is never touched) |

## Where tests live

- `tests/test_<area>.py`: one flat folder for the Python tests. Web routes are `test_web_*.py`,
  CLI commands `test_cli_*.py`, the schedulers and launchers `test_local_*.py`,
  `test_slurm_*.py` and `test_launchers.py`, and the runner `test_runner_*.py`.
- `tests/conftest.py`: the web client helper `auth_client` and autouse fixtures that keep every
  test away from your machine: a guard on `os.kill` and `os.killpg`, `XDG_STATE_HOME` and the
  user config directory pointed into `tmp_path`, and `CRAB_PATH_PRESETS` unset.
- `tests/settings_fixtures.py`: execution settings for the runner. `LOCAL_DIRECT` is a local
  scheduler with every app launched directly.
- `tests/fixtures/wrappers/`: an offline wrappers folder for tests of CRAB's own behavior. Tests
  of the shipped wrappers themselves carry the `requires_wrappers_checkout` mark and are skipped
  when `wrappers/` is not cloned.
- `tests/fake_ssh` and `tests/ssh_server.py`: a fake `ssh` agent for the MPI tests and a real
  loopback SSH server for tests of the transport itself.
- `src/crab/webui/tests/unit/`: Vitest specs. `src/crab/webui/tests/e2e/`: Playwright specs.

## Seams

Test through the seam that fits, and mock only true boundaries: SSH (`Transport`), subprocesses,
time and randomness, and the filesystem through `tmp_path`. Never mock your own modules.

- **Web routes**: `TestClient` in `tests/test_web_<area>.py`. A route that talks to a cluster
  follows `tests/test_web_remotes.py`: a fake `Transport`, so the real command building runs. A
  route that does not follows `tests/test_web_server.py`.
- **Stores** (profiles, library): through their public methods.
- **The CLI contract** (`cli/contract.py`): its gatherers, with injected fake runners and paths.
- **The engine**: pure logic as plain pytest. Runner behavior goes through the real
  `ExperimentRunner` on the direct launcher (`settings=LOCAL_DIRECT`, `tmp_path`); see
  `tests/test_runner_*.py`.
- **Slurm**: `tests/test_slurm_characterization.py` pins the generated job script and the
  `sbatch`, `srun`, `squeue`, `sacct` and `scancel` behavior with literal expected values.
- **Signals**: a test never sends a real signal to a process it did not start. A fabricated pid
  (from a fake state file, `True`, 0, 1 or a negative number) is tested with `os.kill` and
  `os.killpg` monkeypatched. `killpg(1, sig)` is `kill(-1, sig)`, which reaches every process of
  your user; the suite-wide guard fails a test that tries it.
- **Frontend**: the exported functions of `lib/` modules, and the config mapping as a round-trip
  fixture. Component behavior is covered by one Playwright flow per critical user flow, not by
  component tests.

## Writing tests

- Test through public interfaces (an HTTP route, an exported function, a CLI command). A test that
  recomputes its expected value with the same logic as the code under test proves nothing: write
  expected values as literals or take them from real example configs.
- **Config mapping**: when a new config field becomes editable, add a round-trip fixture and add
  the field to the suite's allow-sets. That is what makes a future accidental drop fail loudly
  instead of being silently projected away.
- **E2E**: one spec per user flow, not per widget. E2E is expensive; units are not.
- A fix starts with a failing test at one of the seams above, then the change.

## Visual verification

Type checks cannot see pixels. For user-visible UI changes, verify in a real browser: build,
start an isolated instance, and look at it.

```bash
cd src/crab/webui && npm run build
CRAB_WEB_CONFIG_DIR=$(mktemp -d) CRAB_WEB_DATA_DIR=$(mktemp -d) \
  crab web --no-browser --port 8899
```

Then open `http://127.0.0.1:8899`, or take a screenshot with `npx playwright screenshot ...`.
Confirm the specific behavior that changed, including that the bug being fixed is actually gone.
Stop the server afterwards and check that the port is free: `crab web` re-execs itself, so the
process that listens is not the one you started.

The served page authenticates automatically (the token rides the HTML). For raw `curl` calls,
fetch the token first:

```bash
TOKEN=$(curl -s http://127.0.0.1:8899/ | grep -o 'crab-token" content="[^"]*' | cut -d'"' -f3)
```

and send `-H "X-Crab-Token: $TOKEN"`. With the Vite dev server (`npm run dev`), run `crab web -v`
once, copy the logged token, and store it in the browser as `localStorage.CRAB_DEV_TOKEN`.

## Ground rules

- Never report work as done without having run the relevant gate and seen it pass.
- A change to `src/crab/webui/src` must be committed together with the rebuilt
  `src/crab/web/static/`, and a backend model change with the regenerated
  `src/crab/webui/src/api/generated.ts`; `make verify-full` enforces both.
- A version bump needs a reinstall (`pip install -e .`) before gating, because the version test
  reads the installed metadata.
- If a test is wrong rather than the code, fixing the test is a separate, explained commit.
  Never weaken a check just to get to green.
