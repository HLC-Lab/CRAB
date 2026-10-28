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
| `nic.sh` | Rank wrapper: sets `UCX_NET_DEVICES` (`LAB_NICS`) and `CUDA_VISIBLE_DEVICES` (`LAB_GPUS`) per rank, prints where the rank runs. |
| `diag2.sh` | Two nodes: routes per port, `srun --mpi=pmi2`, the 10-19 one-port-per-rank launch with one change at a time, ports UCX picks at 1/4/8 ranks per node. |
| `diag3.sh` | Two nodes: UCX RoCE settings, policy routing, barrier algorithm vs one port per rank, all2all with one port per rank. |
| `pw.sh` | Blink pairwise alone at 1/4/8 ranks per node, per UCX variant and size: Gb/s per rank on screen, CSV in `out/`. |
| `diag4.sh` | Two nodes: UCX RoCE reachability check off (all2all with one port per rank), which campaign UCX variable matters, the CRAB checkout. |
| `pw2.sh` | Blink pairwise alone at 1/4/8 ranks per node, one port per rank: slowest/fastest rank and node total per size, 1000 iterations. |
| `wb.sh` | ib_write_bw between the two nodes, no MPI: one port, 4 ports of one socket, 2 + 2, all 8, one and both directions. `USE_CUDA=1` puts the buffers in GPU memory. |
| `gpu_check.sh` | Read-only: GPUs, driver, CUDA, NCCL, GPUDirect module, GPU NUMA nodes and nearby ports. |
| `pp.sh` | pairwise_b vs ping-pong per pair (pingpong_pairwise_b) vs pairwise_nb, with and without put_zcopy, 1/4/8 ranks per node, 128 MiB and 2 MiB. |
| `pcie.sh` | Read-only: PCIe path and link speed/width from the CPU to every network port and GPU. |
| `nccl_build.sh` | Checks both nodes (CUDA, NCCL, GPU processes) and builds nccl-tests with MPI into `nccl-tests/`. |
| `nccl_run.sh` | GPU g on one node with GPU g on the other, one port per GPU: 1, 4 (one socket), 2 + 2, 8 pairs at once; sendrecv and broadcast. |
| `gpu_pre.sh` | Read-only preflight for GPU memory tests: CUDA-aware Open MPI/UCX, cmake, libcudart, peermem, perftest `--use_cuda`, GitHub. |
| `blink_gpu_build.sh` | Builds the GPU-capable Blink (`-gpu`) into `blink-gpu/` next to this folder; checks libraries on both nodes. |
| `gpu_pw.sh` | `smoke` then `main`: Blink host vs `-gpu`, pairwise and ping-pong, 8/4/4/1 ranks per node, put_zcopy and GPUDirect off. |
| `lib.sh` | Shared helpers (sourced). |
