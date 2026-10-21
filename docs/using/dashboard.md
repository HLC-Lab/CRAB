# The web dashboard

The web dashboard (`crab web`) is a page in your browser for running CRAB on your clusters. It runs
on your laptop and connects to each cluster over SSH. On the cluster it runs that cluster's own
`crab` command and reads the answer, so the cluster's CRAB decides what is valid and what happens.
The dashboard keeps its own files on the laptop (see [Where the dashboard keeps its
files](#where-the-dashboard-keeps-its-files)). On a cluster it writes only what `crab` writes, the
config it stages for each submission, and the CRAB install when you ask for one.

The top bar has five sections, in the order you use them: **Remotes** (connect clusters),
**Author** (write experiments), **Wrappers** (see what CRAB can run), **Jobs** (submit and watch),
and **Results** (read and compare data). The bar at the bottom shows `backend ok`, the CRAB version
and the API version. The button at the right of the top bar switches between a dark and a light
theme, and the browser remembers your choice.

## Start it

Install the `web` extra, then start the dashboard (see [Installation](installation.md)):

```bash
pip install -e ".[web]"
crab web
```

If the extra is missing, `crab web` offers to install it into the active environment and asks for a
`y` first.

`crab web` starts a server on this laptop and opens `http://127.0.0.1:8765` in your default
browser. It prints the address and runs until you stop it.

| Flag | Meaning |
|---|---|
| `--host HOST` | The address to bind. The default is `127.0.0.1`. |
| `--port PORT` | The port. The default is `8765`. The environment variable `CRAB_WEB_PORT` sets the default, and the flag wins. |
| `--no-browser` | Do not open a browser. Open the address yourself. |
| `-v`, `--verbose` | Log startup and every request. The default prints only warnings and errors. |

When the port is taken, `crab web` says so and exits. Start it on another port with
`crab web --port 8899`.

Stop it with `Ctrl-C` in the terminal that runs it. Closing the browser tab does not stop it.
Cluster connections close when it stops, and you connect again the next time.

### The session token

Every request the page makes to the dashboard's API carries a token. `crab web` makes a new random
token each time it starts and puts it in the page it serves. The token and a check of the `Host`
and `Origin` headers together stop other web pages open in your browser from driving your SSH
connections. The server answers API requests only when they are addressed to `127.0.0.1`, `localhost` or `::1`, so
`--host` does not make the dashboard reachable from another machine.

After you restart `crab web`, an old tab has the old token. Its requests fail with `Missing or
wrong session token. Reload the dashboard tab to pick up the current session.` Reload the tab.

## Connect a cluster

The **Remotes** page lists your cluster profiles. A profile is how to reach one cluster. It holds
no password and no key, only the name of the method.

Click **+ Add cluster**, fill in the form and click **Save**. The form has these fields:

| Field | What to enter |
|---|---|
| `Name` | A name for this cluster in the dashboard. Use letters, digits, `.`, `_` and `-`. The name `local` is reserved for this laptop. It also names the folder of this cluster's cached results. |
| `Transport` | `ssh` (the default) reaches a cluster over SSH. `local` drives the CRAB checkout on this laptop, for example to try the `local` preset. A `local` profile has only `Name`, `Transport` and `Preset`. |
| `Host` | The login node, for example `login.hpc.example.org`. |
| `User` | Your user name on the cluster. |
| `Port` | The SSH port. The default is `22`. |
| `Auth` | How to log in: `agent (ssh-agent / cert)`, `key file` or `password`. See below. |
| `Key path` | Shown for `key file`: the private key, for example `~/.ssh/id_ed25519`. |
| `Host key` | `strict` checks the cluster's host key against your `~/.ssh/known_hosts`. `insecure (rotating login nodes)` skips the check. Use `insecure` only for a cluster whose login nodes present a different host key on each connection. The profile then shows `hostkey:insecure` as a reminder. |
| `Install dir` | The folder on the cluster that holds CRAB. The default is `~`. CRAB lives in a `CRAB` folder inside it, and the field shows the result, for example `CRAB goes in ~/CRAB`. |
| `Crab command (optional)` | One executable that runs `crab` on the cluster, for example `~/.local/bin/crab` for a CRAB installed with pipx. Leave it empty for the CRAB checkout in `Install dir`. See [A cluster where CRAB is a pipx install](#a-cluster-where-crab-is-a-pipx-install). |
| `Preset` | The [preset](../glossary.md#preset) the Submit window starts with for this cluster. Empty means the first preset the cluster reports. |

<!-- screenshot: remotes-add-cluster -->

The three `Auth` methods:

- **`agent`** uses your running `ssh-agent`, which covers keys you loaded and certificates from a
  login tool such as `step-cli`. Start `crab web` from the shell that has the agent, because the
  dashboard reads `SSH_AUTH_SOCK` from its environment. Without it, Connect fails and says so.
- **`key file`** reads the private key at `Key path`.
- **`password`** shows a `password` box next to Connect. The dashboard keeps the password in memory
  for that connection and never writes it down.

The pencil button edits a profile and is disabled while the cluster is connected. The bin button
removes it after a confirmation. Removing a profile deletes only the profile on this laptop.

### Connect and the state of a cluster

Click **Connect**. The dashboard opens an SSH connection and runs `crab info` on the cluster. A
cluster is connected when its dot is green and the button says **Disconnect**. The line under the
name shows the CRAB version there, the install folder and the presets the cluster defines.

<!-- screenshot: remotes-connected -->

If the connection fails, the reason shows under the profile exactly as the dashboard got it. The
common ones are an agent that is not running, a rejected login (an expired certificate, for
example), a host key that does not verify (set `Host key` to `insecure` for a cluster with rotating
login nodes), and a login node that does not answer within the time limit.

A dropped connection is noticed on the next request. Connect again.

### CRAB is not installed there

When the login works but the cluster has no CRAB, the profile shows `CRAB is not installed on this
cluster (looked in <install dir>/CRAB)`, with the steps of the install and an **Install CRAB**
button. The install runs in one login shell on the cluster:

1. Clone the CRAB repository into the `CRAB` folder under `Install dir`.
2. Run `make venv`, then `pip install -e .`, then `crab update --to-release`. The last command
   moves the clone to the newest release when one exists.

The text box `Pre-commands (optional, run once before installing)` takes one command per line.
They run first, in the same shell. Use it for what the cluster needs before Python is usable, for
example `module load python`. Click **Install CRAB** and wait: the clone and build can take a few
minutes. When the install ends, the dashboard checks that CRAB answers. If it does not, the output
of the install stays on the page with its exit code, and the error says what failed. **Reload
steps** shows the steps again.

The guided install does not set up benchmarks. Do that on the cluster with `crab setup` (see
[Installation](installation.md#set-up-benchmarks-crab-setup)), or import a binary from the
[Wrappers page](dashboard-wrappers.md#needs-attention).

### A cluster where CRAB is a pipx install

When CRAB on the cluster was installed with pipx (see
[Install with pip or pipx](installation.md#install-with-pip-or-pipx)), there is no `CRAB` folder
to find. Put the command that starts it in `Crab command (optional)`, for example
`~/.local/bin/crab`, and save. The dashboard then runs that command directly:

- It does not go to `<Install dir>/CRAB` or activate a virtual environment. The lines of
  `remote_setup` still run first.
- The `CRAB goes in ...` hint under `Install dir` disappears, and the **Install CRAB** panel is
  never offered. If the command does not answer, the profile shows `This cluster runs <command>
  directly. You manage that install (pipx install crab-hpc).` Fix the install on the cluster, then
  reconnect.
- The value must be one path or name with no spaces: the form and the backend both refuse a value
  with whitespace. A leading `~` is expanded on the cluster.

Clear the field to go back to the checkout in `Install dir`. The connected line then shows the
CRAB home of that install, for example `~/.local/share/crab`.

### Version skew

The dashboard and the cluster's CRAB must speak the same version of the command output. When they
do not, the profile shows one of two lines in orange:

| Message | What to do |
|---|---|
| `This cluster's CRAB is older than the dashboard. Run crab update on the cluster, then reconnect.` | Update CRAB on the cluster. |
| `This cluster's CRAB is newer than the dashboard. Run crab update on this laptop, then restart crab web.` | Update CRAB on the laptop. |

### Commands the dashboard runs on the cluster

On every command, the dashboard opens a login shell on the cluster, runs the lines of the profile's
`remote_setup` list, goes to `<Install dir>/CRAB`, activates its `.venv` and runs `crab`. With a
`Crab command` set, it runs that command instead of the last three steps. A cluster
that needs `module load` lines before CRAB works can have them in `clusters.json`, in the
`remote_setup` list of its profile, for example `"remote_setup": ["module load python"]`. The form
does not show that field. Edit the file, then reconnect.

## Write an experiment

The **Author** page is an editor for an experiment config, which the dashboard calls a use case.
It writes the same JSON that `crab run` reads (see the [configuration
reference](../reference/configuration.md)), so anything you build here also runs from the command
line.

<!-- screenshot: author-overview -->

The bar at the top of the page:

| Button | What it does |
|---|---|
| **+ New** | Starts an empty use case. When you have unsaved changes it asks `Discard unsaved changes?` first. |
| **Browse…** | Opens the library of saved use cases. Search by name, click one to open it. The icons on a row duplicate or delete that use case. A row shows its number of experiments and when it was last updated. |
| **Save** | Saves the use case to the library under its name. With no name, it asks for one. |
| **Submit…** | Submits the open use case to a cluster (see [Run and watch it](#run-and-watch-it)). It is disabled until the use case has a name. |
| **{ } JSON** | Shows or hides the config as JSON, with **Copy**. **Load from file…** imports a config file from your computer as a new, unsaved draft. |

Submit sends the use case as it stands in the editor. You do not have to save first.

The left rail has two groups. **Setup** holds the settings of the whole use case, and
**Experiments** lists its [experiments](../glossary.md#experiment). The `+` next to
`Experiments` adds one. Hover a row to duplicate or remove it.

### Setup

- **Basics.** `Name` names the run and prefixes its output folder. `Nodes` is the total number of
  nodes to allocate. `Processes per node` is the ppn. The `cluster nodes` link opens a read-only
  list of the Slurm partitions of the cluster you picked under `Wrappers from`, to help size
  `Nodes`.
- **Node allocation.** By default all nodes run one workload. `+ divide the nodes` splits the
  nodes into named slices. Drag a divider or type a percentage to size them, and `+ add a slice`
  adds more. Then pick how the slices are placed: `linear` (a block each), `interleaved` (with
  `every N node(s)`), `random` (with a `seed`) or `shared` (every app on all nodes, with `cores`
  `disjoint` or `shared`). `shared` is available only while there are no slices. The strip under
  the bar is an illustration. The exact assignment happens at run time. For what each mode means,
  see the [configuration reference](../reference/configuration.md#allocation-fields).
- **Run settings.** `Convergence` has `Min runs`, `Max runs`, `Timeout (s)`, `Converge all`,
  `Alpha` and `Beta` (see [Convergence](../glossary.md#convergence)). `Output` has `Format`,
  `Retain files` and `Tags`. `Advanced` has `Extra info`, `Walltime`, `Data path`, `Launcher` and
  `Replace the preset's flags`. `Slurm directives` takes one `#SBATCH` directive per line. CRAB sets
  the node and task counts itself, so `--nodes`, `--ntasks-per-node` and `--ntasks` are refused.
  A blank field is left out of the config, and the placeholder shows the default CRAB then uses.

A dot next to `Node allocation` or `Run settings` in the rail means something is set there.

### An experiment

Click an experiment in the rail. It has:

- `Experiment name` and `Description`.
- **Placement.** The experiment uses the global allocation. `Override for this experiment` gives it
  its own, and `Use global allocation` goes back. The badge says `inherited` or `override`.
- **Override run settings.** Change convergence, output and advanced settings for this experiment
  only. A field left on `inherit` or blank uses the global value.
- The flow diagram: apps that start together sit in a column, and an arrow runs from an app to
  each app that starts after it. Colors are node groups.
- The apps, one card each. `+ Add app` adds a card. The bin icon removes one.

### Pick a wrapper

Each app runs a [wrapper](../glossary.md#wrapper). Click `Choose wrapper…` on the card to open the
picker. It lists the wrappers by suite, and the search box matches the name, the path and the
suite. A tag on each row says where the wrapper exists: `host` (this laptop), `remote` (the
cluster) or `host + remote`. `unloadable` means the wrapper could not be introspected. Its path
still works.

`Wrappers from` above the apps picks which connected cluster supplies the list. With no cluster
connected, the picker shows this laptop's wrappers only. To use a path that is in none of the lists,
type it in the search box and click `+ Add "<path>"`. A warning sign on a card means the wrapper is
on this laptop but not on the chosen cluster, so a run there may fail until the wrapper is
there. `About this wrapper` opens the [Wrappers page](dashboard-wrappers.md) on it.

### The rest of an app card

| Field | Meaning |
|---|---|
| `args` | The arguments passed to the app. |
| node group | When the allocation has slices, the slice this app runs on. An app that starts after another and names no group runs on that app's nodes. |
| `collect metrics` | Parse and store this app's metrics. Turn it off for an app that only generates load. |
| `Starts` | `at start`, `after a delay` (`Delay (s)`) or `after another app` (`After`). |
| `Ends` | `runs to completion`, `stops when the others finish` or `stops after N seconds` (`Seconds`). |
| `CPUs` | The cores the app runs on, such as `0-15,32`. Empty means all cores. |
| `Processes per node` | Empty means the experiment's value. |
| `Environment` | `+ Add variable` adds a `NAME` and `value` pair set for this app. |

### Check and save

The strip under the bar tells you whether the config is ready. It says `Ready to submit`, or
`N thing(s) to fix`, and a click opens the list. The checks are about the shape of the config, for
example:

- `Number of nodes is required.`
- `Add at least one experiment.`
- `Duplicate experiment name "<name>".`
- `<experiment> · app #0: wrapper path is required.`
- `<experiment> · app #1: "after" must reference another app.`
- `<experiment> · app #0: partition "<name>" is not a defined node group.`

These messages do not block anything. **Save** works with them open, and the cluster checks the
config again when you submit. When the server sees a shape problem on save, it saves anyway and
shows `Saved "<name>" with a shape warning: ...`.

<!-- screenshot: author-experiment -->

## Run and watch it

### Submit

**Submit…** on the Author page, or **+ New submission** on the Jobs page, opens the `Submit job`
window:

| Field | What to choose |
|---|---|
| `Remote` | One of the connected clusters. With none connected, the window says so and points you to Remotes. |
| `Config` | Shown on the Jobs page: a saved use case from the library. From Author, the open use case is submitted. |
| `Preset` | The preset to run with, chosen from the ones the cluster reports. It starts at the profile's `Preset`, else the cluster's first. For a cluster whose presets are unknown, type the name. |

The preset is chosen here and is not stored in the use case, so the same use case runs on any
cluster. Under the fields, the window says what the preset runs with (`Runs with: Slurm, srun` or
`Runs with: local scheduler, mpirun, hosts ...`). It also lists problems it can see already, such
as a launcher the preset cannot run, and states that the cluster checks again at submit.

Click **Submit**. The dashboard stages the config on the cluster, in
`<Install dir>/CRAB/.web_staging`, and runs `crab run` there. The window closes when the
dashboard accepts the request. The submission then shows on the Jobs
page as a card with `Submitting…`. When `crab run` fails, the card says `Failed`, with the cluster's
error. Read it, fix the use case and submit again. **Dismiss** removes the card.

### Jobs

The **Jobs** page lists the submissions you made from this dashboard, newest first. Jobs you start
with `crab run` in a terminal do not appear here (they appear under Results).

<!-- screenshot: jobs-list -->

Each card shows the cluster and system, the submit time, the use case name, the job id and the
state. The dot says whether the cluster is connected. For a cluster that is not, the card shows
the last state the dashboard knew.

States come from `squeue` and `sacct` through `crab status`, or from the local scheduler for the
`local` preset. `COMPLETED` is green. `FAILED`, `TIMEOUT`, `OUT_OF_MEMORY`, `NODE_FAIL`,
`BOOT_FAIL`, `DEADLINE` and `LOST` are red. `CANCELLED` and `REVOKED` are grey. `UNKNOWN` is
orange: the scheduler no longer knows the job and the history has nothing for it yet. Anything else,
such as `PENDING` and `RUNNING`, is live.

The page refreshes itself. `Auto-refresh` turns that on or off, the menu next to it sets the
interval (5, 10, 30 or 60 seconds), and **Refresh** refreshes now. A banner `Could not refresh
<cluster>, showing its last known job states` appears when a cluster does not answer. Use the
search box and the `Cluster` and `Status` chips to filter the list.

The buttons on a card:

| Button | What it does |
|---|---|
| **Logs** / **Hide logs** | Shows the job's Slurm output and error logs, kept up to date while the job runs. Long logs show only their most recent part. When the cluster is unreachable, the dashboard shows the last copy it saved and says when that was. |
| **View config** | Shows the exact config the job was submitted with, read-only, with **Copy**. |
| **Rerun** | Only on a failed job. Asks to confirm, then submits the same config to the same cluster as a new job. |
| **Cancel** | Only on a job that has not finished. Asks to confirm, then cancels the job on the cluster. |

Click the card anywhere else to open the job.

### One job

The job page lists every experiment of that submission, with its status, its number of nodes and
ppn, and the apps it ran. A line such as `3/10 runs failed` means the experiment kept its
[convergence](../glossary.md#convergence) loop going and collected data from the runs that worked.
**Logs** on an experiment shows the error log of each app that failed, and says so when nothing
failed.

To run only part of the job again:

- **Rerun N failed experiment(s)** runs exactly the failed experiments, after a confirmation.
- **Select experiments to rerun…** shows a checkbox on each experiment. Tick some and click
  **Rerun selected**.

A rerun is always a new job on the same cluster with the same config. When it covers only some
experiments, only those run. The new job records where it came from. Its page says `Rerun of
<use case> submitted <time> · reran: <experiments>`, and the original job's page ends with a
`Reruns` list, each with its state, so you can follow a chain of retries from either end.

Two links lead on. **View full history for this use case** opens the report. **View results** opens
the job in Results.

### The use case report

The report lists every run of one use case across all connected clusters and over time, grouped by
submission, with the newest group open. It reads each cluster's `crab history`, so it includes runs
you started from the command line. A cluster that is not connected is named in a banner and skipped.
Each group has **Rerun N failed**, and the same selection mode as the job page. Selected
experiments must come from one submission. Runs without a record in this dashboard show `logs
unavailable` and cannot be rerun here.

## Read results

The **Results** page has two tabs, `Jobs` and `Compare`. **Refresh** on the right reloads the list
from the clusters.

### Pick a job

The list holds every job that a connected cluster's `crab history` reports, whether you submitted
it here or in a terminal. A cluster you are not connected to still shows the jobs the dashboard has
history or fetched results for. Search by job name, and filter by `Cluster` and by status:

| Status | Meaning |
|---|---|
| `Not fetched yet` | The data is still on the cluster. **Fetch** downloads it. |
| `Up to date` | You fetched it, and the job has not changed since. |
| `Possibly stale` | You fetched it, but the job's status changed since, or its cluster is not connected. **Refetch** inside the job updates it. |

Fetching copies the job's result folder, the CSV files, to this laptop. The dashboard fetches
only when you ask. Click a card to open the job.

<!-- screenshot: results-picker -->

### One job's results

The job page has a tree on the left and a viewer on the right. Open an experiment in the tree and
click one of its apps. A note such as `3/10 runs failed` marks an experiment where some runs
failed. If nothing is fetched yet, **Fetch results** starts the download. **Refetch** downloads
the job again.

The viewer has two views, `Chart` and `Table`.

**Chart.** Pick the kind: `Scatter`, `Line`, `Bar` or `Violin`. `X` and `Y` list the numeric
columns of the app's data. The default `X` is a size or sweep column when there is one, and the
default `Y` is a time-like column when there is one. `Log scale` is available for scatter and line. **Export image**
saves the chart as a PNG file of 1600 by 900 pixels.

**Table.** Click a column header to sort, type in `Search…` to filter rows, and use `Columns` to
choose which columns show. The table shows up to 5000 rows and says so when it cuts the list.

<!-- screenshot: results-chart -->

**Clear cache** at the top of the page deletes every fetched result on this laptop. The cache size
shows next to it. The data stays on the clusters, and you can fetch it again.

### Compare

The `Compare` tab puts apps from different jobs on one chart, including jobs from different
clusters. The tree on the left lists the jobs you fetched. Open a job and tick the apps you want.
Each ticked app is one series, labeled `job / experiment / app`.

- `Overlay` draws all series in one chart. `Small multiples` draws one chart per series, all on the
  same Y range, so the charts are comparable.
- `X` and `Y` offer the numeric columns that every ticked series has. When there are none, the page
  says `The selected series have no numeric column in common, pick a different combination.`
- The chart kinds and `Log scale` work as in a single job.

Compare has no export. To share data, use `crab export` (see [Reading results](results.md)).

<!-- screenshot: results-compare -->

## Wrappers

The **Wrappers** page shows which wrappers CRAB can see on this laptop and on each connected
cluster, whether their copies are current, and what each one measures. It also fixes a missing
binary and pulls updates. It is covered on its own page: [Wrappers in the web
dashboard](dashboard-wrappers.md).

## Where the dashboard keeps its files

Everything is on your laptop, in the standard folders of your system. On Linux they are:

| Path | Holds |
|---|---|
| `~/.config/crab/clusters.json` | Your cluster profiles. No passwords or keys. |
| `~/.local/share/crab/experiments/` | The use case library, one JSON file per use case. |
| `~/.local/share/crab/jobs.json` | The jobs you submitted, with the config each one ran, and the reruns. |
| `~/.local/share/crab/cache/` | Copies of logs and history, shown with a notice when a cluster is unreachable. |
| `~/.local/share/crab/results_cache/` | The results you fetched, one folder per cluster, system and job. |
| `~/.local/share/crab/logs/web.log` | The dashboard's own log. |

These variables, set before `crab web`, move them:

| Variable | Moves |
|---|---|
| `CRAB_WEB_CONFIG_DIR` | The folder with `clusters.json`. |
| `CRAB_WEB_DATA_DIR` | The data folder: library, jobs, caches and log. |
| `CRAB_WEB_LIBRARY_DIR` | Only the library. Point it at a git repository or a synced folder to version or share your use cases. The first start copies the existing library there. |
| `CRAB_WEB_PORT` | The default port. |

To reset part of it, stop `crab web` and delete what you want gone:

- Fetched results: **Clear cache** on the Results page, or delete `results_cache/`.
- Cached logs and history: delete `cache/`.
- The job list: delete `jobs.json`. The jobs keep running on the clusters, and Results still lists
  them. You lose their config snapshots and rerun links here.
- Cluster profiles: delete `clusters.json`.
- Everything: delete the config folder and the data folder. Back up `experiments/` first, since it
  holds your use cases.

The next start creates the folders again. Your theme choice is not in these folders. The browser
keeps it.
