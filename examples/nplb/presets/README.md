# NPLB presets

The preset of the NPLB H100 test cluster, as a file to copy into your overrides folder. CRAB ships
no preset for it. See [section 2 of the NPLB campaign README](../README.md#2-preset) for the
commands and the other launcher choices.

| File | What it does |
|------|--------------|
| `nplb_h100.json` | The complete preset `nplb_h100` of the 4-node H100 test cluster: Slurm partition `h100_4nodes`, Open MPI's `mpirun` (dialect `openmpi4`) over UCX, `--allow-run-as-root`. `bringup.sh` runs its jobs with `-p nplb_h100`. |

Copy it into the overrides folder (`local/presets/` in a checkout, `~/.config/crab/presets/` in a
pip install; `crab info` prints the one in use), then check it with `crab presets show nplb_h100`.
For another cluster, copy it under a new file name, rename the preset and change the partition.
