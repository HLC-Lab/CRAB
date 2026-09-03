# ADR-026 · SbatchMan work moves to its own dedicated branch

- **Date:** 2026-09-03
- **Status:** accepted

## Context

ADR-025 (plan 084) built the SbatchMan campaign generator as a `--sbatchman`-flag-gated mode
inside the shared dashboard codebase (`feature/sbatchman-integration`, off
`feature/web-dashboard`): with the flag off the dashboard was unchanged, with it on a new
"SbatchMan" nav item exposed campaign authoring plus write and launch actions.

The owner has since decided this integration is scoped to one project's needs, not the general
direction of CRAB: most users will keep running standalone CRAB (no SbatchMan), which needs its
own native variable-sweep support to close the gap SbatchMan currently fills for this project.
Keeping both audiences served by one flag-gated codebase means every future dashboard change has
to consider two UI shapes indefinitely, for a variant that only one project needs and that will
not grow further once it works. Separately, the boundary itself narrowed: this project's actual
workflow has SbatchMan launch, monitor, and plot results entirely on its own — CRAB's role stops
at generating and pushing the campaign YAML, so `POST /api/sbatchman/launch` and its UI no longer
belong in the product at all, not even behind a flag.

## Decision

- **The SbatchMan work moves to its own permanent branch, `sbatchman`** (renamed from
  `feature/sbatchman-integration`, keeping all of plan 084's history), instead of staying a mode
  toggled inside `feature/web-dashboard`. It is a **one-time fork**: standalone-line fixes
  (engine, wrappers, the dashboard itself) are not resynced into it going forward, and it does
  not merge back. `feature/web-dashboard` remains the standalone-CRAB working line and gains a
  roadmap placeholder for native variable-sweep support instead of relying on SbatchMan.
- **The `sbatchman` branch always runs in SbatchMan mode**; the `--sbatchman` flag stops being
  meaningful (`get_settings()` hardcodes it on) rather than the dashboard supporting two shapes
  from one branch.
- **Launch is removed outright, not deferred.** `POST /api/sbatchman/launch`, its request/
  response models, and the UI's "Run sbatchman launch" button are deleted, not merely
  flag-gated. SbatchMan owns launching, monitoring, and plotting results end to end; CRAB's
  contribution stops at Write (compose the campaign YAML, save it locally, push it to the
  cluster). This narrows ADR-025's "generator, not driver" framing further: the generator no
  longer drives anything at all, even once.
- **Classic single-experiment Author (with its own direct `crab run` submit) is unhooked too**,
  leaving the campaign editor as the sole authoring surface on this branch — a one-off campaign
  is just a single group with no sweep variables, so nothing is lost.
- **Removal on this branch is "unhook, not delete."** Author/Jobs/Results view, store, and
  component files, and the `--sbatchman` flag/`Settings.sbatchman`/env-var plumbing, stay in the
  tree unregistered rather than being deleted, since this branch will not be actively maintained
  long enough to justify the larger diff. Recorded in `deferred.md` so it is not forgotten if the
  branch is ever picked up again.

## Alternatives considered

- Keep plan 084's flag mechanism and simply default it on — rejected: still carries the whole
  dual-mode nav/route branching inside one codebase indefinitely, for a variant that will never
  need the other mode again once split out.
- Delete Author/Jobs/Results outright now instead of deferring — rejected: a much larger diff
  for a one-off project branch that will not be actively developed long-term; the owner chose
  deferred deletion, tracked in `deferred.md`.
- Keep the Launch action (flag-gated or not) — rejected: the owner was explicit that SbatchMan
  launches, checks status, and plots results; CRAB triggering `sbatchman launch` duplicates a
  responsibility SbatchMan already owns end to end.
- Periodically resync standalone-line fixes into the `sbatchman` branch — rejected for now: the
  owner chose a one-time fork given the branch's scoped, short-term purpose; revisit if the
  branch turns out to need longer-term maintenance than expected.

## Consequences

Easier: the standalone line (`feature/web-dashboard`, eventually `master`) never has to carry
SbatchMan-mode branching again, and the `sbatchman` branch's UI is simpler (one authoring flow,
no dual-mode nav). Harder: the two branches will drift — an engine or wrapper bugfix landing on
the standalone line does not reach `sbatchman` automatically, and the dead Author/Jobs/Results/
flag code on `sbatchman` is a standing maintenance cost if that branch is ever revived for real
feature work (tracked in `deferred.md`, not silently forgotten). Standalone CRAB now has an
explicit, owner-acknowledged gap (native variable sweeps) that this integration used to paper
over for this one project; closing it is a separate, not-yet-scoped piece of work.
