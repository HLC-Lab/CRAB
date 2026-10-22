# ADR-040 · `minruns` counts successful runs; `maxruns` and `timeout` count every started run

- **Date:** 2026-10-22
- **Status:** accepted

## Context

`minruns`, `maxruns` and `timeout` bound how many runs an experiment makes
(`core/experiment/runner.py`). A failed run used to count toward all three. In one cluster run, runs
1 to 6 failed (an `srun` launch failure on one node, exit 233) and runs 7 to 10 succeeded. The
loop logged "Converged at run 10" with only 4 samples, although `minruns` was 10: the convergence
check ran as soon as 10 runs had been started, and it passed on too few good samples.

## Decision

`minruns` counts successful runs. Convergence is not checked, and the experiment does not stop
early, before that many runs have succeeded. `maxruns` and `timeout` count every started run, so
an app that always fails still ends the experiment after `maxruns` runs at most.

A failed run is not retried, and neither is an `srun` launch failure: it stays a failed run, and
the rule above replaces it with a later good run, up to `maxruns`.

The registry is unchanged: `total_runs` is every started run and `failed_runs` the failed ones. The
convergence log line is now "Converged at run N (M successful)".

## Alternatives considered

- Count every run toward `minruns` (the old rule) — rejected: convergence can rest on fewer good
  samples than `minruns`, as in the case above.
- Count only successful runs toward `maxruns` too — rejected: an app that always fails would then
  run until `timeout`.
- Retry a run that failed to launch — rejected: telling a launch failure from an app crash by exit
  code is fragile across Slurm versions, and the new rule already replaces failed runs with good
  ones.
- A separate `maxfails` cap — not needed now: `maxruns` already bounds the failures.

## Consequences

An experiment with failures runs longer, up to `maxruns` runs, and may end at `maxruns` or
`timeout` with fewer than `minruns` successful runs. Read `failed_runs` against `total_runs` in the
registry to see how many samples there are.
