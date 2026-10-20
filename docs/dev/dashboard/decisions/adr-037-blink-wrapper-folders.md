# ADR-037 · Blink wrappers live in Blink's own source folders

- **Date:** 2026-10-20
- **Status:** accepted

## Context

Upstream Blink keeps each program under `src/<group>/` (`src/alltoall/alltoall_nb.c`,
`src/incast/incast_put.c`, 17 groups). Its binaries stay flat in `build/bin/`. CRAB's Blink
wrappers were flat (`blink/alltoall_nb.py`), so the list of 38 wrappers did not match how Blink
documents and groups its programs. Upstream also removed the two bursty noise programs that only
the fork (blink-clean) had kept, and ADR-035 still kept wrappers for them.

## Decision

- A Blink wrapper sits at `blink/<group>/<name>.py`. `<group>` is the folder under upstream
  `src/` that holds `<name>.c` or `<name>.cpp`, and `<name>` is the program it launches, as in
  ADR-035. The binary lookup is unchanged: programs are still found by name in `build/bin/`.
- A test in the crab-wrappers repository checks that every wrapper sits in its program's upstream
  folder.
- `bursty_noise_a2a` and `bursty_noise_incast` are removed. Upstream makes the same traffic with
  `alltoall_nb` and `incast_nb` and the burst flags `-endl -blength -bpause`. The fork clause of
  ADR-035 goes with them: every Blink wrapper now runs an upstream program.
- A config that still names an old flat path (`blink/alltoall_nb.py`) keeps working. When the
  file is gone but exactly one `blink/<group>/alltoall_nb.py` exists, the wrapper resolver uses
  it, and the experiment logs a warning that names the new path.

## Alternatives considered

- Keep the flat layout: the wrapper list keeps diverging from Blink's own layout and README.
- Group by our own categories (collectives, point-to-point): a second taxonomy to maintain, and
  it drifts at the next upstream reorganisation.
- Hard break for old paths: cheaper by a few lines, but every existing config and campaign fails
  at once, and the fallback is confined to the resolver.

## Consequences

The fallback only looks one folder down and only when the match is unique, so it cannot pick the
wrong program. It is a migration aid: configs, examples and docs use the new paths, and the
warning tells users to update theirs. Results that used the bursty noise wrappers need their
configs rewritten to `alltoall_nb` / `incast_nb` with the burst flags (the crab-wrappers
`blink/README.md` shows how).
