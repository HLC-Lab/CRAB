# ADR-035 · Blink wrappers are named after Blink's programs

- **Date:** 2026-10-12
- **Status:** superseded by [ADR-037](adr-037-blink-wrapper-folders.md) (2026-10-20)

## Context

Upstream Blink (HLC-Lab/blink) moved to CMake on 2026-05-22 and renamed its programs after the
collective they run: `a2a_nb` became `alltoall_nb`, `ardc_b` became `allreduce_b`, and so on.
CRAB's wrappers still launched the old short binaries, so a fresh Blink build left them with
nothing to run. People who read Blink's own documentation could also not tell which wrapper went
with which program.

## Decision

- A wrapper file `blink/<name>.py` launches the Blink program `<name>`. The two names are the same.
- The short names are removed, not kept as aliases.
- A test in the crab-wrappers repository checks that every Blink wrapper matches a program name,
  so a wrapper cannot drift from the program it runs.
- Two wrappers stay that have no upstream program, because they exist only in the fork
  (blink-clean): `bursty_noise_a2a` and `bursty_noise_incast`.
- `workerpool` was removed from that list: it was never a Blink program, and the scheduler it
  needs does not exist in CRAB (the supported schedulers are `slurm` and `local`).
- The Blink recipe builds with CMake (`-DBLINK_TESTS=OFF`) and records `build/bin/` as the
  binary folder.

## Alternatives considered

- Keep the short names: they drift from Blink, and the next upstream rename breaks them again.
- Keep both names through aliases: every program has two names, and configs keep the old ones
  forever.

## Consequences

A config that names an old wrapper fails with "Wrapper not found" until it is renamed. The
crab-wrappers `blink/README.md` has the rename table and a `sed` command that does it. The
`pingpong_b` wrapper now reads Blink's output, which has the five standard durations and no
bandwidth, so results that used the old bandwidth column have no replacement. The wrappers that
need the fork need a receipt that points at the fork's own binaries.
