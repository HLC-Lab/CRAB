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
| The cluster | CRAB (benchmarks, recipes, the `crab worker` that runs inside each job) and SbatchMan (presets, `launch`, `status`), side by side in **one** virtual environment | CRAB `sbatchman` branch + SbatchMan from GitHub, at a pinned commit |

Every job SbatchMan submits runs `crab worker` inside the allocation it obtained. That is why
both tools live in the same environment on the cluster, and why the job itself must be able to
find that environment (see [step 5](#5-create-a-sbatchman-preset-for-crab)).

## Prerequisites

- A laptop with Python 3.10+ and git (any OS; no Slurm, no compilers needed here).
- A cluster account with Slurm, Python 3.10+ and git on the login node, and SSH access (key or
  agent auth; see the dashboard's Remotes page for the supported methods).
- Your Slurm partition and account names, and a QoS name if your site requires one.

## 1. Install the dashboard on your laptop

```bash
git clone --branch sbatchman https://github.com/HLC-Lab/CRAB
cd CRAB
python -m venv .venv
source .venv/bin/activate
pip install -e ".[web]"
crab web
```

This opens the dashboard at `http://127.0.0.1:8765`. The `web` extra is what the dashboard
needs: a plain `make` install, as on the cluster below, lacks it.

## 2. Install CRAB and SbatchMan on the cluster

Pick an install directory on the cluster (your home directory is fine). CRAB goes into
`<install dir>/CRAB`, with its virtual environment at `<install dir>/CRAB/.venv`; SbatchMan is
installed into that same environment.

**Option A, from the dashboard.** Open **Remotes**, add the cluster (name, transport `ssh`,
host, user, auth) and set **Install dir**. Click **Connect**. When CRAB is not there yet, the
page offers **Install CRAB**: it clones the `sbatchman` branch into `<install dir>/CRAB`,
installs it into `<install dir>/CRAB/.venv`, and switches to the newest `+sbatchman` release once
one exists, running anything you put in **Pre-commands** first (for example
`module load python/3.11`). Then add SbatchMan over SSH:

```bash
source <install dir>/CRAB/.venv/bin/activate
pip install "git+https://github.com/LorenzoPichetti/SbatchMan.git@f4537e6312cb064d44a060d7c53901a91735b11a"
```

**Option B, by hand**, over SSH:

```bash
cd <install dir>
git clone --branch sbatchman https://github.com/HLC-Lab/CRAB
cd CRAB
make
source .venv/bin/activate
pip install "git+https://github.com/LorenzoPichetti/SbatchMan.git@f4537e6312cb064d44a060d7c53901a91735b11a"
```

`make` creates `.venv`, installs CRAB into it, clones the shared wrappers into `wrappers/`, and
then opens the `crab setup` wizard. You can build the benchmarks there right away (that is
[step 3](#3-build-or-register-the-benchmarks)), or exit the wizard and come back later with
`crab setup`.

`make` needs `python3` 3.10 or newer. If your site's default is older, load a module for a
recent Python before running it (for example `module load python/3.11`).

Install SbatchMan **from GitHub at this commit**, not from PyPI. PyPI's release (`1.0.8`)
predates a fix this flow depends on: SbatchMan exporting `SBATCHMAN_JOB_DIR` into each job. This
page was verified against commit `f4537e6312cb064d44a060d7c53901a91735b11a` (SbatchMan `2.0.3`;
`sbatchman --version` prints it). If something here stops matching, check whether `main` has moved.

Check both tools from the activated environment:

```bash
crab info          # prints the CRAB version, its root directory, and the known presets
sbatchman --version
```

Note the root directory `crab info` prints: it is the `CRAB_ROOT` you will need in step 5.

## 3. Build or register the benchmarks

Every application a campaign uses must be findable on the cluster. CRAB looks for each wrapper's
binary in this order: the app's `binary` key in the campaign's config, then a receipt, then the
wrapper's `executable` on `PATH` in the job (see
[Finding the binary](../extending/wrappers.md#finding-the-binary)).

- **Build it with CRAB**, for the applications CRAB has a recipe for (the Blink suite, for
  example): in the activated environment run `crab setup` and pick it (see
  [Set up benchmarks](installation.md#set-up-benchmarks-crab-setup)).
- **Already installed through a module?** Add the `module load` to your SbatchMan preset (step 5).
  If the wrapper declares an `executable`, CRAB then finds it on `PATH` with no receipt.
- **Installed somewhere else?** Record it once: `crab receipts set <benchmark_id> --binary
  /path/to/program`, or use **Import binary** on the dashboard's **Wrappers** page.

`crab wrappers list` (or the **Wrappers** page) shows, for every wrapper, where its binary comes
from or that it is missing. A campaign whose binary is missing fails before its first run, with a
message saying what was tried.

## 4. Create a SbatchMan project

SbatchMan keeps its presets and job records in a project folder. Create one once, in the
directory you will launch campaigns from (a directory next to CRAB works well), and tell
SbatchMan the cluster's name:

```bash
mkdir -p <install dir>/campaigns && cd <install dir>/campaigns
sbatchman init
sbatchman set-cluster-name <cluster name>
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
  --partition <partition> \
  --account <account> \
  --qos <qos> \
  --time <hh:mm:ss> \
  --nodes <nodes> \
  --tasks-per-node <tasks per node> \
  --exclusive \
  --env 'PATH=<install dir>/CRAB/.venv/bin:$PATH' \
  --env 'CRAB_ROOT=<install dir>/CRAB' \
  --env 'CRAB_SYSTEM=<cluster name>' \
  --env 'CRAB_PATH_WRAPPERS=<install dir>/CRAB/wrappers' \
  --env 'SBATCHMAN_JOB_DIR={EXP_DIR}'
```

Replace each `<placeholder>` with your value. Leave the `--qos` line out unless your site
requires a QoS for the partition. SbatchMan turns each option into an `#SBATCH` line of the job
script. Each line of the command does this:

| Option | What it sets | When and why |
|---|---|---|
| `--name crab_run` | The preset's name. | A campaign group refers to the preset by this name ([step 6](#6-author-a-campaign)). |
| `--partition <partition>` | `#SBATCH --partition`: the queue the jobs go to. | Most sites require one. Use a partition your account may submit to. |
| `--account <account>` | `#SBATCH --account`: the project the hours are charged to. | Required where the site accounts for usage. |
| `--qos <qos>` | `#SBATCH --qos`: the quality of service. | Only where the site requires one for the partition (some sites use it to select short debug queues). Otherwise leave the line out. |
| `--time <hh:mm:ss>` | `#SBATCH --time`: the walltime limit of each job. | Slurm kills a job that runs past it, and many sites require it or default to a short limit. Set it above your longest job. |
| `--nodes <nodes>` | `#SBATCH --nodes`: how many nodes each job gets. | Match the experiment's `numnodes` (see [Sizing the allocation for co-runs](#sizing-the-allocation-for-co-runs)). |
| `--tasks-per-node <tasks per node>` | `#SBATCH --ntasks-per-node`: how many ranks each node can run. | The allocation must hold the ranks of all apps at once. For a co-run, size it as in [Sizing the allocation for co-runs](#sizing-the-allocation-for-co-runs). |
| `--exclusive` | `#SBATCH --exclusive`: the job gets its nodes to itself. | Without it, the jobs of one campaign can run at the same time on the same nodes and skew each other's network timings. Drop it only if the site forbids it, or if you measure nothing network-sensitive. |
| `--env 'PATH=<install dir>/CRAB/.venv/bin:$PATH'` | Puts `crab` on the job's path. | Without it, the job only finds `crab` if you launched from an activated shell and your site forwards the submitting environment to jobs. |
| `--env 'CRAB_ROOT=<install dir>/CRAB'` | Tells the worker where its install is. | Use the absolute path of the CRAB checkout: the root directory `crab info` printed in [step 2](#2-install-crab-and-sbatchman-on-the-cluster). |
| `--env 'CRAB_SYSTEM=<cluster name>'` | Names the system the job runs on. | Use the cluster name from [step 4](#4-create-a-sbatchman-project). |
| `--env 'CRAB_PATH_WRAPPERS=<install dir>/CRAB/wrappers'` | Where the worker resolves wrapper paths. | Lets it resolve paths like `blink/alltoall/alltoall_b.py`. |
| `--env 'SBATCHMAN_JOB_DIR={EXP_DIR}'` | Points the worker at the job's own directory. | SbatchMan at the pinned commit already exports it. Keeping the line makes the preset work with builds that don't. |

Use absolute paths, and keep the single quotes: SbatchMan writes each `--env` entry into the job
script as a plain `export` line, so `$PATH` is expanded when the job runs, on the compute node,
not now on the login node.

For GPU benchmarks, add `--gpus <n>`: SbatchMan writes it as `#SBATCH --gres=gpu:<n>`.

`{EXP_DIR}` is SbatchMan's per-job-directory token. It is substituted inside `configure`-time
fields like `--env`, **never** inside a campaign's own `command` or `preprocess` text: a token
like `{EXP_DIR}` written directly in a hand-made jobs file's `command:` survives as a literal
string. This is SbatchMan behavior, not specific to CRAB.

See SbatchMan's own documentation for every other `configure slurm` option (`--mem`,
`--constraint`, `--reservation`, ...). To change a preset later, re-run the command with
`--overwrite`.

### Sizing the allocation for co-runs

When an experiment co-runs apps on the same nodes (`allocation.mode: "shared"`), the Slurm
allocation has to hold all of their ranks at once. CRAB sizes Slurm's `--ntasks-per-node` itself in
the batch scripts it writes, but SbatchMan writes its own from the preset, so here you size it by
hand: set `--tasks-per-node` in the preset (SbatchMan writes it as Slurm's `--ntasks-per-node`) to
the largest number of ranks any node carries, and `--nodes` to the experiment's `numnodes`. On
each node, apps that share it add up; apps that start after another one on its nodes
(`start: "sN"`) count once, at the largest `ppn` of the chain.
[Ranks per node](../reference/configuration.md#ranks-per-node-ppn) gives the rule CRAB itself
applies.

For example, two apps on the same 2 nodes, with `ppn` 4 each, need 8 tasks per node:

```bash
sbatchman configure slurm \
  --name crab_corun_2n \
  --partition <partition> \
  --account <account> \
  --qos <qos> \
  --time <hh:mm:ss> \
  --nodes 2 \
  --tasks-per-node 8 \
  --exclusive \
  --env 'PATH=<install dir>/CRAB/.venv/bin:$PATH' \
  --env 'CRAB_ROOT=<install dir>/CRAB' \
  --env 'CRAB_SYSTEM=<cluster name>' \
  --env 'CRAB_PATH_WRAPPERS=<install dir>/CRAB/wrappers' \
  --env 'SBATCHMAN_JOB_DIR={EXP_DIR}'
```

This is [step 5](#5-create-a-sbatchman-preset-for-crab)'s command with three lines changed:

- `--name crab_corun_2n` names a preset for this node count, so another count gets its own.
- `--nodes 2` is the experiment's `numnodes`.
- `--tasks-per-node 8` is two apps times a `ppn` of 4 on each node.

A group that sweeps the node count needs one preset per value (for example `crab_corun_2n` and
`crab_corun_4n`), picked through a `{nodes}`-style placeholder in the group's **SbatchMan preset**.

CRAB does not check this value for a SbatchMan campaign. When it is smaller than the largest
per-node load, the allocation cannot hold every app's ranks at the same time, so the co-run does
not happen as configured: what follows is Slurm's behavior (a step is refused, or waits for
free ranks), not a CRAB error message.

## 6. Author a campaign

In the dashboard, open **SbatchMan** and build your campaign: give the group a tag, set its
**SbatchMan preset** to the name you configured above (`crab_run` in the example), and add an
app the same way you would in a normal CRAB experiment: for `blink`, pick a wrapper such as
`blink/alltoall/alltoall_b.py` from the picker and set its args. Sweep values go in variables and are used as
`{name}` placeholders, including in numeric fields such as the node count or an allocation's
stride.

The preview panel lists anything that would make the campaign fail at launch (a variable without
a name, a `{placeholder}` no variable defines, a group without a preset, an invalid experiment).
**Write files** stays disabled until that list is empty.

The dashboard does not write SbatchMan's jobs-file `configs:` key: each group's **SbatchMan
preset** is resolved by name from the presets you created with `sbatchman configure`.

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
    Both `crab web` and `sbatchman visualize` use port `8765`, and `sbatchman visualize` has no
    option to change it. To run both at once, start the dashboard on a different port with
    `crab web --port <other port>`.

## Writing a wrapper for your own application

A wrapper tells CRAB how to launch an application and how to parse its output. To add one:

```bash
crab wrappers new myapp --local    # in <install dir>/CRAB/local/wrappers/, private to you
crab wrappers test myapp --strict  # the generated example passes
```

Edit `myapp/myapp.py`, replace the example in `myapp/samples/` with real output from your
application and the values it must produce, and run `crab wrappers test myapp` again. Reference
it in a campaign as `myapp/myapp.py`: CRAB looks in `local/wrappers/` before the shared wrappers.
[Writing a wrapper](../extending/wrappers.md) describes the contract; when it works, share it
with everyone who runs the same application by opening a pull request on the shared wrappers
repository.

## Keeping CRAB up to date

Run `crab update` in the activated environment on the cluster, and in your laptop's CRAB checkout.
It moves to the newest version of the `sbatchman` line, reinstalls dependencies only when they
changed, and refuses to run over local edits to tracked files (keep yours in `local/`). When the
laptop and a cluster run versions that no longer understand each other, the dashboard's
**Remotes** page says which side to update.

## 9. Where results land

Each job's `config.json` names an experiment (its key under `experiments` in the campaign).
CRAB writes that experiment's parsed results to `<job directory>/<experiment name>/`. A run that
collects metrics produces `data_app_<index>.csv` there (one file per app in the experiment): for
example, a group whose experiment is named `run` with a single app produces
`<job directory>/run/data_app_0.csv`. The job directory is the one SbatchMan assigned
(`SBATCHMAN_JOB_DIR`/`{EXP_DIR}`), shown in SbatchMan's own job listing; the job's
`stdout.log` and `stderr.log` are in the same directory.

Next to the experiment folders, `crab_run.json` records which CRAB version and which wrapper
files (with their hashes) produced the job. Inside an experiment, each run's working files are in
`run_<n>/app_<index>/`, and files a wrapper asks to keep are copied to `artifacts/run_<n>/`.

Not every app produces this file: a benchmark that collects no metrics (`blink`'s `null_dummy`,
for instance, an isolation sanity check with nothing to measure) never writes a CSV, by design.

Reading that CSV is up to you: a spreadsheet, a notebook, or `sbatchman visualize`'s own parser
script (its `--parser` option, `./parser.py` by default). CRAB's dashboard does not read or plot
it. To parse a saved output again with a wrapper, use `crab parse <wrapper> <output file>`.

## CPU pinning and launch settings

A CRAB worker started by SbatchMan has no CRAB execution preset: the job directory holds no
`execution.json` and the job sets no `CRAB_PRESET`, so the worker uses the Slurm scheduler and the
`srun` launcher with no extra flags. Its log says so:
`Execution settings from defaults (Slurm with srun; no execution.json, no CRAB_PRESET)`.

To pin processes to CPUs, set Slurm's own input variable in the SbatchMan preset's environment
(the job script's environment), next to the other `--env` entries of
[step 5](#5-create-a-sbatchman-preset-for-crab):

```bash
sbatchman configure slurm \
  --name crab_run_pinned \
  --partition <partition> \
  --account <account> \
  --qos <qos> \
  --time <hh:mm:ss> \
  --nodes <nodes> \
  --tasks-per-node <tasks per node> \
  --exclusive \
  --env 'PATH=<install dir>/CRAB/.venv/bin:$PATH' \
  --env 'CRAB_ROOT=<install dir>/CRAB' \
  --env 'CRAB_SYSTEM=<cluster name>' \
  --env 'CRAB_PATH_WRAPPERS=<install dir>/CRAB/wrappers' \
  --env 'SBATCHMAN_JOB_DIR={EXP_DIR}' \
  --env 'SLURM_CPU_BIND=cores'
```

This is step 5's command under another name, plus the `SLURM_CPU_BIND` line, which makes every
`srun` in the job bind its tasks to cores.

`SLURM_CPU_BIND` is the input variable Slurm documents for `srun`'s `--cpu-bind` option, so every
`srun` the worker starts binds as if `--cpu-bind=cores` had been given. Other values work the same
way, for example `SLURM_CPU_BIND=sockets`; see `man srun` for the full list. No CRAB code is
involved: the worker runs `srun --export=ALL`, which passes the job's environment on to each
launched process.

The `CRAB_*` launch variables of older CRAB versions are no longer read: `CRAB_WL_MANAGER`,
`CRAB_SCHEDULER`, `CRAB_MPIRUN`, `CRAB_MPIRUN_ADDITIONAL_FLAGS`, `CRAB_MPIRUN_MAP_BY_NODE_FLAG`,
`CRAB_MPIRUN_HOSTNAMES_FLAG` and `CRAB_PINNING_FLAGS`. A worker that finds one in its environment
stops with an error that names the replacement, for example
`worker environment: CRAB_PINNING_FLAGS is no longer read; use launchers.srun.flags`. Remove them
from your SbatchMan presets and, when you want pinning, use `SLURM_CPU_BIND` as above.

## Troubleshooting

Start with the job's `stderr.log` and `stdout.log`, in its job directory.

| Symptom | Cause | Fix |
|---|---|---|
| `crab: command not found` in a job's `stderr.log` | The job's shell cannot see the CRAB environment | Add `--env 'PATH=<install dir>/CRAB/.venv/bin:$PATH'` to the preset ([step 5](#5-create-a-sbatchman-preset-for-crab)), re-run `configure` with `--overwrite`, relaunch |
| `SbatchMan root not found. Please run 'sbatchman init'`, or a prompt offering to create a project | The command ran outside the project directory | `cd` into the directory where you ran `sbatchman init` ([step 4](#4-create-a-sbatchman-project)) |
| `Cluster name not set. Please run 'sbatchman set-cluster-name'.` | No cluster name in SbatchMan's global settings | `sbatchman set-cluster-name <cluster name>` |
| `Wrapper not found: blink/alltoall/alltoall_b.py` (a relative path) in the job log | `CRAB_PATH_WRAPPERS` is not set in the job | Add it to the preset ([step 5](#5-create-a-sbatchman-preset-for-crab)) |
| `Wrapper not found: blink/alltoall/alltoall_b.py`, and `<install dir>/CRAB/wrappers/` is empty or missing | CRAB was installed with `pip install -e .` alone, which does not fetch the shared wrappers | Run `make` (or `crab update`) in the CRAB checkout |
| `Wrapper not found: <absolute path>` | The wrapper file is not in that CRAB checkout | Check the path, and that the cluster's CRAB is on the `sbatchman` branch and up to date (`crab update`) |
| `No binary for ... Tried: ...` in the job log | No config `binary`, receipt, or program on `PATH` for that wrapper | Build it, load its module in the preset, or record it with `crab receipts set` ([step 3](#3-build-or-register-the-benchmarks)) |
| `PARSE FAILED  app <n>: ...` in the job log, and the run counted as failed | The wrapper could not read the application's output (the reason follows) | Compare the output with what the wrapper expects; fix the wrapper and check it with `crab parse` |
| `This config has schema_version <n>, but this CRAB reads up to <m>` | The campaign was written by a newer dashboard than the cluster's CRAB | `crab update` on the cluster |
| `crab update` stops with `Local edits to tracked files would be overwritten` | A tracked file in the checkout was changed by hand | Move the change to `local/` (presets, receipts, wrappers), or commit or stash it, then retry |
| `Worker fatal error: [Errno 2] No such file or directory: 'config.json'` in the job log | `SBATCHMAN_JOB_DIR` is empty in the job: SbatchMan is the PyPI release, or the preset lacks the entry | Install SbatchMan from GitHub at the pinned commit ([step 2](#2-install-crab-and-sbatchman-on-the-cluster)) and keep the `SBATCHMAN_JOB_DIR={EXP_DIR}` entry |
| `worker environment: CRAB_... is no longer read; use ...` in the job log | The preset still exports an old `CRAB_*` launch variable | Remove it from the preset and re-run `configure` with `--overwrite` ([CPU pinning and launch settings](#cpu-pinning-and-launch-settings)) |
| **Write files** is greyed out | The preview lists problems with the campaign, or no cluster is chosen | Fix each listed item; pick a connected cluster |
| "SSH support isn't installed in this environment" in the dashboard | The laptop install lacks the `web` extra | `pip install -e ".[web]"` from your CRAB checkout, then restart `crab web` |
| `No such option: --ntasks-per-node` from `sbatchman configure slurm` | SbatchMan names that option `--tasks-per-node` (it writes Slurm's `--ntasks-per-node` itself) | Use `--tasks-per-node` and re-run with `--overwrite` ([step 5](#5-create-a-sbatchman-preset-for-crab)) |
| Timings vary between jobs of the same campaign, or `squeue` shows two of its jobs on the same nodes | The preset lacks `--exclusive`, so the jobs share nodes | Add `--exclusive` and re-run `configure` with `--overwrite` ([step 5](#5-create-a-sbatchman-preset-for-crab)) |
| The dashboard or `sbatchman visualize` will not start: port in use | Both use `8765`, and `sbatchman visualize` cannot change it | Start the dashboard on another port with `crab web --port <other port>` (see [step 8](#8-monitor-and-visualize)) |
