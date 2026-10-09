# Running without Slurm

A preset with `"scheduler": "local"` runs the job on the machine where you type `crab run`, with
no Slurm involved. Use it to try CRAB on a laptop or workstation, or to run on a few machines
that you reach over `ssh`. The preset still comes from `config/presets.json` or
`local/presets.json`; see [Configuring your cluster](presets.md) for where presets live and how
one is selected.

CRAB never picks a local preset on its own: ask for it with `-p`.

## A local preset

The shipped `local` preset is the smallest one:

```json
"local": {
    "description": "Local (no Slurm, dev/testing only)",
    "scheduler": "local",
    "launcher": "mpirun"
}
```

The job runs on the preset's `hosts`, which default to `localhost` alone. This preset lists two
workstations:

```json
"lab": {
    "description": "Two lab workstations",
    "scheduler": "local",
    "hosts": ["ws01:16", "ws02:16"],
    "exclusive": true,
    "launcher": "mpirun",
    "launchers": { "mpirun": { "command": "/opt/openmpi/bin/mpirun" } }
}
```

| Field | Meaning |
|-------|---------|
| `hosts` | A list of `"name"` or `"name:cores"`. Only `localhost` may omit its core count. The job uses the first `numnodes` hosts, in order; a config that asks for more nodes than the preset has hosts is refused. |
| `hostfile` | A path (absolute, or starting with `~`) instead of `hosts`. A line is `h`, `h:N` or `h slots=N`, and `#` starts a comment. Give `hosts` or `hostfile`, not both. |
| `exclusive` | `true` (the default) keeps other CRAB jobs off the job's hosts while it runs; see [Exclusive hosts](#exclusive-hosts). |
| `launcher` | `"mpirun"` or `"direct"`. The default is `"direct"`. `"srun"` needs Slurm and is refused here. |

