# Placement that runs on any cluster

Two configs that pin each rank to a GPU, the NIC nearest to it and a core next to both, without naming a
single device. Run them on a cluster that has a `topology` in its preset, and CRAB works out the devices.

| File | What it does |
|---|---|
| [`pinned_gpu.json`](pinned_gpu.json) | All-to-all between GPUs, 2 nodes, 4 ranks per node: `bind` gives each rank its own GPU, the nearest NIC and a core on the same NUMA domain. |
| [`pinned_gpu_control.json`](pinned_gpu_control.json) | The same, but `nic` is `"default"`: no NIC is set and UCX picks. Run it next to the first to see what the pinning changes. |

The two files differ in one word, the value of `bind.nic`. Neither holds a device name, a core list, a
partition or a GPU request: those belong to the cluster, so they live in its preset.

## Run it

1. **Describe one of the cluster's GPU nodes** (once per cluster, inside an allocation on that node type):

   ```bash
   crab probe
   ```

   It prints the GPUs, the NIC ports, which NIC is nearest to which GPU and a `topology` block.
   [`crab probe`](../../docs/reference/cli.md#crab-probe) says what it reads and when it cannot tell.

2. **Put the block in a preset of yours**, and ask for the whole node there. The GPU request is a cluster
   fact, so it goes in the preset's `sbatch` list and not in the config. For a cluster whose preset is
   called `mycluster`, in `local/presets/mycluster-gpu.json`:

   ```json
   {
     "mycluster-gpu": {
       "extends": "mycluster",
       "sbatch": ["--gres=gpu:4"],
       "topology": { "...": "the block crab probe printed" }
     }
   }
   ```

   Use the number of GPUs the node has. `bind.gpu` needs the job to see all of them, and the node check
   refuses a job that sees fewer.

3. **Check the node against the topology**, then run:

   ```bash
   crab probe --check -p mycluster-gpu
   crab run examples/placement/pinned_gpu.json -p mycluster-gpu
   crab run examples/placement/pinned_gpu_control.json -p mycluster-gpu
   ```

   Blink must be built with GPU support (set up with `crab setup`, or give the app a `binary`).

   `crab validate -p mycluster-gpu examples/placement/pinned_gpu.json` checks the config against the preset
   without submitting. If the preset passes `--cpu-bind` to `srun`, it prints a warning that the flag is
   replaced by CRAB's core binding. That is expected: `"cores": "nearest"` binds each rank itself.

## What to look at afterwards

- `crab_run.json` in each run folder has `placements`: for every local rank the GPU, NIC, core and the
  distance class between the GPU and its NIC, and whether the nodes passed the check before the first run.
- Each rank also prints one line to its standard error, kept in `bindings_app_0.log`.
- The folder has `config.resolved.json` next to `config.json`. The first runs on any cluster. The second is
  tied to the preset and topology it was resolved for, and CRAB refuses to replay it on another.

If the preset's topology cannot give what `bind` asks (more ranks than GPUs, a NIC that is down), the run
stops before it starts and the message says what the topology has.
