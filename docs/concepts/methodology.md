# Methodology

This page describes how CRAB measures: what one run is, how runs repeat, when CRAB decides it has
enough of them, how co-running applications start and stop, and what counts as a failure. It also
lists what a result file records and what it does not. Read it when you need to judge the numbers
CRAB produces or to describe them in a paper. Every statement here comes from the engine's code;
the file names point to where.

## Runs and experiments

A [job](../glossary.md#job) is one `crab run`. It holds one or more
[experiments](../glossary.md#experiment), and the worker executes them one after the other, in
sorted key order. An experiment is a fixed set of applications with a fixed allocation, repeated.
One repetition is a [run](../glossary.md#run): every app of the experiment starts once, the run
lasts until its apps are done, and CRAB then collects the output of the apps that have
`collect: true`.

A run is an execution of the whole workload, not one iteration of a benchmark. A benchmark that
loops internally (for example 1000 iterations of an all-to-all) prints many samples in one run, and
CRAB records them all under the same `run_id`. Runs are the repetitions CRAB controls. Iterations
are inside the application.

Some consequences of how the loop is built:

- Runs of one experiment are consecutive, and experiments run one after the other. CRAB does not
  interleave runs of different experiments. If the system changes between two experiments (other
  users' traffic, a different time of day), that change shows up as a difference between them.
- Every run starts the apps afresh, in a working directory of its own (`run_<n>/app_<i>/`).
- CRAB has no warm-up setting. It keeps every run and every sample the application prints. A
  benchmark that needs warm-up iterations must discard them itself.
- After each run CRAB stops any app of it that is still running and, under Slurm, cancels leftover
  job steps, so a step that outlived its app cannot block the next run.

## How many runs: the convergence check

`global_options` (or an experiment's `local_options`) sets four bounds, listed in the
[configuration reference](../reference/configuration.md#global_options):

| Key | Default | Role |
|---|---|---|
| `minruns` | `10` | Convergence is not checked before this many runs have succeeded. |
| `maxruns` | `20` | The experiment never starts more runs than this, failed ones included. |
| `timeout` | `1200.0` | Seconds of wall-clock time for the whole experiment, counted from its start. |
| `alpha`, `beta` | `0.05`, `0.05` | The confidence level and the width threshold below. |

Once `minruns` runs have succeeded, CRAB checks convergence after every run (`check_CI` in
`src/crab/core/data/utils.py`). The experiment stops when `maxruns` runs are done, or `minruns`
runs have succeeded and the check passes, or `timeout` seconds have passed, whichever comes first.
A failed run counts toward `maxruns` and `timeout` but not toward `minruns`, and CRAB does not
retry it: later runs replace it, up to `maxruns`. An experiment with failures can therefore end
at `maxruns` or `timeout` with fewer than `minruns` successful runs.

### What the check computes

The check works on one series of samples at a time. There is one series for each combination of
app, metric and, for a wrapper that declares `keys`, key value (each message size of a sweep is
its own series). For each series that is a target and has not converged yet:

1. Take the samples of the series from all runs so far. Rows whose `role: "check"` metric was
   false are left out.
2. With `n` samples, skip the series if `n` is 1 or less.
3. Compute the mean and the standard error of the mean (sample standard deviation divided by the
   square root of `n`).
4. Compute the two-sided Student t confidence interval for the mean, with confidence level
   `1 - alpha` and `n - 1` degrees of freedom.
5. The series converges when the interval's width, upper bound minus lower bound, is smaller than
   `beta` times the absolute value of the mean. With the defaults the interval spans less than 5%
   of the mean, so its half-width is under 2.5%.

If all samples are identical, the standard error is 0 and the series converges at once. A series
that converged stays converged: later runs do not reopen it.

The experiment has converged when every target series has converged. A target is a numeric metric
flagged `conv` in the wrapper's `metadata`, or every numeric metric when `convergeall` is `true`.
Text and boolean metrics are never targets. If an experiment has no target at all, it never
converges and runs until `maxruns` or `timeout`. A key value that first appears in a later run
starts its own series, and the experiment waits for it too.

### It pools iterations

The samples in step 1 are every value the application printed, from every run. CRAB does not
average the iterations of a run first. A run that prints 1000 iterations adds 1000 samples, and
the interval treats all of them as independent draws.

That has two effects a careful reader should weigh:

- Iterations inside one run share their nodes, placement and the traffic of that moment, so they
  are correlated. The interval then understates the uncertainty of the mean across runs, which is
  what a comparison between experiments depends on.
- `n` counts iterations, not runs, and the interval narrows as `n` grows (step 3). With many
  iterations per run, `n` is large after a few runs. Raise `minruns` when you want more
  repetitions.

CRAB does not yet compute run-level statistics (a summary per run, a confidence interval over run
summaries, run-to-run variation), and convergence does not use them. The raw CSV keeps `run_id` on
every row, so you can compute them yourself: group by `run_id`, summarize each group, and take the
statistic over the groups. [Reading results](../using/results.md#read-the-files) shows the pattern.

## Co-runs

All apps of an experiment belong to the same run and are launched by that run's event loop
(`src/crab/core/experiment/schedule.py`).

Start. The `start` of each app is a delay in seconds from the start of the run, `"0"` by
default. Apps with the same delay are launched in the same pass of the loop, in app order, each
launch starting without waiting for the previous one to come up. There is no barrier between apps,
so the moment the ranks of two apps actually begin to run differs by the launchers' own start-up
time. `"sN"` starts an app only after app N has finished, which turns a run into a
sequence. See [Scheduling](../reference/configuration.md#scheduling-start-and-end).

End. The `end` of an app decides how it stops, and the run lasts as long as the apps that do
not have `end: "f"`:

| `end` | The app |
|---|---|
| `""` | runs to its own end. The run waits for it. |
| a number | is stopped that many seconds after it started. |
| `"f"` | runs in the background. CRAB stops it when every other app is done. |

To stop an app, CRAB sends `SIGTERM` to its process group, waits up to 10 seconds, then sends
`SIGKILL`. `collect` does not affect when a run ends, only whether the output is parsed. An app
that is stopped this way normally exits non-zero, and CRAB does not parse the output of an app that
exited non-zero, so a collected app with a timed or `f` end produces no samples. CRAB warns about that
combination before the run starts.

Placement and sharing. CRAB gives each app a list of nodes before the job starts, from the
`allocation` block: contiguous blocks (`linear`), round-robin (`interleaved`), a shuffled list
(`random`, with a `seed` you give or one CRAB draws and records), named partitions, or `shared`, where every app gets
all the nodes and the apps compete inside each node. An app with `start: "sN"` runs on the nodes
of the app it waits for, unless it names another partition. Each app has its own `ppn`, and can
have its own core list (`cpus`). With `shared`, `cores` says whether apps keep to disjoint cores or
may overlap. The launcher binds ranks as the [configuration
reference](../reference/configuration.md#shared-nodes) describes. Under `srun` and `mpirun` the hosts of each app are in
its launch line in `crab_run.json`.

## What counts as a failed run

A run fails when any of these happens:

- an app exits with a non-zero status, including an `f` app that crashes before CRAB stops it;
- CRAB cannot read an app's output after it ended;
- the wrapper's `read_data()` raises, or returns something that does not match its `keys` and
  `metadata` (a missing or extra field, a value of the wrong type, no rows);
- a metric with `role: "check"` is false in any row.

What a failure leaves behind:

- Samples. An app that exited non-zero, or whose output could not be parsed, adds no rows for
  that run, so its `run_id` is missing from that app's CSV. The other apps of the run still add
  theirs. A row whose check was false is written, and kept out of convergence.
- Logs. An app that exits non-zero gets `error_app_<i>.log` in the experiment folder, with
  its exit code, stdout and stderr. Parse and check failures are logged as `PARSE FAILED` and
  `CHECK FAILED` lines in the job's log.
- The registry. Each experiment adds a row to `metadata.csv` with `status`, `total_runs` and
  `failed_runs`. `status` is `COMPLETED`, `FAILED` or `TIMEOUT`. It becomes `FAILED` when a
  run fails, and later successful runs do not reset it, so read `failed_runs` against
  `total_runs` to see how much data there is. `TIMEOUT` is set when the experiment's `timeout`
  hits in the middle of a run: CRAB stops the apps, the run is counted in `total_runs` but not
  in `failed_runs`, and the apps it stopped add no rows. When `timeout` passes between two runs,
  the experiment just ends with the status it had, usually `COMPLETED`.
- The job. A failed run does not stop the experiment, and an experiment whose runs failed does
  not by itself fail the job. An exception that escapes a run (for example the launcher cannot start) stops
  that experiment with status `FAILED`; CRAB writes the CSV of the runs finished so far, and the
  next experiment still runs. The job then ends failed, listing the experiments that stopped.

## What a result records

Every job folder holds the inputs and the identity of the code that produced the data (see
[What a job writes](../using/results.md#what-a-job-writes)):

- `config.json`, `environment.json` and `execution.json` are the config, the preset's environment
  and the preset's scheduler, launcher and hosts, as the job ran them.
- `crab_run.json` records `crab_version`, `crab_commit` (the checkout's commit; `null` on a pip
  install), `wrapper_api`, the start time and the allocated `nodes`. For the wrappers it records
  the folder, the clone's commit and whether it had local edits, and for each app the wrapper
  file's path and SHA-256. Its `execution` block holds the scheduler, hosts and launcher, and with
  `mpirun` in dialect `auto` the dialect found and the first line of `mpirun --version`. Its
  `launches` block holds each app's first launch line, `ppn`, `env` and `nics`. Its `allocations`
  block holds each experiment's allocation mode, its seed when the layout was random, and each
  app's nodes.
- `bindings_app_<i>.log` holds the launcher's report of how it bound the ranks.
- `metadata.csv` holds the run counts and status of each experiment, and the allocation seed when
  there is one.

With these a reader can tell which CRAB and which wrappers ran, on which nodes, with which launch
lines. To rerun a study, keep the job folder or its `config.json`.

## Known limits

- Convergence pools iterations, as described above. It is a stopping rule for the number of
  runs, not a statement about the confidence interval of the run-level mean.
- The interval assumes independent, roughly normal samples. CRAB does not test either.
  Heavy-tailed latencies break the second assumption.
- `msg_size` comes only from `-msgsize` in the app's `args`. CRAB takes the integer after that
  token, or writes `0` when there is none, and the value is the same on every row of the file.
  An application that takes its message size another way shows `0`. For a sweep, declare `keys`
  in the wrapper: the sizes then go in key columns.
- A collected app that CRAB stops gives no data. A timed `end` or `"f"` stops the app with
  `SIGTERM`, and an app that writes its results only on another signal, or at its normal end,
  writes nothing. CRAB skips such an app without marking the run as failed.
- No application-side record. `crab_run.json` identifies CRAB and the wrapper files. It does
  not store the application's binary checksum or build, the loaded modules (under Slurm they are
  in `crab_job.sh`), or what other jobs sent over the network during the run.
- Placement varies between jobs. Slurm decides which nodes a job gets, and a `random` allocation
  without a `seed` draws a new one for each experiment. CRAB records the seed, so you can repeat
  a layout by setting that `seed` and running on the same node list. Compare experiments of the
  same job when you can, because they share one node list.
- Experiments are sequential. Differences between experiments include any drift of the system
  over the job's duration.
