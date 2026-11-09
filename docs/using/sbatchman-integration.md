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
| The cluster | CRAB (benchmarks, recipes, the `crab worker` that runs inside each job) and SbatchMan (its project, `launch`, `status`), side by side in one virtual environment. Also your configs file, your campaign files and the results. | CRAB `sbatchman` branch + SbatchMan 2.0.4 from PyPI |
| Your laptop (optional) | CRAB's web dashboard (`crab web`), where you author campaigns | CRAB, `sbatchman` branch, `web` extra |

Every job SbatchMan submits runs `crab worker` inside the allocation it obtained. Both tools
therefore live in the same environment on the cluster, and the job must be able to find that
environment (see [step 5](#5-get-the-sbatchman-configs-file)).

## Prerequisites

- A cluster account with Slurm, Python 3.10+ and git on the login node, and SSH access.
- Your Slurm account name, and the partition and QoS names your site uses (the configs file of
  [step 5](#5-get-the-sbatchman-configs-file) asks for them).
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

`crab info` prints the CRAB version, the root directory, and `layout: checkout` for a checkout like
this one.

Activate the environment (`source <install dir>/CRAB/.venv/bin/activate`) in **every** new shell
before the steps below. If a cluster Python module is loaded in that shell, run
`unset PYTHONPATH PYTHONHOME` first (see the
[note in the installation page](installation.md#install-the-framework)). Then check which
`sbatchman` the shell runs:

```bash
which sbatchman
```

The path it prints must be inside the CRAB `.venv` (`<install dir>/CRAB/.venv/bin/sbatchman`).
A shell without the environment can find another SbatchMan install on its `PATH`, and an older one
ignores the `configs:` line of a jobs file: the jobs then keep the settings stored by an earlier
launch (see [troubleshooting](#troubleshooting)).

### 2. Install SbatchMan

Install SbatchMan from PyPI into the same environment, then check that it runs:

```bash
pip install "sbatchman==2.0.4"
sbatchman --version
```

`pip install "sbatchman==2.0.4"` installs exactly version 2.0.4, the version this guide was
written against, and `sbatchman --version` prints the installed version.

### 3. Build or register the benchmarks

Every application a campaign uses must be findable on the cluster. CRAB looks for each wrapper's
binary in this order: the app's `binary` key in the campaign's config, then a receipt, then the
wrapper's `executable` on `PATH` in the job (see
[Finding the binary](../extending/wrappers.md#finding-the-binary)).

- **Blink**: build it with CRAB. On the login node, in the activated environment, run `crab setup`
  (it is interactive), pick the Blink suite and the strategy "Download and compile from source
  layout" (see [Set up benchmarks](installation.md#set-up-benchmarks-crab-setup)). Building Blink
  needs CMake 3.16 or newer and a C compiler with MPI, for example the site's gcc and openmpi
  modules. The wizard asks for a module line (the prompt shows `module load gcc openmpi` as an
  example): type the line your site needs, and add `cmake` to it if CMake is a module on your
  cluster. The line is saved in the receipt, and the worker applies it again when the job runs.
  The wizard then asks for the GPU backend: accept the default, `auto` (it uses CUDA or HIP when
  CMake finds them, else builds for CPU only).
- **Quantum ESPRESSO** (needed only by `qe-alone.yaml` and `qe-noise.yaml`): both examples run
  `quantum-espresso/pw/v7.py`, so register version 7. In `crab setup`, pick the Quantum ESPRESSO
  suite and, at the version question, keep only "Quantum ESPRESSO v7" ticked (untick v6). If your
  site has a QE module, choose the strategy "Map via cluster Environment Module system": the
  wizard asks for the exact module command and then for the executable the module provides, which
  is `pw.x`. On Leonardo give `module load profile/chem-phys quantum-espresso` (without a version
  it takes the default): the QE module is only visible once the `profile/chem-phys` profile is
  loaded, so a line with the module alone finds nothing. On another site, give the module name
  that site uses. The module command is saved in the receipt like Blink's. Without a QE module,
  choose "Download and compile from source layout" instead.
- **Installed somewhere else?** Record it once: `crab receipts set <benchmark_id> --binary
  /path/to/program`. (A program you load with a module of your own can also go in the `modules`
  list of the configs file of [step 5](#5-get-the-sbatchman-configs-file); if the wrapper declares an
  `executable`, CRAB then finds it on `PATH` with no receipt.)

Check the result:

```bash
crab list-benchmarks
crab wrappers list
```

`crab list-benchmarks` prints "Installed benchmarks": what `crab setup` recorded. `crab wrappers
list` shows, for every wrapper, where its binary comes from (`config`, `receipt` or `path`) or
`missing`. A program that was never built shows `missing`. A campaign whose binary is missing fails
before its first run, with a message saying what was tried. For the Blink examples below,
`blink/alltoall/alltoall_b.py`, `blink/alltoall/alltoall_nb.py` and `blink/incast/incast_nb.py`
must not show `missing`, and for the QE examples `quantum-espresso/pw/v7.py` must not either.

#### Apps built with different module stacks

When apps built with different module stacks share one job (for example QE from the HPC-X
module and Blink from the gcc and openmpi modules, as in `qe-noise.yaml`), the app's `pre_run`
hook runs in an environment that already holds the modules the job loaded for the other. A plain
`module load gcc openmpi` does not unset what the HPC-X module set: `OPAL_PREFIX` and
`HPCX_MPI_HOME` stay in the environment, and Blink's Open MPI loads HPC-X's plugins. Start each
receipt's `pre_run` hook with `module purge`, so that one app's module variables do not leak into
the next. For Blink, edit `local/receipts/blink.json` in the CRAB checkout:

```json
"pre_run": ["module purge", "module load gcc openmpi"]
```

- `module purge` unloads every module the job had loaded, `OPAL_PREFIX` and `HPCX_MPI_HOME`
  included.
- `module load gcc openmpi` then loads Blink's own stack, as before.

The symptom of a missing `module purge` is `symbol lookup error` in the app's `bindings_app_<n>.log`
and the app exiting with code 127.

### 4. Create a SbatchMan project

SbatchMan keeps its job records in a project folder. Create one in the directory you will launch
campaigns from (a directory next to CRAB works well), and tell SbatchMan the cluster's name:

```bash
mkdir -p <install dir>/campaigns && cd <install dir>/campaigns
sbatchman init
sbatchman set-cluster-name leonardo
```

`mkdir -p <install dir>/campaigns` creates the folder (`-p` makes parents as needed and does not
fail if it exists), and `cd` enters it. `sbatchman init` creates a `SbatchMan/` folder there. Every
later `sbatchman` command looks for that folder in the current directory and its parents (then in
your home directory), so run `launch` and `status` from inside this directory.
`sbatchman set-cluster-name leonardo` stores the cluster's name. **It must equal the name of the
cluster block in the configs file of the next step** (`leonardo` in the file this guide uses). On
another cluster, use that cluster's block name in both places.

### 5. Get the SbatchMan configs file

A SbatchMan configs file holds the settings every job is submitted with: the Slurm allocation, the
modules to load, and the environment. SbatchMan owns all of it: on this line the job's environment
comes from the configs file alone. CRAB ships one for Leonardo:
`examples/sbatchman/configs.leonardo.yaml`. Copy it into the project directory of
[step 4](#4-create-a-sbatchman-project) and edit the copy:

```bash
cd <install dir>/campaigns
cp <install dir>/CRAB/examples/sbatchman/configs.leonardo.yaml .
```

`cp` copies the file into the current directory, the project folder. Then edit the copy:

- Replace `<YOUR_ACCOUNT>` with your Slurm account (the project the hours are charged to).
- Replace every `<CRAB_DIR>` with the absolute path of your CRAB checkout (the folder that holds
  `wrappers/` and `.venv/`, `<install dir>/CRAB` here).
- Review `partition`, `qos`, `time` and `modules` for your site (see the table below).

The file has two parts. The first is the allocation, plain SbatchMan fields:

| Field | What it sets | When and why |
|---|---|---|
| `account` | `#SBATCH --account`: the project the hours are charged to. | Required where the site accounts for usage. |
| `partition` | `#SBATCH --partition`: the queue the jobs go to. | Use a partition your account may submit to. |
| `qos` | `#SBATCH --qos`: the quality of service. | The template sets `normal`. For a quick check, set the site's debug QOS after reading its node and time limits. Remove the line where your site has no QoS. |
| `time` | `#SBATCH --time`: the walltime limit of each job. | The template sets 30 minutes. Slurm kills a job that runs past it. A job that holds several experiments needs time for all of them, one after another ([step 6](#6-write-a-campaign)). |
| `tasks_per_node` | `#SBATCH --ntasks-per-node`: the ranks per node. | The template sets 4. It must be at least the experiment's `ppn`: without `--exclusive`, Slurm gives one task per node when a job asks only for `--nodes`, and the worker launches `ppn` ranks per node. |
| `modules` (under each config) | `module load` lines the job runs first. | `crab_run_{nodes}` loads the gcc and openmpi modules Blink was built with on Leonardo. `crab_qe_{nodes}` loads `profile/chem-phys` and `quantum-espresso`. Use the modules your build needs. |
| `gpus` (under `crab_qe_{nodes}`) | `#SBATCH --gres=gpu:<n>`: the GPUs per node. | The template sets 4 for the QE config, one per rank at `ppn: 4`. The `crab_run_{nodes}` config sets none. |
| `nodes` (under `configs`) | `#SBATCH --nodes`: how many nodes each job gets. | The `variables: nodes` list of the file creates one config per value for each of the two configs: `crab_run_2`, `crab_run_4`, `crab_run_8`, `crab_run_16` and `crab_qe_2`, `crab_qe_4`, `crab_qe_8`, `crab_qe_16`. |

The file defines two configs per node count. `crab_run_{nodes}` is the CPU config the Blink examples
use. `crab_qe_{nodes}` is the config the two QE examples use: the QE module on Leonardo is a CUDA
build (`pw.x` links the CUDA libraries), and without a GPU `pw.x` aborts at start with `error in
Message Passing (mp) module, error code 9100`; with `gpus: 4` the same run finishes.

`modules` sits in each config and not in `default_conf` on purpose. SbatchMan appends the list
fields (`modules`, `env`) of a config to those of `default_conf`, and a config cannot replace the
list there, so a `modules` list in `default_conf` would stay loaded in the QE config and mix two MPI stacks (the
gcc and Open MPI modules of Blink with the HPC-X MPI of the QE module). The `env` list, the CRAB env
block below, is the same for both configs and stays in `default_conf`.

Each config's `modules` must also put an MPI `mpirun` on the `PATH`. The worker looks for `mpirun`
in its own environment, before any app starts, to detect which MPI the site has. For QE that is the
`mpirun` the QE module provides.

The template does not set `exclusive: true` (`#SBATCH --exclusive`): the jobs share nodes with other
jobs, which starts them sooner on a busy machine, and the node timings are not clean. Add
`exclusive: true` to `default_conf` when other jobs on the nodes must not disturb the timings, and
expect a longer wait in the queue.

The second part is the **CRAB env block**, the `env` entries every job needs so that `crab worker`
runs. Each entry is exported in the job script as a plain `export` line, so `$PATH` is expanded
when the job runs, on the compute node:

| Entry | What it does |
|---|---|
| `PATH=<CRAB_DIR>/.venv/bin:$PATH` | Puts `crab` on the job's path. Without it, the job only finds `crab` if you launched from an activated shell and your site forwards the submitting environment to jobs. |
| `CRAB_ROOT=<CRAB_DIR>` | Tells the worker where its CRAB install is. |
| `CRAB_PATH_WRAPPERS=<CRAB_DIR>/wrappers` | Where the worker resolves wrapper paths such as `blink/alltoall/alltoall_b.py`. |
| `CRAB_SYSTEM=leonardo` | A label recorded with the results: the name of the system the job ran on. |
| `CRAB_IB_DEVICES`, `UCX_IB_SL`, `NCCL_IB_SL` | Leonardo's InfiniBand settings: the device list (`CRAB_IB_DEVICES` is read by the `ib_send_lat` wrapper) and the service level UCX and NCCL use. |
| `SLURM_CPU_BIND=verbose,socket` | How `srun` pins ranks: the input variable of `srun`'s `--cpu-bind` option (see [CPU pinning](#cpu-pinning-and-launch-settings)). |

How SbatchMan applies the file:

- The job script runs `module load` for the `modules`, then exports `SBATCHMAN_JOB_DIR` and the
  other variables SbatchMan sets, then the config's `env`, then the job's `preprocess` and
  `command`. The worker started by `command` therefore inherits all of it.
- A jobs file's `configs:` key makes every `sbatchman launch -f` read the configs file, and a
  relative path is relative to the jobs file. SbatchMan reloads the configs when the file, the
  variable files it references or a generated script changed; otherwise it prints `are unchanged;
  reusing existing configurations.` and keeps them. A dry run also writes the config templates, so
  the next real launch shows "unchanged; reusing". Every example jobs file starts with
  `configs: configs.leonardo.yaml`, so you never run `sbatchman configure`.
- The top-level cluster block of the file (`leonardo`) must match the cluster name, which comes
  from `sbatchman set-cluster-name` or, when it is set, from the `SBATCHMAN_CLUSTER_NAME`
  environment variable (the variable wins). When they differ, SbatchMan writes no config for the
  block and `sbatchman launch -f` stops with `Error: No configurations found for cluster '<name>'.`
  and exit code 1.
- A variable defined twice replaces the earlier value, it does not merge: if you add your own
  `PATH=...` entry, include everything you need in it.
- `bind` and `cpus` in a CRAB experiment are not supported on this line. A worker that finds
  one in an app exits at start with a message saying so. Binding comes from `SLURM_CPU_BIND`.
- `nodes` in the configs file is the Slurm allocation. CRAB's `ppn` in the experiment sets the
  ranks per node.

**Another configs file.** To use a different SbatchMan configs file (for example one your site or
group maintains, such as HICREST's), keep the config names `crab_run_{nodes}` and `crab_qe_{nodes}`
(the second only for the QE examples) and add the CRAB env entries above to them. Nothing else in
the example jobs files depends on the configs file.

#### Sizing the allocation for co-runs

When an experiment co-runs apps, the Slurm allocation has to hold all of them. What matters:

- `nodes` is the experiment's `numnodes` (in the examples' `global_options`), and it must cover
  every app. When the apps run on separate nodes, it is the sum of their nodes: the Blink noise
  examples give the victim and the aggressor half of the allocation each, so `nodes: 4` means 2
  nodes for the victim and 2 for the aggressor.
- `tasks_per_node` must be at least the experiment's `ppn` (the examples use 4). The template does
  not set `exclusive`, so the nodes are shared with other jobs; add `exclusive: true` when they
  must not be.
- `time` must cover all the experiments of the job together, run one after another. CRAB gives
  each experiment 1200 seconds by default.

CRAB's `ppn` in the experiment sets the ranks per node; the configs file only sets the number of
nodes. When apps share the same nodes (`allocation.mode: "shared"`),
[Ranks per node](../reference/configuration.md#ranks-per-node-ppn) gives the rule CRAB applies to
the ranks each node carries.

### 6. Write a campaign

A campaign is a SbatchMan jobs file: a YAML file that lists the jobs to submit. In each job,
`preprocess` writes CRAB's experiment as `config.json` into the job's directory, and `command`
runs `crab worker` there. The shape:

```yaml
configs: configs.leonardo.yaml # the file of step 5, relative to this jobs file
variables:
  nodes: [2, 4, 8, 16]         # SbatchMan runs the jobs once per value
jobs:
  - config: crab_run_{nodes}   # the config of step 5 with that node count
    tag: a2a-smoke-{nodes}     # names the job and its result folder
    preprocess: |
      mkdir -p "$SBATCHMAN_JOB_DIR"
      cat > "$SBATCHMAN_JOB_DIR/config.json" <<'JSON'
      { "schema_version": 2, "global_options": { ... }, "experiments": { "run": { ... } } }
      JSON
    command: crab worker --workdir "$SBATCHMAN_JOB_DIR"
```

`configs:` loads the configs file, `variables:` lists the values SbatchMan substitutes, and each
entry of `jobs:` names the config to submit with, a `tag`, the `preprocess` script that writes
the experiment, and the `command` that runs it. `crab worker --workdir "$SBATCHMAN_JOB_DIR"`
runs the experiment found in that directory. SbatchMan substitutes a `{var}` placeholder (here
`{nodes}`) in `tag`, `preprocess` and `command`. Its own `{EXP_DIR}` token is not substituted
there, so use the shell variable `$SBATCHMAN_JOB_DIR`, which SbatchMan exports into the job. The QE
files write their heredoc unquoted (`<<JSON`, not `<<'JSON'`) so the shell expands `$CRAB_ROOT`
inside the JSON.

One job can hold several experiments. Under `"experiments"` you can list as many as you like: CRAB
runs them one after another, in sorted name order, inside the single Slurm allocation the job got,
so they all run on the same nodes. This is how a baseline and a noise run share nodes: they are
experiments of the same job, not two jobs. Name the experiments so that the order you want is the
sorted order (`r1_baseline`, `r1_noise`, `r2_baseline`, ... run in exactly that order). A failing
experiment does not stop the ones after it. The job's walltime (`time` in the configs file of
[step 5](#5-get-the-sbatchman-configs-file)) must cover all of its experiments together.

The quickest start is to copy ready-made campaigns from `examples/sbatchman/` in the CRAB
checkout, next to your configs file:

```bash
cd <install dir>/campaigns
cp <install dir>/CRAB/examples/sbatchman/{smoke,blink-noise,qe-alone,qe-noise}.yaml .
```

`cd` enters the project directory of step 4. `cp` copies files into the current directory (`.`),
and the shell expands the braces into the four file names `smoke.yaml`, `blink-noise.yaml`,
`qe-alone.yaml` and `qe-noise.yaml`. Do not copy with `*.yaml`: that would also copy the
template `configs.leonardo.yaml` and overwrite the copy you edited in step 5. Copy the sweeps
(`a2a-vs-incast.yaml`, `a2a-vs-a2a.yaml`) the same way when you want them.

The folder has six jobs files and the configs file:

| File | What it does |
|---|---|
| `smoke.yaml` | One all-to-all (Blink `alltoall_b`) alone on 2 nodes, 4 ranks per node. Run it first. |
| `blink-noise.yaml` | The small co-run: one 4-node job, an all-to-all against continuous incast noise, 1 MiB messages. |
| `a2a-vs-incast.yaml` | The sweep: how much incast noise slows an all-to-all, 12 jobs (`nodes` 4, 8, 16 times `msgsize` 1 KiB, 64 KiB, 1 MiB, 4 MiB). |
| `a2a-vs-a2a.yaml` | The same sweep with all-to-all noise instead of incast. |
| `qe-alone.yaml` | Quantum ESPRESSO `pw.x` (a silicon SCF run) alone on 2 nodes: the reference run. Needs QE from [step 3](#3-build-or-register-the-benchmarks). |
| `qe-noise.yaml` | `pw.x` as the victim on 2 nodes against a continuous incast aggressor on 2 others, baseline and noise in one allocation. |
| `configs.leonardo.yaml` | The configs file of step 5. |

Every noise job runs six experiments in one allocation: `r1_baseline`, `r1_noise`, `r2_baseline`,
`r2_noise`, `r3_baseline` and `r3_noise`. The baseline experiments run only the victim; the noise
experiments run the victim next to the aggressor. The
[README](https://github.com/HLC-Lab/CRAB/blob/sbatchman/examples/sbatchman/README.md) explains each
file and what it measures, and it lists the cost of one job per `(nodes, msgsize)` pair: more
queue waits, and a different allocation for each message size. Edit the `args` of an app, the
`variables`, or the experiment, or write your own file in the same shape, keeping every `nodes`
value covered by a `crab_run_<nodes>` config. The `"experiments"` object is a normal CRAB config:
see the [configuration reference](../reference/configuration.md).

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

`-f smoke.yaml` names the jobs file. `--dry-run` lists the jobs SbatchMan would submit (`Would
generate <config> / <tag>`) and submits nothing. It also writes the config templates, but it renders
no run script. `squeue --me` lists your Slurm jobs. Launch the small `smoke.yaml` first: when it finishes
cleanly, your project, configs file and CRAB's worker work together. Then launch the others the
same way, one file at a time, a dry run first:

```bash
sbatchman launch -f blink-noise.yaml --dry-run
sbatchman launch -f blink-noise.yaml
sbatchman launch -f qe-alone.yaml --dry-run
sbatchman launch -f qe-alone.yaml
sbatchman launch -f qe-noise.yaml --dry-run
sbatchman launch -f qe-noise.yaml
```

A healthy launch prints a `Loading configs from file ...` line, then a `Configuration '<name>' saved
to ...` line for each config (or, when the configs are unchanged, one `are unchanged; reusing
existing configurations.` line), then a `Submitted <config> / <tag>` line for each job, and ends
with `Submitted successfully N jobs`. A launch that prints no `Loading configs` line did not read
the configs file, and its jobs would run with older settings: check `which sbatchman`
([step 1](#1-install-crab)) and see [troubleshooting](#troubleshooting). Read the summary count and
`sbatchman status`, never the exit code or a per-job line: the exit code is 0 even when no job was
submitted (see the `Socket timed out` row of the troubleshooting table).

The two QE files need Quantum ESPRESSO registered in [step 3](#3-build-or-register-the-benchmarks).
On Leonardo, one run of the example input takes about 18 s on 2 nodes with the GPU config, and
`qe-alone.yaml` ends in about 4 minutes. A sweep file you copied the same way launches 12 jobs, and each job runs six experiments, so size
`time` in the configs file for all six.

SbatchMan skips a job that matches one already in the project on cluster, config and tag (archived
jobs included, and a job in any status blocks, a failed one too), printing `Skipping job: An
identical job already exists`. The commands are compared only with `--compare-commands-in-dup-check`.
Our tags encode `nodes` and `msgsize` but not the other arguments, so editing `args` and
relaunching with the same tag is skipped until the tag changes or you use `--force`. To run a job
again, add `--force`, which skips the check: `sbatchman launch -f smoke.yaml --force`.
`sbatchman campaign` cannot force today, so a rerun of a campaign launches 0 jobs.

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

`<config>` is the SbatchMan config (for example `crab_run_8`) and `<tag>` is the job's tag. This is the
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
  a wrapper asks to keep are copied to `artifacts/run_<n>/app_<id>/`.

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
cluster. The cluster needs CRAB, SbatchMan, the benchmarks and the configs file first, as set up by steps
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
preset** to the config name `crab_run_{nodes}` of the configs file of
[step 5](#5-get-the-sbatchman-configs-file), with `nodes` as a sweep variable. Add an app the same way you would in a normal CRAB experiment: for `blink`, pick a
wrapper such as `blink/alltoall/alltoall_b.py` from the picker and set its args. Sweep values go in
variables and are used as `{name}` placeholders, including in numeric fields such as the node
count or an allocation's stride.

The preview panel lists anything that would make the campaign fail at launch: a variable without a
name, a `{placeholder}` no variable defines, a group without a preset, an invalid experiment, and
wrappers the connected cluster does not have. **Write files** stays disabled until that list is
empty.

Choose the connected cluster and click **Write files**. The page shows two paths:

- On the cluster, the file is `<Install dir>/CRAB/.web_staging/<campaign-slug>.yaml`. The file name
  (`<campaign-slug>`) is the campaign name in lower case, with every run of other characters
  replaced by `-` (`My A2A run` becomes `my-a2a-run.yaml`).
- On your laptop, a copy is kept in the dashboard's data folder, in `sbatchman_campaigns/`
  (`~/.local/share/crab/sbatchman_campaigns/` on Linux), as `<timestamp>-<campaign name>.yaml`.

The generator writes the jobs file only: it does not write the `configs:` key; add it by hand.
The configs file is the one of [step 5](#5-get-the-sbatchman-configs-file), and the **SbatchMan
preset** field of the dashboard is the name of a config in it: `crab_run_{nodes}` when the group
sweeps `nodes`. Before you launch the staged file, copy it into the project directory next to the
configs file and add the line `configs: configs.leonardo.yaml` at its top level (the shape of
[step 6](#6-write-a-campaign)), as the next section shows.

### Launch it

Launching is Track 1's [step 7](#7-launch-it), run on the cluster, with the path the page showed
you:

```bash
cd <install dir>/campaigns
cp <install dir>/CRAB/.web_staging/<campaign-slug>.yaml .
# edit <campaign-slug>.yaml: add the line  configs: configs.leonardo.yaml
sbatchman launch -f <campaign-slug>.yaml --dry-run
sbatchman launch -f <campaign-slug>.yaml
```

`cp` copies the staged file into the project directory, where `configs.leonardo.yaml` sits (a
relative `configs:` path is read from the jobs file's folder). The two `launch` lines are the
dry run and the real launch of step 7.

Then monitor and read results with steps 8 and 9.

## CPU pinning and launch settings

A CRAB worker started by SbatchMan has no CRAB execution preset: the job directory holds no
`execution.json` and the job sets no `CRAB_PRESET`, so the worker uses the Slurm scheduler and the
`srun` launcher with no extra flags. Its log says so:
`Execution settings from defaults (Slurm with srun; no execution.json, no CRAB_PRESET)`.

To pin processes to CPUs, set Slurm's own input variable in the `env` list of the configs file of
[step 5](#5-get-the-sbatchman-configs-file). The template sets it already:

```yaml
    env:
      - "SLURM_CPU_BIND=verbose,socket"
```

`SLURM_CPU_BIND` is the input variable Slurm documents for `srun`'s `--cpu-bind` option, so every
`srun` the worker starts binds as if `--cpu-bind=verbose,socket` had been given (`verbose` makes
`srun` report the binding it applied). Change the value to
change the binding, for example `cores`; see `man srun` for the full list. No CRAB code is
involved: the worker runs `srun --export=ALL`, which passes the job's environment on to each
launched process. A `bind` key in a CRAB experiment is not supported on this line (see
[step 5](#5-get-the-sbatchman-configs-file)).

The `CRAB_*` launch variables of older CRAB versions are no longer read: `CRAB_WL_MANAGER`,
`CRAB_SCHEDULER`, `CRAB_MPIRUN`, `CRAB_MPIRUN_ADDITIONAL_FLAGS`, `CRAB_MPIRUN_MAP_BY_NODE_FLAG`,
`CRAB_MPIRUN_HOSTNAMES_FLAG` and `CRAB_PINNING_FLAGS`. A worker that finds one in its environment
stops with an error that names the replacement, for example
`worker environment: CRAB_PINNING_FLAGS is no longer read; use launchers.srun.flags`. Remove them
from the `env` of your configs file and, when you want pinning, use `SLURM_CPU_BIND` as above.

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
| `crab: command not found` in a job's `stderr.log` | The job's shell cannot see the CRAB environment | Check that the configs file has the `PATH=<CRAB_DIR>/.venv/bin:$PATH` entry, with `<CRAB_DIR>` filled in ([step 5](#5-get-the-sbatchman-configs-file)), then relaunch with `--force` |
| `SbatchMan root not found. Please run 'sbatchman init'`, or a prompt offering to create a project | The command ran outside the project directory | `cd` into the directory where you ran `sbatchman init` ([step 4](#4-create-a-sbatchman-project)) |
| `Cluster name not set. Please run 'sbatchman set-cluster-name'.` | No cluster name in SbatchMan's global settings | `sbatchman set-cluster-name <cluster name>` |
| `wrapper blink/alltoall/alltoall_b.py not found on the wrapper search path (<folders>)` in the job log | `CRAB_PATH_WRAPPERS` is not set in the job | Check that the configs file has the `CRAB_PATH_WRAPPERS=<CRAB_DIR>/wrappers` entry ([step 5](#5-get-the-sbatchman-configs-file)), then relaunch with `--force`. [The troubleshooting entry](troubleshooting.md#wrapper-not-found-on-the-wrapper-search-path) explains the message |
| ``wrapper blink/alltoall/alltoall_b.py not found on the wrapper search path (<folders>). wrappers/ is missing: run `crab update` to clone the wrappers (https://github.com/HLC-Lab/crab-wrappers)`` in the job log, and `<install dir>/CRAB/wrappers/` is empty or missing | CRAB was installed with `pip install -e .` alone, which does not fetch the shared wrappers | Run `make` (or `crab update`) in the CRAB checkout |
| `Wrapper not found: <absolute path>` | The wrapper file is not in that CRAB checkout | Check the path, and that the cluster's CRAB is on the `sbatchman` branch and up to date (`crab update`) |
| `No binary for ... Tried: ...` in the job log | No config `binary`, receipt, or program on `PATH` for that wrapper | Build or register it with `crab setup`, add its module to `modules` in the configs file, or record it with `crab receipts set` ([step 3](#3-build-or-register-the-benchmarks)) |
| `PARSE FAILED  app <n>: ...` in the job log, and the run counted as failed | The wrapper could not read the application's output (the reason follows) | Compare the output with what the wrapper expects; fix the wrapper and check it with `crab parse` |
| `This config has schema_version <n>, but this CRAB reads up to <m>` | The campaign was written by a newer dashboard than the cluster's CRAB | `crab update` on the cluster |
| `crab update` stops with `Local edits to tracked files would be overwritten` | A tracked file in the checkout was changed by hand | Move the change to `local/` (presets, receipts, wrappers), or commit or stash it, then retry |
| `crab update` stops with `The presets moved to crab-wrappers in this version, and <file> is gone: put your changes as patches in local/presets/<name>.json, run `git checkout <file>`, then `crab update` again` | The checkout has local edits to a presets file (`<file>` is `config/presets.json` or `src/crab/presets.json`) that this release removed: the presets now live in crab-wrappers | Move the values you changed into `local/presets/<name>.json` as patches ([Your overrides](presets.md#your-overrides)), run `git checkout <file>`, then run `crab update` again |
| `crab update --head` stops with `CRAB is on release <tag>, not a branch: run `git checkout <branch>` in <dir>, then `crab update --head`` | A plain `crab update` left the checkout on a release tag, and `--head` needs a branch to follow | Run `git checkout sbatchman` in the CRAB checkout, then `crab update --head` |
| `crab update` stops with `The wrappers clone <dir> has local edits (<files>): commit or stash them (or keep private wrappers in local/wrappers), then run crab update again.` | A tracked file in the `wrappers/` clone was edited by hand | Commit or stash the edits, or move private wrappers to `local/wrappers/` ([Writing a wrapper](#writing-a-wrapper-for-your-own-application)), then run `crab update` again |
| `Worker fatal error: [Errno 2] No such file or directory: 'config.json'` in the job log | `SBATCHMAN_JOB_DIR` is empty in the job, or the job's `preprocess` did not write `config.json` there | Check that SbatchMan is 2.0.4 ([step 2](#2-install-sbatchman)) and that the jobs file's `preprocess` writes `$SBATCHMAN_JOB_DIR/config.json` as in [step 6](#6-write-a-campaign) |
| `worker environment: CRAB_... is no longer read; use ...` in the job log | The configs file still exports an old `CRAB_*` launch variable | Remove that entry from `env` in the configs file ([CPU pinning and launch settings](#cpu-pinning-and-launch-settings)) |
| Track 2: **Write files** is greyed out | The preview lists problems with the campaign, or no cluster is chosen | Fix each listed item; pick a connected cluster |
| "SSH support isn't installed in this environment" in the dashboard | The laptop install lacks the `web` extra | `pip install -e ".[web]"` from your CRAB checkout, then restart `crab web` |
| Timings vary between jobs of the same campaign, or `squeue` shows two of its jobs on the same nodes | The nodes are shared with other jobs (the template does not set `exclusive`) | Add `exclusive: true` to `default_conf` ([step 5](#5-get-the-sbatchman-configs-file)) and relaunch with `--force`; expect a longer queue wait |
| The worker exits at start with ``worker: experiment '<name>', app <n>: `bind` ...`` and `This is not supported when SbatchMan provides the environment` | An app in the experiment has a `bind` or `cpus` key, which this line does not support | Remove the key from the experiment in the jobs file. Binding comes from `SLURM_CPU_BIND` in the configs file ([CPU pinning and launch settings](#cpu-pinning-and-launch-settings)) |
| `error in Message Passing (mp) module ... error code 9100` from `pw.x` in the job log | The QE module on Leonardo is a GPU build, and the job had no GPU | Use the `crab_qe_{nodes}` config in the jobs file: it requests GPUs (`gpus: 4`) ([step 5](#5-get-the-sbatchman-configs-file)). Relaunch with `--force` |
| `Cannot find 'mpirun' to detect its MPI` in the job log | The config the job uses loads no MPI module, so the worker finds no `mpirun` on its `PATH` | Add the MPI module to that config's `modules` in the configs file ([step 5](#5-get-the-sbatchman-configs-file)), then relaunch with `--force` |
| `symbol lookup error` in `bindings_app_<n>.log` of an app, and the app exits with code 127 | The app shares the job with an app from another module stack (QE with HPC-X next to Blink with Open MPI), and the first stack's module variables (`OPAL_PREFIX`, `HPCX_MPI_HOME`) leaked into it | Start the app's receipt `pre_run` hook with `module purge` ([Apps built with different module stacks](#apps-built-with-different-module-stacks)), then relaunch with `--force` |
| Slurm reports the job `COMPLETED`, but an experiment folder has no `data_app_0.csv`, or an empty one | A failed run does not fail the job: the worker finishes and Slurm reports success (QE failures looked like this) | Check that each experiment folder holds a non-empty `data_app_0.csv` (for QE, a `0_wall_time_seconds` column). When it does not, read `bindings_app_0.log` and `error_app_0.log` in that experiment folder, and `stderr.log` and `stdout.log` of the job |
| The jobs keep old settings (for example an old `--qos` or `--exclusive`), and `sbatchman launch` printed no `Loading configs from file` line | A different `sbatchman` is first on the `PATH` (the shell has no CRAB environment active). An older one ignores the `configs:` line and reuses the configs it stored earlier | Activate the CRAB `.venv` (after `unset PYTHONPATH PYTHONHOME` if a Python module is loaded), check that `which sbatchman` prints a path inside it ([step 1](#1-install-crab)), then relaunch with `--force` |
| `sbatch: error: Batch job submission failed: Socket timed out on send/recv operation`, and the job's state is `FAILED_SUBMISSION` (`job_id: 0`) and `launch -f` printed `Failed to submit job: Job submission failed with error code N. ... Error stream: <sbatch's message>` instead of a `Submitted <config> / <tag>` line (the message is empty, `Failed to submit job: `, when `sbatch` wrote nothing to stderr) | A transient error of the Slurm controller. SbatchMan records the job as `FAILED_SUBMISSION` and does not count it in the summary | Read the `Failed to submit job:` lines and the summary count (`Submitted successfully N jobs`), check `sbatchman status`, and never rely on the exit code (it is 0 even when nothing was submitted). Run the same `sbatchman launch -f <file> --force` again: the failed record counts as an existing job |
| `Skipping job: An identical job already exists` and no job is submitted | SbatchMan found a job with the same cluster, config and tag in the project, in any status and archived or not (commands are compared only with `--compare-commands-in-dup-check`) | Add `--force` to `sbatchman launch -f <file>` ([step 7](#7-launch-it)), or change the tag. `sbatchman campaign` cannot force today |
| `invalid account` from `sbatch` when SbatchMan submits the job | `<YOUR_ACCOUNT>` is still in the configs file, or the account is not yours | Fill in your Slurm account in the configs file ([step 5](#5-get-the-sbatchman-configs-file)) |
| `Error: No configurations found for cluster '<name>'.` from `sbatchman launch -f`, exit code 1 | The cluster block name in the configs file differs from the cluster name (`sbatchman set-cluster-name`, or `SBATCHMAN_CLUSTER_NAME` when it is set), so SbatchMan wrote no config for it | Run `sbatchman set-cluster-name leonardo` (the block's name) in the project ([step 4](#4-create-a-sbatchman-project)), and unset `SBATCHMAN_CLUSTER_NAME` or set it to the block's name |
| The dashboard or `sbatchman visualize` will not start: port in use | Both use `8765`, and `sbatchman visualize` cannot change it | Start the dashboard on another port with `crab web --port <other port>` (see [step 8](#8-monitor)) |
