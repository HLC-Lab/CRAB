# ADR-005 · Authored configs emit only what the user set

- **Date:** 2026-06-19
- **Status:** accepted

## Context

The engine applies its own defaults to absent config keys. The authoring UI could either
write a "complete" config with every default spelled out, or write only what the user chose.

## Decision

Emit-on-set. An untouched editor emits no key; a bare linear allocation with no split and no
groups emits no `allocation` at all (the engine default applies). Boolean-ish options are
tri-state in the UI (unset / true / false) so an explicit `false` survives and "unset" defers
to the engine. One deliberate exception: a per-experiment allocation override is force-emitted
even when it equals a bare default, because in `local_options` absence means "inherit the
global" — presence is the override signal itself.

## Alternatives considered

- Full-default emission — freezes today's engine defaults into every saved file and makes
  hand-reading configs noisy.
- An explicit on/off toggle per section — tried for allocation and removed; content-derived
  emission matches how people think.

## Consequences

Easier: saved configs stay minimal and diff-able; engine default changes propagate. Harder:
mapping code must distinguish unset from explicit values everywhere (the round-trip test suite
guards this), and readers must know the `local_options` exception.
