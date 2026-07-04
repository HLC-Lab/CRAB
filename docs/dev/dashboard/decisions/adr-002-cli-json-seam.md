# ADR-002 · Talk to clusters only through `crab … --json`

- **Date:** 2026-06-19
- **Status:** accepted

## Context

The laptop backend needs machine-readable answers from clusters (version, benchmarks, nodes,
submission results, job status, history). Parsing human CLI output is brittle; a remote
daemon/API is ruled out by ADR-001.

## Decision

Extend the CRAB CLI with a `--json` mode (`crab info/list-benchmarks/nodes/run/status/history`,
implemented in `src/crab/cli/contract.py`). The backend executes these over SSH and parses
stdout; it never screen-scrapes and never re-implements engine logic. Shapes carry an integer
`CONTRACT_SCHEMA`; the connect handshake (`crab info --json`) detects skew. Adding fields is
backward-safe; renaming/removing requires a schema bump. `crab run --json` keeps stdout as
clean JSON by routing logs to stderr.

## Alternatives considered

- Remote agent/REST service on the cluster — persistent process, forbidden by design.
- Parsing existing human output — breaks on any wording change.
- Direct Python RPC (run engine code over SSH) — couples laptop and cluster versions tightly.

## Consequences

Easier: the seam doubles as an automation API for plain CLI users; testable offline with fake
runners. Harder: features must land on the cluster's CRAB before the dashboard can use them
(the guided bootstrap currently clones the feature branch for this reason — pre-v1 debt).
