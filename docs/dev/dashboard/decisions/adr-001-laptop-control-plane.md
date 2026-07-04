# ADR-001 · Run the dashboard as a laptop-local control plane

- **Date:** 2026-06-19
- **Status:** accepted

## Context

CRAB needed one interactive surface for managing clusters, experiments, and results. HPC
centers will not host persistent user services, and the CRAB worker must run inside the Slurm
allocation regardless.

## Decision

The web app runs on the researcher's own machine (`crab web`, uvicorn bound to 127.0.0.1) and
drives each cluster's existing `crab` CLI over SSH. It is a personal, single-user tool: no
central server, no daemon or open port on any cluster, nothing persistent remote. The engine
on the cluster remains authoritative for all execution semantics.

## Alternatives considered

- Server hosted on the cluster login node — persistent processes/ports are not allowed and
  would need per-center blessing.
- Central multi-user service — conflicts with per-user SSH identities and adds auth/ops burden
  for a research tool.

## Consequences

Easier: works with every cluster a user can SSH to; no admin involvement; offline-capable for
cached data. Harder: "live" monitoring exists only while the laptop is connected; each
laptop⇄cluster pair must handle version skew (see ADR-002).