The core count of a host is what CRAB uses to split cores between applications that share a host
(see [Shared nodes](../reference/configuration.md#shared-nodes)). A bare `localhost` counts as
this machine's CPU count.

The launcher decides how each application starts:

- `mpirun` starts each application with `mpirun`, which has to be installed. CRAB reads
  `mpirun --version` to learn which MPI it is; see [The mpirun dialects](#the-mpirun-dialects).
- `direct` runs each application's command as is, with no MPI and no wrapper around it. It is for
  a single process only: `ppn` 1 and `numnodes` 1.

The [presets.json format](../reference/presets.md#execution-fields) lists every field.

Run an experiment with the preset like any other:

```bash
crab run examples/my_experiment.json -p lab
```

The job starts in the background and `crab run` returns, as it does after an `sbatch`. The job
survives the end of your shell. Its output files are the same as on a cluster, and its two log
files are named like Slurm's, `slurm_output.log` and `slurm_error.log`, in the run's data
directory.

!!! note "Slurm directives do not apply"
    `sbatch_directives` in the config are ignored under a local preset, and CRAB warns about
    them. A preset's `header` lines are not run either; see
    [Module flow](presets.md#module-flow).

## Several hosts

With more than one host, `mpirun` starts the ranks on the other hosts over `ssh`. The hosts
therefore need passwordless `ssh` between them, and the same software at the same paths: CRAB
and your benchmarks' binaries, with the same MPI installation. CRAB forwards the environment
variables the remote ranks need, as [the presets reference](../reference/presets.md#the-mpirun-launcher)
describes.

## Job ids, state and logs

A local job has the id `local-<n>`, where `n` counts up for each submit. A Slurm job id is all
digits, and `crab status` and `crab cancel` tell the two apart by their shape.

CRAB keeps the state of local jobs in your user's state directory, outside the checkout:
`~/.local/state/crab/` (or `crab/` under `$XDG_STATE_HOME`, when that is set to an absolute path).

- `jobs/local-<n>.json` is the job's record: its id, the host that submitted it, its data
  directory and the process that supervises it. `jobs/counter` holds the last number used.
- `jobs/local-<n>.lock` is held for as long as the job's supervisor is alive.
- `hosts/<name>.lock` holds a host for an [exclusive](#exclusive-hosts) job.

Each job also writes `local_state.json` and, when it ends, `local_exit_code` in its own data
directory, next to its logs.

The record holds the name of the host that submitted the job, so a job can only be queried or
cancelled from that host. Asking from another machine is an error that names both.

## Checking and cancelling a job

`crab status` takes one or more job ids and `crab cancel` takes one:

```bash
crab status local-3 local-4
```

```text
  local-3      RUNNING      (local)
  local-4      PENDING      (local)
```

```bash
crab cancel local-3
```

`crab nodes -p <preset>` shows what a preset can use. For a local preset it lists the hosts and
their core counts, and never runs `sinfo`:

```bash
crab nodes -p lab
```

```text
hosts:
  ws01               cores=16
  ws02               cores=16
```

See the [CLI reference](../reference/cli.md) for the `--json` forms.

Cancelling sends the job's supervisor a termination signal and returns at once. The supervisor
passes it on to the worker, which stops every application (a termination signal, then a kill
after 10 seconds for any that remain), so the job reads `CANCELLED` some seconds later. A job
that is still waiting for its hosts is cancelled at once. Cancelling a job that has finished does
nothing and says so, and so does an id with no record (`no such local job`).

### States

| State | Meaning |
|-------|---------|
| `PENDING` | Submitted, and waiting: for the hosts of an exclusive job, or for the job to start. |
| `RUNNING` | The worker is running the experiments. |
| `COMPLETED` | The worker exited with code 0. |
| `FAILED` | The worker exited with another code. |
| `CANCELLED` | The job was cancelled. |
| `TIMEOUT` | The job ran out of walltime and was stopped. |
| `LOST` | The job's supervisor died without recording how the job ended, and no exit code exists. See [Logging out](#logging-out). |

The names are Slurm's, so a script can treat both schedulers alike. A local id that has no
record, such as one that was never submitted on this machine, reads `UNKNOWN`. For a finished
job `crab status --json` also gives its `exit_code`.

## Walltime

A local job has a time limit like a Slurm job. CRAB reads it from the config's
`global_options.walltime`, which defaults to `00:10:00` (ten minutes), in the formats of Slurm's
`sbatch --time`:

| Format | Example | Meaning |
|--------|---------|---------|
| `M` | `90` | minutes |
| `M:S` | `30:15` | minutes and seconds |
| `H:M:S` | `02:00:00` | hours, minutes, seconds |
| `D-H` | `1-12` | days and hours |
| `D-H:M` | `1-12:30` | days, hours, minutes |
| `D-H:M:S` | `1-12:30:15` | days, hours, minutes, seconds |

Minutes and seconds must be below 60 after the first field. A walltime that is zero, not in one of
these formats, or `UNLIMITED` or `INFINITE` is refused when the job is submitted: a local job
needs a finite limit.

The clock starts when the job is `RUNNING`, so time spent waiting for hosts does not count. When
it runs out, CRAB stops the job the same way a cancel does, and the job ends `TIMEOUT`. A
`--time` in `sbatch_directives` has no effect here: set `walltime`.

## Exclusive hosts

With `exclusive: true`, the default, a job holds a lock on each of its hosts while it runs. A
second local job that needs one of those hosts waits in `PENDING` until the first one ends, so
two measurements never disturb each other. Jobs on different hosts run side by side. Set
`"exclusive": false` to skip the locks, for instance when a host has room for two small jobs.

The locks are files in the state directory of the machine that submits the job. They keep apart
the jobs submitted from that machine by that user. A job submitted from another machine, or by
another user, does not see them, and neither does any program that is not CRAB.

## Logging out

A local job is a background process on the machine where you submitted it, and it outlives the
shell. On a machine where systemd has `KillUserProcesses` enabled, though, logging out ends every
process of your session, and the job's supervisor with it. The worker notices that its supervisor
is gone and stops its applications, and the job reads `LOST`.

To keep jobs running after you log out, enable lingering for your user, once, on that machine:

```bash
loginctl enable-linger <user>
```

## The mpirun dialects

Open MPI 4, Open MPI 5 and MPICH's Hydra take different command-line syntax for hosts, binding
and forwarded variables. With the default `"dialect": "auto"`, CRAB runs `<mpirun> --version`
once and picks the matching syntax:

| `mpirun --version` shows | Dialect |
|---|---|
| Open MPI 4.x | `openmpi4` |
| Open MPI 5.x | `openmpi5` |
| `HYDRA build details` (MPICH) | `hydra` |

The probe runs when the job starts its first application. Anything else makes that launch fail,
and the error in the job's log says what was found:

- Intel MPI is refused. It is Hydra-based, but CRAB does not support it.
- An Open MPI older than 4 is refused.
- An `mpirun` that only prints that it wants `srun` (an Open MPI built without its own launcher)
  is refused.
- An `mpirun` that cannot be found, does not answer within 10 seconds or prints something else is
  refused.

Set `launchers.mpirun.command` to another `mpirun`, or `launchers.mpirun.dialect` to
`"openmpi4"`, `"openmpi5"` or `"hydra"` to skip the probe. The
[presets reference](../reference/presets.md#the-mpirun-launcher) gives the command line each dialect
produces.

A local job started inside a Slurm allocation (for example after `salloc`) does not see it: CRAB
removes every `SLURM*` variable from the job's environment, so the MPI uses the hosts of the
preset and not the allocation.

## What a run records about its launches

For every application started with `mpirun`, CRAB asks `mpirun` to report how it bound the ranks
to cores, and keeps the report in `<experiment dir>/bindings_app_<id>.log`, where the experiment
dir is `<run data dir>/<experiment name>/` and `<id>` is the application's number. Each run of
the experiment appends to it, under a `# run <n>` line. Because the file lives in the experiment
directory and not in a run directory, it stays even when `retain_files` is `false`. Use it to
check that the applications sit on the cores you meant.

The job's `crab_run.json`, in the run's data directory, records which CRAB and which wrapper
files produced the results. Two of its sections describe the launches:

- `execution`: the `scheduler`, the `hosts_used`, the preset's `launcher`, and an `mpirun` object
  with its `command`, its `dialect` and its `version`, the first line of `mpirun --version`.
  The version and the detected dialect are only filled in when the preset's launcher is `mpirun`
  with `"dialect": "auto"`. If the probe fails, `dialect` is `null` and `probe_error` says why;
  the failure does not stop the job at this point.
- `launches`: for each experiment and application, the first launch line that was run (`line`),
  the launcher `kind`, the `dialect` (`null` unless it was `mpirun`) and the path of its
  `binding_report` (`null` unless it was `mpirun`).
