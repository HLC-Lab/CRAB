# ADR-003 · No secrets at rest; one reused SSH connection per cluster

- **Date:** 2026-06-19
- **Status:** accepted

## Context

Clusters use SSH keys, passwords, and 2FA (e.g. certificate flows via an SSH agent). Storing
credentials in a research tool is an unacceptable liability.

## Decision

Profiles (`clusters.json`) hold only non-secret fields (host, user, auth type, paths).
Authentication uses, in order of preference: the inherited SSH agent (`SSH_AUTH_SOCK`, which
also covers certificate-based 2FA flows), an explicit key file, or a password typed at connect
time and held only in memory. Each remote gets one lazily-opened asyncssh connection, reused
for the whole session, liveness-checked and evicted on failure. Host-key policy is per profile
(`strict`, or `insecure` for round-robin login nodes without stable keys). Secrets never
appear in logs, errors, or files.

## Alternatives considered

- Encrypted credential store — still a secret at rest, plus key-management UX.
- Per-command SSH (no reuse) — repeated 2FA prompts make it unusable on hardened clusters.

## Consequences

Easier: nothing sensitive to protect on disk; hardened-cluster flows work by inheriting the
user's agent. Harder: `crab web` must be launched from a shell that has the agent loaded;
connections drop with the agent's certificate lifetime.
