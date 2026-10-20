# ADR-009 · Experiment library stored on the laptop

- **Date:** 2026-06-19
- **Status:** superseded by [ADR-014](adr-014-library-dir.md) (2026-07-04)

## Context

Authored experiment configs need a home. They are small JSON files, edited in the UI, and
eventually pushed to a cluster at submit time.

## Decision

Configs live in a local library on the laptop (`<user_data_dir>/crab/experiments/*.json`, one
file per entry, atomic writes, slug-validated ids), managed via `/api/experiments`. They are
pushed to a cluster only when submitting.

## Alternatives considered

- Store in the cluster's CRAB checkout — ties authoring to connectivity and one cluster.
- Store in the project repo (like `examples/`) — mixes personal drafts into a shared repo.

## Consequences

Easier: authoring works offline; duplicating/iterating is instant. Harder: configs are not
versioned, not synced across a researcher's machines, and invisible to colleagues — which is
why this decision is under review; a user-chosen library directory (potentially a git repo)
is the leading alternative.
