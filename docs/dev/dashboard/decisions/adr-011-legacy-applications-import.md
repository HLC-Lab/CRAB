# ADR-011 · Legacy `applications` configs import cleanly

- **Date:** 2026-06-19
- **Status:** accepted

## Context

Older CRAB configs use a top-level `applications` key (flat, or with an `apps` sub-object)
instead of the current `experiments` structure. The engine still accepts and rewrites that
form; researchers still have such files.

## Decision

The dashboard's importer accepts both legacy shapes and normalizes them into a single
experiment, mirroring the engine's own rewrite. The UI notes the conversion to the user.
Saving re-emits the modern `experiments` form.

## Alternatives considered

- Reject legacy files — pointless friction while the engine itself accepts them.

## Consequences

Easier: any config that runs also loads in the UI. Harder: the importer carries a second
parse path that must be kept in sync with the engine's rewrite rules (covered by round-trip
fixtures).
