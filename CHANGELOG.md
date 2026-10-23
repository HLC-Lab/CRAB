# Changelog

All notable changes to CRAB are listed here, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). From 1.0.0 the versions follow
[semantic versioning](https://semver.org/), as defined in
[ADR-039](https://hlc-crab.readthedocs.io/en/latest/dev/dashboard/decisions/adr-039-public-surface-1-0/).

Each product-line release is listed here. The SbatchMan partner line is tagged `vX.Y.Z+sbatchman`,
usually alongside the product release. A SbatchMan-only release has only the `+sbatchman` tag, and
its notes live in the tag and the GitHub release.

## [Unreleased]

Planned as 1.2.0.

### Breaking: read before upgrading

This release changes the presets format and where presets live, inside the 1.x line
([ADR-041](https://hlc-crab.readthedocs.io/en/latest/dev/dashboard/decisions/adr-041-presets-in-crab-wrappers/)).

- The single-file overrides are refused. `local/presets.json` (in CRAB's home) and `presets.json`
  in the config folder (`~/.config/crab/presets.json` or `$CRAB_HOME/config/presets.json`) are no
  longer read. While one exists, every command that loads presets stops with a message that names
  the file. Move your presets into the overrides folder, `local/presets/` in a checkout or
  `~/.config/crab/presets/` in an install, as any `*.json` file. `CRAB_PATH_PRESETS` now names that
  folder, not a file; one that still names a file is not read.
- A preset in your overrides with the name of a shared preset now patches it. Before, it replaced
  it whole. A copy of a whole preset that you moved over still loads, but it repeats the shared
  `header` lines and any flag not written as `--name=value`: cut it down to the lines you changed.
  A patch cannot remove a shared flag or header line. To drop one, write a complete preset under a
  new name.
- `_common` is removed. A `_common` key in any presets file is an error. CRAB now sets
  `CRAB_ROOT=__CWD__` for every preset on its own, and a preset's own `CRAB_ROOT` wins.
- CRAB ships no presets. They are `presets/<cluster>.json` in the crab-wrappers clone, so CRAB 1.2.0
  needs a clone at the tag `v1.2.0`, which `crab update` checks out. A clone left on an older tag
  has no `presets/` folder and offers no presets: run `crab update`. CRAB says so when it finds
  none.
- A checkout with local edits to the tracked `src/crab/presets.json` (or `config/presets.json`) is
  refused by `crab update`, with the steps to move the edits into `local/presets/`.
- The shared presets write the account of a cluster as a required mark, `--account=<YOUR_ACCOUNT>`.
  `crab run`, `crab validate -p` and `crab nodes -p` refuse such a preset until your overrides set
  it.

### Added

- Presets live in crab-wrappers and move with it: `crab update` brings the presets of the release
  you run, and `crab update --head` the newest ones. Each folder of `CRAB_PATH_WRAPPERS` can hold a
  `presets/` folder, and the first folder that has a name wins.
- `extends`: a preset can start from another one (`"extends": "leonardo"`) and set only what
  differs, for a variant such as a GPU or a CPU partition.
- `notes`: a short list of remarks on a preset.
- Required marks, `<YOUR_NAME>`, for values only the user knows. They are allowed in `env` values,
  in `--name=value` flags and in a launcher `command`, and not in `header` lines.
- `crab presets show <name> [--json]` prints a preset as CRAB will use it, the file each value
  came from, what is still unset and the override to write.
- `crab info --json` gains, per preset, `needs` (the unset marks) and `notes`, and at the top level
  `presets_sources` (the shared folders searched), `presets_overrides` (the overrides folder) and
  `presets_error` (the reason when no presets can be loaded). The contract schema stays `2`. The
  text output of `crab info` prints the overrides folder and marks the presets that need setup.
- The Remotes card of the dashboard flags the presets that need setup.

### Changed

- The merge rules are by field. `env` merges by key and `null` removes a key. `sbatch` and launcher
  `flags` replace a shared item by its `--name=` and append every other item. `header` and `notes`
  add to the shared ones. The mpirun `export` list is a union. Everything else is replaced. See the
  [presets format](https://hlc-crab.readthedocs.io/en/latest/reference/presets/).
- Preset errors name the file: a name defined twice in one folder, an `extends` that points nowhere
  or loops, an override with no base preset.
- The documentation covers the four kinds of per-user values (account, a choice the cluster
  offers, your own environment, a cluster gotcha), with recipes for your own UCX or OpenMPI build, a
  virtual environment or modules, and a QoS.

### Removed

- `src/crab/presets.json` and the `_common` block.
- The `example_preset` entry, and its exclusion from tab-completion.

## [1.1.0] - 2026-10-21

### Changed

- `crab validate` and `crab run` refuse an app whose wrapper `path` is not on the wrapper search
  path, before anything is submitted. Before, the job started and failed inside its first run with
  `Wrapper not found`. For a Blink wrapper renamed in 1.0 (for example `blink/a2a_b.py`) the
  message names the new path. For the removed `bursty_noise` wrappers it names the replacement and
  the flags to use. A `CRAB_PATH_WRAPPERS` set in the preset's `env` is honored (`crab validate`
  needs `-p` to see it), and `crab worker` gives the same rename message.
- The dashboard's Author view lists a wrapper the selected cluster does not have
  (`wrapper <path> is not on <cluster>.`) once that cluster's wrapper list has loaded.
- crab-wrappers 1.1.0 lets the gpubench wrappers be listed without `CRAB_SYSTEM` set.

### Fixed

- A binary path from a receipt or from a wrapper's built-in default counts only when every path in
  it exists. Before, `crab wrappers list` showed `receipt` for programs that were never built, and
  a run failed on the missing file. Now the lookup goes on to the wrapper's `executable` on `PATH`,
  and when that fails too, the missing-binary error names the absent path. The app's `binary` key
  in the config still wins even when the file is absent.

## [1.0.0] - 2026-10-21

### Read before upgrading

- The terminal UI is removed. `crab tui` and the `tui` extra are gone; use the web dashboard
  (`crab web`) instead.
- The distribution is now named `crab-hpc`. The import name and the command stay `crab`. An old
  `crab` distribution in the same environment is uninstalled by `crab update`, because it owns the
  `crab` script the new one writes.
- The shipped presets moved from `config/presets.json` into the package (`src/crab/presets.json`).
  Presets now merge in three layers: the shipped file, then your user presets file
  (`~/.config/crab/presets.json` on an install, or the path in `CRAB_PATH_PRESETS`), then the
  checkout's `local/presets.json`. A preset with the same name replaces the earlier one whole. A
  checkout with local edits to the moved file is refused by `crab update`: move them to
  `local/presets.json`.
- `crab update` now goes to the newest release by default: a checkout moves to the newest
  `vX.Y.Z` tag and the wrappers clone to the matching tag. `crab update --head` follows the branch
  for both, as `crab update` did before. `--to-release` is still accepted and is the default.
- The old `read_data()` return shape, one list of samples per metric, is an error. A wrapper that
  still returns it fails its runs, and `crab parse` exits with an error that names the wrapper.
  Return rows, one dict per sample. The stock wrappers in crab-wrappers are migrated; update them
  with `crab update`. Private wrappers need the change by hand (see
  [Writing a wrapper](https://hlc-crab.readthedocs.io/en/latest/extending/wrappers/)).
- A receipt file that exists but cannot be read or parsed is an error. Before, CRAB copied it to
  `.bak` and carried on as if there were no receipt. Now the command stops with a message that
  names the file, and leaves it as it is: fix or delete it, or register the binary again with
  `crab receipts set`.
- The shipped presets no longer set `CRAB_PATH_WRAPPERS`, so a value you export reaches the jobs.
  A preset that sets it in its `env` still wins. The default search path is unchanged.
- The product branch is `develop` (it was `feature/web-dashboard`). `master` gets releases.

### Added

- `crab validate <config> [-p <preset>] [--json]` runs the pre-submit checks of `crab run` without
  submitting. With a preset it also checks the config against what that preset can run. The
  dashboard shows the same config-only checks as warnings when you save an experiment.
- CRAB installs with pip or pipx as `crab-hpc` (`pipx install "crab-hpc[web]"`), next to the git
  checkout. There are two layouts, decided in one module, `crab.paths`
  ([ADR-038](https://hlc-crab.readthedocs.io/en/latest/dev/dashboard/decisions/adr-038-two-layouts/)). A checkout keeps its state in
  the repository as before. An install keeps results, benchmarks and the wrappers clone in
  `~/.local/share/crab`, and receipts, private wrappers and the user presets file in
  `~/.config/crab`. `CRAB_HOME` and `CRAB_PATH_PRESETS` move them. `crab info` prints the layout,
  and `crab info --json` gains a `layout` field.
- An installed CRAB clones crab-wrappers on first use, with one printed line. It never does so
  inside a `--json` call or in `crab worker`, which print a hint instead.
- The user presets file between the shipped presets and `local/presets.json`.
- The public surface 1.0 keeps stable: the CLI, its `--json` outputs, the `crab worker` seam, the
  config and presets formats, the result files, the wrapper base class, receipts and the
  environment variables
  ([ADR-039](https://hlc-crab.readthedocs.io/en/latest/dev/dashboard/decisions/adr-039-public-surface-1-0/)).
- The dashboard's Wrappers section. It shows each target (This laptop and every connected
  cluster) with its crab-wrappers clone compared to GitHub, a Pull or Clone button, the wrappers
  that need attention, a Library tab with the wrapper tree and each wrapper's summary, and a
  command to start a new wrapper. The experiment editor links each wrapper to its summary. It is
  backed by `crab wrappers status --json`, `crab wrappers pull --json` and
  `crab wrappers show --json`.
- A cluster profile in the dashboard can name its own `crab` command, for a cluster where CRAB is
  installed with pipx. The dashboard then skips the installer and the CRAB folder on that cluster.
- A user guide for the web dashboard, a CLI reference that covers every command with its real
  flags, and a results page that explains what a job writes and the three ways to read it.
- CI builds the wheel and the sdist, installs the wheel in a clean virtual environment and runs
  CRAB from it (Python 3.10 and 3.12). A contents check on the wheel keeps the presets and the setup
  recipes in it.

### Changed

- The generated Slurm script activates the virtual environment of the interpreter that submitted
  the job (`<sys.prefix>/bin/activate`), instead of the checkout's `.venv`.
- The dashboard's guided install on a cluster clones `develop`.
- `crab web` offers to install the web extra for a pip install too, and says to run
  `pipx install --force "crab-hpc[web]"` if that fails.
- `crab update` on an install updates only the wrappers and prints `pipx upgrade crab-hpc`.
- A Blink wrapper path from before the folder move (`blink/<name>.py`) resolves to its new folder
  with a warning, as long as the name is unambiguous, so old configs keep running.
- The completion log names the scheduler that took the job (the local scheduler or Slurm) instead
  of always saying Slurm.
- The package metadata is complete (license, classifiers, URLs), the dependency list no longer pins
  transitive packages by hand, and the `all` extra now equals `web`.
- The Blink examples use Blink's folder layout for wrapper paths.

### Removed

- The TUI (`crab tui`, the `tui` extra and its `textual` dependencies).
- The list-per-metric `read_data()` shape, and the warning that announced its removal.

### Fixed

- A value of `CRAB_PATH_WRAPPERS` that you export reaches the jobs (the shipped presets used to
  overwrite it).
- `crab_run.json` and the missing-wrappers hint no longer assume a checkout: an install records no
  commit and tells you to run `crab wrappers pull`.
- Errors from a cluster in the dashboard keep the end of stderr, where the cause usually is.
- `crab list-benchmarks` skips sample files.
- `crab wrappers` import output stays off `--json` stdout.
- The dashboard links a wrapper's summary only for wrappers that the target lists.

## [0.5.1] - 2026-10-19

### Fixed

- `crab setup`: a blank module line when building from source now loads no modules. It used to
  load `module load gcc openmpi`, the example the prompt showed as its default.
- `crab web` in an environment without the web extra offers to install it, instead of stopping
  with "No module named 'uvicorn'".

Update with `crab update --to-release`.

## [0.5.0] - 2026-10-15

### Read before upgrading

- Presets changed shape. How a machine runs work is now set by preset fields (`scheduler`,
  `launcher`, `launchers.srun`, `launchers.mpirun`) instead of `CRAB_*` variables in `env`. The
  shipped presets are converted; convert your own in `local/presets.json` by hand: add
  `"scheduler": "slurm"`, set `"launcher"`, move the flags into `"launchers"` and delete the old
  keys from `env`. A preset without `scheduler`, with an unknown key, or with an execution field
  in `_common` is refused.
- `CRAB_WL_MANAGER`, `CRAB_SCHEDULER`, `CRAB_MPIRUN`, `CRAB_MPIRUN_ADDITIONAL_FLAGS`,
  `CRAB_MPIRUN_MAP_BY_NODE_FLAG`, `CRAB_MPIRUN_HOSTNAMES_FLAG` and `CRAB_PINNING_FLAGS` are no
  longer read. In a preset's `env`, an app's `env` or a worker's environment they stop CRAB with
  an error that names the replacement, for example "env.CRAB_PINNING_FLAGS is no longer read; use
  launchers.srun.flags". `CRAB_MPIRUN_HOSTNAMES_FLAG` has none: delete it, mpirun finds the job's
  nodes itself.
- Configs are written with `schema_version` 2, by the dashboard and in the shipped examples. CRAB
  0.4.0 refuses them ("This config has schema_version 2, but this CRAB reads up to 1"), so update
  CRAB on every cluster you submit to. Configs with version 1 or no version run as before.
- Update the dashboard and the clusters together: the `--json` contract is now version 2, and the
  dashboard shows "This cluster's CRAB is older than the dashboard. Run crab update on the
  cluster, then reconnect." for a cluster still on 0.4.0.
- The wrappers left the CRAB repository. They live in HLC-Lab/crab-wrappers, and `make` and
  `crab update` clone them into `wrappers/`. Coming from 0.4.0, run `crab update` twice: the first
  run still uses the old update code, the second clones the wrappers. Without them, a command that
  needs a wrapper stops with "wrappers/ is missing: run `crab update` to clone the wrappers".
  `wrappers/` follows the main branch of crab-wrappers; private wrappers go in `local/wrappers/`.
- Blink wrappers are named after Blink's own programs, and the short names are gone: a config that
  still uses one fails with "Wrapper not found". `a2a_*` is now `alltoall_*`, `agtr_comm_only`
  `allgather_comm_only`, `ardc_*` `allreduce_*`, `bdc_*` `broadcast_*`, `barrier` `barrier_b`,
  `inc_*` `incast_*`, `o2o_*` `pairwise_*`, `ping-pong_b` `pingpong_b`, `pw-ping-pong_b`
  `pingpong_pairwise_b`. `ring_bsnbr`, `ring_nb`, `checker` and `null_dummy` keep their names.
  `blink/README.md` in crab-wrappers has the table and a sed command that renames the wrappers in
  a config.
- Blink builds with CMake (3.16 or newer) since upstream dropped its Makefile, and its programs
  end up in `build/bin/`. A blink receipt from an older CRAB points at the old `bin/`, where the
  new names do not exist: run `crab setup` again (it offers to clear the old build), or
  `crab receipts set blink --binary <blink checkout>/build/bin`.
- Upstream Blink also renamed `-bprand` and `-blrand` to `-bpdist` and `-bldist` (an old flag
  aborts with "Unknown argument"), and `pingpong_b` now reports the five standard durations,
  without bandwidth. `bursty_noise_a2a` and `bursty_noise_incast` exist only in the blink-clean
  fork and need a receipt or a `binary` key that points at it.
- Slurm directives that CRAB sets itself are refused before submitting instead of being ignored:
  `--nodes` (`-N`), `--ntasks-per-node` and `--ntasks` (`-n`), in `sbatch_directives` or in a
  preset's `sbatch`. Use `numnodes` and `ppn`.
- The allocation keys from before the allocation block (`allocationmode`, `partitionsplit`,
  `partitionlayout`, `allocationsplit`) are refused, and the error shows the equivalent allocation
  form. Before, they were ignored without a word.
- `ppn` in an experiment's `local_options` now takes effect (it was ignored), and an app can set
  its own. An app's `cpus`, `env`, `nics` and `ppn` are CRAB keys and no longer reach the wrapper
  as attributes.
- A receipt's `launcher_override` takes only `"srun"` or `"mpirun"` (`""` uses the preset's
  launcher); any other value fails the experiment at setup. `crab receipts set --launcher` accepts
  the same.
- The shipped local preset starts each app with mpirun (0.4.0 ran the command directly), so mpirun
  must be installed. Local jobs now have a time limit: `global_options.walltime`, `00:10:00` by
  default, after which the job ends TIMEOUT.
- Local jobs submitted by 0.4.0 (with a process id as job id) are not tracked any more: let them
  end before you upgrade. The `.crab_local_jobs/` folder in the checkout is not read and can be
  deleted.

### Added

- Running without Slurm. A preset with `"scheduler": "local"` runs the job on this machine, or on
  several machines reached over ssh (`hosts` or `hostfile` in the preset; passwordless ssh and the
  same software at the same paths). The job uses the first `numnodes` hosts.
    - Each local job runs under a detached supervisor that outlives your shell. Its id is
      `local-<n>` and its state lives in `~/.local/state/crab/`. `crab status` and `crab cancel`
      take `local-<n>` ids next to Slurm ones, and `crab nodes -p <preset>` lists a local preset's
      hosts and cores.
    - With exclusive hosts (the default) a job locks its hosts, and a second job that needs one of
      them waits in PENDING. `"exclusive": false` turns the locks off.
    - The walltime is enforced and the job ends TIMEOUT. Cancel and the walltime stop every
      process of the job, MPI ranks included. A job whose supervisor died, for example at logout,
      reads LOST; `loginctl enable-linger` keeps jobs running after you log out.
    - The direct launcher runs one process with no MPI (`ppn` 1, `numnodes` 1). `srun` with a
      local preset, or more nodes than the preset has hosts, is refused before submitting.
- mpirun as a launcher, on local hosts and inside Slurm jobs, where CRAB passes it the allocated
  hosts. It is set in the preset's `launchers.mpirun` (`command`, `flags`, `export`), and a config
  can pick the launcher and its flags per run or per experiment (`launcher`, `launcher_flags`).
    - Open MPI 4, Open MPI 5 and MPICH (Hydra) are supported, detected from `mpirun --version` or
      set with `launchers.mpirun.dialect`. Intel MPI, Open MPI older than 4 and an mpirun that
      only asks for srun are refused with a message that says what to set.
    - An app with `cpus` is bound to those cores in each MPI's own syntax (Open MPI 4:
      `--cpu-list` with `--bind-to cpu-list:ordered`). Open MPI's binding report is kept in
      `<experiment dir>/bindings_app_<id>.log`; Hydra gets none, since MPICH rejects
      `-report-bindings`.
    - Open MPI forwards only the variables it is told to: CRAB passes `PATH`, `LD_LIBRARY_PATH`,
      the preset's `env`, every `CRAB_*` variable, the app's `env` and the names in
      `launchers.mpirun.export`.
    - `crab_run.json` records the scheduler, the hosts used, the launcher, the mpirun version and
      dialect, and for each app its launch line, `ppn` and `env`.
- Shared nodes and co-runs. Allocation `"mode": "shared"` runs every app on all the nodes of its
  scope at the same time, to measure interference inside a node. `"cores": "disjoint"` (the
  default) keeps the apps on separate cores, `"shared"` lets them overlap. It goes at the top
  level of `allocation` or inside a partition.
    - New app keys: `cpus` (an explicit core list, one core per rank), `env` (variables for that
      app's ranks only), `ppn` (ranks per node: the app's, else `local_options`, else
      `global_options`) and `nics` (one NIC per rank through `UCX_NET_DEVICES`: rank i uses
      `nics[i % len(nics)]`).
    - Under srun, apps that share nodes run at the same time: each gets explicit step flags
      (`--exact --mem=0` for disjoint cores, `--overlap` with `map_cpu` for `cpus`, `--overlap`
      with no binding for shared cores). CRAB's binding replaces a `--cpu-bind` from the preset or
      the config for those apps, and the pre-submit check warns about it.
    - The job's `--ntasks-per-node` is the largest per-node load: apps that share a node add up,
      and a chain (start `sN`) counts once, at its largest `ppn`.
    - Refused before submitting: `cpus` lists that overlap in disjoint mode, `cpus` shorter than
      `ppn`, `cpus` under Slurm without `--exclusive`, co-run apps under Slurm where only some set
      `cpus`, and more cores than a local host has. `cpus` with the direct launcher, which does
      not bind, is a warning.
    - srun's CPU masks and each rank's NIC line go to `bindings_app_<id>.log` in the experiment
      directory, so the results show where every rank ran.
- Dashboard: the config editor sets the launcher and its flags per config and per experiment, the
  shared allocation mode and its cores, `ppn` per experiment and per app, and each app's `cpus`
  and `env`. At submit, the dialog shows what the chosen preset runs with (scheduler, launcher,
  local hosts) and flags what it cannot run, such as srun on a local preset or more nodes than
  hosts. Local jobs are submitted and followed like Slurm ones; a LOST local job counts as failed.
  The sbatch editor flags `--nodes`, `--ntasks-per-node` and `--ntasks` (and `-N`, `-n`) as
  refused, and warns about `-o` and `-e` as well as `--output` and `--error`.
- New warnings before a run: `collect` true with an `end` of `"f"` or a number (the output is
  lost if the app is still running when stopped), `numnodes`, `walltime` or `sbatch_directives` in
  `local_options` (ignored there), and unknown experiment keys.
- Examples: `examples/local/concurrent_collectives_stress` runs its apps together on shared nodes,
  and the new `examples/leonardo/congestion/corun_nics.json` co-runs a victim and an aggressor,
  each on its own NIC.

### Changed

- Apps are stopped with SIGTERM first and SIGKILL after 10 seconds, for the whole process group.
  srun gets to end its step and mpirun its ranks; before, SIGKILL first could leave MPI ranks
  running. The same order applies to `"f"` apps (stopped together, sharing one grace period),
  timed ends, the experiment timeout, and a worker that receives SIGTERM (`crab cancel`,
  `scancel`).
- After each run, a Slurm step of an app that is still running is cancelled with a warning, so it
  cannot hold the next run's nodes. A process that survives SIGKILL is named in the log.
- A split that sums to less than 100 leaves the remaining nodes idle, with a warning. Before, some
  of them were handed to the apps anyway.
- Every example declares `schema_version` 2 and uses Blink's program names. Most interference
  examples (cluster_di, leonardo scale and congestion) run their aggressors until stopped
  (`-endl`) and start the victims 3 s later, so the collected samples are taken under noise. The
  bursty noise uses upstream `alltoall_man` and `incast_nb` with 2 MiB messages instead of the
  fork-only `bursty_noise` programs.

### Removed

- The workerpool example and wrapper: workerpool was never a Blink program, and its scheduler no
  longer exists.

### Fixed

- `crab cancel` on a local job stops its apps. Before, they kept running and the job read UNKNOWN
  forever. CRAB also never signals pid 0, 1 or its own group from a local job's state file: a
  damaged file could make a cancel signal every process of the user.
- Jobs load modules on Lmod sites that have no `/etc/profile.d/modules.sh`.
- `crab setup` stops with the shell's error when the module line cannot be loaded, and offers to
  clear a leftover build from a failed attempt before building again.
- A wrapper path that cannot be imported as Python names the file in the error.
- `metadata.csv` rows written by a SbatchMan job carry the time the experiment started instead of
  "unknown".
- The editor reads "true" and "false" written as text the way the engine does, accepts `{name}`
  placeholders in allocation fields, and keeps one-dash sbatch directives (`-J name`) as written.

## [0.4.0] - 2026-10-07

### Read before upgrading

- An app with `start` `"sN"` now runs on app N's nodes, all along a chain (0, s0, s1 all run on
  app 0's nodes). Before, every chained app needed its own share of the allocation.
- A chained app that names a different partition gets a share of that partition instead: "start
  when app N ends, but on other nodes".
- `split` and partition splits list only the apps that get a share. Drop the entries of chained
  apps; a split that still lists them is rejected before submitting, and the error names them.
- Rejected before submitting: two apps that would both run on the same app's nodes at the same
  time, `"sN"` naming an app that does not exist, and start cycles.
- Before the first run on a cluster CRAB knows (such as leonardo), set your project account in
  `local/presets.json`: the shipped presets carry a placeholder. The tutorial and installation
  pages now say so.

### Changed

- Loading a config that uses `split` in the dashboard keeps chained apps on their predecessor's
  nodes, and the app card shows "on #N's nodes" for them.
- Wrapper paths in the examples point at the family folders (`blink/a2a_b.py`, not `a2a_b.py`):
  11 example files failed at launch with "Wrapper not found". `pingpong_interference`'s ring now
  uses `blink/ring_bsnbr.py` (`ring_b.py` never existed).
- `sequential_barrier`, `cong_analysis`, `allreduce_interference` and `pingpong_interference` pass
  the pre-submit checks. The interference baselines run on the victim group; experiments meant for
  all nodes get their own allocation. The 256-node killer chain runs its incast on the aggressor
  group.

### Fixed

- A job in which an experiment crashed (for example a missing wrapper) now ends failed, with the
  failed experiments named in the log. The other experiments still run first. Before, the job
  ended COMPLETED.
- When sbatch refuses a job, the error now shows Slurm's reason (for example "invalid account or
  expired budget") instead of only "exit status 1".

## [0.3.1] - 2026-10-07

### Fixed

- A blink run whose output was cut off (job killed while printing) is now an error. Before, the
  rows printed so far were recorded as a complete run.
- blink parses its table by content: MPI/UCX warning lines before or inside the table no longer
  break the parse, and the row count must match the benchmark's own "Measured M iterations".
- `pw-ping-pong_b` parses again (its Targets line used to break every run).
- blink wrappers return rows (no more one-list-per-metric deprecation warning).
- First real golden samples: `a2a_b` and `ardc_b`, captured on Leonardo.

## [0.3.0] - 2026-10-07

### Read before upgrading

- Allocations are checked before any run: a split over 100, a wrong split length, apps with zero
  nodes, and unknown or missing partitions are errors. A split under 100 is only a warning.
- Chained apps (`start` `sN`) still need their own nodes: CRAB does not reuse nodes yet.

### Fixed

- A run never records a previous run's output again (hard timeout, unreadable output).
- An experiment that fails midway keeps the runs it already completed (CSV and FAILED registry
  row).
- An empty app command is an error instead of a silent successful run.
- Wrappers raise on unparseable output instead of recording zeros (NCCL, gpubench, amg, miniFE,
  graph500).
- ember wrappers parse again; halo3d runs its own binary.
- Quantum ESPRESSO reads wall times in every QE format (s, m, h, d) and the final `ph.x` total.
- ph wrappers run `ph.x`, not `pw.x`; QE honours an explicit binary.
- graph500 and blink work with module installs.
- `crab setup` proposes real executables for module installs and its searches look for them.
- Dashboard: re-fetching results replaces the cache instead of nesting it, one failing cluster no
  longer breaks the Jobs and Results lists, history caches are scoped per cluster and system, and
  `~` expands only in path arguments, so profile names and result paths cannot escape their
  folders.

## [0.2.1] - 2026-10-06

The tag carries no release notes.

## [0.2.0] - 2026-09-30

The tag carries no release notes.
