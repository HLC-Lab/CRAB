# Supported benchmarks

These are the benchmarks CRAB officially supports — the ones with a **recipe**, so `crab setup`
can obtain and build them and generate a [receipt](../glossary.md#receipt) automatically.

| Benchmark | `benchmark_id` | Source | Built binary | Launcher |
|-----------|----------------|--------|--------------|----------|
| **Blink Suite** | `blink` | [SharkGamerZ/blink-clean](https://github.com/SharkGamerZ/blink-clean) | `bin/` (e.g. `ping-pong_b`) | cluster default |
| **Graph500** | `g500` | [graph500/graph500](https://github.com/graph500/graph500) | `src/graph500_reference_bfs` | cluster default |
| **Quantum ESPRESSO v6** | `qe-v6` | [QEF/q-e](https://gitlab.com/QEF/q-e) (tag `qe-6.8`) | `bin/pw.x` | `mpirun` (override) |
| **Quantum ESPRESSO v7** | `qe-v7` | [QEF/q-e](https://gitlab.com/QEF/q-e) | `bin/pw.x` | `mpirun` (override) |

## Blink Suite

A suite of MPI communication microbenchmarks (all-to-all, ping-pong, incast, reductions, ring,
and more). A **single recipe builds the whole suite** — `make CC=mpicc CXX=mpicxx` produces all
the binaries under `bin/`. The individual microbenchmarks are exposed through the wrappers in
`wrappers/blink/`, which you reference from an experiment config by file name (e.g.
`a2a_comm_only.py`, `ping-pong_b.py`). This is the suite most communication-interference studies
build on.

## Graph500

The reference BFS implementation of the Graph500 benchmark. Built with
`make MPICC="mpicc -fcommon"`; the wrapper parses its output into graph-traversal metrics
(e.g. GTEPS).

## Quantum ESPRESSO

The QE DFT application (`pw.x`), available as two versions sharing one suite:

- **v6** pins to the `qe-6.8` tag; **v7** builds the latest `master`.
- Both build with CMake and accept a **`cpu` / `gpu` architecture** parameter during
  `crab setup` (the GPU path configures NVHPC/CUDA flags).
- Both set a `launcher_override` of `mpirun`, so QE is launched with `mpirun` even on Slurm
  systems where the default launcher is `srun`.

## Benchmarks without a recipe

The `wrappers/` tree contains additional wrappers (under `wrappers/others/` — AMG, miniFE,
Ember, GPU-communication benchmarks, DNN proxies, and others). These do **not** currently have
recipes, so `crab setup` cannot build them from source. However, if you already have the binary
installed on your cluster, you can still use them: run `crab setup`, choose **"Register a custom
already-installed benchmark"**, and supply the path. The wrapper will then resolve the binary from
the generated receipt like any other benchmark. The wrappers themselves are not guaranteed to work
as-is — treat them as work-in-progress.

## Adding your own

To make CRAB run an application that isn't listed here — whether something you wrote or an
existing application not yet supported — you write a wrapper (and optionally a recipe). See
[Extending CRAB](../extending/overview.md).
