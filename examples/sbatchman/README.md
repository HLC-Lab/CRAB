# SbatchMan example campaigns

Three ready-to-launch campaigns for [SbatchMan](https://github.com/LorenzoPichetti/SbatchMan), the
job manager that submits and tracks the runs. Copy a file to your SbatchMan project directory,
generate `configs.yaml` once, and launch it as it is. Each job runs CRAB's worker (`crab worker`)
on the allocation SbatchMan requests, and the worker runs the experiment embedded in the job. The
[SbatchMan guide](../../docs/using/sbatchman-integration.md) walks through the whole flow, from
installing CRAB on the cluster to reading results.

## Files

`smoke.yaml` runs one all-to-all (Blink `alltoall_b`) alone on 2 nodes with 4 ranks per node,
1 MiB messages and 100 iterations per run. It is small enough for a debug queue. Launch it first
to check that your project, your `configs.yaml` and CRAB's worker work together.

`a2a-vs-incast.yaml` measures how much incast noise slows an all-to-all. `a2a-vs-a2a.yaml`
measures how much all-to-all noise slows the same all-to-all. Each file has one job template and
two variables: `nodes` takes 4, 8 and 16, and `msgsize` takes 1024, 65536, 1048576 and 4194304
bytes. SbatchMan launches one job for every pair, so each file launches 12 jobs, tagged
`a2a-incast-<nodes>n-<msgsize>B` (or `a2a-a2a-<nodes>n-<msgsize>B`).

Every job runs six experiments, one after another, on one allocation:

- `r1_baseline`, `r2_baseline` and `r3_baseline` run only the victim,
  `blink/alltoall/alltoall_b.py`, with `-msgsize <msgsize> -iter 100`. Its results are collected.
- `r1_noise`, `r2_noise` and `r3_noise` run the same victim and an aggressor. The aggressor is
  `blink/incast/incast_nb.py` (or `blink/alltoall/alltoall_nb.py`). It sends 2 MiB messages in
  bursts of 1 second with a pause of 1 second (`-msgsize 2097152 -endl -blength 1 -bpause 1`).
  It is not collected, and `"end": "f"` makes CRAB stop it when the victim finishes.

CRAB runs the experiments of a config in sorted name order. The names start with the round
(`r1_`, `r2_`, `r3_`), so they sort, and run, as baseline, noise, baseline, noise, baseline,
noise.

## Why one job, and why alternate

Baseline and noise are experiments of the same job, so they run in the same Slurm allocation, on
the same physical nodes. Two separate jobs would land on different nodes, with different switches
and different neighbours, and the comparison would mix that difference with the noise.

The experiments run in turns, three rounds of baseline then noise, so a slow drift (other jobs
on the network, a warming cache) lands on both sides instead of only on whichever ran last. This
is advice for any noise study, not a CRAB rule: CRAB runs whatever order the names sort to.

The price of one job per (nodes, msgsize) pair is stated plainly:

- More jobs, so more queue waits: 12 jobs per file, 24 if you launch both.
- Two sizes never share an allocation, so a comparison across message sizes spans different
  nodes. Compare baseline and noise inside a job, and read trends across sizes with that in mind.
- A job runs six experiments back to back, so ask for a time limit that fits all of them at the
  largest size and node count.

## The fairness rule

`nodes` is the total allocation, which is 2N: 4, 8 and 16 give the victim 2, 4 and 8 nodes. All
six experiments of a job use the same 2N nodes and place them the same way: the nodes are split
50/50 between a victim partition and an aggressor partition, interleaved, so the victim gets every
second node. The baseline leaves the aggressor partition idle. The noise experiments run the
aggressor on it.

Only the aggressor differs. A baseline that allocated only the victim's N nodes would change the
victim's node count and, with it, which switches its nodes sit behind, and the comparison would
mix that change with the noise. The tests in `tests/test_sbatchman_examples.py` check that the
six experiments of a file share the allocation block and the same victim app, and that the
noise experiments add exactly the one aggressor.

## What you need

- CRAB installed on the cluster, with Blink built (`crab setup`) and the wrappers folder
  (`crab wrappers list` shows `blink/alltoall/alltoall_b.py`, `blink/alltoall/alltoall_nb.py` and
  `blink/incast/incast_nb.py`).
- A SbatchMan project directory on the cluster, with the cluster name set:
  `sbatchman set-cluster-name <cluster name>`. The name must equal the `--system` value you give
  `crab sbatchman-preset`.
- A cluster that allows 16 nodes for the two noise files, and 2 for `smoke.yaml`.
- A time limit for the largest noise job that fits six experiments, each with 10 to 20 runs of
  100 iterations. CRAB gives each experiment 1200 seconds by default, so six experiments can use
  two hours.

## Steps

Run these in the SbatchMan project directory.

1. Generate the configs. Use your cluster name, partition, account and a time limit that fits the
   largest job:

   ```bash
   crab sbatchman-preset --configs-file --nodes 2,4,8,16 --system <cluster name> \
       --partition <partition> --account <account> --time 02:00:00 --tasks-per-node 4 > configs.yaml
   ```

   The file defines `crab_run_2`, `crab_run_4`, `crab_run_8` and `crab_run_16`, one per node
   count. The jobs name them as `crab_run_{nodes}`. Leave out `2` if you only run the two noise
   files.

2. Copy the examples next to `configs.yaml`:

   ```bash
   cp <path to CRAB>/examples/sbatchman/*.yaml .
   ```

   The jobs files read `configs.yaml` from the folder they sit in.

3. Check what SbatchMan would submit, then launch the smoke test:

   ```bash
   sbatchman launch -f smoke.yaml --dry-run
   sbatchman launch -f smoke.yaml
   ```

4. When it finishes cleanly, launch the campaigns:

   ```bash
   sbatchman launch -f a2a-vs-incast.yaml --dry-run
   sbatchman launch -f a2a-vs-incast.yaml
   sbatchman launch -f a2a-vs-a2a.yaml
   ```

   Each file submits 12 jobs. To try one first, shrink the lists under `variables` as described
   below.

   `sbatchman status` shows the jobs.

## Where the results land

Each job has a folder in the SbatchMan project (SbatchMan gives it to the job as
`$SBATCHMAN_JOB_DIR`). In it:

- `config.json` is the config the job wrote and ran, with the six experiments.
- `r1_baseline/`, `r1_noise/`, `r2_baseline/` and so on, one folder per experiment. Each holds
  `data_app_0.csv`, the victim's measurements, one row per iteration. The aggressor writes none,
  because it is not collected.
- `crab_run.json` is the run summary the worker wrote.
- SbatchMan's `metadata.yaml` records the tag, the config, the job id and the state.

[Reading results](../../docs/using/results.md) describes the CRAB files.

## Compare baseline and noise

Work inside one job, for example `a2a-incast-8n-65536B`. Each `data_app_0.csv` has one row per
sample, with the columns `run_id` and `msg_size`, and the metric columns named
`<app index>_<metric>_<unit>`. The victim is app 0, so the column to use is `0_Avg-Duration_s`
(app 0's Avg-Duration metric, in seconds).

1. Pool the rows of `0_Avg-Duration_s` from `r1_baseline`, `r2_baseline` and `r3_baseline`.
2. Pool the rows from `r1_noise`, `r2_noise` and `r3_noise`.
3. The slowdown is `mean(noise) / mean(baseline)`.

Repeat this for each job, then put the slowdowns side by side: along `nodes` to see how the
effect changes with the allocation size, along `msgsize` to see which messages suffer. Each
slowdown comes from one allocation, so a difference between two jobs may include a difference
between their nodes as well as in the noise.

To change the study, edit the lists under `variables` (for example `nodes: [4]` and
`msgsize: [1048576]` give a single job), and keep every `nodes` value covered by the `--nodes`
list you gave `crab sbatchman-preset`. If you edit an experiment by hand, keep the victim and the
allocation identical in all six, and run `crab validate` on the heredoc's JSON.
