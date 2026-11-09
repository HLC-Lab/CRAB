# ADR-045 · On the SbatchMan line, SbatchMan configs own the job environment

- **Date:** 2026-11-05
- **Status:** accepted; the SbatchMan pin moved to 2.0.4 on 2026-11-09 (see the update at the end)

## Context

SbatchMan 2.0.3 (PyPI, installed with `pip install sbatchman==2.0.3`) builds each job script in
this order: `module load`, exports of `SBATCHMAN_WD`, `SBATCHMAN_JOB_DIR`, `SBATCHMAN_JOB_TAG` and
`SBATCHMAN_JOB_CONFIG`, then the config's `env` entries, then the job's `preprocess` and
`command`. It substitutes `{var}` placeholders in the tag, `preprocess` and `command`, but not
`{EXP_DIR}` (use `$SBATCHMAN_JOB_DIR` instead). A jobs file's `configs:` key loads a configs file
at every launch (overwriting what was loaded before), relative to the jobs file; the cluster block
of that file that applies is the one named by `sbatchman set-cluster-name`.

The worker inherits that environment ([ADR-027](adr-027-sbatchman-seam-v2.md)). A CRAB preset
would repeat what the SbatchMan config already says (account, partition, modules, environment)
and put the same facts in two places.

## Decision

- **On the `sbatchman` line the job environment comes only from the SbatchMan configs file.** It
  holds the allocation (account, partition, qos, time, nodes, modules) and a "CRAB env block":
  `PATH` to the CRAB virtual environment, `CRAB_ROOT`, `CRAB_SYSTEM`, `CRAB_PATH_WRAPPERS`, plus
  the site's network settings. CRAB presets (`presets/*.json` in crab-wrappers,
  [ADR-041](adr-041-presets-in-crab-wrappers.md)) are not used there. The shared preset code stays
  in the tree because the product line uses it.
- **The configs file has one config per job kind**, because SbatchMan appends the `modules` and
  `env` lists of a config to those of `default_conf`: a shared `modules` list would stay loaded in
  every config. `crab_run_{nodes}` is the CPU config (Blink) and `crab_qe_{nodes}` asks for 4 GPUs
  (Quantum ESPRESSO, whose module on Leonardo is a CUDA build), each with its own `modules`. The
  worker probes the MPI dialect with `mpirun` before any app starts, so each config's modules must
  put an `mpirun` on `PATH`. The `env` list (the CRAB env block) stays in `default_conf`.
- **`crab sbatchman-preset` does not exist** (it was removed). A partner starts from a committed
  configs file, `examples/sbatchman/configs.leonardo.yaml`. Any other configs file works if it
  keeps the config names `crab_run_{nodes}` and `crab_qe_{nodes}` (the second only for QE jobs) and
  carries the CRAB env block.
- **`bind` and `cpus` are not supported there.** Without a CRAB preset the worker has no node
  topology ([ADR-043](adr-043-placement-intent-and-topology-facts.md)), so it exits at start
  with a message naming each offending experiment and app, instead of failing after the queue
  wait. `cpus` is refused too, because CRAB cannot see whether the allocation is exclusive, which
  SbatchMan sets.
- **crab-wrappers stays a single branch.** The SbatchMan line ignores its `presets/` folder.
- **Jobs files carry `configs:`**, so `sbatchman configure` is never run by hand.

This supersedes the install and `{token}` points of ADR-027. It corrects the amendment in
[ADR-025](adr-025-sbatchman-worker-seam-and-campaign-generator.md): in 2.0.3 the `configs:` key
works.

## Alternatives considered

- Keep `crab sbatchman-preset` printing `sbatchman configure` commands: the environment then
  comes from two places, so it was dropped.
- A crab-wrappers branch without presets: it needs changes to `crab update` and to the clone, double
  tagging, a second CI job, and it hits a modify/delete conflict on every preset edit.
- Select a CRAB preset by name with `CRAB_PRESET` on the SbatchMan line: the account placeholder
  would still have to be fixed by hand, and a preset's `env` is not applied to apps today.

## Consequences

- SbatchMan skips relaunching an identical job unless `--force` is given, and `sbatchman campaign`
  cannot force it.
- The dashboard campaign generator does not write `configs:` yet.
- The generator's QE campaigns would still name `crab_run_{nodes}` (CPU only), so they fail on
  Leonardo until the generator is updated to name `crab_qe_{nodes}`.
- Supporting `bind` and `cpus` under SbatchMan would need CRAB to read the topology from somewhere
  other than a preset.
- Revisit this if the SbatchMan line is merged into one product line.

## Update 2026-11-09: SbatchMan 2.0.4

The SbatchMan line now pins SbatchMan 2.0.4 (PyPI, released 2026-11-06), installed with
`pip install "sbatchman==2.0.4"`. This supersedes the 2.0.3 pin in the Context above, which
records the original decision. File references are paths inside the SbatchMan 2.0.4 sources,
relative to `src/sbatchman/`.

What changed in SbatchMan:

- **The duplicate check ignores the command by default.** A job counts as a duplicate when
  cluster, config and tag match. The command, `preprocess` and `postprocess` are compared only
  with `--compare-commands-in-dup-check` (`cli.py:276-278`, `core/jobs_manager.py:47-100`).
  Archived jobs count too, and a job in any status blocks a relaunch, a failed one included.
  `--force` skips the check (`core/launcher.py:359-396`). **Tag rule:** a job whose arguments
  change needs a changed tag, or `--force`. CRAB's example tags encode `nodes` and `msgsize`,
  not the other arguments of the experiment.
- **The configs registry is per cluster.** It is `SbatchMan/configs/<cluster>/configurations.yaml`.
  When that file does not exist, SbatchMan reads the old shared
  `SbatchMan/configs/configurations.yaml` instead and takes only the entry of the current cluster
  (`config/project_config.py:111-145`). Writes always go to the per-cluster file
  (`schedulers/base.py:197-235`).
- **`SBATCHMAN_CLUSTER_NAME` sets the cluster name.** A non-empty value wins over the name stored
  by `sbatchman set-cluster-name` (`config/global_config.py:44-60`). Job scripts export it
  (`schedulers/base.py:98-102`).
- **A cache can skip rewriting the configs.** A jobs file's `configs:` key is still read on every
  `launch -f`. When the configs file, the variable files it references and all generated scripts
  are unchanged, SbatchMan prints that the configurations are unchanged and does not rewrite them
  (`core/launcher.py:699-743`, fingerprint in `core/launcher.py:38-89`). The cache key does not
  include the cluster name (`core/launcher.py:714-717`).

What did not change, and this ADR still relies on:

- The run script keeps its order: `module load`, the `SBATCHMAN_*` exports, the config's `env`
  entries, `preprocess`, then `command` (`schedulers/base.py:173`).
- `$SBATCHMAN_JOB_DIR` is exported, and `{EXP_DIR}` is still not substituted in `command` or
  `preprocess` (`schedulers/base.py:98-102`, `core/launcher.py:418-436`).
- `{var}` is substituted in `command`, `preprocess` and the tag (`core/launcher.py:1136-1142`).
- A list field of a config (`modules`, `env`) is appended to the same list of `default_conf`
  (`core/config_manager.py:264-272`).
