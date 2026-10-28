# ADR-042 · CRAB is declared stable from 2.0; 1.x may change its public surface

- **Date:** 2026-10-27
- **Status:** accepted; supersedes the "from 1.0" timing of [ADR-039](adr-039-public-surface-1-0.md)

## Context

ADR-039 listed nine surfaces and promised them from 1.0: a change that breaks an existing user
would need a new major version. That promise was written when 1.0 was expected to be the finished
product. It is not. The work still planned before 2.0 reshapes several of those very surfaces:
the presets and where they live, the SbatchMan layout, the documentation, the plots and the
benchmark set. Keeping the 1.0 promise would either freeze formats that are not final, or force a
new major version for every one of those changes.

## Decision

CRAB 1.x is not declared stable. Any 1.x minor release may change the surfaces ADR-039 lists: the
CLI and its `--json` outputs, the `crab worker` seam, the config and presets formats, the result
files, the wrapper base class, receipts and the environment variables. Every such change is listed
in `CHANGELOG.md` under the release that makes it, in a note that says it breaks something.

2.0 is the first release that promises those surfaces. From then on the rules in ADR-039 apply as
written: a change that breaks an existing user needs a new major version, after a minor release
that warns about it (ADR-030's deprecation rule). ADR-039's list of surfaces, and what "stable"
means for each of them, stay valid as the definition of what 2.0 promises. Only its "from 1.0"
timing is replaced by this decision.

The version numbers still follow semver's syntax. What changes is the project's choice for the 1.x
series: it is treated like a pre-stable series, in the way semver treats 0.x.

## Alternatives considered

- Keep the 1.0 promise: formats that are still being redesigned would freeze, or each redesign
  would cost a major version and leave 1.x with a short life.
- Ship the product as 0.x until it is complete: 1.0 is already tagged and partners install it, and
  renumbering backwards would break their pins and the package history.

## Consequences

- Changelog entries flag breaking changes explicitly. Someone who upgrades from one 1.x release to
  another reads the notes first.
- Partners pin an exact 1.x version (a release tag, or the `vX.Y.Z+sbatchman` tag for the
  SbatchMan line) and move when they have read the notes.
- ADR-039's list becomes the 2.0 promise. Surfaces that gain a new form before 2.0 can still be
  reworked once more, and the promise starts from whatever 2.0 ships.
- The format versions keep being bumped on every breaking change, so that tools can detect one:
  `RESULTS_SCHEMA` (`src/crab/core/provenance.py:25`), the `schema` of `--json` outputs
  (`CONTRACT_SCHEMA`), the config `schema_version` (`CONFIG_SCHEMA_VERSION`) and `wrapper_api`.
  ADR-039's wording that bumps wait for a major release applies from 2.0.
