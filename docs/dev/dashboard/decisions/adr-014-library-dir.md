# ADR-014 · The experiment library lives in a user-chosen folder

- **Date:** 2026-07-04
- **Status:** accepted (supersedes ADR-009)

## Context

ADR-009 put authored configs in the platform app-data folder: functional, but unversioned,
hard to find, and awkward to share. Owner interview (2026-07-04): one laptop, occasional
sharing of configs with colleagues, and a preference for a folder under the user's control.

## Decision

The library location is configurable: set `CRAB_WEB_LIBRARY_DIR` to any directory (a git
repo, a synced folder, anything) and the dashboard reads/writes its plain per-entry JSON
files there; unset, it defaults to the app-data folder as before. On the first run with a
fresh custom folder, existing entries are **copied** (not moved) into it, so unsetting the
override rolls back cleanly. Entries remain individual, hand-shareable JSON files either way.

## Alternatives considered

- Keep app-data only — no versioning or sharing story.
- Sync configs to each cluster's CRAB checkout — ties authoring to connectivity; submit-time
  staging (planned for the submit phase) already covers getting a config onto the cluster.
- Store in the CRAB repo itself — mixes personal drafts into a shared codebase.

## Consequences

Easier: put the folder under git and every config is versioned and shareable. Harder: two
possible locations means support questions start with "is `CRAB_WEB_LIBRARY_DIR` set?"; a
future settings UI could expose the choice more visibly.
