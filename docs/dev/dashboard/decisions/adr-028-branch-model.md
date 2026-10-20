# ADR-028 · Keep `sbatchman` as a long-lived project branch fed by merges from the product line

- **Date:** 2026-09-24
- **Status:** accepted

## Context

ADR-026 moved the SbatchMan campaign work onto its own `sbatchman` branch. Over the following
weeks every change landed there, including fixes that have nothing to do with SbatchMan (worker
path resolution, the process-environment fallback, a g500 build regression, the local execution
path). The product line, `feature/web-dashboard`, fell behind on those fixes while partners
started cloning `sbatchman` directly. Without a rule, the two lines drift apart and the eventual
v1.0 merge has to untangle which commits belong where.

## Decision

- `feature/web-dashboard` is the product line (it plays the role of a develop branch) and is
  what merges to `master` at v1.0. The CRAB dashboard in v1.0 has no SbatchMan authoring.
- `sbatchman` stays a long-lived branch for the SbatchMan project. It carries only
  campaign-specific commits (the campaign editor, its API, its docs).
- Generic work lands on the product line first. `sbatchman` then takes it with a regular merge
  commit, at least at the end of every milestone. `sbatchman` is never rebased, because
  partners clone it.
- A change to a file both lines share is made on the product line first, even when only the
  SbatchMan flow needs it right now.
- The views `sbatchman` does not route to stay in its tree (so merges stay clean), and their
  unit tests are excluded there instead of deleted.
- After v1.0, partners keep installing CRAB from the `sbatchman` branch with git, pinned to
  tags named after the release they build on (`v1.0.0+sbatchman`).

## Alternatives considered

- Fold `sbatchman` back into the product line behind a flag: one line to maintain, but it puts
  a project-specific mode into the general product. Can be revisited after v1.0.
- Cherry-pick fixes in whichever direction is convenient: flexible, but drift is easy to miss
  and there is no single source for generic code.
- Delete the unrouted views on `sbatchman`: a leaner branch, but every merge from the product
  line would then hit modify/delete conflicts.

## Consequences

Generic fixes reach partners one merge later than they reach the product line. Merges carry
some noise in shared files that `sbatchman` changes for its own reasons (for example, which
branch the dashboard's "Install CRAB" clones), and those need a quick look at each merge. The
built frontend in `src/crab/web/static/` differs per branch, so a merge that touches frontend
source is resolved by rebuilding on `sbatchman` rather than by picking a side.
