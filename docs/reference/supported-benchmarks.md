# Supported benchmarks

These are the benchmarks CRAB officially supports — the ones with a **recipe**, so `crab setup`
can obtain and build them and generate a [receipt](../glossary.md#receipt) automatically.

| Benchmark | `benchmark_id` | Source | Built binary | Launcher |
|-----------|----------------|--------|--------------|----------|
| **Blink Suite** | `blink` | [HLC-Lab/blink](https://github.com/HLC-Lab/blink) | `build/bin/` (e.g. `pingpong_b`) | cluster default |
| **Graph500** | `g500` | [graph500/graph500](https://github.com/graph500/graph500) | `src/graph500_reference_bfs` | cluster default |
| **Quantum ESPRESSO v6** | `qe-v6` | [QEF/q-e](https://gitlab.com/QEF/q-e) (tag `qe-6.8`) | `bin/pw.x` | `mpirun` (override) |
| **Quantum ESPRESSO v7** | `qe-v7` | [QEF/q-e](https://gitlab.com/QEF/q-e) | `bin/pw.x` | `mpirun` (override) |

## Blink Suite

A suite of MPI communication microbenchmarks (all-to-all, ping-pong, incast, reductions, ring,
and more). A **single recipe builds the whole suite**. Blink builds with CMake, and the recipe
runs `cmake ... -DBLINK_TESTS=OFF` (this skips Blink's own tests, which would download
googletest), so all the programs end up in `build/bin/`. This is the suite most
communication-interference studies build on.

The wrappers for the individual programs are in the
[crab-wrappers](https://github.com/HLC-Lab/crab-wrappers) repository, under `blink/`, in the same
folders Blink keeps their sources in (`src/alltoall/` → `blink/alltoall/`). Each one is named after
the program it launches: `blink/alltoall/alltoall_comm_only.py` runs `alltoall_comm_only`,
`blink/pingpong/pingpong_b.py` runs `pingpong_b`. You reference them from an experiment config by
that path.

A config that still uses a path from before the folders (`blink/alltoall_comm_only.py`) keeps
working: CRAB finds the file in its folder and the run log has a warning with the new path, so you
can update the config.

Configs written for the older short names (such as `a2a_comm_only`) need renaming. The
[rename table and a `sed` command](https://github.com/HLC-Lab/crab-wrappers/blob/main/blink/README.md)
that does it are in the wrappers repository.

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

The wrappers repository contains additional wrappers (under `others/` — AMG, miniFE,
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
