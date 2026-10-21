# NPLB H100 configs

One config for the NPLB H100 nodes, written by `make_configs.py` from the cluster profile and then
kept here as a worked example of the campaign's all-to-all study. See the
[NPLB campaign README](../README.md) for the tools, the network configurations and how to run a
set.

| File | What it does |
|------|--------------|
| `a2a_vs_a2a_C0_P1.json` | Phase 2 of the md set with an all-to-all as the victim and an all-to-all as the aggressor, on network configuration C0 (ECMP, NPLB off, selective repeat off) with one UCX path (`P1`). |

## What `a2a_vs_a2a_C0_P1.json` measures

It splits 4 nodes in two partitions of 50% each, `victim` and `aggressor`, with 16 ranks per node.
Each rank is pinned to one core and one NIC port of the H100 nodes (`cpus` and `nics`), and the
UCX variables `UCX_MAX_RNDV_RAILS=1` and `UCX_IB_NUM_PATHS=1` apply to every app. It has two
experiments:

- `p2_a2a_alone_C0_P1`: the victim, `alltoall_nb`, over message sizes from 1 B to 512 MiB, alone.
  This is the reference time.
- `p2_a2a_vs_a2a_C0_P1`: the same victim sweep while a steady `alltoall_nb` of 2 MiB messages
  runs on the aggressor partition. The aggressor starts 3 seconds before the victim and stops when
  the last size of the victim ends.

The sizes of one experiment run one after the other as a chain, so the aggressor runs once for the
whole sweep. The experiments run 20 to 40 times (`minruns` and `maxruns`, the C0 count of the
md set). Comparing the two experiments gives the slowdown that the aggressor causes under ECMP.

## Ready to run or a template

It is a template. The config is complete and valid, but it is tied to the NPLB H100 nodes: the
core list and the NIC names come from the probe of those nodes, and the nodes carry network
configuration C0 only when the network team has set it. On another cluster, or after the nodes
change, generate a fresh file with `make_configs.py` from your own profile (it also writes the
P4 and C2 variants), or copy this one and change `nics`, `cpus` and `numnodes`. A preset for the
cluster is still needed (`-p <preset>`; see section 2 of the campaign README).
