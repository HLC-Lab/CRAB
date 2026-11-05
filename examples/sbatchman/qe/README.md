# Quantum ESPRESSO input for the SbatchMan examples

The jobs files `qe-alone.yaml` and `qe-noise.yaml` (one folder up) run Quantum ESPRESSO `pw.x` on
this input.

| File | What it is |
|---|---|
| `si-scf.in` | A `pw.x` self-consistent-field input for 8-atom cubic diamond silicon (`a` = 10.26 bohr, `ecutwfc = 40`, a 4x4x4 k-point grid). |
| `Si.pz-vbc.UPF` | The silicon LDA norm-conserving pseudopotential `si-scf.in` names under `ATOMIC_SPECIES`. It comes from the Quantum ESPRESSO pseudopotential library: <https://pseudopotentials.quantum-espresso.org/upf_files/Si.pz-vbc.UPF>. |
| `README.md` | This file. |

The jobs files read both from the CRAB checkout through `$CRAB_ROOT` (`input_file` and `pseudo_dir`
point at this folder), so nothing needs copying. The CRAB wrapper rewrites the `outdir` and
`pseudo_dir` lines of the input into the run folder, which is why the input carries placeholder
values for them. No download happens on a compute node: the pseudopotential is committed here.

The input is sized as a guess. To make a run longer or shorter, change `ecutwfc` (the plane-wave
cutoff, in Ry) or the k-point grid on the `K_POINTS` line, and time it on the cluster.
