# NPLB lab scripts (temporary)

Stand-alone diagnostics for the NPLB lab: plain `mpirun` and Blink, no CRAB. This branch shares
no history with the CRAB branches and is deleted when the lab work ends.

Setup, once, in a folder every node sees (next to the CRAB checkout):

    git clone -b nplb-lab --single-branch https://github.com/HLC-Lab/CRAB.git ~/crab/lab

Update: `git -C ~/crab/lab pull`. Logs go to `~/crab/lab/out/` (git-ignored).
Remove: `rm -rf ~/crab/lab`.

| Script | What it does |
|---|---|
| `diag1.sh` | Two nodes: ports, routes between ports, which port pairs MPI can connect, where ranks of a plain run land. |
| `facts.sh` | Per-node helper of `diag1.sh` (ports, routes). |
| `nic.sh` | Rank wrapper: sets `UCX_NET_DEVICES` per rank, prints where the rank runs. |
| `diag2.sh` | Two nodes: routes per port, `srun --mpi=pmi2`, the 10-19 one-port-per-rank launch with one change at a time, ports UCX picks at 1/4/8 ranks per node. |
| `diag3.sh` | Two nodes: UCX RoCE settings, policy routing, barrier algorithm vs one port per rank, all2all with one port per rank. |
| `pw.sh` | Blink pairwise alone at 1/4/8 ranks per node, per UCX variant and size: Gb/s per rank on screen, CSV in `out/`. |
| `diag4.sh` | Two nodes: UCX RoCE reachability check off (all2all with one port per rank), which campaign UCX variable matters, the CRAB checkout. |
| `pw2.sh` | Blink pairwise alone at 1/4/8 ranks per node, one port per rank: slowest/fastest rank and node total per size, 1000 iterations. |
| `wb.sh` | ib_write_bw between the two nodes, no MPI: one port, 4 ports of one socket, 2 + 2, all 8, one and both directions. |
| `gpu_check.sh` | Read-only: GPUs, driver, CUDA, NCCL, GPUDirect module, GPU NUMA nodes and nearby ports. |
| `pp.sh` | pairwise_b vs ping-pong per pair (pingpong_pairwise_b) vs pairwise_nb, with and without put_zcopy, 1/4/8 ranks per node, 128 MiB and 2 MiB. |
| `pcie.sh` | Read-only: PCIe path and link speed/width from the CPU to every network port and GPU. |
| `lib.sh` | Shared helpers (sourced). |
