# Reading results

A job writes one folder. It holds the data each experiment collected, the config that produced it,
and a record of which CRAB and which wrappers ran. This page describes that folder, then three ways
to read it: the files, the `crab` command line and the web dashboard. It ends with `crab export`,
which turns a folder into one HTML file you can send to someone.

## What a job writes

Results go under `<datapath>/<system>/<name>_<timestamp>/`.

- `datapath` is the [`global_options`](../reference/configuration.md#global_options) key of that
  name. It defaults to `data/` in the CRAB checkout.
- `system` is `CRAB_SYSTEM` from the [preset](presets.md). It defaults to the preset's name. A
  character other than a letter, digit, `_` or `-` becomes `_`.
- `name` is `global_options.name`, with the same character rule. Without a name the folder is
  only the timestamp, such as `2026-06-09_14-30-05-123456`.

A job with one experiment, `a2a_vs_g500`, whose app 0 is collected and whose app 1 failed in one
run, looks like this:

```
data/leonardo/
├── metadata.csv                         # one row per experiment, every job on this system
└── interference_2026-06-09_14-30-05-123456/
    ├── config.json                      # the config that ran
    ├── environment.json                 # the resolved environment
    ├── execution.json                   # the preset's scheduler, launcher and hosts
    ├── crab_run.json                    # which CRAB and wrappers produced this
    ├── crab_job.sh                      # the batch script (Slurm only)
    ├── slurm_output.log                 # the job's stdout
    ├── slurm_error.log                  # the job's stderr
    └── a2a_vs_g500/
        ├── data_app_0.csv               # the collected metrics of app 0
        ├── error_app_1.log              # only for an app that exited non-zero
        ├── bindings_app_0.log           # only under mpirun or srun
        ├── bindings_app_1.log
        ├── artifacts/
        │   └── run_3/app_0/...          # files a wrapper asked to keep
        └── run_3/app_1/                 # only when the app wrote visible files
```

The files, from the job folder down:

| File | What it holds |
|---|---|
| `config.json`, `environment.json`, `execution.json` | The config, environment and execution settings the job ran with. Keep the folder, or only `config.json`, to run the same study again. |
| `crab_run.json` | Provenance. See [below](#crab_runjson). |
| `slurm_output.log`, `slurm_error.log` | The job's stdout and stderr. The local scheduler writes files with the same names. A local job also leaves `local_job.json` and `local_state.json`. |
| `<experiment>/data_app_<i>.csv` | The metrics of app `i`, if the app has `collect: true`. See [below](#data_app_icsv). |
| `<experiment>/error_app_<i>.log` | The exit code, stdout and stderr of app `i` after a run in which it exited non-zero. Each failure overwrites the file, so it shows the latest one. |
| `<experiment>/bindings_app_<i>.log` | The launcher's stderr for app `i`, with a `# run <n>` line before each run. It records how `mpirun` or `srun` bound the ranks. |
| `<experiment>/artifacts/run_<n>/app_<i>/` | The files a wrapper's `get_extra_artifacts()` named, copied for every run, failed ones included. |
| `<experiment>/run_<n>/app_<i>/` | The working directory of app `i` in run `n`. It holds the files the app wrote, and a hidden `.wrappers/` folder with the launch scripts. CRAB deletes a folder that holds nothing else. With `retain_files: false`, it deletes the whole `run_<n>/` of every successful run. |

CRAB does not keep the stdout of an app that exits 0, only the metrics parsed from it.

CRAB writes `data_app_<i>.csv` when the experiment ends, so an experiment that is still running has
none. If an error stops an experiment after at least one run finished, CRAB writes the runs it has.

### `data_app_<i>.csv`

`<i>` is the app's position in the experiment's `apps`, counted from 0 in key order. Each row is
one sample. The columns, in order:

1. `run_id`: the number of the run that produced the sample. It is the real run number, so a
   run that failed leaves a gap.
2. `msg_size`: the integer after `-msgsize` in the app's `args`, or `0` when there is none. It is
   the same on every row of the file.
3. One column for each of the wrapper's `keys`, if it declares any. Keys name the dimensions of a
   sweep, such as a message size printed once per size.
4. One column per metric, named `<i>_<metric>_<unit>`, from the wrapper's `metadata`.

`run_id` and `msg_size` stay the first two columns, and the metric columns keep this naming from
one release to the next.

A wrapper with no `keys` that declares five duration metrics gives this file for app 0. The numbers
come from the 1 KiB sample of the `alltoall_b` wrapper, with run 3 missing:

```
run_id,msg_size,0_Avg-Duration_s,0_Min-Duration_s,0_Max-Duration_s,0_Median-Duration_s,0_MainRank-Duration_s
1,1024,6.2232e-05,5.5686e-05,7.1978e-05,6.1958e-05,6.2605e-05
1,1024,6.297e-05,5.6632e-05,7.2165e-05,5.9424e-05,6.9811e-05
2,1024,5.4545e-05,4.7944e-05,6.3304e-05,5.3861e-05,5.5459e-05
2,1024,7.0876e-05,5.999e-05,9.1593e-05,6.8458e-05,6.4048e-05
4,1024,4.4845e-05,3.7731e-05,5.1879e-05,4.443e-05,4.6831e-05
4,1024,4.6131e-05,4.0037e-05,5.3085e-05,4.6485e-05,4.7364e-05
```

An app can give several samples per run, which share a `run_id`. A wrapper with `keys = ["size"]`
and one metric `latency` in `us` gets a key column before the metric:

```
run_id,msg_size,size,0_latency_us
1,0,1,1.52
1,0,1024,2.31
1,0,1048576,118.4
2,0,1,1.58
```

Rows are ordered by `run_id`, then by key value in order of first appearance, then by sample.
A metric declared with another `type` holds what the wrapper returned: an integer, text, or `True`
or `False`. A metric with `role: "check"` is a `True` or `False` column. CRAB keeps a row whose
check is `False`, counts that run as failed, and leaves the row out of convergence.

### `crab_run.json`

The worker writes this file into the job folder before the first experiment starts. It records
which code produced the data. The values below are an example:

```json
{
  "results_schema": 1,
  "crab_version": "0.5.1",
  "crab_commit": "d7ce8e1a...",
  "wrapper_api": 1,
  "started": "2026-06-09T14:30:05+02:00",
  "nodes": ["node001", "node002", "node003", "node004"],
  "wrappers": {"dir": "/path/to/wrappers", "commit": "3f9a1c2b...", "dirty": false},
  "apps": {
    "a2a_vs_g500": {
      "0": {"path": "/path/to/wrappers/blink/alltoall/alltoall_b.py", "sha256": "9b1e..."}
    }
  },
  "execution": {
    "scheduler": "slurm",
    "hosts_used": ["node001", "node002", "node003", "node004"],
    "launcher": "srun",
    "mpirun": {"command": "mpirun", "dialect": "auto", "version": null}
  },
  "launches": {
    "a2a_vs_g500": {
      "0": {"line": "srun ...", "kind": "srun", "dialect": null, "binding_report": "...", "ppn": 16, "env": {}, "nics": null}
    }
  },
  "allocations": {
    "a2a_vs_g500": {
      "mode": "random",
      "seed": 2918453017,
      "apps": {
        "0": ["node003", "node001"],
        "1": ["node004", "node002"]
      }
    }
  }
}
```

The fields:

- `crab_version`, `crab_commit` and `wrapper_api` name the CRAB that ran and the wrapper contract
  it implements. A commit or hash is `null` when CRAB could not read it, for example without git.
- `wrappers` is the wrappers checkout: its folder, its commit, and `dirty`, which is `true` when it
  had local edits.
- `apps` lists, for each experiment and app key, the wrapper file and the SHA-256 of its contents.
- `execution` records the scheduler, the hosts and the default launcher. With the `mpirun`
  launcher and dialect `auto`, it also records the dialect found and the first line of
  `mpirun --version`.
- `launches` records the first launch of each app: the launch line, the launcher `kind` (`srun`,
  `mpirun` or `direct`), ranks per node, the app's extra environment and its NICs. CRAB adds it
  when the app first starts, so it can be missing from a job that failed early.
- `allocations` records, for each experiment, how its nodes were laid out: the allocation `mode`
  and each app's nodes under `apps`, keyed by the same app keys as `launches`. A random layout
  also has `seed`, the seed given in the config or the one CRAB drew. With `partitions`, the
  entry has `"partitioned": true`, `mode` is the top-level mode, and `partition_seeds` holds the
  seed of each partition that shuffled. CRAB writes it when it sets the experiment up, once per
  experiment: every run of the experiment uses this layout. To repeat the layout, set the recorded
  `seed` in the config's `allocation` and run on the same node list. The run log prints the same
  information as `Allocation: random, seed N` and one `App <id>: <nodes>` line per app.

### The registry, `metadata.csv`

CRAB appends one row per experiment to `<datapath>/<system>/metadata.csv` when the experiment
ends. It creates the file, with its header, on the first row. CRAB takes a file lock first, so
jobs that run at the same time do not corrupt it.

```
job_name,experiment_name,timestamp,numnodes,ppn,apps_list,status,tags,relative_path,total_runs,failed_runs,seed
interference,a2a_vs_g500,2026-06-09_14-30-05,4,16,alltoall_b graph500,COMPLETED,demo,./interference_2026-06-09_14-30-05-123456/a2a_vs_g500,12,1,2918453017
```

`status` is `COMPLETED`, `FAILED` or `TIMEOUT`. `total_runs` counts the runs that started and
`failed_runs` those that did not succeed. `apps_list` holds the unique benchmark names, sorted.
`seed` is the seed of the experiment's random layout (the top-level one, see
[`allocations`](#crab_runjson)), and is empty when the layout was not random. A file written by an
older CRAB keeps its old header, and new rows are added under it, cut to its width. CRAB does not
migrate it, so a new row in such a file has no `seed` column; the seed is still in `crab_run.json`.

## How many runs the CSV holds

CRAB repeats an experiment until at least `minruns` runs have succeeded (default 10) and starts
at most `maxruns` runs (default 20, failed ones included). It also stops at `timeout`. Once
`minruns` runs have succeeded, it stops early when the confidence interval of
the mean of each metric flagged `conv` (or of every metric, with `convergeall`) is narrower than
`beta` times that mean (see [Convergence](../glossary.md#convergence)). With keys, each key value has to converge on its own.
The CSV does not say whether the experiment converged or ran out of runs. `total_runs` in the
registry says how many runs it took.
[Methodology](../concepts/methodology.md#how-many-runs-the-convergence-check) describes the
check and what it pools.

A run fails when an app exits non-zero, when a wrapper cannot parse an app's output, or when a
check is `False`. An app that exits non-zero or whose output cannot be parsed adds no rows to that
run, so its `run_id` is absent from that app's file. The other apps still add their rows.
`failed_runs` in the registry counts the failed runs.

## Read the files

Everything above is plain CSV and JSON. This reads the file from the layout above and the registry
of the system:

```python
import pandas as pd

job = "data/leonardo/interference_2026-06-09_14-30-05-123456"
df = pd.read_csv(f"{job}/a2a_vs_g500/data_app_0.csv")

per_run = df.groupby("run_id")["0_Avg-Duration_s"].mean()
print(per_run)
print(per_run.agg(["mean", "std"]))

registry = pd.read_csv("data/leonardo/metadata.csv")
print(registry.loc[registry["status"] == "COMPLETED", ["job_name", "experiment_name", "total_runs", "failed_runs"]])
```

On the files shown above it prints:

```
run_id
1    0.000063
2    0.000063
4    0.000045
Name: 0_Avg-Duration_s, dtype: float64
mean    0.000057
std     0.000010
Name: 0_Avg-Duration_s, dtype: float64
       job_name experiment_name  total_runs  failed_runs
0  interference     a2a_vs_g500          12            1
```

The snippet averages inside each run first and then across runs, because the samples of one run
are not independent measurements. For a sweep, group by the key column as well.

## Read from the command line

These commands run where the data is: on the cluster, or on your laptop for a local run.

`crab history [-s SYSTEM] [--json]` lists the experiments in the registries under the `data/`
folder of the CRAB checkout. It does not read a custom `datapath`. With `-s` it reads one system.

```
$ crab history
1 experiment(s):
  [COMPLETED] leonardo/interference/a2a_vs_g500  2026-06-09_14-30-05  apps=alltoall_b graph500
```

With `--json`, each experiment has the registry's columns plus `system` and `absolute_path`, the
experiment's folder.

`crab logs --data-dir DIR [--experiment NAME] [--json]` prints the end of a job's logs. `DIR` is
the job folder. By default it prints `slurm_output.log` and `slurm_error.log`, and says so when a
file is not written yet. With `--experiment NAME` it prints that experiment's `error_app_<i>.log`
files instead, or says that nothing failed. It shows the last 200,000 bytes of each file and marks
a cut.

`crab parse WRAPPER OUTPUT` runs a wrapper's parser on a file that holds an app's stdout. It uses
the checks a run uses, and prints the rows as CSV, or as JSON with `--json`. The options are
`--stderr FILE`, `--dir DIR`, `--args ARGS` and `--set KEY=VALUE`. Use it to see what a wrapper
makes of output you saved by hand, for example text you copied from the `STDOUT` section of an
`error_app_<i>.log`. See [Writing a wrapper](../extending/wrappers.md) and the [CLI
reference](../reference/cli.md#crab-parse).

## Read in the web dashboard

The Results page of the [web dashboard](dashboard.md) lists every job a connected cluster's
`crab history` reports, fetches a job's folder to your laptop when you ask, and shows an app's data
as a chart or a table. The `Compare` tab puts apps from different jobs, on different clusters, on
one chart. See [Read results](dashboard.md#read-results) for the controls.

The dashboard charts the `data_app_<i>.csv` files only. It fetches the whole job folder, so
`crab_run.json`, the logs and the artifacts are in the fetched copy under
`~/.local/share/crab/results_cache/` (see [Where the dashboard keeps its
files](dashboard.md#where-the-dashboard-keeps-its-files)).

## Share results with `crab export`

`crab export` turns a results folder into one HTML file. The file holds the CSV data and a viewer
with charts (scatter, line, bar and violin), column and series toggles, a `Compare` tab, and dark
and light themes. It needs no server, no CRAB and no access to your cluster. Whoever has the file
opens it in a browser.

```bash
crab export data/leonardo/interference_2026-06-09_14-30-05-123456/ -o results.html
```

- `data_dir` is a job folder, or a folder above it. CRAB reads every CSV that is not
  `metadata.csv` or `description.csv`, in the folder, in its subfolders and in the subfolders of
  those.
- It names each app after the wrapper file in `config.json` (the file name without `.py`), and
  `App 0`, `App 1` and so on when there is no `config.json`.
- `-o` sets the output path. The default is `crab_export.html` in the current directory.
- It exits with an error when it finds no CSV data.

The file holds the data and nothing else: not `crab_run.json`, the logs or the artifacts. Do not
commit it to git. See [`crab export`](../reference/cli.md#crab-export) for the options.

Use the dashboard when you work with your own clusters: it fetches fresh results and compares jobs
across clusters. Use `crab export` to give results to someone who has neither, to attach them to a
message, or to keep a snapshot that opens without CRAB. The exported viewer is separate from the
dashboard and has its own controls. You can also open `src/crab/crab_dashboard.html` from the
checkout and drop a results folder on it.
