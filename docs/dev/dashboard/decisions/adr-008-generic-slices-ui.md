# ADR-008 · Allocation UI: generic node slices, no baked-in scenario taxonomy

- **Date:** 2026-07-02
- **Status:** accepted

## Context

A "shape picker" (solo / victim-vs-aggressor / co-schedule / advanced presets) was considered
for the allocation editor. Analysis of the real experiment configs showed these are all the
same structure — percentage slices of the node pool — differing only in names and counts.

## Decision

One direct editor: a draggable division bar of generic slices. A single slice means the whole
machine and shows no placement ceremony. The first division defaults to editable names
"victim"/"aggressor" (the dominant use case); further slices get numbered defaults. Placement
(linear / interleaved / random, with stride/seed) is chosen alongside, with an illustrative
node strip that explicitly does not claim to predict the engine's exact assignment.

## Alternatives considered

- Scenario presets — the same thing recolored; more UI, no new expressiveness.
- Keep the abstract form fields (mode dropdown + "60,40" text) — correct but forced users to
  reason in percentages instead of seeing the division.

## Consequences

Easier: the common case is two drags; diagrams and pickers share the slice palette and names.
Harder: exotic engine features (per-partition inner modes) remain pass-through-only, edited by
hand in JSON.
