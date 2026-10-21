# Scale

Two templates that run many short experiments in one job on a large allocation. Each experiment
repeats a few times only (`minruns` 1 to 3), because a large job is expensive. They check that a
cluster handles big runs, and they cover the scheduling patterns CRAB offers.

`comprehensive_64_nodes.json` needs 64 nodes and a 30 minute limit. It has 18 experiments and
splits the nodes into a victim partition and an aggressor partition of 32 nodes each:

- 01 to 05: all-to-all with 1 KiB to 4 MiB messages, on the victim partition.
- 06 to 09: allreduce, broadcast, incast and ring with 4 KiB messages.
- 10, 11, 12 and 17: dependencies and timing. A ring that starts after an all-to-all, an all-to-all
  that starts 1 second after an incast, an incast that CRAB stops when the all-to-all finishes, and
  a barrier, an incast and an all-to-all started in turn.
- 13 to 16: interference between the two partitions. All-to-all against incast, ring against
  broadcast, allreduce against incast noise, and two independent all-to-alls.
- 18: a longer all-to-all run, to check that results stay stable over time.

`comprehensive_256_nodes.json` needs 256 nodes and a 2 hour limit. It has 21 experiments:

- 01 to 08: all-to-all with 1 KiB to 256 MiB messages. Experiment 01 runs on all 256 nodes
  (its `local_options` replace the partitions). Experiments 02 to 08 run on the victim partition of
  128 nodes.
- 09 to 12: dependencies and timing, as above, with a barrier added to the last one.
- 13 to 16: interference between two partitions of 128 nodes, and a quiet-neighbour reference
  where the second half stays idle.
- 17 to 20: allreduce, broadcast, incast and ring on all 256 nodes.
- 21: an all-to-all with 5000 iterations.

A large job is the main cost. To try one of these on a smaller cluster, lower `numnodes`: the
partitions split the nodes equally. The largest messages need a lot of memory on every rank, so
lower them too if a job runs out of memory.
