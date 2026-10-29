# NPLB presets

The preset of the NPLB H100 test cluster, as a file to copy into your overrides folder. CRAB ships
no preset for it. See [section 2 of the NPLB campaign README](../README.md#2-preset) for the
commands and the other launcher choices.

| File | What it does |
|------|--------------|
| `nplb_h100.json` | The complete preset `nplb_h100` of the 4-node H100 test cluster: Slurm partition `h100_4nodes`, Open MPI's `mpirun` (dialect `openmpi4`) over UCX, `--allow-run-as-root`. `bringup.sh` runs its jobs with `-p nplb_h100`. |
| `nplb_h100_gpu.json` | The preset `nplb_h100_gpu`: the same as `nplb_h100`, plus the CUDA-aware `mpirun` (`launchers.mpirun.command`, `/root/crab/lab/ompi-cuda/bin/mpirun`) and an `env` `LD_LIBRARY_PATH` that puts the lab's CUDA-aware UCX, CUDA 12.3 and that Open MPI first, so Blink `-gpu` runs launch through it. It needs `~/crab/lab/ompi-cuda` and `~/crab/lab/ucx-cuda` built on the nodes. Use it as `-p nplb_h100_gpu`. |

Copy both files together into the overrides folder (`local/presets/` in a checkout, `~/.config/crab/presets/` in a
pip install; `crab info` prints the one in use), then check it with `crab presets show nplb_h100`.
For another cluster, copy it under a new file name, rename the preset and change the partition.
