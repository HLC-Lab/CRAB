# Contributing to CRAB

Thanks for helping. This page is the short version. The full guide, with the project layout and
where each kind of change goes, is [docs/contributing.md](docs/contributing.md).

## Set up

```bash
git clone https://github.com/HLC-Lab/CRAB
cd CRAB
make                                  # creates .venv, installs CRAB (editable), clones the wrappers
source .venv/bin/activate
pip install -e ".[web,dev]"           # the web dashboard and the developer tools
cd src/crab/webui && npm install      # only for frontend work
```

CRAB needs Python 3.10 or newer.

## The gate

Run this from the repository root before every commit, and read its exit code:

```bash
make verify
```

It runs the linters, the type checks and the Python and frontend tests. Run `make verify-full` as
well when you touched `src/crab/webui/` (it adds the frontend build and the end-to-end tests).
What each step runs is in [Testing & verification](docs/dev/dashboard/testing.md).

## Commits

One line, in plain English, with an area prefix: `web:` `core:` `cli:` `fix:` `test:` `docs:`
`chore:` `examples:`. For example, `cli: crab validate runs the pre-submit checks`. One concern per
commit: a refactor, a behavior change and a formatting pass go in separate commits.

## Branches

- Work lands on `develop`. Open pull requests against it.
- `master` gets releases, tagged `vX.Y.Z`.
- `sbatchman` and `nplb` are partner branches. They merge down from `develop` (a merge, never a
  rebase) and carry only commits specific to their campaign. A change that both lines need is made
  on `develop` first.

The reasoning is in [ADR-028](docs/dev/dashboard/decisions/adr-028-branch-model.md).

## Wrappers

Application wrappers do not live in this repository. Contribute them to
[HLC-Lab/crab-wrappers](https://github.com/HLC-Lab/crab-wrappers). While you are writing one, keep
it in `local/wrappers/` (see [Writing a wrapper](docs/extending/wrappers.md)).

## Reports and questions

Use the [issue templates](https://github.com/HLC-Lab/CRAB/issues/new/choose) for bugs and feature
requests. For a security problem, follow [SECURITY.md](SECURITY.md) instead of opening an issue.
