# Troubleshooting

This page lists the failures people hit most, each with the message CRAB prints, the usual cause
and the fix. Messages are quoted as the code writes them; `<...>` stands for a value that changes.
Where a message is long, the first sentence is enough to find the entry.

Start every new config with `crab validate`, and read the job's logs when a job fails after it was
submitted.

## Before you submit: `crab validate`

```bash
crab validate -p <preset> config.json
```

It runs the checks `crab run` makes before it submits, without submitting: the config's
`schema_version`, the option values, the allocation, `ppn`, the launcher, and with `-p` the
config against the preset (scheduler, launcher, number of hosts). It exits 1 and prints each
problem to stderr, or prints `ok: <file>` followed by the warnings. See
[`crab validate`](../reference/cli.md#crab-validate).

It checks that each app's wrapper `path` names a file on this machine's wrapper search path (see
[a wrapper not on the search path](#wrapper-not-found-on-the-wrapper-search-path)). It does not
check that binaries or receipts exist, and it does not ask Slurm about your account or partition.
Those problems appear when the job starts, in the entries below.

## Where to look when a job fails

A job that was accepted and then failed leaves its output in the job folder, under
`<datapath>/<system>/<name>_<timestamp>/`:

- `slurm_output.log` and `slurm_error.log` hold the job's stdout and stderr. A local job writes
  files with the same names. `crab logs --data-dir <job folder>` prints the end of both.
- `<experiment>/error_app_<i>.log` holds the exit code, stdout and stderr of an app that exited
  non-zero. `crab logs --data-dir <job folder> --experiment <name>` prints them.

[Reading results](results.md#what-a-job-writes) lists the rest of the folder.

## Wrappers, binaries and receipts

### "wrappers/ is missing"

```text
wrapper <path> not found on the wrapper search path (<folder>, <folder>). wrappers/ is missing: run `<command>` to clone the wrappers (<repository URL>)
```

The wrappers are a separate repository, cloned next to CRAB. This message appears when the
folder is missing or holds only leftovers of an older copy. On a git checkout `<command>` is
`crab update`. On a pip or pipx install it is `crab wrappers pull`, because there is no checkout
to update.

Run the command the message names. Both clone the wrappers when the folder is missing. They need
git and network access on that machine; a pip install tries the clone itself on first use and
says `Cloning the CRAB wrappers into <folder> (first use)`. If you set `CRAB_PATH_WRAPPERS`, CRAB
does not print the hint, because you chose the folders yourself.

### "wrapper <path> not found on the wrapper search path"

```text
wrapper <path> not found on the wrapper search path (<folder>, <folder>)
```

`crab validate` prints this as an error, and `crab run` stops with it before it submits anything,
when an app's `path` names no file on the wrapper search path. The folders in parentheses are the
search path as this machine sees it: `local/wrappers/` first (`~/.config/crab/wrappers` on a pip
install), then each folder of `CRAB_PATH_WRAPPERS`, or `wrappers/` when that variable is unset.
Nothing is looked up relative to the working directory, so a wrapper next to the config or in the
folder you launched from is not found: put it in one of those folders, or write its absolute path.
The same message can appear in a job's logs when the wrappers differ between the submitting machine
and the compute node. A path that is still an old
Blink name gets a second sentence with the fix:

```text
wrapper <path> was renamed to <new path>: update the config
wrapper <path> was removed: use <replacement> with the burst flags (...); set "collect": false, "end": "f" for the app
```

Change `path` in the config to the new one. The removed `bursty_noise` wrappers are replaced by
the non-blocking `alltoall` and `incast` wrappers with burst flags, as the message spells out. For
any other path, check it against `crab wrappers list`. When `wrappers/` is missing, the message
ends with the hint from the entry above.

The check uses this machine's search path, and a `CRAB_PATH_WRAPPERS` set in the preset's `env`
replaces the one in your shell, as it does in the job. `crab validate` sees the preset's `env`
only with `-p`. If the wrappers are on the cluster and not on this machine, validate there, or run
`crab update` (or `crab wrappers pull` on a pip install) to get the wrappers. A path with a
`{name}` placeholder is skipped, because it is known only when the sweep expands. `crab worker`
does not run this check up front, but it gives the same rename message when it reaches the
wrapper.

Check the path against `crab wrappers list`, which prints every wrapper CRAB can see with its path
relative to the wrappers folders. Paths are relative to the folder, such as
`blink/alltoall/alltoall_comm_only.py`. An absolute path is used as written. The search order is
in [Where CRAB looks for wrappers](../extending/wrappers.md#where-crab-looks-for-wrappers).

A path of the old form `<suite>/<name>.py` still works when the wrapper moved into a group folder,
with this warning, where `<path>` is the old path:

```text
wrapper <path> moved to <suite>/<group>/<name>: update the config
```

Change the config to the new path.

### A missing binary

```text
No binary for <module> (app <n>). Tried: <the sources CRAB looked at>.
```

The experiment stops before its first run because CRAB found no executable for that app. The
message lists the sources it tried, in order: the app's `binary` key in the config, the receipt
(or a note that the wrapper has no `benchmark_id`), and the wrapper's `executable` on `PATH` (or a
note that it declares none). Fix one of them:

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
benchmark_id)` in that place instead.

### A receipt that cannot be read

```text
crab: receipt <file> is not valid JSON (<parser error>). Fix or delete the file, or register the binary again.
crab: cannot read receipt <file>: <reason>
crab: receipt <file> must hold a JSON object, got <type>
```

A receipt is `local/receipts/<id>.json` (`~/.config/crab/receipts/<id>.json` on a pip install).
When CRAB finds the file but cannot read it, the command stops with exit status 1 and one of these
messages, and it leaves the file as it is. The message names the file. Repair that file, or delete
it, then record the binary again with `crab receipts set` or `crab setup`.

## Presets and configs

### "No preset selected"

```text
No preset selected. Pass -p <preset> or set CRAB_PRESET (use -p local to run without Slurm).
```

`crab run` never picks a preset for you, so a forgotten `-p` cannot run a benchmark on a login
node. Pass `-p <name>`, set `CRAB_PRESET`, or put the name in a `.env` file in the working
directory. See [Selecting your preset](presets.md#selecting-your-preset).

### Preset not found

```text
The preset '<name>' was not found in the shared presets (<folders>) or your overrides (<folder>). No shared presets folder exists: run `crab update` to fetch crab-wrappers.
```

No shared preset file and no override file defines that name. The message gives the folders it
searched, so you can open them. If it ends with the `crab update` sentence, the crab-wrappers
clone is missing or has no `presets/` folder (a clone left on a tag from before the presets moved
looks the same): run `crab update`. Otherwise check the spelling
against `crab info`, which lists the presets CRAB can see. To add your own, put it in a `.json`
file in your overrides folder (`local/presets/` on a checkout, `~/.config/crab/presets/` on an
install). See [Your overrides](presets.md#your-overrides) and
[where presets are read from](../reference/presets.md#where-presets-are-read-from).

A presets file that exists but is damaged stops CRAB with `<file> is not valid JSON: <error>` or
`<file> must contain a JSON object of presets.`, and the message names the file. The other
refusals name the file too: a name defined twice, an `extends` that does not exist or loops, and
an override with no shared preset to patch.

### Your presets file from before 1.2 is refused

```text
<file>: single-file preset overrides were removed in CRAB 1.2. Move your presets into <folder>/ (any *.json name); a preset with a shared name now patches it instead of replacing it. See the Presets page of the docs.
```

`~/.config/crab/presets.json` and `local/presets.json` are no longer read. Move what is in them
into a `.json` file in the folder the message names. A preset that has the name of a shared one
now patches it, so a copy of a whole shared preset can shrink to the few values you changed. See
[Your overrides](presets.md#your-overrides).

The same stop happens when `CRAB_PATH_PRESETS` names a file instead of a folder: the message starts
with that path and says it must be a folder of `*.json` override files. Move the file into a folder
and point `CRAB_PATH_PRESETS` at the folder.

### The preset needs your own value

```text
Preset '<name>' needs your own value for <YOUR_...>. Add this to a .json file in <folder>/ (merge it into your entry for '<name>' if you already have one), replacing each <YOUR_...>:
{ ... }
```

The shared preset has a mark, such as `--account=<YOUR_ACCOUNT>`, that only you can fill in.
`crab run`, `crab validate` and `crab nodes` stop until it is set. Paste the printed JSON into a
file in the folder the message names and replace each `<YOUR_...>` with your value.
`crab presets show <name>` prints the same snippet, the resolved preset and the file each value
came from.

### A config from a newer CRAB

```text
This config has schema_version <n>, but this CRAB reads up to <m>. Run `crab update` on this machine.
```

The config was written by a newer CRAB or dashboard than the one that is reading it, for example a
config saved in the dashboard on your laptop and submitted to a cluster whose CRAB is older. "This
machine" is the one where the message appears. Run `crab update` there. See
[Keep it up to date](installation.md#keep-it-up-to-date).

## Updating: `crab update` stops

`crab update` checks everything before it changes anything, and prints `crab: update stopped:`
followed by the reason. [`crab update`](../reference/cli.md#crab-update) describes each mode.

Local edits in the checkout:

```text
Local edits to tracked files would be overwritten: <files>. Move per-machine changes to local/ (presets, receipts, wrappers), or commit or stash them, then retry.
```

You edited files that belong to CRAB. Move per-machine changes to `local/`, or commit or stash
them, then run `crab update` again.

Edits to an old presets file, which moved into crab-wrappers:

```text
The presets moved to crab-wrappers in this version, and <file> is gone: put your changes as patches in local/presets/<name>.json, run `git checkout <file>`, then `crab update` again
```

`<file>` is `config/presets.json` or `src/crab/presets.json`. Do what the message says. See
[Your overrides](presets.md#your-overrides).

`--head` on a release tag:

```text
CRAB is on release <tag>, not a branch: run `git checkout <branch>` in <folder>, then `crab update --head`
```

`--head` follows a branch, and the checkout sits on a release tag (a plain `crab update` leaves it
there). Check out a branch first, or run `crab update` without `--head`.

Edits in the wrappers clone:

```text
The wrappers clone <folder> has local edits (<files>): commit or stash them (or keep private wrappers in local/wrappers), then run crab update again.
```

You edited a file inside the wrappers clone. Commit or stash the edit, or move a private wrapper
to `local/wrappers`.

Two more:

- `<folder> is not a git checkout; reinstall CRAB with git to update it.` The install was copied
  without its git history. Reinstall by cloning the repository.
- `CRAB is now at <version>; the wrappers step failed: <reason>` CRAB moved, but the wrappers
  clone could not. Fix the reason (often no network), then run `crab update` again.

`crab wrappers pull` has its own refusal for edits in the wrappers clone: `wrappers pull stopped:
<folder> has local edits to tracked files: <files>; commit, stash or move them to local/wrappers,
then pull again`.

On a pip or pipx install `crab update` leaves CRAB itself alone and prints
`pipx upgrade crab-hpc`; run that to upgrade the program.

## Slurm

### The job is refused at submit

```text
sbatch refused the job (exit <n>): <Slurm's reason>
```

This is printed by `crab run` (the dashboard shows it as the submit error) when `sbatch` rejects
the job, and the reason is Slurm's own text. The usual causes are an account or partition that
Slurm does not accept, a time limit above the partition's limit, or a QOS that does not exist.

A shared preset with an account holds the mark `--account=<YOUR_ACCOUNT>`, and CRAB refuses it
before `sbatch` is called (see [The preset needs your own value](#the-preset-needs-your-own-value)),
so a refusal here means the value you set is wrong: set your real account in an override file
(see [Your overrides](presets.md#your-overrides)). Partitions, accounts and time limits can also be set
per config with
[`sbatch_directives`](../reference/configuration.md#sbatch-directives). Slurm tools such as
`sacctmgr show assoc user=$USER` and `sinfo` show what your account may use.

CRAB also refuses directives it sets itself (`--nodes`, `--ntasks-per-node`, `--ntasks`). The
message names `numnodes` or `ppn` instead.

### The job was accepted and then failed

When `sbatch` accepts the job and it then ends `FAILED`, the reason is in the job folder, in
`slurm_error.log` and `slurm_output.log`: a wrapper or binary that is missing on the compute
node, a module that did not load, or an MPI that CRAB cannot use. Read them with
`crab logs --data-dir <job folder>`. A failure of one experiment is logged as
`Experiment <name> failed: <reason>`, and the job ends failed after the other experiments
have run.

## mpirun

```text
Cannot tell which MPI this mpirun is: its --version output was '<first lines>'. Set launchers.mpirun.command to an Open MPI or MPICH mpirun, or use srun.
```

With the default `"dialect": "auto"`, CRAB runs `<mpirun> --version` when the job starts its first
app and picks the command-line syntax from the answer. It supports Open MPI 4 and 5 and MPICH
(Hydra). The message appears in the job's logs, and `crab_run.json` keeps it under
`execution.mpirun.probe_error`. Related messages say what was found: `Intel MPI is not supported.`,
`unsupported Open MPI version <version>: need 4.x or 5.x.`, and `Cannot find '<command>' to detect
its MPI.`

Point `launchers.mpirun.command` at an Open MPI or MPICH `mpirun`, set
`launchers.mpirun.dialect` to `"openmpi4"`, `"openmpi5"` or `"hydra"` to skip the probe, or use
the `srun` launcher. See [The mpirun dialects](running-without-slurm.md#the-mpirun-dialects).

## The web dashboard

### The port is in use

```text
[ERROR] Could not start the server on <host>:<port>: <error>
        Another instance may be running, or the port is taken. Try: crab web --port <other>
```

Another `crab web` (or another program) holds port 8765. Stop the other instance, or start on
another port with `crab web --port 8899`. `CRAB_WEB_PORT` sets the default.

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
`crab wrappers` command. Run `crab update` on the cluster, then reconnect.

A related warning, in orange under the cluster, is `This cluster's CRAB is older than the
dashboard. Run crab update on the cluster, then reconnect.` Its mirror, `This cluster's CRAB is
newer than the dashboard. Run crab update on this laptop, then restart crab web.`, means the
laptop is the old side. Both come from a version mismatch of the `--json` output the dashboard
reads. See [Version skew](dashboard.md#version-skew).

### CRAB is a pipx install on the cluster

The dashboard looks for a checkout in `<Install dir>/CRAB` and activates its virtual environment.
A cluster where CRAB was installed with pipx has neither, so after Connect the profile shows
`CRAB is not installed on this cluster (looked in <install dir>/CRAB)`, or a command fails with a
message that starts like this:

```text
Could not parse JSON from the cluster's `crab` output
```

Run `which crab` on the cluster, put that path in the profile's `Crab command (optional)` field,
for example `~/.local/bin/crab`, save and reconnect. The field takes one executable with no
spaces. Once it is set, the dashboard never offers the guided install, and if the command does
not answer the profile shows this line:

```text
This cluster runs `<command>` directly. You manage that install (pipx install crab-hpc).
```

Fix the install on the cluster in that case. See
[A cluster where CRAB is a pipx install](dashboard.md#a-cluster-where-crab-is-a-pipx-install).
