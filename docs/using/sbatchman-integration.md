# Using CRAB with SbatchMan

CRAB and [SbatchMan](https://github.com/LorenzoPichetti/SbatchMan) are independent, standalone
tools with a fixed division of labor: CRAB downloads, builds, and co-runs benchmarks and parses
their output; SbatchMan owns scheduler configuration, job submission, monitoring, and results
visualization, end to end, through its own CLI/TUI. **CRAB's dashboard does not launch, monitor,
or visualize a SbatchMan campaign**: its only role in this flow is authoring a campaign and
generating the SbatchMan jobs file. See
[the roadmap's "Relationship with SbatchMan" section](../dev/roadmap.md#relationship-with-sbatchman)
for the full picture.

This page walks through the whole flow once, from a clean cluster account to results on disk,
with CRAB's `blink` benchmark suite as the worked example throughout. A
[troubleshooting](#troubleshooting) section at the end lists the failures people actually hit
and how to fix each one.

## What goes where

| Machine | What runs there | Installed from |
|---|---|---|
| Your laptop | CRAB's web dashboard (`crab web`), where you author campaigns | CRAB, `sbatchman` branch, `web` extra |
| The cluster | CRAB (benchmarks, recipes, the `crab worker` that runs inside each job) and SbatchMan (presets, `launch`, `status`), side by side in **one** virtual environment | CRAB `sbatchman` branch + SbatchMan GitHub `main` |

Every job SbatchMan submits runs `crab worker` inside the allocation it obtained. That is why
both tools live in the same environment on the cluster, and why the job itself must be able to
find that environment (see [step 5](#5-create-a-sbatchman-preset-for-crab)).

## Prerequisites

- A laptop with Python 3.10+ and git (any OS; no Slurm, no compilers needed here).
- A cluster account with Slurm, Python 3.10+ and git on the login node, and SSH access (key or
  agent auth; see the dashboard's Remotes page for the supported methods).
- Your Slurm partition and account names.

## 1. Install the dashboard on your laptop

```bash
git clone --branch sbatchman https://github.com/HLC-Lab/CRAB
cd CRAB
python -m venv .venv
source .venv/bin/activate
pip install -e ".[web]"
crab web
```

This opens the dashboard at `http://127.0.0.1:8765`.

## 2. Install CRAB and SbatchMan on the cluster

Pick an install directory on the cluster (your home directory is fine). CRAB goes into
`<install dir>/CRAB`, with its virtual environment at `<install dir>/CRAB/.venv`; SbatchMan is
installed into that same environment.

**Option A, from the dashboard.** Open **Remotes**, add the cluster (name, transport `ssh`,
host, user, auth) and set **Install dir**. Click **Connect**. When CRAB is not there yet, the
page offers **Install CRAB**: it clones the `sbatchman` branch into `<install dir>/CRAB` and
installs it into `<install dir>/CRAB/.venv`, running anything you put in **Pre-commands** first
(for example `module load python/3.11`). Then add SbatchMan over SSH:

```bash
source <install dir>/CRAB/.venv/bin/activate
pip install git+https://github.com/LorenzoPichetti/SbatchMan.git
```

**Option B, by hand**, over SSH:

```bash
cd <install dir>
git clone --branch sbatchman https://github.com/HLC-Lab/CRAB
cd CRAB
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install git+https://github.com/LorenzoPichetti/SbatchMan.git
```

If your site needs a module for a recent Python, load it before creating the environment
(for example `module load python/3.11`).

Install SbatchMan **from GitHub `main`, not from PyPI**. As of this writing, PyPI's release
(`1.0.8`) predates two fixes on `main` that this flow depends on: SbatchMan exporting
`SBATCHMAN_JOB_DIR` into each job, and its jobs-file `configs:` key actually being read. This
page was verified against `main` at commit `18701e426d627d6bb7c5b8e4ef9fced533868345`; `main`
has no version pin, so if something here stops matching what you see, check that first.

Check both tools from the activated environment:

```bash
crab info          # prints the CRAB version, its root directory, and the known presets
sbatchman --version
```

Note the root directory `crab info` prints: it is the `CRAB_ROOT` you will need in step 5.

## 3. Build the benchmarks

Still on the cluster, in the activated environment:

```bash
crab setup
```

and select the Blink suite (see
[Set up benchmarks](installation.md#set-up-benchmarks-crab-setup) for the wizard walkthrough).
Build every benchmark a campaign will use before launching it.

## 4. Create a SbatchMan project

SbatchMan keeps its presets and job records in a project folder. Create one once, in the
directory you will launch campaigns from (a directory next to CRAB works well), and tell
SbatchMan the cluster's name:

```bash
mkdir -p <install dir>/campaigns && cd <install dir>/campaigns
sbatchman init
sbatchman set-cluster-name your_cluster_name
```

`sbatchman init` creates a `SbatchMan/` folder there. Every later `sbatchman` command looks for
that folder in the current directory and its parents (then in your home directory), so run
`configure` and `launch` from inside this directory.

## 5. Create a SbatchMan preset for CRAB

A SbatchMan preset (a "configuration") holds the Slurm settings and the environment every job
gets. CRAB's worker takes its environment from here: it does not write its own environment file
when run under SbatchMan (see
[ADR-027](../dev/dashboard/decisions/adr-027-sbatchman-seam-v2.md) for the reasoning). From the
project directory:

```bash
sbatchman configure slurm \
  --name crab_run \
  --partition your_partition \
  --account your_account \
  --env 'PATH=<install dir>/CRAB/.venv/bin:$PATH' \
  --env 'CRAB_ROOT=<install dir>/CRAB' \
  --env 'CRAB_SYSTEM=your_cluster_name' \
  --env 'CRAB_PATH_WRAPPERS=<install dir>/CRAB/wrappers' \
  --env 'SBATCHMAN_JOB_DIR={EXP_DIR}'
```

Use absolute paths, and keep the single quotes: SbatchMan writes each `--env` entry into the job
script as a plain `export` line, so `$PATH` is expanded when the job runs, on the compute node,
not now on the login node.

- `PATH` puts `crab` on the job's path. Without it, the job only finds `crab` if you launched
  from an activated shell and your site forwards the submitting environment to jobs.
- `CRAB_ROOT`, `CRAB_SYSTEM` and `CRAB_PATH_WRAPPERS` are what the worker needs to find its
  install, name the system, and resolve wrapper paths like `blink/a2a_b.py`.
- `SBATCHMAN_JOB_DIR={EXP_DIR}` points the worker at the job's own directory. Recent SbatchMan
  `main` already exports it; keeping the line makes the preset work with builds that don't.

`{EXP_DIR}` is SbatchMan's per-job-directory token. It is substituted inside `configure`-time
fields like `--env`, **never** inside a campaign's own `command` or `preprocess` text: a token
like `{EXP_DIR}` written directly in a hand-made jobs file's `command:` survives as a literal
string. This is SbatchMan behavior, not specific to CRAB.

See SbatchMan's own documentation for every other `configure slurm` option (`--time`,
`--nodes`, `--gpus`, `--qos`, ...). To change a preset later, re-run the command with
`--overwrite`.

## 6. Author a campaign

In the dashboard, open **SbatchMan** and build your campaign: give the group a tag, set its
**SbatchMan preset** to the name you configured above (`crab_run` in the example), and add an
app the same way you would in a normal CRAB experiment: for `blink`, pick a wrapper such as
`blink/a2a_b.py` from the picker and set its args. Sweep values go in variables and are used as
`{name}` placeholders, including in numeric fields such as the node count or an allocation's
stride.

The preview panel lists anything that would make the campaign fail at launch (a variable without
a name, a `{placeholder}` no variable defines, a group without a preset, an invalid experiment).
**Write files** stays disabled until that list is empty.

Leave the **SbatchMan configs.yaml** field blank unless you specifically want SbatchMan's
jobs-file `configs:` key to auto-provision configurations from a bulk config-definition file (the
same format `sbatchman configure -f <file>` accepts). Pointing it at your project's own
`SbatchMan/configs/configurations.yaml` registry (the file `sbatchman configure` itself writes
to) does not work: SbatchMan reads a non-empty `configs:` value as a *definition* file to
re-provision from, not a reference to the registry, and the registry's shape does not match, so
it errors out. An empty field (the default) skips that step and resolves your preset by name.

Choose the connected cluster and click **Write files**. This saves the jobs YAML on your laptop
and copies it to the cluster's staging directory; the page shows both paths. Each job's
`preprocess` writes only `config.json` (your experiment) into the job's directory.

## 7. Launch it

This is your step, not a dashboard button: CRAB never runs `sbatchman launch` itself. On the
cluster, from the project directory, with the path the Write step showed you:

```bash
cd <install dir>/campaigns
sbatchman launch -f /path/shown/by/the/dashboard/campaign.yaml --dry-run   # list the jobs only
sbatchman launch -f /path/shown/by/the/dashboard/campaign.yaml
```

## 8. Monitor and visualize

Use SbatchMan's own tooling from here: `sbatchman status`, `sbatchman campaign-tui`, or
`sbatchman visualize`. CRAB's dashboard has no view into a SbatchMan campaign's job status or
results.

!!! warning "Port collision"
    Both `crab web` and `sbatchman visualize` default to port `8765`. To run both at once, start
    one of them on a different port (`crab web --port <other port>`, or check
    `sbatchman visualize --help` for its own port option).

## 9. Where results land

Each job's `config.json` names an experiment (its key under `experiments` in the campaign).
CRAB writes that experiment's parsed results to `<job directory>/<experiment name>/`. A run that
collects metrics produces `data_app_<index>.csv` there (one file per app in the experiment): for
example, a group whose experiment is named `run` with a single app produces
`<job directory>/run/data_app_0.csv`. The job directory is the one SbatchMan assigned
(`SBATCHMAN_JOB_DIR`/`{EXP_DIR}`), shown in SbatchMan's own job listing; the job's
`stdout.log` and `stderr.log` are in the same directory.

Not every app produces this file: a benchmark that collects no metrics (`blink`'s `null_dummy`,
for instance, an isolation sanity check with nothing to measure) never writes a CSV, by design.

Reading that CSV is up to you: a spreadsheet, a notebook, or `sbatchman visualize`'s own parser
script (its `--parser` option, `./parser.py` by default). CRAB's dashboard does not read or plot
it.

## Troubleshooting

Start with the job's `stderr.log` and `stdout.log`, in its job directory.

| Symptom | Cause | Fix |
|---|---|---|
| `crab: command not found` in a job's `stderr.log` | The job's shell cannot see the CRAB environment | Add `--env 'PATH=<install dir>/CRAB/.venv/bin:$PATH'` to the preset ([step 5](#5-create-a-sbatchman-preset-for-crab)), re-run `configure` with `--overwrite`, relaunch |
| `SbatchMan root not found. Please run 'sbatchman init'`, or a prompt offering to create a project | The command ran outside the project directory | `cd` into the directory where you ran `sbatchman init` ([step 4](#4-create-a-sbatchman-project)) |
| `Cluster name not set. Please run 'sbatchman set-cluster-name'.` | No cluster name in SbatchMan's global settings | `sbatchman set-cluster-name your_cluster_name` |
| `Wrapper not found: blink/a2a_b.py` (a relative path) in the job log | `CRAB_PATH_WRAPPERS` is not set in the job | Add it to the preset ([step 5](#5-create-a-sbatchman-preset-for-crab)) |
| `Wrapper not found: <absolute path>` | The wrapper file is not in that CRAB checkout | Check the path, and that the cluster's CRAB is on the `sbatchman` branch and up to date (`git -C <install dir>/CRAB pull`) |
| The job starts but the benchmark never runs, or fails with an empty launch command | The benchmark was not built on this cluster; CRAB does not yet report a missing build clearly | Run `crab setup` for it ([step 3](#3-build-the-benchmarks)) |
| `sbatchman launch` fails with a `TypeError` | The campaign's **SbatchMan configs.yaml** field points at the project's configuration registry | Clear that field in the dashboard, write the campaign again ([step 6](#6-author-a-campaign)) |
| `Worker fatal error: [Errno 2] No such file or directory: 'config.json'` in the job log | `SBATCHMAN_JOB_DIR` is empty in the job: SbatchMan is the PyPI release, or the preset lacks the entry | Install SbatchMan from GitHub `main` ([step 2](#2-install-crab-and-sbatchman-on-the-cluster)) and keep the `SBATCHMAN_JOB_DIR={EXP_DIR}` entry |
| **Write files** is greyed out | The preview lists problems with the campaign, or no cluster is chosen | Fix each listed item; pick a connected cluster |
| "SSH support isn't installed in this environment" in the dashboard | The laptop install lacks the `web` extra | `pip install -e ".[web]"` from your CRAB checkout, then restart `crab web` |
| The dashboard or `sbatchman visualize` will not start: port in use | Both default to `8765` | Start one on another port (see [step 8](#8-monitor-and-visualize)) |
