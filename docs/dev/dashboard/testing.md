# Testing and verification

Everything a change must pass before it is considered done. The single entry point is:

```bash
make verify        # after every change
make verify-full   # at task boundaries and whenever frontend source changed
```

`verify` runs: Ruff (lint + format check), mypy (on `src/crab/web` and
`src/crab/cli/contract.py`), the pytest suite, Prettier check, ESLint, `vue-tsc`, and the
Vitest unit suite. `verify-full` adds the production frontend build, a check that the
committed `src/crab/web/static/` matches the source, and the Playwright end-to-end suite.
Green means: every tool exits 0 and pytest/vitest/playwright report no failures or errors.

## The suites

| Suite | Command | What it covers |
|---|---|---|
| Backend | `.venv/bin/python -m pytest -q` | Engine/core units + all web API routes. Web tests use fake SSH transports — no network, no cluster needed |
| Frontend unit | `cd src/crab/webui && npx vitest run` | Pure logic, above all the **config round-trip suite**: engine JSON → UI draft → JSON must be lossless over handled fields, checked against synthetic fixtures and the real configs in `examples/` |
| Frontend types | `cd src/crab/webui && npm run type-check` | Strict TypeScript across the SPA |
| End-to-end | `cd src/crab/webui && npx playwright test` | The critical authoring flow against a real `crab web` instance started on temporary data dirs (your real library is never touched) |

## Writing tests

- **Backend**: put API/store tests in `tests/test_web_<area>.py`; follow the fake-transport
  pattern in `tests/test_web_remotes.py`. Mock only true boundaries (SSH, subprocess, time);
  never your own modules.
- **Config mapping**: when a new config field becomes editable, add a round-trip fixture *and*
  add the field to the suite's allow-sets — that is what makes a future accidental drop fail
  loudly instead of being silently projected away.
- **E2E**: one spec per user *flow*, not per widget. E2E is expensive; units are not.
- Test through public interfaces (an HTTP route, an exported function). A test that recomputes
  its expected value with the same logic as the code under test proves nothing.

## Visual verification

Type checks cannot see pixels. For user-visible UI changes, verify in a real browser: build,
start an isolated instance, and look at it —

```bash
cd src/crab/webui && npm run build
CRAB_WEB_CONFIG_DIR=$(mktemp -d) CRAB_WEB_DATA_DIR=$(mktemp -d) \
  crab web --no-browser --port 8899
```

then open `http://127.0.0.1:8899` (or take a screenshot with
`npx playwright screenshot ...`). Confirm the specific behavior that changed, including that
the bug being fixed is actually gone.

## Ground rules

- Never report work as done without having run the relevant gate and seen it pass.
- A change to `src/crab/webui/src` must be committed together with the rebuilt
  `src/crab/web/static/` (`verify-full` enforces this).
- If a test is wrong rather than the code, fixing the test is a separate, explained commit —
  never weaken a check just to get to green.
