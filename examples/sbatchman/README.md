# SbatchMan example campaigns

Six ready-to-launch campaigns for [SbatchMan](https://github.com/LorenzoPichetti/SbatchMan), the
job manager that submits and tracks the runs, plus the SbatchMan configs file they use. Each job
runs CRAB's worker (`crab worker`) on the allocation SbatchMan requests, and the worker runs the
experiment embedded in the job. The [SbatchMan guide](../../docs/using/sbatchman-integration.md)
walks through the whole flow, from installing CRAB on the cluster to reading results.

## Files

| File | What it is |
|---|---|
| `configs.leonardo.yaml` | The SbatchMan configs file: the Leonardo allocation (account, partition, time) and the CRAB env block every job needs, with two configs per node count: `crab_run_<nodes>` (CPU, gcc and Open MPI modules, for the Blink files) and `crab_qe_<nodes>` (4 GPUs per node, the `profile/chem-phys` and `quantum-espresso` modules, for the QE files). Every jobs file loads it. |
| `smoke.yaml` | One all-to-all (Blink `alltoall_b`) alone on 2 nodes, 4 ranks per node, 1 MiB messages, 100 iterations. Run it first. |
| `blink-noise.yaml` | The small co-run: one 4-node job, an all-to-all against continuous incast noise, 1 MiB messages. |
| `a2a-vs-incast.yaml` | The sweep: how much incast noise slows an all-to-all, 12 jobs (`nodes` 4, 8, 16 times `msgsize` 1 KiB, 64 KiB, 1 MiB, 4 MiB). |
| `a2a-vs-a2a.yaml` | The same sweep with all-to-all noise instead of incast. |
| `qe-alone.yaml` | Quantum ESPRESSO `pw.x` (a silicon SCF run) alone on 2 nodes, 4 ranks per node: the reference run. |
| `qe-noise.yaml` | The `blink-noise.yaml` design with `pw.x` as the victim: one 4-node job, QE on 2 nodes against a continuous incast aggressor on the other 2, baseline and noise in one allocation. |
| `qe/` | The input and pseudopotential the two QE files read from the checkout. |

## Steps

1. Install CRAB (with Blink built and the wrappers folder) and SbatchMan on the cluster, as
   described in the [SbatchMan guide](../../docs/using/sbatchman-integration.md).

2. In your SbatchMan project directory (the folder you launch from), once:

   ```bash
   sbatchman init
   sbatchman set-cluster-name leonardo
   cp <CRAB checkout>/examples/sbatchman/configs.leonardo.yaml .
   ```

   Edit the copy and fill in `<YOUR_ACCOUNT>` (your Slurm account) and `<CRAB_DIR>` (the absolute
   path of your CRAB checkout). It defines two configs per node count, `crab_run_2`, `crab_run_4`,
   `crab_run_8` and `crab_run_16` for Blink, and `crab_qe_2` and so on for Quantum ESPRESSO, and the
   jobs files read it from the folder they sit in.

   The QE module on Leonardo (`quantum-espresso`, behind the `profile/chem-phys` profile) is a CUDA
   build: `pw.x` aborts at start with `error in Message Passing (mp) module, error code 9100` when
   the job has no GPU. So `crab_qe_<nodes>` asks for 4 GPUs per node (`gpus: 4`, one per rank at 4
   ranks per node) and loads that module, whose own `mpirun` the worker needs. The modules are set
   per config, not in `default_conf`, so the Blink and QE MPI stacks never mix.

   Also check `time` and `qos`. `time` is 30 minutes by default, and a job runs six experiments
   back to back, so raise it for the 12-job sweeps at 16 nodes and 4 MiB. `qos` is `normal` by
   default; for a quick check set the site's debug QOS, after reading its node and time limits.

3. Copy a jobs file next to it (the command below copies four), check what SbatchMan would submit, then launch:

   ```bash
   cp <CRAB checkout>/examples/sbatchman/{smoke,blink-noise,qe-alone,qe-noise}.yaml .
   sbatchman launch -f smoke.yaml --dry-run
   sbatchman launch -f smoke.yaml
   ```

   The `cp` line copies four files (the shell expands the braces). Do not copy with `*.yaml`: it
   would overwrite the `configs.leonardo.yaml` you edited. Copy the sweep files
   (`a2a-vs-incast.yaml`, `a2a-vs-a2a.yaml`) the same way when you want them.

   Launch `smoke.yaml` first. Then launch the others one at a time, a dry run first:

   ```bash
   sbatchman launch -f blink-noise.yaml --dry-run
   sbatchman launch -f blink-noise.yaml
   sbatchman launch -f qe-alone.yaml --dry-run
   sbatchman launch -f qe-alone.yaml
   sbatchman launch -f qe-noise.yaml --dry-run
   sbatchman launch -f qe-noise.yaml
   ```

   `sbatchman status` shows the jobs.

   The two QE files need Quantum ESPRESSO registered in CRAB on the cluster (`crab setup`; a
   module install is fine, and on Leonardo the module command is `module load profile/chem-phys
   quantum-espresso`). They use the `crab_qe_<nodes>` config.

   SbatchMan skips a job identical to one that already ran in the project (same cluster, config,
   tag and commands). To run it again, use `sbatchman launch -f <file> --force`.

## What a noise job runs

`blink-noise.yaml` is one job. The two sweep files have one job template and two variables:
SbatchMan launches one job for every (`nodes`, `msgsize`) pair, tagged
`a2a-incast-<nodes>n-<msgsize>B` (or `a2a-a2a-<nodes>n-<msgsize>B`), and `blink-noise.yaml` is tagged
`blink-noise-4n-1048576B`.

Every noise job runs six experiments, one after another, on one allocation:

- `r1_baseline`, `r2_baseline` and `r3_baseline` run only the victim,
  `blink/alltoall/alltoall_b.py`, with `-msgsize <msgsize> -iter 100`. Its results are collected.
- `r1_noise`, `r2_noise` and `r3_noise` run the same victim and an aggressor. The aggressor is
  `blink/incast/incast_nb.py` (or `blink/alltoall/alltoall_nb.py` in `a2a-vs-a2a.yaml`). It sends
  2 MiB messages continuously (`-msgsize 2097152 -endl`). It is not collected, and `"end": "f"`
  makes CRAB stop it when the victim finishes.

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

- More jobs, so more queue waits: 12 jobs per sweep file, 24 if you launch both.
- Two sizes never share an allocation, so a comparison across message sizes spans different
  nodes. Compare baseline and noise inside a job, and read trends across sizes with that in mind.
- A job runs six experiments back to back, so the `time` in `configs.leonardo.yaml` must fit all
  of them at the largest size and node count. CRAB gives each experiment 1200 seconds by default.

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

For the QE files the victim is Quantum ESPRESSO, also app 0, and its metric column is
`0_wall_time_seconds` (the wrapper records `wall_time`, in seconds).

Repeat this for each job, then put the slowdowns side by side: along `nodes` to see how the
effect changes with the allocation size, along `msgsize` to see which messages suffer. Each
slowdown comes from one allocation, so a difference between two jobs may include a difference
between their nodes as well as in the noise.

To change the study, edit the lists under `variables` (for example `nodes: [4]` and
`msgsize: [1048576]` give a single job), and keep every `nodes` value covered by the `nodes` list
in `configs.leonardo.yaml`. If you edit an experiment by hand, keep the victim and the allocation
identical in all six, and run `crab validate` on the heredoc's JSON.
