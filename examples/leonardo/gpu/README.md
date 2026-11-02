# GPU

Two templates that run a Blink collective on GPU memory, one rank per GPU, on 2 nodes.

| File | What it measures |
|---|---|
| [`alltoall.json`](alltoall.json) | The communication-only all-to-all (`alltoall_comm_only`) between GPUs. |
| [`allgather.json`](allgather.json) | The communication-only allgather (`allgather_comm_only`) between GPUs. |

Each file runs 2 nodes with 4 ranks per node, so every rank has a GPU of its own: 8 ranks in total.
Each has two experiments, one per message size: 1 MiB (`-iter 50`) and 16 MiB (`-iter 20`).
In the all-to-all, `-msgsize` is the number of bytes each rank sends to every other rank; in the
allgather it is the number of bytes each rank contributes. Every experiment repeats between 3 and 5
times, and the time limit is 30 minutes.

## What you need

- A Slurm cluster whose preset has a `topology` and asks for the four GPUs of the node. On Leonardo
  that is `-p leonardo_gpu`. It extends `leonardo`, so the `<YOUR_ACCOUNT>` mark is set the same way
  as for the other Leonardo examples: see [Leonardo examples](../README.md#what-you-need).
- Blink built with GPU support. In `crab setup` the wizard asks for the GPU backend: choose `cuda`.
- The wrappers folder.

## Check and run

```bash
crab validate -p leonardo_gpu examples/leonardo/gpu/alltoall.json
crab run -p leonardo_gpu examples/leonardo/gpu/alltoall.json
```

Use `allgather.json` the same way.

## What the files set

- `-gpu` keeps Blink's buffers in GPU memory, so the MPI library must be GPU-aware.
- `bind` pins each rank's GPU, the NIC nearest to that GPU and a core on the same NUMA domain, from the
  preset's `topology`. The configs name no device, core, partition or GPU request: those are the
  preset's. [Placement that runs on any cluster](../../placement/README.md) explains `bind`.
- `crab validate` prints one warning per experiment that the `--cpu-bind` flag of the preset is
  replaced by CRAB's core binding. That is expected: `"cores": "nearest"` binds each rank itself.
