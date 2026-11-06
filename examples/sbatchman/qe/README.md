# Quantum ESPRESSO input for the SbatchMan examples

The jobs files `qe-alone.yaml` and `qe-noise.yaml` (one folder up) run Quantum ESPRESSO `pw.x` on
this input.

| File | What it is |
|---|---|
| `si-scf.in` | A `pw.x` self-consistent-field input for a 128-atom silicon supercell (2x2x4 diamond cells of 10.26 bohr, `ecutwfc = 60`, a 2x2x1 k-point grid). One run takes about 20 s on one Leonardo node with 4 GPUs. |
| `Si.pz-vbc.UPF` | The silicon LDA norm-conserving pseudopotential `si-scf.in` names under `ATOMIC_SPECIES`. It comes from the Quantum ESPRESSO pseudopotential library: <https://pseudopotentials.quantum-espresso.org/upf_files/Si.pz-vbc.UPF>. |
| `README.md` | This file. |

The jobs files read both from the CRAB checkout through `$CRAB_ROOT` (`input_file` and `pseudo_dir`
point at this folder), so nothing needs copying. The CRAB wrapper rewrites the `outdir` and
`pseudo_dir` lines of the input into the run folder, which is why the input carries placeholder
values for them. No download happens on a compute node: the pseudopotential is committed here.

Both jobs files use the `crab_qe_<nodes>` config of `configs.leonardo.yaml`, not `crab_run_<nodes>`.
The only QE module on Leonardo is a CUDA build behind the `profile/chem-phys` profile
(`module load profile/chem-phys quantum-espresso`): without a GPU, `pw.x` aborts at start with
`error in Message Passing (mp) module, error code 9100`. `crab_qe_<nodes>` therefore requests 4 GPUs
per node and loads that module. The receipt `crab setup` writes must load the profile too, or
`module load quantum-espresso` finds nothing.

`qe-noise.yaml` also needs the Blink receipt's `pre_run` hook to start with `module purge`
(`"pre_run": ["module purge", "module load gcc openmpi"]` in `local/receipts/blink.json`), see
"Apps built with different module stacks" in `docs/using/sbatchman-integration.md`.

The input is sized from a timing on Leonardo (one node, 4 A100 GPUs, 4 MPI ranks): CRAB repeats an
experiment until it converges (10 successful runs) and `qe-noise.yaml` runs six experiments in one
job, so one run has to stay short. To make a run longer or shorter, change the supercell,
`ecutwfc` (the plane-wave cutoff, in Ry) or the k-point grid on the `K_POINTS` line, and time it on
the cluster.
