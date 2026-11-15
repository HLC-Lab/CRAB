# Troubleshooting

Messages are quoted as the code writes them, and `<...>` stands for a value that changes. Where a
message is long, the first sentence is enough to find the entry.

## Before you submit: `crab validate`

Start every new config with `crab validate`:

```bash
crab validate -p <preset> config.json
```

It runs the checks `crab run` makes before it submits, without submitting: the config's
`schema_version`, the option values, the allocation, `ppn`, the launcher, and with `-p` the
config against the preset (scheduler, launcher, number of hosts). It exits 1 and prints each
problem to stderr, or prints `ok: <file>` followed by the warnings. See
[`crab validate`](../reference/cli.md#crab-validate). It checks that each app's wrapper `path`
names a file on this machine's wrapper search path (see
[a wrapper not on the search path](#wrapper-not-found-on-the-wrapper-search-path)). It does not
check that binaries or receipts exist, and it does not ask Slurm about your account or partition.
Those problems appear when the job starts.

## Where to look when a job fails

A job that was accepted and then failed leaves its output in the job folder, under
`<datapath>/<system>/<name>_<timestamp>/` ([Reading results](results.md#what-a-job-writes) lists
the rest of the folder):

- `slurm_output.log` and `slurm_error.log` hold the job's stdout and stderr. A local job writes
  files with the same names. `crab logs --data-dir <job folder>` prints the end of both.
- `<experiment>/error_app_<i>.log` holds the exit code, stdout and stderr of an app that exited
  non-zero. `crab logs --data-dir <job folder> --experiment <name>` prints them.

## Installation

### ImportError from a path outside `.venv`

A cluster's Python module often sets `PYTHONPATH`, which Python searches before the environment's
own packages. So `pip` or `crab` can fail with an `ImportError` from a path outside `.venv`. `make`
ignores `PYTHONPATH` and `PYTHONHOME`, and `source .venv/bin/activate` clears both in your shell
(`activate.fish` does the same). `deactivate` does not put them back: open a new shell, or load the
module again, to get the module's search path back. An environment from an older checkout gets the same lines the next time
you run `make` or `make core`, and the output says `added the PYTHONPATH reset to .venv/bin/activate`.

### A half-built environment

`argcomplete` is missing and `activate` has no completion lines. An earlier `make` failed halfway
(for example offline) and left a half-built environment, which `make` counts as an existing one.
Delete that environment's folder and run `make` again.

## Wrappers, binaries and receipts

### "wrappers/ is missing"

```text
wrapper <path> not found on the wrapper search path (<folder>, <folder>). wrappers/ is missing: run `<command>` to clone the wrappers (<repository URL>)
```

The wrappers are a separate repository, cloned next to CRAB. The message appears when the folder
is missing or holds only leftovers of an older copy. `<command>` is `crab update` on a git
checkout and `crab wrappers pull` on a pip or pipx install, which has no checkout to update.

Run the command the message names. Both clone the wrappers when the folder is missing, and both
need git and network access on that machine. A pip install tries the clone itself on first use and
says `Cloning the CRAB wrappers into <folder> (first use)`. With `CRAB_PATH_WRAPPERS` set, CRAB
prints no hint, because you chose the folders yourself.

### "wrapper <path> not found on the wrapper search path"

```text
wrapper <path> not found on the wrapper search path (<folder>, <folder>)
```

`crab validate` prints this as an error, and `crab run` stops with it before it submits anything,
when an app's `path` names no file on the wrapper search path. It can also appear in a job's logs
when the wrappers differ between the submitting machine and the compute node. The folders in
parentheses are the search path as this machine sees it: `local/wrappers/`
(`~/.config/crab/wrappers` on a pip install), then each folder of `CRAB_PATH_WRAPPERS`, or
`wrappers/` when that variable is unset. Nothing is looked up relative to the working directory, so a wrapper next to the config or in the
folder you launched from is not found. To fix it:

- Check the path against `crab wrappers list`, which prints every wrapper CRAB can see with its
  path relative to the wrappers folders, such as `blink/alltoall/alltoall_comm_only.py`.
- Put your wrapper in one of the folders, or write its absolute path, which is used as written. See
  [Where CRAB looks for wrappers](../extending/wrappers.md#where-crab-looks-for-wrappers).
- If the wrappers are on the cluster and not on this machine, validate there, or run `crab update`
  (`crab wrappers pull` on a pip install) to get them.

A `CRAB_PATH_WRAPPERS` set in the preset's `env` replaces the one in your shell, as it does in the
job. `crab validate` sees the preset's `env` only with `-p`. A path with a `{name}` placeholder is
skipped, because it is known only when the sweep expands. `crab worker` does not run this check up
front, but it gives the same rename message when it reaches the wrapper. When `wrappers/` is
missing, the message ends with the hint from [wrappers/ is missing](#wrappers-is-missing).

### A renamed, removed or moved wrapper

```text
wrapper <path> was renamed to <new path>: update the config
wrapper <path> was removed: use <replacement> with the burst flags (...); set "collect": false, "end": "f" for the app
wrapper <path> moved to <suite>/<group>/<name>: update the config
```

When `path` is an old Blink name, the "not found" message gets the first or second line as a second
sentence. Change `path` in the config to the new one. The removed `bursty_noise` wrappers are
replaced by the non-blocking `alltoall` and `incast` wrappers with burst flags, as the message
spells out.

A path of the old form `<suite>/<name>.py` still works when the wrapper moved into a group folder,
and gives the third line as a warning, where `<path>` is the old path. Change the config to the new
path.

### A missing binary

```text
No binary for <module> (app <n>). Tried: <the sources CRAB looked at>.
```

The experiment stops before its first run because CRAB found no executable for that app. The
message lists the sources it tried, in order: the app's `binary` key in the config, the receipt
(or a line saying the wrapper has no `benchmark_id`), and the wrapper's `executable` on `PATH` (or
a line saying it declares none). Fix one of them:

- add a `binary` key next to `path` in the app's config entry;
- record the binary with `crab setup`, or for a program you installed yourself with
  `crab receipts set <id> --binary <path>` (see [`crab receipts set`](../reference/cli.md#crab-receipts-set));
- make the wrapper's `executable` available on `PATH` in the job, for example with a
  `module load` in the preset's `header`.

`crab wrappers list` shows, for each wrapper, which of the three sources gives its binary, or
`missing`.

A receipt, or a default path built into the wrapper, counts only when the file exists. When it
does not, the second item of the message names the path:

```text
No binary for <module> (app <n>). Tried: the app's 'binary' key in the config; receipt or wrapper default '<path>' (does not exist); '<executable>' on PATH.
```

The program was never built there, or was moved or deleted. Build it (`crab setup`), point the
receipt at the real file with `crab receipts set <id> --binary <path>`, or set the app's `binary`
key. A wrapper with no such path says `receipt '<id>'` or `no receipt (the wrapper has no
benchmark_id)` in that place. A receipt that holds a relative path, for example one written by an
older CRAB, is not used: `crab wrappers list` shows it as `missing`, so register the binary again.

### A receipt that cannot be read

```text
crab: receipt <file> is not valid JSON (<parser error>). Fix or delete the file, or register the binary again.
crab: cannot read receipt <file>: <reason>
crab: receipt <file> must hold a JSON object, got <type>
```

A receipt is `local/receipts/<id>.json` (`~/.config/crab/receipts/<id>.json` on a pip install).
When CRAB finds the file but cannot read it, the command stops with exit status 1, names the file
in one of these messages and leaves the file as it is. Repair the file, or delete it, then record
the binary again with `crab receipts set` or `crab setup`.

## Presets and configs

### "No preset selected"

```text
No preset selected. Pass -p <preset> or set CRAB_PRESET (use -p local to run without Slurm).
```

`crab run` never picks a preset for you, so a forgotten `-p` cannot run a benchmark on a login
node. Pass `-p <name>`, set `CRAB_PRESET`, or put the name in a `.env` file in CRAB's home (the
repository root in a checkout, `~/.local/share/crab` or `$CRAB_HOME` in a pip install, not the
folder you run `crab` from). See [Selecting your preset](presets.md#selecting-your-preset).

### Preset not found

```text
The preset '<name>' was not found in the shared presets (<folders>) or your overrides (<folder>). No shared presets folder exists: run `crab update` to fetch crab-wrappers.
```

No shared preset file and no override file defines that name. The message gives the folders it
searched. If it ends with the `crab update` sentence, the crab-wrappers clone is missing or has no
`presets/` folder (a clone left on a tag from before the presets moved looks the same): run
`crab update`. Otherwise check the spelling against `crab info`, which lists the presets CRAB can
see. To add your own, put it in a `.json` file in your overrides folder (`local/presets/` on a
checkout, `~/.config/crab/presets/` on an install). See [Your overrides](presets.md#your-overrides)
and [where presets are read from](../reference/presets.md#where-presets-are-read-from).

A presets file that exists but is damaged stops CRAB with `<file> is not valid JSON: <error>` or
`<file> must contain a JSON object of presets.` The other refusals name the file too: a name
defined twice, an `extends` that does not exist or loops, and an override with no shared preset to
patch.

### Your presets file from before 1.2 is refused

```text
<file>: single-file preset overrides were removed in CRAB 1.2. Move your presets into <folder>/ (any *.json name); a preset with a shared name now patches it instead of replacing it. See the Presets page of the docs.
```

`~/.config/crab/presets.json` and `local/presets.json` are no longer read. Move what is in them
into a `.json` file in the folder the message names. A preset with the name of a shared one now
patches it, so a copy of a whole shared preset can shrink to the few values you changed. See
[Your overrides](presets.md#your-overrides).

The same stop happens when `CRAB_PATH_PRESETS` names a file instead of a folder. The message starts
with that path and says it must be a folder of `*.json` override files: move the file into a folder
and point `CRAB_PATH_PRESETS` at it.

### The preset needs your own value

```text
Preset '<name>' needs your own value for <YOUR_...>. Add this to a .json file in <folder>/ (merge it into your entry for '<name>' if you already have one), replacing each <YOUR_...>:
{ ... }
```

The shared preset has a mark, such as `--account=<YOUR_ACCOUNT>`, that only you can fill in.
`crab run`, `crab validate` and `crab nodes` stop until it is set. Paste the printed JSON into a
file in the folder the message names and replace each `<YOUR_...>` with your value.
`crab presets show <name>` prints the same snippet, the resolved preset and where each value came
from.

### A config from a newer CRAB

```text
This config has schema_version <n>, but this CRAB reads up to <m>. Run `crab update` on this machine.
```

A newer CRAB or dashboard wrote the config, for example a config saved in the dashboard on your
laptop and submitted to a cluster whose CRAB is older. "This machine" is the one where the message
appears: run `crab update` there. See [Keep it up to date](installation.md#keep-it-up-to-date).

## Updating: `crab update` stops

`crab update` checks everything before it changes anything. When it stops, it prints
`crab: update stopped:` followed by the reason. [`crab update`](../reference/cli.md#crab-update)
describes each mode.

| Message | Cause | Fix |
|---|---|---|
| `Local edits to tracked files would be overwritten: <files>. ...` | You edited files that belong to CRAB. | Move per-machine changes to `local/` (presets, receipts, wrappers), or commit or stash them, then run `crab update` again. |
| `The presets moved to crab-wrappers in this version, and <file> is gone: ...` | `<file>` is `config/presets.json` or `src/crab/presets.json`, an old presets file with your edits. | Do what the message says: put your changes as patches in `local/presets/<name>.json`, run `git checkout <file>`, then `crab update` again. See [Your overrides](presets.md#your-overrides). |
| ``CRAB is on release <tag>, not a branch: run `git checkout <branch>` in <folder>, then `crab update --head` `` | `--head` follows a branch, and the checkout sits on a release tag (a plain `crab update` leaves it there). | Check out a branch first, or run `crab update` without `--head`. |
| `The wrappers clone <folder> has local edits (<files>): ...`, or `wrappers pull stopped: <folder> has local edits to tracked files: <files>; ...` from `crab wrappers pull` | You edited a file inside the wrappers clone. | Commit or stash the edit, or move a private wrapper to `local/wrappers`. |
| `<folder> is not a git checkout; reinstall CRAB with git to update it.` | The install was copied without its git history. | Reinstall by cloning the repository. |
| `CRAB is now at <version>; the wrappers step failed: <reason>` | CRAB moved, but the wrappers clone could not. | Fix the reason (often no network), then run `crab update` again. |

A checkout from v0.4.0 or older needs `crab update` twice. The first run still executes the old
update code, which does not know how to fetch the wrappers. The second run uses the new code and
clones them.

## Slurm

### The job is refused at submit

```text
sbatch refused the job (exit <n>): <Slurm's reason>
```

`crab run` prints this (the dashboard shows it as the submit error) when `sbatch` rejects the job.
The reason is Slurm's own text. The usual causes are an account or partition that Slurm does not
accept, a time limit over the partition's limit, or a QOS that does not exist.

CRAB refuses an unset `--account=<YOUR_ACCOUNT>` mark before it calls `sbatch` (see
[The preset needs your own value](#the-preset-needs-your-own-value)), so a refusal here means the
value you set is wrong. Set your real account in an override file (see
[Your overrides](presets.md#your-overrides)), or set partitions, accounts and time limits per config
with [`sbatch_directives`](../reference/configuration.md#sbatch-directives). `sacctmgr show assoc
user=$USER` and `sinfo` show what your account can use. CRAB also refuses the directives it sets
itself (`--nodes`, `--ntasks-per-node`, `--ntasks`) and names `numnodes` or `ppn` instead.

### The job was accepted and then failed

When `sbatch` accepts the job and it then ends `FAILED`, the reason is in the logs (see
[Where to look when a job fails](#where-to-look-when-a-job-fails)): a wrapper or binary that is
missing on the compute node, a module that did not load, or an MPI that CRAB cannot use. A failure
of one experiment is logged as `Experiment <name> failed: <reason>`, and the job ends failed after
the other experiments have run.

## mpirun

```text
Cannot tell which MPI this mpirun is: its --version output was '<first lines>'. Set launchers.mpirun.command to an Open MPI or MPICH mpirun, or use srun.
```

With the default `"dialect": "auto"`, CRAB runs `<mpirun> --version` when the job starts its first
app and picks the command-line syntax from the answer. It supports Open MPI 4 and 5 and MPICH
(Hydra). The message appears in the job's logs, and `crab_run.json` keeps it under
`execution.mpirun.probe_error`. Related messages are `Intel MPI is not supported.`,
`unsupported Open MPI version <version>: need 4.x or 5.x.` and `Cannot find '<command>' to detect
its MPI.`

To fix it, point `launchers.mpirun.command` at an Open MPI or MPICH `mpirun`, set
`launchers.mpirun.dialect` to `"openmpi4"`, `"openmpi5"` or `"hydra"` to skip the probe, or use
the `srun` launcher. See [The mpirun dialects](running-without-slurm.md#the-mpirun-dialects).

## The web dashboard

### The port is in use

```text
[ERROR] Could not start the server on <host>:<port>: <error>
        Another instance may be running, or the port is taken. Try: crab web --port <other>
```

Another `crab web` (or another program) holds the port, 8899 by default. Stop the other instance,
or start on another port with `crab web --port <other-port>`. `CRAB_WEB_PORT` sets the default.

### Connect is slow

While **Connect** works, a line under the cluster shows the step, what it waits for and the
seconds waited, such as `12 s waited, gives up after 30 s`. A growing counter means the dashboard
is waiting, not stuck.

| Step | What it does | When it gives up |
|---|---|---|
| `Connecting to the cluster` | The SSH connection and login. A login node that rotates between addresses, or a certificate login, can take several seconds. | At 30 s, with `Timed out connecting to <host>:<port> after <seconds>s.` (see [Connect fails](#connect-fails)). |
| `Checking CRAB on the cluster` | Runs `crab info --json` in a login shell. A slow shell start-up (a long `.bashrc`, a `module load`) shows here. | At 30 s. The cluster is not left connected. |
| `Installing CRAB on the cluster` | Can run for several minutes. | After 10 minutes. |

If the line stays on one step until it gives up, test the same step by hand: `ssh <user>@<host>`
for the first, then `crab info --json` in a login shell on the cluster for the second.

### Connect fails

The reason shows under the cluster, exactly as the dashboard got it. The common ones:

| Message | Cause and fix |
|---|---|
| `No SSH agent found (SSH_AUTH_SOCK is unset).` | The profile uses `agent` auth and `crab web` has no agent. Start `ssh-agent`, load your key or certificate, and start `crab web` again from that same shell. |
| `Authentication was rejected by the cluster.` | The login failed, for instance because a certificate expired. Log in again into the same agent, then reconnect. |
| `This cluster needs a password; none was provided.` | The profile uses `password` auth. Type the password in the box next to Connect. |
| `Auth method 'key' selected but no key_path is set.` | Fill in `Key path` in the profile. |
| `Host key verification failed.` | The login node's host key is not in `known_hosts`, or the cluster rotates keys. Check the key, or set the profile's `Host key` to `insecure` for rotating login nodes. |
| `Timed out connecting to <host>:<port> after <seconds>s.` | The login node did not answer within 30 seconds: it is down, overloaded or unreachable from your network. Try again, or test `ssh` by hand. |
| `Could not connect to <host>:<port>.` | The host name or port is wrong, or a firewall blocks it. The detail line has the system's error. |

See [Connect a cluster](dashboard.md#connect-a-cluster).

### "Update crab on <cluster>"

```text
Update crab on <cluster> (crab update) to see this.
```

The Wrappers page shows this when the cluster's CRAB is older than the dashboard and lacks the
`crab wrappers` command. Run `crab update` on the cluster, then reconnect. The two orange warnings
about a version mismatch are in [Version skew](dashboard.md#version-skew).

### CRAB is a pipx install on the cluster

The dashboard looks for a checkout in `<Install dir>/CRAB`. A cluster where CRAB was installed with
pipx has none, so after Connect the profile shows
`CRAB is not installed on this cluster (looked in <install dir>/CRAB)`, or a command fails with a
message that starts like this:

```text
Could not parse JSON from the cluster's `crab` output
```

Run `which crab` on the cluster, put that path in the profile's `Crab command (optional)` field,
for example `~/.local/bin/crab`, save and reconnect. The field takes one executable with no
spaces. See [A cluster where CRAB is a pipx install](dashboard.md#a-cluster-where-crab-is-a-pipx-install)
for what the field changes, and for the line the profile shows when the command does not answer.
