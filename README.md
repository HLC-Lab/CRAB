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
| `lib.sh` | Shared helpers (sourced). |
