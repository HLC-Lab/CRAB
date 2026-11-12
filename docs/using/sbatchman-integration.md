# Using CRAB with SbatchMan

On the `sbatchman` line, [SbatchMan](https://github.com/LorenzoPichetti/SbatchMan) submits and monitors the jobs, and each job runs `crab worker`. CRAB builds, runs and parses the benchmarks inside the allocation SbatchMan obtained.

The dashboard does not launch, monitor or plot a SbatchMan campaign. The [roadmap](../dev/roadmap.md#relationship-with-sbatchman) says why.

## Partner umbrella

The install, the project setup, the campaigns, the launch and the results walkthrough are in [crab-sbatchman](https://github.com/HLC-Lab/crab-sbatchman), the partner umbrella repo. It pins CRAB and the wrappers to tested commits.

Inside the umbrella, `crab update` does not apply. Update the umbrella with `git pull` and `git submodule update --init`. `crab update` stops with:

```text
CRAB is a submodule of <dir>: update the umbrella (`git pull`, then `git submodule update --init`), not CRAB itself.
```

## Jobs file keys

A jobs file is a YAML file that SbatchMan reads. CRAB never reads it: the `preprocess` script writes the CRAB config into the job folder, and `command` runs the worker on it.

| Key | Meaning | Example |
|---|---|---|
| `configs` | The SbatchMan configs file to load, relative to the jobs file. | `configs: configs.leonardo.yaml` |
| `variables` | Values SbatchMan sweeps: one job per value. | `nodes: [2, 4, 8]` |
| `jobs[].config` | The config of the configs file to submit with. | `crab_run_{nodes}` |
| `jobs[].tag` | The job name and its result folder. | `a2a-smoke-{nodes}` |
| `jobs[].preprocess` | A shell script that runs first and writes `config.json`. | `cat > "$SBATCHMAN_JOB_DIR/config.json" <<'JSON'` |
| `jobs[].command` | The line that runs the experiment. | `crab worker --workdir "$SBATCHMAN_JOB_DIR"` |

SbatchMan replaces `{var}` in `tag`, `preprocess` and `command`. It does not replace `{EXP_DIR}`: write the shell variable `$SBATCHMAN_JOB_DIR` for the job folder.

Write the heredoc marker quoted (`<<'JSON'`) so the shell leaves the JSON as it is. The QE examples leave it unquoted, so the shell expands `$CRAB_ROOT` inside the JSON.

### Running a job again

SbatchMan skips a job whose cluster, config and tag match an earlier job, archived and failed ones included. To run it again, change the `tag` and the `"name"` in the JSON together, or pass `--force`.

## The `crab worker` launcher

A job's command line is:

```bash
crab worker --workdir "$SBATCHMAN_JOB_DIR"
```

The worker reads `config.json` in that folder: the [CRAB config](../reference/configuration.md) that `preprocess` wrote. This is the shape of the examples:

```json
{
  "schema_version": 2,
  "global_options": {"numnodes": "{nodes}", "ppn": "4", "name": "a2a-smoke-{nodes}"},
  "experiments": {
    "run": {
      "apps": {
        "0": {"path": "blink/alltoall/alltoall_b.py", "args": "-msgsize 1048576 -iter 100",
              "collect": true, "start": "0", "end": ""}
      }
    }
  }
}
```

- The config's `nodes` is the Slurm allocation, and it must cover `numnodes`. `ppn` sets the ranks per node: [Ranks per node](../reference/configuration.md#ranks-per-node-ppn) gives the rule.
- The examples set `"name"` equal to the `tag`. The dashboard does the same when you leave `name` empty.
- One job can hold several experiments. CRAB runs them one after another, in sorted name order, on the same nodes. The job's `time` must cover all of them. A failed experiment does not stop the next ones, and the job ends failed.
- With no `execution.json` in the folder and no `CRAB_PRESET` in the environment, the worker uses Slurm with `srun` and no extra flags. It logs `Execution settings from defaults (Slurm with srun; no execution.json, no CRAB_PRESET)`.

Check the config before you launch. Copy the JSON into a file and run:

```bash
crab validate config.json
```

### The job environment

The worker inherits the environment SbatchMan builds from the configs file. Each config needs these `env` entries:

| Entry | Meaning |
|---|---|
| `PATH=<CRAB_DIR>/.venv/bin:$PATH` | Puts `crab` on the job's path. |
| `CRAB_ROOT=<CRAB_DIR>` | The CRAB install the worker uses. |
| `CRAB_PATH_WRAPPERS=<CRAB_DIR>/wrappers` | Where the worker resolves wrapper paths such as `blink/alltoall/alltoall_b.py`. |
| `CRAB_SYSTEM=<system>` | A label recorded with the results. |

Each config's `modules` must put an `mpirun` on `PATH`: the worker looks for `mpirun` in the job environment before any app starts, to detect the MPI. Set `modules` in each config, not in `default_conf`: SbatchMan appends a config's list to the one in `default_conf`, so a shared list mixes MPI stacks. [ADR-045](../dev/dashboard/decisions/adr-045-sbatchman-line-configs-own-the-environment.md) has the reasoning.

## Receipts and wrapper attributes

Every app in the config needs a binary. CRAB looks in this order: the app's `binary` key, the wrapper's receipt, then the wrapper's `executable` on `PATH` in the job ([Finding the binary](../extending/wrappers.md#finding-the-binary)).

Register an installed program once, with `crab receipts set <benchmark_id> --binary <path>`. The receipt also holds the `pre_run` commands, such as `module load` lines, that run before each launch. [From recipe to receipt](../extending/receipts.md) has the fields.

Any key of an app entry besides `path`, `args`, `collect`, `start`, `end` and `partition` becomes an attribute of the wrapper ([Custom configuration keys](../extending/wrappers.md#custom-configuration-keys)). The Quantum ESPRESSO examples use two:

```json
"input_file": "$CRAB_ROOT/examples/sbatchman/qe/si-scf.in",
"pseudo_dir": "$CRAB_ROOT/examples/sbatchman/qe"
```

The worker also writes `crab_run.json` next to `config.json`. It records the CRAB version and commit, and the wrapper files with their hashes.

## Apps built with different module stacks

When apps with different module stacks share one job, the second app's `pre_run` hook runs in an environment that holds the first app's modules. `OPAL_PREFIX` and `HPCX_MPI_HOME` of the HPC-X module stay set, and Blink's Open MPI loads HPC-X's plugins.

Start the receipt's `pre_run` list with `module purge`. For Blink, in `local/receipts/blink.json`:

```json
"pre_run": ["module purge", "module load gcc openmpi"]
```

Without it, the app's `bindings_app_<n>.log` shows `symbol lookup error` and the app exits with code 127.

## CPU pinning

Set Slurm's own input variable in the `env` list of the configs file. The example configs file sets it already:

```yaml
env:
  - "SLURM_CPU_BIND=verbose,socket"
```

Every `srun` the worker starts binds as if `--cpu-bind=verbose,socket` had been given. CRAB runs `srun --export=ALL`, which passes the job's environment on. Change the value, for example to `cores`: `man srun` lists them.

A `bind` or `cpus` key in an app is not supported here: the worker has no node topology. The `CRAB_*` launch variables of older versions (`CRAB_WL_MANAGER`, `CRAB_SCHEDULER`, `CRAB_MPIRUN`, `CRAB_PINNING_FLAGS` and others) are no longer read.

## CRAB's examples

`examples/sbatchman/` holds CRAB's own jobs files for the Blink and Quantum ESPRESSO suites on Leonardo. Each noise job runs six experiments in one allocation (`r1_baseline`, `r1_noise`, up to `r3_noise`), so baseline and noise share the nodes. The [README](https://github.com/HLC-Lab/CRAB/blob/sbatchman/examples/sbatchman/README.md) explains each file.

| File | What it shows |
|---|---|
| `configs.leonardo.yaml` | A configs file: the allocation, the CRAB env block, `crab_run_{nodes}` (CPU) and `crab_qe_{nodes}` (4 GPUs per node). |
| `smoke.yaml` | One all-to-all alone on 2 nodes: the smallest job that exercises the worker. |
| `blink-noise.yaml` | A co-run: an all-to-all against incast noise on 4 nodes, baseline and noise in one job. |
| `a2a-vs-incast.yaml` | A sweep: `nodes` times `msgsize`, 12 jobs of an all-to-all against incast noise. |
| `a2a-vs-a2a.yaml` | The same sweep with all-to-all noise. |
| `qe-alone.yaml` | `pw.x` alone on 2 nodes: app attributes (`input_file`, `pseudo_dir`) and an unquoted heredoc. |
| `qe-noise.yaml` | `pw.x` as the victim against incast on 2 other nodes, in one allocation. |
| `qe/` | The input and pseudopotential the two QE files read through `$CRAB_ROOT`. |

The QE module on Leonardo is a CUDA build, so `qe-alone.yaml` and `qe-noise.yaml` use the GPU config. On a CPU config, `pw.x` aborts with `error in Message Passing (mp) module, error code 9100`.

## Results

Each job writes to `<project>/SbatchMan/experiments/<cluster>/<config>/<tag>/<timestamp>/`. In it, `config.json` and `crab_run.json` sit next to one folder per experiment, with a `data_app_<index>.csv` for each app that has `"collect": true`.

Copy the project's `SbatchMan/` folder to your laptop and run `sbatchman visualize` there ([ADR-046](../dev/dashboard/decisions/adr-046-results-through-sbatchman-visualize.md)). The umbrella docs have the `parser.py` and `plots.json` it needs, and the steps.

## The dashboard

The dashboard's **SbatchMan** view writes a jobs file for a campaign and stages it on the cluster as `<Install dir>/CRAB/.web_staging/<campaign-slug>.yaml`. A copy stays on your laptop in `sbatchman_campaigns/` in the dashboard's data folder.

The generated file has no `configs:` line: add `configs: <configs file>` at its top level before you launch. Connect the cluster as in [Connect a cluster](dashboard.md#connect-a-cluster).

## Troubleshooting

Start with the job's `stderr.log` and `stdout.log`, in its job folder.

| Symptom | Cause | Fix |
|---|---|---|
| `crab: command not found` | The job's `PATH` lacks the CRAB environment. | Add `PATH=<CRAB_DIR>/.venv/bin:$PATH` to `env` in the configs file, then relaunch with `--force`. |
| `wrapper <path> not found on the wrapper search path (<folders>)` | `CRAB_PATH_WRAPPERS` is not set in the job. | Add it to `env` in the configs file. [Wrapper not found](troubleshooting.md#wrapper-not-found-on-the-wrapper-search-path) explains the message. |
| `No binary for ... Tried: ...` | No `binary` key, receipt or `PATH` entry gives the program. | Register it with `crab receipts set`, or add its module to `modules` in the configs file. |
| `PARSE FAILED  app <n>: ...` | The wrapper could not read the application's output. | Compare the output with the wrapper's samples, then fix the wrapper and check it with `crab parse`. |
| `This config has schema_version <n>, but this CRAB reads up to <m>` | The config came from a newer dashboard than the CRAB on the cluster. | Update CRAB on the cluster. |
| `Worker fatal error: [Errno 2] No such file or directory: 'config.json'` | `$SBATCHMAN_JOB_DIR` is empty, or `preprocess` did not write `config.json` there. | Write `$SBATCHMAN_JOB_DIR/config.json` in `preprocess`, as in [Jobs file keys](#jobs-file-keys). |
| `worker environment: CRAB_... is no longer read; use ...` | The configs file exports an old `CRAB_*` launch variable. | Remove that entry from `env`, and pin with [`SLURM_CPU_BIND`](#cpu-pinning). |
| ``worker: experiment '<name>', app <n>: `bind` ...`` | An app has a `bind` or `cpus` key. | Remove the key from the config. |
| `Cannot find 'mpirun' to detect its MPI` | The config's `modules` put no `mpirun` on `PATH`. | Add the MPI module to that config's `modules`, then relaunch with `--force`. |
| `symbol lookup error` in `bindings_app_<n>.log`, exit code 127 | One app's module variables leaked into another's. | Start the receipt's `pre_run` with `module purge`: [Apps built with different module stacks](#apps-built-with-different-module-stacks). |
