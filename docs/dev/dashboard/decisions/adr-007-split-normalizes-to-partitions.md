# ADR-007 · The editor standardizes allocations on named partitions

- **Date:** 2026-07-02
- **Status:** accepted

## Context

The engine accepts two allocation forms: a top-level percentage `split` (apps mapped by
order) and named `partitions` (apps tagged via `partition`). Maintaining two editing models
doubled UI complexity for no expressive gain.

## Decision

The allocation editor works exclusively in named-slices (partitions) form. Importing a config
that uses top-level `split` normalizes it to partitions (auto-named groups; apps tagged by
order) — semantically equivalent but not byte-identical. Round-trip "losslessness" for
split-form inputs is therefore defined as semantic equivalence, verified by a canonicalizer
in the round-trip suite. Even shares are omitted on emit; single-slice allocations emit
nothing (ADR-005).

## Alternatives considered

- Preserve split-form for unnamed slices — rejected: significant mapping complexity for a
  form no new config would author.

## Consequences

Easier: one mental model (slices) everywhere — editor, per-app pickers, diagrams. Harder:
re-saving an old split-form config rewrites its allocation block (equivalent, but a diff);
the engine's split form remains supported for hand-written files.
