# Baselines

One template: `collectives.json`.

`collectives.json` measures each collective alone on 8 nodes with 1 rank per node. It has ten
experiments, one per collective and message size:

- `a2a_8B`, `a2a_64B`, `a2a_4KB`, `a2a_64KB`, `a2a_1MB`: the communication-only all-to-all
  (`alltoall_comm_only`).
- `incast_8B`, `incast_64B`, `incast_1MB`: incast (`incast_b`), where every rank sends to one.
- `agtr_64B`, `agtr_1MB`: the communication-only allgather (`allgather_comm_only`).

Nothing else runs, so the results are the reference time of each collective. Compare them with the
runs in [`../congestion/`](../congestion/README.md): the ratio is the slowdown an aggressor causes.
Each experiment repeats between 10 and 30 times, and stops early once the average duration has
converged (`alpha` and `beta` 0.05).

It needs 8 nodes, Blink and the three wrappers above. Change `numnodes` for a smaller or larger
cluster. The time limit is 2 hours.
