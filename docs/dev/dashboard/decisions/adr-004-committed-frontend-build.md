# ADR-004 · Commit the built frontend and ship it in the wheel

- **Date:** 2026-06-19
- **Status:** accepted

## Context

End users install CRAB with pip on machines (and clusters) where installing node/npm is
unwelcome. The SPA must still reach them somehow.

## Decision

`npm run build` outputs to `src/crab/web/static/`, which is committed to git and included in
the wheel via package data. FastAPI serves it. Node is a development-only dependency. Any
change to `src/crab/webui/src` must be committed together with the rebuilt static output, and
the verification gate checks the two are in sync.

## Alternatives considered

- Build at install time — requires node on user machines.
- Publish assets separately (CDN/release artifact) — the app must work fully offline.

## Consequences

Easier: `pip install crab[web]` just works, offline. Harder: binary-ish diffs in git; the
source⇄build sync must be machine-checked or the served app silently lags the source.
