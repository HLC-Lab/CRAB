# ADR-045 · On the SbatchMan line, SbatchMan configs own the job environment

- **Date:** 2026-11-05
- **Status:** accepted

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
- **`crab sbatchman-preset` does not exist** (it was removed). A partner starts from a committed
  configs file, `examples/sbatchman/configs.leonardo.yaml`. Any other configs file works if it
  keeps the config name `crab_run_{nodes}` and carries the CRAB env block.
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
- Supporting `bind` and `cpus` under SbatchMan would need CRAB to read the topology from somewhere
  other than a preset.
- Revisit this if the SbatchMan line is merged into one product line.
