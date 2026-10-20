# ADR-006 · Roles derive from `end`; `collect` is a separate "measured" flag

- **Date:** 2026-06-20
- **Status:** accepted

## Context

Early UI drafts offered a per-app "Role: victim / aggressor" selector bound to the `collect`
flag. But in the engine, victim/aggressor behavior is defined by the `end` field (`""` = runs
to completion, `"f"` = stops when the others finish, a number = timed), while `collect` only
controls whether the app's metrics are parsed and stored.

## Decision

Match the engine. The UI's per-app control is an honest "collect metrics" toggle (default
false, as the engine documents). Any role badge shown in diagrams derives from `end`
(victim / aggressor / timed), with a separate "measured" marker when `collect` is on. The two
concepts are never re-coupled.

## Alternatives considered

- Keep the friendly role selector setting `collect` — actively misleading: an unmeasured
  victim or a measured aggressor are both legitimate configurations.

## Consequences

Easier: what the UI says matches what the engine does; hand-written configs display
correctly. Harder: "role" is no longer directly editable — users set Start/End semantics
instead, which is the truthful model.
