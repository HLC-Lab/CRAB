# Reading results

A run produces a self-contained, timestamped directory of provenance and data. This page covers
what's in it, the CSV format, and the dashboard for visualizing results.

## Output directory layout

Results are written under `data/<CRAB_SYSTEM>/<name>_<timestamp>/`, where `<CRAB_SYSTEM>` comes
from the preset and `<name>` from `global_options.name`:

```
data/leonardo/interference_2026-06-09_14-30-05-123456/
├── config.json              # the exact config that ran (reproducibility)
├── environment.json         # the resolved environment
├── crab_job.sh              # the generated Slurm batch script
├── slurm_output.log         # job stdout
├── slurm_error.log          # job stderr
└── <experiment_name>/
    ├── data_app_0.csv        # collected metrics, one file per collected app
    ├── data_app_1.csv
    ├── error_app_<id>.log    # written only if an app failed
    └── run_<n>/.wrappers/    # per-run launch scripts (if retained)
```

A system-level `data/<CRAB_SYSTEM>/metadata.csv` **registry** is also appended to — one row per
experiment (job name, timestamp, node count, ppn, app list, status, tags, relative path) — so you
can index every run on a system from a single file. It's written with file locking, safe under
concurrent CRAB jobs. See [Architecture → Output layout](../concepts/architecture.md#output-layout).

!!! tip "`config.json` makes a run reproducible"
    Because the resolved config and environment are saved alongside the data, an output directory
    is a complete record of what ran. Keep it (or just `config.json`) to re-run the same study.

## The CSV format

Each collected application produces `data_app_<id>.csv`. Columns:

- `run_id` — which repetition the sample came from.
- `msg_size` — message size parsed from the app's `args` (0 if not applicable).
- one column per metric, named `<app_id>_<metric_name>_<unit>` (from the wrapper's `metadata`).

A wrapper that declares metrics `performance` (GTEPS) and `time` (s) for app `0` yields columns
like `0_performance_GTEPS` and `0_time_s`. Multiple samples per run are stacked as separate rows
sharing a `run_id`.

Load it straight into pandas:

```python
import pandas as pd
df = pd.read_csv("data/leonardo/interference_.../a2a_vs_g500/data_app_0.csv")
df.groupby("run_id")["0_performance_GTEPS"].mean()
```

(Set `outformat: "hdf"` in `global_options` to get `.h5` files instead of CSV.)

## The dashboard

`crab_dashboard.html` (at the repo root) is a standalone, single-file viewer — no build step, just
open it in a browser. It loads CRAB CSV files two ways:

=== "From a server on the cluster"

    On the cluster, start a simple HTTP server in the directory **above** your results and forward
    the port over SSH:

    ```bash
    # on the cluster, in the parent of your data/ tree
    python3 -m http.server 8000
    ```
    ```bash
    # on your laptop
    ssh -L 8000:localhost:8000 user@cluster
    ```

    Then open `crab_dashboard.html`, enter the forwarded URL (e.g. `http://localhost:8000/`), and
    click **Go**. The dashboard crawls the directory listing for `data_app_*.csv` files across
    systems and experiments.

=== "From a local folder"

    Drag a downloaded experiment folder onto the dashboard's drop zone (or click to browse). It
    reads the `.csv` files directly in your browser — nothing is uploaded anywhere.

Once loaded, the dashboard groups results by system and experiment, lets you toggle which columns
and series are shown, choose axes, and recolor series for comparison plots — handy for eyeballing
victim-vs-aggressor differences before deeper analysis in pandas or R.
