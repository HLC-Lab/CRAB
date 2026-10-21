# SbatchMan example campaigns

Three ready-to-launch campaigns for [SbatchMan](https://github.com/LorenzoPichetti/SbatchMan), the
job manager that submits and tracks the runs. Copy a file to your SbatchMan project directory,
generate `configs.yaml` once, and launch it as it is. Each job runs CRAB's worker (`crab worker`)
on the allocation SbatchMan requests, and the worker runs the experiment embedded in the job. The
[SbatchMan guide](../../docs/using/sbatchman-integration.md) walks through the whole flow, from
installing CRAB on the cluster to reading results.

## Files

`smoke.yaml` runs one all-to-all (Blink `alltoall_b`) alone on 2 nodes with 4 ranks per node. It
is small enough for a debug queue. Launch it first to check that your project, your
`configs.yaml` and CRAB's worker work together.

`a2a-vs-incast.yaml` measures how much incast noise slows an all-to-all. `a2a-vs-a2a.yaml`
measures how much all-to-all noise slows the same all-to-all. Each has two jobs, a baseline and a
noise job, and the variable `nodes` takes the values 4, 8 and 16, so each file launches six jobs.

- `a2a-baseline-<nodes>` runs only the victim, `blink/alltoall/alltoall_b.py`. Its results are
  collected.
- `a2a-incast-noise-<nodes>` (or `a2a-a2a-noise-<nodes>`) runs the victim and an aggressor. The
  aggressor is `blink/incast/incast_nb.py` (or `blink/alltoall/alltoall_nb.py`). It sends 2 MiB
  messages in bursts of 1 second with a pause of 1 second (`-msgsize 2097152 -endl -blength 1
  -bpause 1`). It is not collected, and `"end": "f"` makes CRAB stop it when the victim
  finishes.

## The fairness rule

`nodes` is the total allocation, which is 2N: 4, 8 and 16 give the victim 2, 4 and 8 nodes. Both
jobs of a pair allocate the same 2N nodes and place them the same way: the nodes are split 50/50
between a victim partition and an aggressor partition, interleaved, so the victim gets every
second node. The baseline leaves the aggressor partition idle. The noise job runs the aggressor
on it.

Only the aggressor differs. A baseline that allocated only the victim's N nodes would change the
victim's node count and, with it, which switches its nodes sit behind, and the comparison would
mix that change with the noise. The tests in `tests/test_sbatchman_examples.py` check that the
two jobs of a file have the same `numnodes`, `ppn`, allocation block and victim app.

`a2a-vs-incast.yaml` and `a2a-vs-a2a.yaml` use the same baseline job tag, so the baseline is the
same measurement in both. If you launch both files in one project, SbatchMan may treat the second
baseline as a run it has already done and skip it. Compare both noise jobs with that one
baseline.

## What you need

- CRAB installed on the cluster, with Blink built (`crab setup`) and the wrappers folder
  (`crab wrappers list` shows `blink/alltoall/alltoall_b.py`, `blink/alltoall/alltoall_nb.py` and
  `blink/incast/incast_nb.py`).
- A SbatchMan project directory on the cluster, with the cluster name set:
  `sbatchman set-cluster-name <cluster name>`. The name must equal the `--system` value you give
  `crab sbatchman-preset`.
- A cluster that allows 16 nodes for the two noise files, and 2 for `smoke.yaml`.

## Steps

Run these in the SbatchMan project directory.

1. Generate the configs. Use your cluster name, partition, account and a time limit that fits the
   largest job:

   ```bash
   crab sbatchman-preset --configs-file --nodes 2,4,8,16 --system <cluster name> \
       --partition <partition> --account <account> --time 01:00:00 --tasks-per-node 4 > configs.yaml
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

   `sbatchman status` shows the jobs.

## Where the results land

Each job has a folder in the SbatchMan project (SbatchMan gives it to the job as
`$SBATCHMAN_JOB_DIR`). In it:

- `config.json` is the experiment the job wrote and ran.
- `run/data_app_0.csv` holds the victim's measurements, one row per iteration. The aggressor
  writes none, because it is not collected.
- `crab_run.json` is the run summary the worker wrote.
- SbatchMan's `metadata.yaml` records the tag, the config, the job id and the state.

[Reading results](../../docs/using/results.md) describes the CRAB files.

## Compare baseline and noise

Pair the jobs by node count: `a2a-baseline-8` against `a2a-incast-noise-8`. Open the two
`run/data_app_0.csv` files. Each row is one sample of one run (`run_id`) at one message size
(`msg_size`), and the metric columns are prefixed with the app index and end with the unit: the
victim is app 0, so compare `0_Avg-Duration_s` (app 0's Avg-Duration metric, in seconds) of the two
files for each `msg_size`. The ratio of the noise job's mean to the baseline's is the slowdown. Do
it for 4, 8 and 16 to see how the effect changes with the allocation size.

To change the study, edit the `args` of the victim in both jobs of a file (for example
`-msgsize 8192` for another message size), and keep the two identical. Run
`crab validate` on the heredoc's JSON if you edit it by hand.
