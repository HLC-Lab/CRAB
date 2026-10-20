# ADR-020 · Track rerun lineage as additive fields, not a new entity

- **Date:** 2026-07-06
- **Status:** accepted

## Context

A rerun (whole-job or a subset of experiments) was submitted as a brand new, unrelated job
record — there was no way to tell, looking at either job, that one was a retry of the other, or
which experiments a partial rerun covered.

## Decision

`JobRecord` gains two optional fields: `rerun_of` (the parent job's id, if this job is itself a
rerun) and `rerun_experiments` (the experiment keys included; `None` means a whole-config
rerun, not "unknown" — there was no `--only` filter to narrow it). Both are additive and
optional, so existing records on disk keep working unread; no migration or schema version bump.
Whole-job and partial reruns both set `rerun_of`, so the two rerun buttons in the UI behave
consistently. The per-job detail endpoint resolves `rerun_of` into the parent record and looks
up every job whose `rerun_of` points back at the current one, so a job's detail view can show
both "this is a rerun of X" and "these are the reruns of this job" without a dedicated lineage
table.

## Alternatives considered

- A separate "reruns" table/entity linking two job ids — more normalized, but the local job
  registry is a single flat JSON array with no delete semantics; a link field on the record
  itself is simpler and matches the existing storage shape.
- Only track partial reruns, not whole-job ones — cheaper, but leaves the two rerun code paths
  behaving differently for no real reason.

## Consequences

Easier: viewing a job's rerun history, or a rerun's origin, is one lookup over the existing
registry, no new storage. Harder: since lookups scan the full job list, this stays cheap only as
long as the registry itself is small (same assumption the rest of the registry already makes,
per ADR-016).
