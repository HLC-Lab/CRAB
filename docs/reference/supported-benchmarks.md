# Supported benchmarks

These are the benchmarks CRAB officially supports: the ones with a recipe, so `crab setup`
can obtain and build them and generate a [receipt](../glossary.md#receipt) automatically.

| Benchmark | `benchmark_id` | Source | Built binary | Launcher |
|-----------|----------------|--------|--------------|----------|
| Blink Suite | `blink` | [HLC-Lab/blink](https://github.com/HLC-Lab/blink) | `build/bin/` (e.g. `pingpong_b`) | cluster default |
| Graph500 | `g500` | [graph500/graph500](https://github.com/graph500/graph500) | `src/graph500_reference_bfs` | cluster default |
| Quantum ESPRESSO v6 | `qe-v6` | [QEF/q-e](https://gitlab.com/QEF/q-e) (tag `qe-6.8`) | `bin/pw.x` | `mpirun` (override) |
| Quantum ESPRESSO v7 | `qe-v7` | [QEF/q-e](https://gitlab.com/QEF/q-e) | `bin/pw.x` | `mpirun` (override) |

## Blink Suite

A suite of MPI communication microbenchmarks (all-to-all, ping-pong, incast, reductions, ring,
and more). A single recipe builds the whole suite. Blink builds with CMake, and the recipe
runs `cmake ... -DBLINK_TESTS=OFF` (this skips Blink's own tests, which would download
googletest), so all the programs end up in `build/bin/`. This is the suite most
communication-interference studies build on.

### Build Blink with `crab setup`

The [tutorial](../tutorial.md#step-1-build-the-benchmark) walks through the wizard for Blink. After
the module line, the wizard asks "GPU backend (auto uses CUDA or HIP when found, else CPU only)".
Press Enter to accept `auto`, or type one of the other answers:

| Answer | What the build does |
|--------|---------------------|
| `auto` (default) | Builds GPU support only if CMake finds a CUDA or HIP toolkit, otherwise a CPU-only build. |
| `off` | Builds for the CPU only, even if a toolkit is loaded. |
| `cuda` | Builds with CUDA. If CMake finds no CUDA toolkit, the configure step fails. |
| `hip` | Builds with HIP. If CMake finds no HIP toolkit, the configure step fails. |

A build panel then shows the steps (clone, configure with CMake, compile) with the last lines of
the log. Blink is built under `benchmarks/blink/`, and the wizard saves the receipt
`local/receipts/blink.json`.

The default `auto` is right on a machine without a GPU toolkit. When `cuda` or `hip` fails, the
message names the `gpu` setting and CMake's output, and tells you to load a module that provides
the toolkit or to set `gpu` to `auto` or `off`. The receipt records the answer you gave, not the
backend CMake found, so with `auto` it does not say whether the build has GPU support.

!!! note "Blink with CUDA on Leonardo"
    Checked on Leonardo, November 2026: after `module purge`, the module line
    `module load gcc/12.2.0 openmpi/4.1.6--gcc--12.2.0-cuda-12.2 cuda/12.2 cmake/3.27.9` configures
    and builds Blink with `gpu` set to `cuda`. CRAB's own virtual environment on that cluster also
    needs `module load python`. To run the result, use the `leonardo_gpu` preset and the configs in
    [`examples/leonardo/gpu/`](https://github.com/HLC-Lab/CRAB/tree/develop/examples/leonardo/gpu).

If an earlier build failed, it left a `benchmarks/blink` folder behind. The wizard then asks
"Found a leftover build at ... Remove it and start again?" Answer yes to remove the folder and
build again. If you answer no, Blink is skipped.

### Blink wrappers

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

- v6 pins to the `qe-6.8` tag; v7 builds the latest `master`.
- Both build with CMake and accept a `cpu` / `gpu` architecture parameter during
  `crab setup` (the GPU path configures NVHPC/CUDA flags).
- Both set a `launcher_override` of `mpirun`, so QE is launched with `mpirun` even on Slurm
  systems where the default launcher is `srun`.

## Benchmarks without a recipe

The wrappers repository contains additional wrappers (under `others/`: AMG, miniFE,
Ember, GPU-communication benchmarks, DNN proxies, and others). These have no
recipes, so `crab setup` cannot build them from source. If you already have the binary
installed on your cluster, you can still use them: run `crab setup`, choose "Register a custom
already-installed benchmark", and supply the path. The wrapper will then resolve the binary from
the generated receipt like any other benchmark. The wrappers themselves are not guaranteed to work
as-is, so treat them as work-in-progress.

## Adding your own

To make CRAB run an application that isn't listed here (whether something you wrote or an
existing application not yet supported), you write a wrapper (and optionally a recipe). See
[Extending CRAB](../extending/overview.md).
