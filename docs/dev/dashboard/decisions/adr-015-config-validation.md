# ADR-015 · Config shape is described in Python; saves warn, never reject

- **Date:** 2026-07-04
- **Status:** accepted

## Context

The engine reads experiment configs via raw dict access, so the only machine-readable
description of the config shape was the dashboard's TypeScript validator — invisible to the
backend and to any other tool. Separately, the frontend's API types were a hand-kept mirror of
the backend's Pydantic models, free to drift silently.

## Decision

Two related sources of truth:

1. **`crab.web.models.config`** — Pydantic models describing the config JSON exactly as the
   engine accepts it (every `examples/*.json` must validate warning-free; tests enforce this).
   The models are deliberately permissive: unknown keys pass (wrapper attributes are a
   feature), numbers may be strings, the legacy `applications` form is legal. Saving through
   `/api/experiments` reports `warnings` in the response — **never a rejection**: configs are
   hand-editable working documents, and the cluster engine remains the final authority. The
   UI shows the first warning after a save. (UI-authored configs are normalized by the editor,
   so warnings mostly guard hand-written imports and future drift.)
2. **Generated API types** — backend-owned response shapes (`Profile`, `RemoteListItem`,
   `LibraryEntry`, `SavedEntry`) are generated from the FastAPI OpenAPI schema
   (`npm run gen:api` → `src/api/generated.ts`); the verification gate regenerates and fails
   on any diff. Cluster-contract passthrough shapes (benchmarks/nodes) stay hand-written —
   the backend forwards them verbatim from `crab … --json`, so they are not in the OpenAPI
   schema.

No config schema-version field for now: the engine ignores unknown keys, additions are
backward-safe, and the models warn rather than reject — revisit if a breaking config change
ever becomes necessary.

## Alternatives considered

- Reject invalid configs at save — punishes work-in-progress drafts and hand-edits; the
  engine, not the laptop, decides what runs.
- Make the engine itself consume the Pydantic models — right long-term home, but touching the
  engine's config reading is out of the dashboard's scope; the models live in `crab.web` until
  the engine wants them.
- Generate ALL frontend types from OpenAPI — impossible for shapes the backend only forwards
  from the cluster CLI; those belong to the CLI contract, not the HTTP API.

## Consequences

Easier: a wrong-shaped config is flagged the moment it is saved; backend/frontend response
drift breaks the build instead of production. Harder: the models must track engine changes
(the examples-validate-clean test is the tripwire), and backend contributors must re-run
`npm run gen:api` when routes change (the gate reminds them).
