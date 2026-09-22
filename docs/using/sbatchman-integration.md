# Using CRAB with SbatchMan

CRAB and [SbatchMan](https://github.com/LorenzoPichetti/SbatchMan) are independent, standalone
tools with a fixed division of labor: CRAB downloads, builds, and co-runs benchmarks and parses
their output; SbatchMan owns scheduler configuration, job submission, monitoring, and results
visualization, end to end, through its own CLI/TUI. **CRAB's dashboard does not launch, monitor,
or visualize a SbatchMan campaign**: its only role in this flow is authoring a campaign and
generating the SbatchMan jobs file. See
[the roadmap's "Relationship with SbatchMan" section](../dev/roadmap.md#relationship-with-sbatchman)
for the full picture.

This page walks through the whole flow once, with CRAB's `blink` benchmark suite as the worked
example throughout. It assumes you already have a cluster account and, for the remote steps, SSH
access.

## Prerequisites

- A laptop to run CRAB's web dashboard on (any OS with Python 3.10+; no Slurm, no compilers, no
  build tooling needed here).
- A remote cluster with Slurm, where CRAB and SbatchMan both get installed and where benchmarks
  actually build and run.
- SSH access to that cluster (key or agent auth; see the dashboard's Remotes page for the
  supported auth methods).

## Install CRAB's dashboard on your laptop

The dashboard only needs the `web` extra: no build tooling, no Slurm.

```bash
git clone https://github.com/HLC-Lab/CRAB
cd CRAB
git checkout sbatchman
python -m venv .venv
source .venv/bin/activate
pip install -e ".[web]"
```

Then start it:

```bash
crab web
```

This opens the dashboard at `http://127.0.0.1:8765`. See the note on a port collision with
`sbatchman visualize` below before you run both at once.

## Install CRAB and SbatchMan on the remote cluster

On the cluster (over SSH), install CRAB the normal way (see
[Installation & benchmark setup](installation.md#install-the-framework)), checked out on the
**`sbatchman` branch**, the same branch the dashboard above is running from. Then install
SbatchMan **from GitHub `main`**, not from PyPI:

```bash
pip install git+https://github.com/LorenzoPichetti/SbatchMan.git
```

This matters: as of this writing, PyPI's SbatchMan release (`1.0.8`) predates a real fix on
`main` that this flow depends on (SbatchMan auto-exporting `SBATCHMAN_JOB_DIR` into the job's
environment, and its jobs-file `configs:` key actually being read). Installing from PyPI instead
will fail in ways this page doesn't cover. This page's steps were verified against `main` at
commit `18701e426d627d6bb7c5b8e4ef9fced533868345`; since `main` has no version pin, if something
here stops matching what you see, that is the first thing to check.

Then build the benchmark you'll run. For `blink`:

```bash
crab setup
```

and select the Blink suite (see
[Set up benchmarks](installation.md#set-up-benchmarks-crab-setup) for the wizard walkthrough).

## Connect the cluster from the dashboard

Back on your laptop, open the dashboard's **Remotes** page and add the cluster: give it a name,
pick transport `ssh`, fill in host/user/auth, and set **Install dir** to wherever you installed
CRAB above (CRAB is expected at `<install dir>/CRAB`). Save, then click **Connect**.

## Set up a SbatchMan preset for CRAB

CRAB's worker no longer writes its own environment file when running under SbatchMan: it
inherits whatever your SbatchMan preset already exports into the job's shell (see
[ADR-027](../dev/dashboard/decisions/adr-027-sbatchman-seam-v2.md) if you want the reasoning).
So before launching anything, create (once) a SbatchMan configuration that exports the four
variables CRAB's worker needs. On the cluster:

```bash
sbatchman configure slurm \
  --name crab_run \
  --env "CRAB_ROOT=/absolute/path/to/your/CRAB/install/CRAB" \
  --env "CRAB_SYSTEM=your_cluster_name" \
  --env "CRAB_PATH_WRAPPERS=/absolute/path/to/your/CRAB/install/CRAB/wrappers" \
  --env "SBATCHMAN_JOB_DIR={EXP_DIR}" \
  --partition your_partition \
  --account your_account
```

(Substitute your real paths, cluster name, partition, and account; see SbatchMan's own docs for
every `configure slurm` option beyond the four env vars above, which are CRAB-specific.)

`{EXP_DIR}` is SbatchMan's own per-job-directory substitution token. It only fires inside
`configure`-time fields like `--env`, **never** inside a campaign's own `command` or
`preprocess` text. If you ever write a SbatchMan jobs file by hand and put a token like
`{EXP_DIR}` directly in a `command:`, it will not be substituted; it survives as a literal
string. This is a permanent SbatchMan behavior, not specific to CRAB.

## Author a campaign

In the dashboard, open **SbatchMan** and build your campaign: give the group a tag, set its
**SbatchMan preset** to the name you configured above (`crab_run` in the example), and add an
app the same way you would in a normal CRAB experiment: for `blink`, pick a wrapper such as
`blink/a2a_b.py` from the picker and set its args.

Leave the **SbatchMan configs.yaml** field blank unless you specifically want SbatchMan's
jobs-file `configs:` key to auto-provision configurations from a bulk config-definition file (the
same format `sbatchman configure -f <file>` accepts). Pointing it at your project's own
`configs/configurations.yaml` registry (the file `sbatchman configure` itself writes to) does
not work: SbatchMan reads a non-empty `configs:` value as a *definition* file to re-provision
from, not a reference to the registry, and the registry's shape does not match, so it errors out.
An empty field (the default) skips that step entirely and just resolves your preset by name, as
in the example above.

Click **Write**. This saves the composed jobs YAML locally and pushes a copy to the connected
cluster's staging directory; the page shows both paths. The YAML's `preprocess` step now heredocs
only `config.json` (your experiment definition) into the job's working directory: no
`environment.json` is written or needed any more.

## Launch it

This is your own step, not a dashboard button: CRAB never runs `sbatchman launch` itself. On
the cluster, using the path the Write step showed you:

```bash
sbatchman launch -f /path/shown/by/the/dashboard/campaign.yaml
```

## Monitor and visualize

Use SbatchMan's own tooling for everything from here: `sbatchman status`, `sbatchman
campaign-tui`, or `sbatchman visualize`. CRAB's dashboard has no view into a SbatchMan campaign's
job status or results.

!!! warning "Port collision"
    Both `crab web` and `sbatchman visualize` default to port `8765`. If you want to run the CRAB
    dashboard and SbatchMan's visualizer at the same time, start one of them on a different port
    (`crab web --port <other port>`, or check `sbatchman visualize --help` for its own port
    option).

## Where results land

Each job's `config.json` names an experiment (its key under `experiments` in the campaign).
CRAB writes that experiment's parsed results to `<job working dir>/<experiment name>/`. A run
that collects metrics produces `data_app_<index>.csv` there (one file per app in the
experiment): for example, a group whose experiment is named `run` with a single app produces
`<job working dir>/run/data_app_0.csv`. The job working directory is whatever SbatchMan assigned
that job (`SBATCHMAN_JOB_DIR`/`{EXP_DIR}`), visible in SbatchMan's own job listing.

Not every app produces this file: a benchmark that collects no metrics (`blink`'s `null_dummy`,
for instance, an isolation sanity check with nothing to measure) never writes a CSV, by design.
That is expected, not a failure.

Reading that CSV is up to you: a spreadsheet, a notebook, or wiring it into `sbatchman
visualize`'s own parser script (its `--parser` option, `./parser.py` by default). CRAB's
dashboard does not read or plot it.
