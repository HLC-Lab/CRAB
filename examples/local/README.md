# Local examples

Two configs that run on one machine with no Slurm. Both are ready to run: they name no account,
partition or module, and they use the `local` preset that ships with CRAB. Start with
`sequential_barrier.json`.

## What you need

- CRAB, with the wrappers folder (`crab update` clones it when it is missing).
- Open MPI or MPICH, so that `mpirun` works. The `local` preset launches every app with it.
- Blink, the benchmark suite behind `blink/...` wrappers. `crab setup` builds it: tick only the Blink
  Suite and choose the build-from-source strategy. `crab list-benchmarks` shows whether it is installed.

Always pass `-p local`. CRAB never picks a local preset on its own.

## Files

`sequential_barrier.json` runs `barrier_b` four times in a chain, on 2 ranks. Each run starts only
after the previous one has finished (`"start": "s0"`, `"s1"`, `"s2"`). It measures how long an MPI
barrier takes on your machine, and it shows how `start` and `end` order apps. It is quick, so use
it to check that CRAB and Blink work.

`concurrent_collectives_stress.json` measures an all-to-all (`alltoall_b`) and a ring (`ring_nb`),
both with 1 MiB messages, while a barrier loop (`barrier_b -endl`) runs next to them. The
barrier app is the aggressor: it is not collected, and CRAB stops it when the other two finish. All
three apps share the machine, and the config pins each one to its own cores with `cpus`: cores
0 and 1, 2 and 3, 4 and 5. Compare it with a run where the barrier app is removed to see what the
neighbour costs. It needs six cores. On a smaller machine, lower `ppn` and shorten the `cpus` lists
so that each app keeps its own cores.

## Run

```bash
crab validate -p local examples/local/sequential_barrier.json
crab run -p local examples/local/sequential_barrier.json
```

`crab run` returns at once with a job id such as `local-1`. Use `crab status local-1` to see when it
ends, and look for the results in `data/local/`. [Running without Slurm](../../docs/using/running-without-slurm.md)
explains local jobs, and [Reading results](../../docs/using/results.md) the files.
