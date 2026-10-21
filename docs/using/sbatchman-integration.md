# Using CRAB with SbatchMan

CRAB and [SbatchMan](https://github.com/LorenzoPichetti/SbatchMan) are independent, standalone
tools with a fixed division of labor: CRAB downloads, builds, and co-runs benchmarks and parses
their output. SbatchMan owns scheduler configuration, job submission, monitoring, and results
visualization, through its own CLI and TUI. **CRAB's dashboard does not launch, monitor, or
visualize a SbatchMan campaign.** Its only optional role in this flow is authoring a campaign and
generating the SbatchMan jobs file. See
[the roadmap's "Relationship with SbatchMan" section](../dev/roadmap.md#relationship-with-sbatchman)
for the full picture.

This page has two tracks, and you can follow either one top to bottom:

- **[Track 1](#track-1-everything-on-the-cluster)** does everything on the cluster over SSH, from
  installing CRAB to reading results. Nothing is installed on your laptop. This is all you need.
- **[Track 2](#track-2-author-campaigns-on-your-laptop-optional)** adds the dashboard on your
  laptop, as a nicer way to write the campaign file of
  [step 6](#6-write-a-campaign). It sends the file to the cluster, and you launch it with
  [step 7](#7-launch-it) of Track 1.

CRAB's `blink` benchmark suite is the worked example. The [troubleshooting](#troubleshooting)
section at the end lists the failures people hit and how to fix each one.

## What goes where

| Machine | What runs there | Installed from |
|---|---|---|
| The cluster | CRAB (benchmarks, recipes, the `crab worker` that runs inside each job) and SbatchMan (presets, `launch`, `status`), side by side in one virtual environment. Also your campaign files and the results. | CRAB `sbatchman` branch + SbatchMan from GitHub, at a pinned commit |
| Your laptop (optional) | CRAB's web dashboard (`crab web`), where you author campaigns | CRAB, `sbatchman` branch, `web` extra |

Every job SbatchMan submits runs `crab worker` inside the allocation it obtained. Both tools
therefore live in the same environment on the cluster, and the job must be able to find that
environment (see [step 5](#5-create-the-sbatchman-presets)).

## Prerequisites

- A cluster account with Slurm, Python 3.10+ and git on the login node, and SSH access.
- Your Slurm partition and account names, and a QoS name if your site requires one.
- Only for Track 2: a laptop with Python 3.10+ and git (any OS; no Slurm, no compilers needed
  there), and a way to log in to the cluster from it with a key or an agent (see
  [Connect the cluster](#start-it-and-connect-the-cluster)).

## Track 1: everything on the cluster

Log in to the cluster over SSH and run every command of this track there.

### 1. Install CRAB

Pick an install directory on the cluster (your home directory is fine). CRAB goes into
`<install dir>/CRAB`, with its virtual environment at `<install dir>/CRAB/.venv`.

```bash
cd <install dir>
git clone --branch sbatchman https://github.com/HLC-Lab/CRAB
cd CRAB
make
source .venv/bin/activate
crab info
```

`make` creates `.venv`, installs CRAB into it, clones the shared wrappers into `wrappers/`, and
then opens the `crab setup` wizard. You can build the benchmarks there right away (that is
[step 3](#3-build-or-register-the-benchmarks)), or exit the wizard and come back later with
`crab setup`. `make` needs `python3` 3.10 or newer. If your site's default is older, load a module
for a recent Python before running it (for example `module load python/3.11`).

`crab info` prints the CRAB version, the root directory, `layout: checkout` for a checkout like
this one, and the known presets. Activate the environment (`source <install dir>/CRAB/.venv/bin/activate`)
in every new shell before the steps below.

### 2. Install SbatchMan

Install SbatchMan into the same environment, **from GitHub at this commit**, not from PyPI:

```bash
pip install "git+https://github.com/LorenzoPichetti/SbatchMan.git@f4537e6312cb064d44a060d7c53901a91735b11a"
sbatchman --version
```

PyPI's release (`1.0.8`) predates a fix this flow depends on: SbatchMan exporting
`SBATCHMAN_JOB_DIR` into each job.

The Leonardo pilot (2026-10-19) ran SbatchMan commit `18701e4`, and the pinned commit
`f4537e6312cb064d44a060d7c53901a91735b11a` (SbatchMan `2.0.3`; `sbatchman --version` prints it)
was checked only on a laptop, with the same behavior. If something here stops matching, check
whether `main` has moved.

### 3. Build or register the benchmarks

Every application a campaign uses must be findable on the cluster. CRAB looks for each wrapper's
binary in this order: the app's `binary` key in the campaign's config, then a receipt, then the
wrapper's `executable` on `PATH` in the job (see
[Finding the binary](../extending/wrappers.md#finding-the-binary)).

- **Build it with CRAB**, for the applications CRAB has a recipe for (the Blink suite, for
  example): in the activated environment run `crab setup` and pick it (see
  [Set up benchmarks](installation.md#set-up-benchmarks-crab-setup)). Building Blink needs CMake
  3.16 or newer and a C compiler with MPI, for example the site's gcc and openmpi modules. The
  wizard asks for a module line (default `module load gcc openmpi`): add `cmake` to it if CMake
  is a module on your cluster.
- **Already installed through a module?** Add the `module load` to your SbatchMan preset (step 5).
  If the wrapper declares an `executable`, CRAB then finds it on `PATH` with no receipt.
- **Installed somewhere else?** Record it once: `crab receipts set <benchmark_id> --binary
  /path/to/program`.

Check the result:

```bash
crab wrappers list
```

It shows, for every wrapper, where its binary comes from (`config`, `receipt` or `path`) or
`missing`. A program that was never built shows `missing`. A campaign whose binary is missing
fails before its first run, with a message saying what was tried. For the Blink examples below,
`blink/alltoall/alltoall_b.py`, `blink/alltoall/alltoall_nb.py` and `blink/incast/incast_nb.py`
must not show `missing`.

### 4. Create a SbatchMan project

SbatchMan keeps its presets and job records in a project folder. Create one in the directory you
will launch campaigns from (a directory next to CRAB works well), and tell SbatchMan the
cluster's name:

```bash
mkdir -p <install dir>/campaigns && cd <install dir>/campaigns
sbatchman init
sbatchman set-cluster-name <cluster name>
```

`sbatchman init` creates a `SbatchMan/` folder there. Every later `sbatchman` command looks for
that folder in the current directory and its parents (then in your home directory), so run
`configure` and `launch` from inside this directory. The cluster name is the one you pass as
`--system` in the next step.

### 5. Create the SbatchMan presets

A SbatchMan preset (a "configuration") holds the Slurm settings and the environment every job
gets. SbatchMan submits each job with the allocation its preset asks for, so **the node count
lives in the preset**. A `{nodes}` variable in CRAB's own config changes only what CRAB tries to
run inside the allocation, not the allocation Slurm gives the job. CRAB's worker also takes its
environment from the preset: it does not write its own environment file when run under SbatchMan
(see [ADR-027](../dev/dashboard/decisions/adr-027-sbatchman-seam-v2.md) for the reasoning).

`crab sbatchman-preset` prints what you need for the CRAB install that runs it. It fills in the
paths (`PATH`, `CRAB_ROOT`, `CRAB_PATH_WRAPPERS`) and takes the Slurm values from flags. It has
two forms. Run it in the activated environment, and replace each `<placeholder>` with your value.
Add `--qos <qos>` only where your site requires a QoS for the partition. A value you leave out
stays a `<placeholder>` in the output, so give every flag or edit the file before you use it.
[`crab sbatchman-preset`](../reference/cli.md#crab-sbatchman-preset) lists every flag.

**One preset**, for one node count. The command writes a `sbatchman configure slurm` command, named
`crab_run` unless you pass `--name`. Run it from the project directory of
[step 4](#4-create-a-sbatchman-project):

```bash
cd <install dir>/campaigns
crab sbatchman-preset \
  --system <cluster name> \
  --partition <partition> \
  --account <account> \
  --time <hh:mm:ss> \
  --nodes <nodes> \
  --tasks-per-node <tasks per node> > preset.sh
sh preset.sh
```

**A node sweep**, for a campaign that runs the same experiment on several node counts. With
`--configs-file` the command writes a SbatchMan configs file instead, and `--nodes` takes a comma
list:

```bash
cd <install dir>/campaigns
crab sbatchman-preset --configs-file --nodes 2,4,8,16 \
  --system <cluster name> \
  --partition <partition> \
  --account <account> \
  --time <hh:mm:ss> \
  --tasks-per-node <tasks per node> > configs.yaml
```

`configs.yaml` defines one preset per node count, named `crab_run_2`, `crab_run_4`, `crab_run_8`
and `crab_run_16`. The top-level key of the file is the `--system` name and must equal the output
of `sbatchman set-cluster-name`. A jobs file uses the file with `configs: configs.yaml` and picks
the preset with `config: crab_run_{nodes}` ([step 6](#6-write-a-campaign)). To use the file
without a jobs file naming it, load it into the project once with
`sbatchman configure --file configs.yaml`.

Read the output before you use it. If CRAB prints a warning that `wrappers/` is missing, run
`crab update` in the CRAB checkout: the warning goes to the terminal, not into the file.

SbatchMan turns each option into an `#SBATCH` line of the job script. Each line of the printed
command (and each key of the configs file) does this:

| Option | What it sets | When and why |
|---|---|---|
| `--name crab_run` | The preset's name. | A campaign group refers to the preset by this name ([step 6](#6-write-a-campaign)). Change it with `--name`. |
| `--partition <partition>` | `#SBATCH --partition`: the queue the jobs go to. | Most sites require one. Use a partition your account may submit to. |
| `--account <account>` | `#SBATCH --account`: the project the hours are charged to. | Required where the site accounts for usage. |
| `--qos <qos>` | `#SBATCH --qos`: the quality of service. | Printed only when you pass `--qos`. Some sites use it to select short debug queues. |
| `--time <hh:mm:ss>` | `#SBATCH --time`: the walltime limit of each job. | Slurm kills a job that runs past it, and many sites require it or default to a short limit. Set it above your longest job. A job that holds several experiments needs time for all of them, one after another ([step 6](#6-write-a-campaign)). |
| `--nodes <nodes>` | `#SBATCH --nodes`: how many nodes each job gets. | Match the experiment's `numnodes` (see [Sizing the allocation for co-runs](#sizing-the-allocation-for-co-runs)). |
| `--tasks-per-node <tasks per node>` | `#SBATCH --ntasks-per-node`: how many ranks each node can run. | The allocation must hold the ranks of all apps at once. For a co-run, size it as in [Sizing the allocation for co-runs](#sizing-the-allocation-for-co-runs). |
| `--exclusive` | `#SBATCH --exclusive`: the job gets its nodes to itself. | Without it, the jobs of one campaign can run at the same time on the same nodes and skew each other's network timings. Pass `--no-exclusive` to `crab sbatchman-preset` only if the site forbids it, or if you measure nothing network-sensitive. |
| `--env 'PATH=<install dir>/CRAB/.venv/bin:$PATH'` | Puts `crab` on the job's path. | Without it, the job only finds `crab` if you launched from an activated shell and your site forwards the submitting environment to jobs. |
| `--env 'CRAB_ROOT=<install dir>/CRAB'` | Tells the worker where its install is. | The absolute path of the CRAB checkout that ran `crab sbatchman-preset`. |
| `--env 'CRAB_SYSTEM=<cluster name>'` | Names the system the job runs on. | The value of `--system`: use the cluster name from [step 4](#4-create-a-sbatchman-project). |
| `--env 'CRAB_PATH_WRAPPERS=<install dir>/CRAB/wrappers'` | Where the worker resolves wrapper paths. | Lets it resolve paths like `blink/alltoall/alltoall_b.py`. |
| `--env 'SBATCHMAN_JOB_DIR={EXP_DIR}'` | Points the worker at the job's own directory. | SbatchMan at the pinned commit already exports it. Keeping the line makes the preset work with builds that don't. |


In the table, the one-preset form writes `--name crab_run`; a campaign refers to the preset by that
name ([step 6](#6-write-a-campaign)), and `--name` changes it. The configs file names its presets
`<name>_<nodes>` instead.

The command keeps single quotes around each `--env` entry. If you edit them, keep the quotes:
SbatchMan writes each entry into the job script as a plain `export` line, so `$PATH` is expanded
when the job runs, on the compute node, not now on the login node.

`{EXP_DIR}` is SbatchMan's per-job-directory token. It is substituted inside `configure`-time
fields like `--env`, never inside a campaign's own `command` or `preprocess` text: a token like
`{EXP_DIR}` written directly in a hand-made jobs file's `command:` survives as a literal string.
This is SbatchMan behavior, not specific to CRAB.

To add a `sbatchman configure slurm` option the generator has no flag for, append it to the last
line of `preset.sh` before you run it. For GPU benchmarks, append ` --gpus <n>`: SbatchMan writes
it as `#SBATCH --gres=gpu:<n>`. See SbatchMan's own documentation for the other options (`--mem`,
`--constraint`, `--reservation`, ...). To change a preset later, regenerate `preset.sh`, append
` --overwrite` to its last line, and run it again.

#### Sizing the allocation for co-runs

When an experiment co-runs apps on the same nodes (`allocation.mode: "shared"`), the Slurm
allocation has to hold all of their ranks at once. CRAB sizes Slurm's `--ntasks-per-node` itself in
the batch scripts it writes, but SbatchMan writes its own from the preset, so here you size it by
hand: set `--tasks-per-node` (SbatchMan writes it as Slurm's `--ntasks-per-node`) to the largest
number of ranks any node carries, and `--nodes` to the experiment's `numnodes`. On
each node, apps that share it add up; apps that start after another one on its nodes
(`start: "sN"`) count once, at the largest `ppn` of the chain.
[Ranks per node](../reference/configuration.md#ranks-per-node-ppn) gives the rule CRAB itself
applies.

For example, two apps on the same 2 nodes, with `ppn` 4 each, need 8 tasks per node:

```bash
crab sbatchman-preset \
  --name crab_corun_2n \
  --system <cluster name> \
  --partition <partition> \
  --account <account> \
  --time <hh:mm:ss> \
  --nodes 2 \
  --tasks-per-node 8 > preset_2n.sh
```


This is [step 5](#5-create-the-sbatchman-presets)'s one-preset form with three flags set for the
co-run:

- `--name crab_corun_2n` names a preset for this node count, so another count gets its own.
- `--nodes 2` is the experiment's `numnodes`.
- `--tasks-per-node 8` is two apps times a `ppn` of 4 on each node.

A campaign that sweeps the node count needs one preset per value. The easiest way is the
configs file of step 5 (`--configs-file --nodes 2,4`), picked through `config: crab_run_{nodes}`.

CRAB does not check this value for a SbatchMan campaign. When it is smaller than the largest
per-node load, the allocation cannot hold every app's ranks at the same time, so the co-run does
not happen as configured: what follows is Slurm's behavior (a step is refused, or waits for
free ranks), not a CRAB error message.

### 6. Write a campaign

A campaign is a SbatchMan jobs file: a YAML file that lists the jobs to submit. In each job,
`preprocess` writes CRAB's experiment as `config.json` into the job's directory, and `command`
runs `crab worker` there. The shape:

```yaml
configs: configs.yaml          # from step 5; leave out for a single preset made with preset.sh
variables:
  nodes: [2, 4, 8, 16]         # SbatchMan runs the jobs once per value
jobs:
  - config: crab_run_{nodes}   # the preset of step 5 (crab_run for a single preset)
    tag: a2a-smoke-{nodes}
    preprocess: |
      mkdir -p "$SBATCHMAN_JOB_DIR"
      cat > "$SBATCHMAN_JOB_DIR/config.json" <<'JSON'
      { "schema_version": 2, "global_options": { ... }, "experiments": { "run": { ... } } }
      JSON
    command: crab worker --workdir "$SBATCHMAN_JOB_DIR"
```

One job can hold several experiments. Under `"experiments"` you can list as many as you like: CRAB
runs them one after another, in sorted name order, inside the single Slurm allocation the job got,
so they all run on the same nodes. This is how a baseline and a noise run share nodes: they are
experiments of the same job, not two jobs. Name the experiments so that the order you want is the
sorted order (`r1_baseline`, `r1_noise`, `r2_baseline`, ... run in exactly that order). A failing
experiment does not stop the ones after it. The job's walltime (`--time` in the preset of
[step 5](#5-create-the-sbatchman-presets)) must cover all of its experiments together, and CRAB
gives each experiment 1200 seconds by default, so a job of six experiments can use two hours.

The quickest start is to copy a ready-made campaign from `examples/sbatchman/` in the CRAB
checkout, next to your `configs.yaml`:

```bash
cd <install dir>/campaigns
cp <install dir>/CRAB/examples/sbatchman/*.yaml .
```

The folder has `smoke.yaml` (one all-to-all on 2 nodes, small enough for a debug queue),
`a2a-vs-incast.yaml` and `a2a-vs-a2a.yaml`. The last two measure how much an incast aggressor (or
an all-to-all aggressor) slows an all-to-all. Each one has `nodes` (4, 8 and 16) and `msgsize` (1024,
65536, 1048576 and 4194304 bytes) as variables, so SbatchMan launches 12 jobs per file, one for
every pair. Every job runs six experiments in one allocation: `r1_baseline`, `r1_noise`,
`r2_baseline`, `r2_noise`, `r3_baseline` and `r3_noise`. The baseline experiments run only the
victim, `blink/alltoall/alltoall_b.py` with `-msgsize {msgsize} -iter 100`. The noise experiments
run the same victim next to the aggressor. Its
[README](https://github.com/HLC-Lab/CRAB/blob/sbatchman/examples/sbatchman/README.md) explains each
file and what it measures, and it lists the cost of one job per `(nodes, msgsize)` pair: more
queue waits, and a different allocation for each message size. Edit the `args` of an app, the `variables`, or the experiment, or write
your own file in the same shape. The `"experiments"` object is a normal CRAB config: see the
[configuration reference](../reference/configuration.md).

To check an experiment before you launch, copy the JSON between the heredoc lines into a file and
run `crab validate config.json`. This step is optional: it catches a mistake in the experiment,
not in the jobs file.


### 7. Launch it

CRAB never runs `sbatchman launch` for you. From the project directory:

```bash
cd <install dir>/campaigns
sbatchman launch -f smoke.yaml --dry-run   # list the jobs only
sbatchman launch -f smoke.yaml
squeue --me
```

`--dry-run` shows what SbatchMan would submit and changes nothing. `squeue --me` lists your Slurm
jobs. Launch the small `smoke.yaml` first: when it finishes cleanly, your project, presets and
CRAB's worker work together. Then launch the real campaign the same way. Each of the two campaign
files submits 12 jobs, and each job runs six experiments, so size the `--time` of the largest
preset for all six.

### 8. Monitor

Use SbatchMan's own tooling: `sbatchman status` lists the jobs and their state,
`sbatchman campaign-tui` is a terminal view of a campaign, and `sbatchman visualize` serves a
results page. CRAB's dashboard has no view into a SbatchMan campaign's job status or results.

!!! warning "Port collision"
    Both `crab web` and `sbatchman visualize` use port `8765`, and `sbatchman visualize` has no
    option to change it. To run both on the same machine, start the dashboard on a different port
    with `crab web --port <other port>`.

### 9. Read the results

SbatchMan gives each job a directory:

```text
<project>/SbatchMan/experiments/<cluster name>/<config>/<tag>/<timestamp>/
```

`<config>` is the preset (for example `crab_run_8`) and `<tag>` is the job's tag. This is the
directory SbatchMan exports as `SBATCHMAN_JOB_DIR`; `sbatchman status` also lists it. In it:

- `metadata.yaml` is SbatchMan's record of the job: its tag, config, job id and state.
- `config.json` is the config the job wrote and ran, with all of its experiments, and
  `stdout.log` and `stderr.log` are the job's output.
- `crab_run.json`, next to `config.json`, records which CRAB version and which wrapper files (with
  their hashes) produced the job.
- One folder per experiment, named after the experiment's key under `experiments`: `r1_baseline/`,
  `r1_noise/`, `r2_baseline/`, `r2_noise/`, `r3_baseline/` and `r3_noise/` in the examples (a
  single-experiment job such as `smoke.yaml` has just `run/`). An experiment that collects
  metrics produces `data_app_<index>.csv` there, one file per app that has `"collect": true`, so
  `data_app_0.csv` for the first app. In the examples only the victim (app 0) is collected, so
  the noise experiments have the victim's CSV and none for the aggressor.
  `bindings_app_<index>.log` holds the launcher's report of how it bound the ranks.
- Inside an experiment folder, each run's working files are in `run_<n>/app_<index>/`, and files
  a wrapper asks to keep are copied to `artifacts/run_<n>/`.

Each `data_app_<index>.csv` has one row per sample of one run. The columns are `run_id`,
`msg_size`, and the metric columns, named `<app index>_<metric>_<unit>`. CRAB fills `msg_size`
from the app's `-msgsize` argument: if the app's `args` have no `-msgsize`, the column is 0. Always
pass `-msgsize` (as the examples do) so the column tells the sizes apart.

Not every app produces a CSV: a benchmark that collects no metrics (`blink`'s `null_dummy`, for
instance, an isolation sanity check with nothing to measure) never writes one, by design.

**Compare a baseline with noise.** Work inside one job, for example the one tagged
`a2a-incast-8n-65536B` (8 nodes, 64 KiB messages). For the victim (app 0) the metric columns are
`0_Avg-Duration_s`, `0_Min-Duration_s`, `0_Max-Duration_s`, `0_Median-Duration_s` and
`0_MainRank-Duration_s`, in seconds. Use `0_Avg-Duration_s`:

1. Pool the rows of `0_Avg-Duration_s` from `r1_baseline/data_app_0.csv`,
   `r2_baseline/data_app_0.csv` and `r3_baseline/data_app_0.csv`.
2. Pool the rows from the three `r*_noise` experiments.
3. The slowdown is the ratio of the means: `mean(noise) / mean(baseline)`.

Repeat this for each job, which is one message size at one node count, then put the slowdowns side
by side: along `nodes` to see how the effect changes with the allocation size, along `msgsize` to
see which messages suffer. Each slowdown comes from one allocation, so a difference between two
jobs may include a difference between their nodes as well as in the noise.

The comparison is only fair when the baseline and the noise run get the same allocation and the
same placement of the victim: the same job. Baseline and noise are experiments of one job, so
they run on the same nodes, split between a victim partition and an aggressor partition in the
same way, and only the aggressor differs. The examples are built that way (see the
[README's fairness rule](https://github.com/HLC-Lab/CRAB/blob/sbatchman/examples/sbatchman/README.md#the-fairness-rule)).
Two separate jobs would land on different nodes, and a baseline that allocated fewer nodes would
change which switches the victim's nodes sit behind, so either way the result would mix that
change with the noise. Running them in turns (baseline, noise, baseline, noise, ...) also spreads
any slow drift of the network over both sides.

Reading the CSV is up to you: a spreadsheet, a notebook, or `sbatchman visualize`'s own parser
script (its `--parser` option, `./parser.py` by default). To parse a saved output again with a
wrapper, use `crab parse <wrapper> <output file>`.

## Track 2: author campaigns on your laptop (optional)

The dashboard writes the jobs file of [step 6](#6-write-a-campaign) for you and sends it to the
cluster. The cluster needs CRAB, SbatchMan, the benchmarks and the presets first, as set up by steps
1 to 5 of Track 1 (the dashboard can also install CRAB itself, see below).

### Install the dashboard

On your laptop:

```bash
git clone --branch sbatchman https://github.com/HLC-Lab/CRAB
cd CRAB
python -m venv .venv
source .venv/bin/activate
pip install -e ".[web]"
```

The `web` extra holds the dashboard's dependencies: a plain `make` install, as on the cluster,
lacks it.

### Start it and connect the cluster

The usual way to log in is your `ssh-agent`. The dashboard reads `SSH_AUTH_SOCK` from the
environment of the process that started it, so an agent started in another shell is invisible to
it. Start the agent, load your key and start the dashboard **in the same shell**:

```bash
eval $(ssh-agent)
ssh-add
crab web
```

The dashboard opens at `http://127.0.0.1:8765`. Open **Remotes**, click **+ Add cluster**, and fill
in:

- **Name**: a name for the cluster in the dashboard.
- **Transport**: `ssh`.
- **Host** and **User**: the login node and your user name there.
- **Auth**: `agent`. The other methods are `key file` and `password` (see
  [Connect a cluster](dashboard.md#connect-a-cluster)).
- **Install dir**: the **parent** folder of the CRAB checkout on the cluster, not the checkout
  itself. CRAB lives in a `CRAB` folder inside it. If CRAB is at `~/crab-v1/CRAB`, Install dir is
  `~/crab-v1`. The field shows the result, for example `CRAB goes in ~/crab-v1/CRAB`.

Click **Save**, then **Connect**. The line under the name shows the CRAB version on the cluster.
When CRAB is not there yet, the page offers **Install CRAB**; the manual install of Track 1 works
just as well. After it, add SbatchMan as in [step 2](#2-install-sbatchman).

### Author a campaign and write the files

Open **SbatchMan** and build your campaign: give the group a tag, and set its **SbatchMan
preset** to the preset you created in [step 5](#5-create-the-sbatchman-presets): `crab_run` for a
single preset, or `crab_run_{nodes}` when the presets came from the configs file and the group
sweeps `nodes`. Add an app the same way you would in a normal CRAB experiment: for `blink`, pick a
wrapper such as `blink/alltoall/alltoall_b.py` from the picker and set its args. Sweep values go in
variables and are used as `{name}` placeholders, including in numeric fields such as the node
count or an allocation's stride.

The preview panel lists anything that would make the campaign fail at launch: a variable without a
name, a `{placeholder}` no variable defines, a group without a preset, an invalid experiment, and
wrappers the connected cluster does not have. **Write files** stays disabled until that list is
empty.

Choose the connected cluster and click **Write files**. The page shows two paths:

- On the cluster, the file is `<Install dir>/CRAB/.web_staging/<campaign name>.yaml`. The file name
  is the campaign name in lower case, with every run of other characters replaced by `-`
  (`My A2A run` becomes `my-a2a-run.yaml`).
- On your laptop, a copy is kept in the dashboard's data folder, in `sbatchman_campaigns/`
  (`~/.local/share/crab/sbatchman_campaigns/` on Linux), as `<timestamp>-<campaign name>.yaml`.

The dashboard does not write the jobs file's `configs:` key: each group's preset is resolved by
name from the presets already in your SbatchMan project. If you made them with the configs file,
load it once from the project directory with `sbatchman configure --file configs.yaml`.

### Launch it

Launching is Track 1's [step 7](#7-launch-it), run on the cluster, with the path the page showed
you:

```bash
cd <install dir>/campaigns
sbatchman launch -f <install dir>/CRAB/.web_staging/<campaign name>.yaml --dry-run
sbatchman launch -f <install dir>/CRAB/.web_staging/<campaign name>.yaml
```

Then monitor and read results with steps 8 and 9.

## CPU pinning and launch settings

A CRAB worker started by SbatchMan has no CRAB execution preset: the job directory holds no
`execution.json` and the job sets no `CRAB_PRESET`, so the worker uses the Slurm scheduler and the
`srun` launcher with no extra flags. Its log says so:
`Execution settings from defaults (Slurm with srun; no execution.json, no CRAB_PRESET)`.

To pin processes to CPUs, set Slurm's own input variable in the SbatchMan preset's environment
(the job script's environment). `--cpu-bind cores` adds it to the command of
[step 5](#5-create-the-sbatchman-presets):

```bash
crab sbatchman-preset \
  --name crab_run_pinned \
  --system <cluster name> \
  --partition <partition> \
  --account <account> \
  --time <hh:mm:ss> \
  --nodes <nodes> \
  --tasks-per-node <tasks per node> \
  --cpu-bind cores > preset_pinned.sh
```

The printed command is step 5's under another name, plus a last `--env 'SLURM_CPU_BIND=cores'`
line. Run it as in step 5. The node-sweep form takes `--cpu-bind` too, and puts `SLURM_CPU_BIND` into each preset of the configs file.

`SLURM_CPU_BIND` is the input variable Slurm documents for `srun`'s `--cpu-bind` option, so every
`srun` the worker starts binds as if `--cpu-bind=cores` had been given. Other values work the same
way, for example `--cpu-bind sockets`; see `man srun` for the full list. No CRAB code is
involved: the worker runs `srun --export=ALL`, which passes the job's environment on to each
launched process.

The `CRAB_*` launch variables of older CRAB versions are no longer read: `CRAB_WL_MANAGER`,
`CRAB_SCHEDULER`, `CRAB_MPIRUN`, `CRAB_MPIRUN_ADDITIONAL_FLAGS`, `CRAB_MPIRUN_MAP_BY_NODE_FLAG`,
`CRAB_MPIRUN_HOSTNAMES_FLAG` and `CRAB_PINNING_FLAGS`. A worker that finds one in its environment
stops with an error that names the replacement, for example
`worker environment: CRAB_PINNING_FLAGS is no longer read; use launchers.srun.flags`. Remove them
from your SbatchMan presets and, when you want pinning, use `SLURM_CPU_BIND` as above.

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

Run `crab update` in the activated environment on the cluster, and, if you use Track 2, in your laptop's CRAB checkout.
By default it moves CRAB to the newest `+sbatchman` release tag (`vX.Y.Z+sbatchman`) and the
shared wrappers clone to the tag `vX.Y.Z` of the CRAB version it ends on, so the two match. It
reinstalls dependencies only when they changed, and refuses to run over local edits to tracked
files (keep yours in `local/`). `crab update --to-release` does the same as plain `crab update`.

To follow the `sbatchman` branch between releases, run `crab update --head`: CRAB and the wrappers
both fast-forward to their branch HEAD. A checkout that a plain `crab update` left on a release
tag has to return to the branch first: run `git checkout sbatchman`, then `crab update --head`.

With Track 2, when the laptop and a cluster run versions that no longer understand each other, the dashboard's
**Remotes** page says which side to update.

## Troubleshooting

Start with the job's `stderr.log` and `stdout.log`, in its job directory.

| Symptom | Cause | Fix |
|---|---|---|
| `crab: command not found` in a job's `stderr.log` | The job's shell cannot see the CRAB environment | Regenerate the preset with `crab sbatchman-preset` from the activated environment: its command carries the `PATH` entry ([step 5](#5-create-the-sbatchman-presets)). Append ` --overwrite` to the last line of the file, run it, then relaunch |
| `SbatchMan root not found. Please run 'sbatchman init'`, or a prompt offering to create a project | The command ran outside the project directory | `cd` into the directory where you ran `sbatchman init` ([step 4](#4-create-a-sbatchman-project)) |
| `Cluster name not set. Please run 'sbatchman set-cluster-name'.` | No cluster name in SbatchMan's global settings | `sbatchman set-cluster-name <cluster name>` |
| `Wrapper not found: blink/alltoall/alltoall_b.py` (a relative path) in the job log | `CRAB_PATH_WRAPPERS` is not set in the job | Regenerate the preset with `crab sbatchman-preset`: its command carries the entry ([step 5](#5-create-the-sbatchman-presets)). Append ` --overwrite` to the last line of the file, run it, then relaunch |
| `Wrapper not found: blink/alltoall/alltoall_b.py`, and `<install dir>/CRAB/wrappers/` is empty or missing | CRAB was installed with `pip install -e .` alone, which does not fetch the shared wrappers | Run `make` (or `crab update`) in the CRAB checkout |
| `Wrapper not found: <absolute path>` | The wrapper file is not in that CRAB checkout | Check the path, and that the cluster's CRAB is on the `sbatchman` branch and up to date (`crab update`) |
| `No binary for ... Tried: ...` in the job log | No config `binary`, receipt, or program on `PATH` for that wrapper | Build it, load its module in the preset, or record it with `crab receipts set` ([step 3](#3-build-or-register-the-benchmarks)) |
| `PARSE FAILED  app <n>: ...` in the job log, and the run counted as failed | The wrapper could not read the application's output (the reason follows) | Compare the output with what the wrapper expects; fix the wrapper and check it with `crab parse` |
| `This config has schema_version <n>, but this CRAB reads up to <m>` | The campaign was written by a newer dashboard than the cluster's CRAB | `crab update` on the cluster |
| `crab update` stops with `Local edits to tracked files would be overwritten` | A tracked file in the checkout was changed by hand | Move the change to `local/` (presets, receipts, wrappers), or commit or stash it, then retry |
| `crab update` stops with `config/presets.json moved to src/crab/presets.json in this version: copy your edits to local/presets.json, run `git checkout config/presets.json`, then `crab update` again` | The checkout has edits to the old `config/presets.json`, which now lives inside the package | Do what the message says: copy your edits to `local/presets.json`, run `git checkout config/presets.json`, then run `crab update` again |
| `crab update --head` stops with `CRAB is on release <tag>, not a branch: run `git checkout <branch>` in <dir>, then `crab update --head`` | A plain `crab update` left the checkout on a release tag, and `--head` needs a branch to follow | Run `git checkout sbatchman` in the CRAB checkout, then `crab update --head` |
| `crab update` stops with `The wrappers clone <dir> has local edits (<files>): commit or stash them (or keep private wrappers in local/wrappers), then run crab update again.` | A tracked file in the `wrappers/` clone was edited by hand | Commit or stash the edits, or move private wrappers to `local/wrappers/` ([Writing a wrapper](#writing-a-wrapper-for-your-own-application)), then run `crab update` again |
| `Worker fatal error: [Errno 2] No such file or directory: 'config.json'` in the job log | `SBATCHMAN_JOB_DIR` is empty in the job: SbatchMan is the PyPI release, or the preset lacks the entry | Install SbatchMan from GitHub at the pinned commit ([step 2](#2-install-sbatchman)) and keep the `SBATCHMAN_JOB_DIR={EXP_DIR}` entry that `crab sbatchman-preset` prints |
| `worker environment: CRAB_... is no longer read; use ...` in the job log | The preset still exports an old `CRAB_*` launch variable | Regenerate the preset with `crab sbatchman-preset`, append ` --overwrite` to the last line of the file, and run it ([CPU pinning and launch settings](#cpu-pinning-and-launch-settings)) |
| Track 2: **Write files** is greyed out | The preview lists problems with the campaign, or no cluster is chosen | Fix each listed item; pick a connected cluster |
| "SSH support isn't installed in this environment" in the dashboard | The laptop install lacks the `web` extra | `pip install -e ".[web]"` from your CRAB checkout, then restart `crab web` |
| `No such option: --ntasks-per-node` from `sbatchman configure slurm` | SbatchMan names that option `--tasks-per-node` (it writes Slurm's `--ntasks-per-node` itself) | Use `--tasks-per-node`, as `crab sbatchman-preset` does, and re-run with `--overwrite` ([step 5](#5-create-the-sbatchman-presets)) |
| Timings vary between jobs of the same campaign, or `squeue` shows two of its jobs on the same nodes | The preset lacks `--exclusive`, so the jobs share nodes | Regenerate the preset without `--no-exclusive`, append ` --overwrite` to the last line of the file, and run it ([step 5](#5-create-the-sbatchman-presets)) |
| The dashboard or `sbatchman visualize` will not start: port in use | Both use `8765`, and `sbatchman visualize` cannot change it | Start the dashboard on another port with `crab web --port <other port>` (see [step 8](#8-monitor)) |
