# NPLB campaign

Tools to measure how a fabric's per-packet load balancing (NPLB) compares with ECMP, with Blink
microbenchmarks run through CRAB. Every number in the results comes from Blink timings; NIC
counters are read only by the [NIC check](#check-that-each-rank-uses-its-own-nic) before the
campaign.

| File | What it does |
|------|--------------|
| `preflight.sh` | Checks over ssh that every node sees the CRAB checkout, the Blink programs and the same MPI, so ranks can start there. |
| `sr_state.sh` | Reads the Selective Repeat setting of every active NIC on every node (read only). |
| `sr_set.sh` | Sets the Selective Repeat setting of every active NIC on the given nodes and reads it back; see [below](#read-the-nic-side-of-the-network-configuration). |
| `probe.py` | Reads each node's RDMA NICs (ports, PCI address, speed, NUMA node, netdev, memory, GPU layout) and writes a cluster profile with the `nics` and `cpus` lists to use. |
| `nic_check.sh` | Runs Blink in several placements on 2 nodes and shows per-rank bandwidth and the bytes each NIC sent, to confirm every rank uses its own NIC. |
| `make_configs.py` | Writes the CRAB configs of both test sets from the profile (`md_set.py`, `colleague_set.py`, `naming.py`). |
| `gpu_set.py` | Writes the GPU comm-only suites (all-to-all and all-gather victims with the buffers in GPU memory, against a steady all-to-all aggressor); see [below](#gpu-comm-only-suites). |
| `topology.sh` | Reports each RDMA port of every node over ssh and listens for LLDP to name the leaf switch behind it (round 2, run by `bringup.sh topo`). |
| `topology_summary.py` | Turns the TSV of `topology.sh` into a one-screen grid and verdict, as `topology.sh` does at its end. |
| `pingpong_set.py` | Builds the ping-pong ceiling configs for `make_configs.py --set pingpong` (see [round 2](#round-2-topology-and-ping-pong-ceilings)). |
| `diagnose.sh` | Tries mpirun binding flags and `nic_check.sh` on NIC subsets and node pairs to find where the per-node bandwidth cap comes from; see [below](#diagnose-binding-and-the-bandwidth-cap). |
| `analyze.py` | Reads CRAB data directories and writes `summary.csv` and `summary.md`. |
| `plot.py` | Draws PNG charts from `summary.csv` (needs matplotlib). |

The scripts need Python 3.10 or newer. `probe.py` and `make_configs.py` use only the standard
library; `analyze.py` needs pandas (CRAB's `.venv` has it); `plot.py` also needs matplotlib.

## The network configurations

The network team sets these outside CRAB. Nothing inside a run can see which one is active, so log
it for every job (see [Run log](#run-log)).

| ID | Routing | NIC selective repeat | Runs |
|----|---------|----------------------|------|
| C0 | ECMP (NPLB off) | off | everything |
| C2 | NPLB | on | everything |
| C3 | NPLB | off | phase 1 only, one round, 1 path (sanity check: expected to degrade) |

The path count `P` is `UCX_IB_NUM_PATHS`. The generator writes it into every app's `env`, so `P1`
and `P4` configs differ only there. With one path, each rank pair is one flow, which ECMP hashes
onto one spine link; with four, the pair's traffic is split over four independently hashed flows.

## 1. Install

On the cluster, from your home or work directory:

```bash
git clone https://github.com/HLC-Lab/CRAB
cd CRAB
git checkout nplb
make            # virtual environment, wrappers, then the setup wizard
```

In the wizard, install Blink (the build needs CMake ≥ 3.16, a C/C++ compiler and an MPI). If
CRAB is already installed, `git fetch && git checkout nplb && git pull` and run `crab setup` only
if Blink is missing. Check the Blink build has the programs the configs use:

```bash
BLINK=$(.venv/bin/python -c "import crab.setup.memory as m; print(m.get_receipt('blink')['binary_path'])")
ls "$BLINK" | grep -E '^(pingpong_b|pairwise_nb|alltoall_nb|allgather_nb|incast_nb|alltoall_man|alltoall_comm_only|allgather_comm_only|allreduce_nb)$'
```

All nine names must be listed. If the folder only has older names (`o2o_nb`, `a2a_nb`, ...), the
receipt points at an old Blink build: run `crab setup` again and rebuild Blink.

## 2. Preset

CRAB ships no preset for the NPLB clusters: you put one in your overrides folder (see
`docs/using/presets.md#your-overrides`). It is any `*.json` file there, and a file may hold several
presets. In a checkout the folder is `local/presets/` (git-ignored); in a pip install it is
`~/.config/crab/presets/`; `crab info` prints the one in use on its `overrides:` line.

### The H100 test cluster

`examples/nplb/presets/nplb_h100.json` is the complete preset of the 4-node H100 test cluster
(partition `h100_4nodes`, Open MPI's own `mpirun` over UCX, see
[below](#clusters-where-srun-cannot-start-mpi)). Copy it into the overrides folder, then check
that CRAB reads it:

```bash
mkdir -p local/presets                            # make the overrides folder (it does not exist yet)
cp examples/nplb/presets/nplb_h100.json local/presets/   # the preset is now found by name: -p nplb_h100
.venv/bin/crab presets show nplb_h100             # the preset as CRAB will use it, and the file it came from
```

`bringup.sh` runs its jobs with `-p nplb_h100`, so keep that name for this cluster. For another
cluster, copy the file under a new file name, rename the preset in it and change the partition
(and the launcher advice below), for example `local/presets/mycluster.json`.

### Another cluster

A starting point for a Slurm cluster with Open MPI built with PMIx, as `local/presets/mycluster.json`:

```json
{
  "mycluster": {
    "description": "NPLB test cluster",
    "scheduler": "slurm",
    "launcher": "srun",
    "launchers": { "srun": { "flags": ["--mpi=pmix"] } },
    "env": {},
    "sbatch": ["--account=<account>", "--partition=<partition>"],
    "header": ["module purge", "module load <the modules Blink was built with>"]
  }
}
```

Leave `--cpu-bind` out of the flags: the configs set `cpus`, and CRAB binds those apps itself (it
warns at submit if it has to drop a `--cpu-bind`). `srun --mpi=list` shows which PMI plugins the
cluster offers.

### Clusters where srun cannot start MPI

If `srun --mpi=list` offers no PMI plugin that the cluster's Open MPI was built with (no `pmix`),
launch with Open MPI's own `mpirun` instead; it still runs inside the Slurm allocation:

```json
{
  "mycluster": {
    "description": "NPLB test cluster, mpirun",
    "scheduler": "slurm",
    "launcher": "mpirun",
    "launchers": {
      "mpirun": {
        "dialect": "openmpi4",
        "flags": ["--mca", "pml", "ucx", "--mca", "btl", "^openib"]
      }
    },
    "env": {},
    "sbatch": ["--partition=<partition>"],
    "header": []
  }
}
```

CRAB passes the app's `cpus` as `--cpu-list <cores> --bind-to cpu-list:ordered`, which binds
local rank *i* to the *i*-th core (with Open MPI 4.1, `--cpu-set` is only an envelope and leaves
the ranks unbound). Leave `--map-by` and `--bind-to` out of the preset flags. Add
`--allow-run-as-root` when the jobs run as root.

`mpirun` reads every program and every per-rank script from the same path on every node, so the
CRAB checkout (its `benchmarks/` and `data/` folders included) must be on a filesystem all nodes
share. Without one, export the checkout's parent folder from the node that holds it over NFS and
mount it at the same path on the others. Then check every node from that node:

```bash
examples/nplb/preflight.sh <node1> <node2> <node3> <node4>   # or: PARTITION=<partition> examples/nplb/preflight.sh
```

It prints one `OK`, `WARN` or `FAIL` line per check and node and exits 1 on any `FAIL`. It checks
that the checkout is there and NFS-mounted (exported, on the node you run it from), that `data/`
is writable, that the nine Blink programs are there, and that `mpirun`, the `libmpi` that Blink
loads, `ompi_info`, `ucx_info` and CRAB's Python are the same as on the node you run it from. The
`route` line shows which network interface reaches that node: a `WARN` naming an RDMA device
means the NFS traffic shares a link with the measurements.

## 3. Probe the nodes

Run the probe once per node, on the nodes the campaign will use, and merge the lines:

```bash
srun -N4 --ntasks-per-node=1 --exclusive -A <account> -p <partition> -t 00:05:00 \
     .venv/bin/python examples/nplb/probe.py --json > probe.jsonl
.venv/bin/python examples/nplb/probe.py --merge probe.jsonl --out profile.json
```

The merge prints a table and a list of warnings, and writes `profile.json`:

- `nodes`: the hostnames in Slurm's order. The configs assume the order Server1, Server2, Server3,
  Server4 (partitions are dealt in this order). If Slurm orders them differently, pin the order
  with `--nodelist` in the preset's `sbatch` or ask the admins.
- `nics` and `cpus`: one active port per rank and one core next to each card, already ordered so
  that rank *i* gets `nics[i]` and the *i*-th core (CRAB sorts `cpus`, so the NICs follow the
  cores).
- `ppn`: the number of active ports, which becomes the ranks per node.
- `mem_gib`: the smallest node memory, used to skip message sizes whose buffers would not fit.

Read every warning before going on. In particular:

- **A device with two active ports** may be one dual-port NIC exposed as two devices: then one
  device per rank uses only half the card. Report it before running the campaign.
- **Link rate** per port: record it in the run log. It sets the expected bandwidth ceilings.
- **Link layer** must be `Ethernet` (RoCE) on the NPLB fabric.

When each card shows up as two devices (two PCI functions of one slot, such as `mlx5_0` and
`mlx5_1` on `0000:19:00.0` and `0000:19:00.1`), the default profile has one rank per port. For
one rank per card, merge again with `--per-card`: it keeps the lowest active port of each card
(the device with the lowest PCI function that has one) and pairs the cores the same way:

```bash
.venv/bin/python examples/nplb/probe.py --merge probe.jsonl --out profile16.json
.venv/bin/python examples/nplb/probe.py --merge probe.jsonl --per-card --out profile8.json
```

Which port of a card faces which switch is not visible from the node: ask the network team
before comparing per-card results with per-port ones.

### Read the NIC side of the network configuration

```bash
examples/nplb/sr_state.sh <node1> <node2> <node3> <node4>
```

For every active device of every node it reads the `ROCE_ACCL` register with `mlxreg` and prints
Selective Repeat (`SR`) and adaptive routing (`AR`). It exits 0 only when every device was read
and all SR values agree. It never changes them. Record the result in the run log; the switch side
(NPLB on or off) has to be read on the switches.

`sr_set.sh` changes the setting:

```bash
examples/nplb/sr_set.sh 1 <node1> <node2> <node3> <node4>    # 1 turns SR on, 0 turns it off
```

On each node it writes the selective repeat field of `ROCE_ACCL` on every device with an active
port, reads it back and prints `OK` or `FAIL` per device. It exits 0 only when every device reads
back the value. Use it to put the NICs in the state a network configuration needs (C2 has SR on,
C0 and C3 have it off). The change is lost at reboot, so run `sr_state.sh` afterwards and again
before a campaign.

The probe cannot see which leaf each NIC is cabled to. Get the NIC-to-leaf wiring from the
network team and record it; the 3-node layout and `p0_local` depend on it.

### Check that each rank uses its own NIC

On a new cluster, run this before generating configs. If the ranks of a node end up sharing one
card, every phase-2 slowdown reads 1.00, because each node is already capped before the aggressor
starts. In a 2-node allocation:

```bash
salloc -N2 --exclusive -A <account> -p <partition> -t 00:15:00
BLINK=$(.venv/bin/python -c "import crab.setup.memory as m; print(m.get_receipt('blink')['binary_path'])")
SRUN_FLAGS="--mpi=pmix" examples/nplb/nic_check.sh profile.json "$BLINK"
```

Load the modules Blink was built with first, as in the preset's `header`. `SRUN_FLAGS` takes the
preset's launcher flags (leave it out if it has none); `MSG` and `ITER` change the message size
(default 2 MiB) and the iterations (default 200). The script runs Blink's `pairwise_nb` on the two
nodes in four placements (1 rank per node; all ranks with one NIC each; all ranks on the first
NIC; the same with Open MPI forced onto UCX). For each it prints the per-rank bandwidth and the
GB each NIC sent, read from the nodes' RDMA counters (`port_xmit_data`).

Good: `per-rank` is close to `one-rank` (about one link's rate per rank) and its bytes are spread
evenly over every NIC. Bad: `per-rank` is close to `first-nic`, about `one-rank` divided by the
ranks per node, which means the ranks share one card's bandwidth whatever `UCX_NET_DEVICES` says;
the counters then show where the bytes went. A `per-rank-ucx` that fails or differs from `per-rank`
is a finding too: note it.

If `per-rank` is not close to `one-rank`, stop: phase 2 would show slowdown 1.00 because each node
is capped. A counter shown as `n/a` is not readable on that node; the bandwidth is still valid.

On a cluster that launches with `mpirun` (see the preset section), run it with:

```bash
LAUNCHER=mpirun MPIRUN_FLAGS="--mca pml ucx --mca btl ^openib" \
    examples/nplb/nic_check.sh profile.json "$BLINK"
```

(add `--allow-run-as-root` to `MPIRUN_FLAGS` when running as root). The ranks then start with
`mpirun -H <the two allocated hosts> --cpu-list <the profile's cores> --bind-to cpu-list:ordered`,
the same way CRAB launches them; the NIC counters are still read with plain `srun`. There are
three cases (no `per-rank-ucx`: the flags already choose UCX), each followed by `unbound ranks: N`
from mpirun's `--report-bindings`, and `per-rank` also lists the binding of every rank. Good: 0
unbound ranks, and local rank *i* bound to the *i*-th core of the profile's `cpus`.

## 4. Generate the configs

```bash
.venv/bin/python examples/nplb/make_configs.py profile.json --out configs/
```

Defaults: both test sets, C0 and C2 with 1 and 4 paths, plus C3 for phase 1 with 1 path. The
summary lists every file, its experiments, the message sizes skipped for memory, and the
assumptions it made. Useful flags: `--set md|colleague|both`, `--net C0 C2`, `--paths 1`,
`--minruns`, `--maxruns`, `--walltime`, `--timeout`, `--mem-gib`, `--iter-small`,
`--iter-128m`, `--iter-512m`. `--smoke` writes a short version (3 sizes, 50 iterations, one run)
to test the whole chain. `--aggressors a2a burst incast` limits phase 2 of the md set to those
aggressors (`burst` is the whole burst grid; the 3-node layout only has `pw`), for example
`--aggressors a2a` for the victims alone and against a steady all-to-all.

`--tag T` appends `_T` to every file name, config name and job name (experiment names do not
change), so two variants of the same campaign stay apart, such as `--tag ppn8` from
`profile8.json` and `--tag ppn16` from `profile16.json`. A tag has letters, digits and `-` only.

In the md set, every app except the aggressors times `-grty G` operations per sample, with
`G = min(100, ceil(2 MiB / message size))`: 100 up to 4 KiB, 64 at 32 KiB, 8 at 256 KiB, 1 from
2 MiB. Blink synchronizes the ranks before every timed sample, so with one small operation per
sample the times mostly measure how far apart the ranks start. The colleague set keeps its own
arguments.

The layout follows the node count in the profile: 4 nodes use partitions Server1+Server3 (victim)
and Server2+Server4 (aggressor); 3 nodes use Server1+Server3 (victim) and Server2 (aggressor).

## 5. Run

One config is one Slurm job. Run the files of the network configuration that is active now, for
example C0:

```bash
for f in configs/md_*_C0_P1.json configs/col_*_C0_P1.json; do
    .venv/bin/crab run -p mycluster "$f"
done
```

Change `UCX_IB_NUM_PATHS` rounds by running the `_P4` files. When the network team switches to
C2, run the `_C2_` files. Log each job (see [Run log](#run-log)).

Results go to `data/<preset>/<config name>_<timestamp>/`.

### Check the first job

In the job's data directory:

- `crab_run.json` → `launches`: each app's launch line, `ppn`, `env` (with `UCX_IB_NUM_PATHS`
  and `UCX_MAX_RNDV_RAILS`) and `nics`.
- `<experiment>/bindings_app_<id>.log`: one line per rank, such as
  `crab: <host> local rank 3 -> UCX_NET_DEVICES=mlx5_3:1`, and srun's CPU masks
  (`cpu-bind=MAP ... mask 0x...`). Every local rank should name a different card and a core next
  to it.

## 6. Analyze and plot

```bash
.venv/bin/python examples/nplb/analyze.py data/mycluster/md_* data/mycluster/col_* --out results/
```

With tagged configs, analyze one tag at a time (`data/mycluster/md_*_ppn8_*` into one `--out`,
`*_ppn16_*` into another): the C0-vs-C2 tables pair rows by experiment, not by file, so two
variants in one call would be mixed.

`results/summary.csv` has one row per data directory, experiment and message size;
`results/summary.md` has the tables, including **C0 vs C2** side by side. The metrics:

| Column | Meaning |
|--------|---------|
| `bw_gbps` | per-rank bandwidth: message size × granularity / median time; for pingpong, one-way, from rank 0's round trip / 2 (Blink's `MainRank` column) |
| `spread_med`, `spread_p99` | max / min time across ranks, per iteration (median and p99): the collision signal. `–` for pingpong. At small sizes it mostly measures how far apart the ranks enter each timed window (Blink runs an `MPI_Allreduce` before every window), so read it at large sizes |
| `run_cv` | coefficient of variation of the per-run medians: ECMP hash luck |
| `slowdown_med`, `slowdown_p99` | victim time with the aggressor / victim time alone, same file and size |

The plots need matplotlib, which is not part of CRAB. Use a separate environment, on the cluster or
after copying `summary.csv` to another machine:

```bash
python3 -m venv ~/nplb-plot && ~/nplb-plot/bin/pip install matplotlib pandas
~/nplb-plot/bin/python examples/nplb/plot.py results/summary.csv --out results/plots/
```

It writes `bw_*.png` and `spread_*.png` per test without an aggressor, and `slowdown_*.png` with
one panel per aggressor, C0 and C2 on the same axes.

## Round 2: topology and ping-pong ceilings

Two stages, one command each, run on the node that holds the CRAB checkout. Both read
`profile8.json` (per card) and `profile16.json` (every port) from `PROFILE_DIR` (the checkout by
default), written in [section 3](#3-probe-the-nodes). `bringup.sh` sources `env.sh`, echoes every
command before it runs and stops at the first failure.

```bash
examples/nplb/bringup.sh topo           # the topology probe
examples/nplb/bringup.sh pingpong       # generate and submit the 4 ping-pong jobs
examples/nplb/bringup.sh analyze pingpong   # when the jobs are done
examples/nplb/bringup.sh pw 4 8         # node total with 4 and with 8 ranks per node, one long run
examples/nplb/bringup.sh analyze pw     # when those jobs are done
```

**`topo [HOST...]`** runs `topology.sh`: over ssh, every node reports each RDMA port (state,
speed, netdev, bond, PCI, NUMA) and listens for one LLDP frame per netdev, which names the leaf
switch and port behind it. Without a HOST it lists the nodes of `PARTITION`. It changes nothing
on the nodes or the switches. It takes about `LLDP_WAIT` (40 s by default) plus the ssh round
trips, around a minute, and prints a summary of at most 40 lines; the raw lines go to
`$WORK/topology_<time>.tsv`. It exits with the code of `topology.sh` (0 all nodes answered, 1 a
node failed, 2 setup missing).

A good result: every port has a speed and a `leaf:port`, every node is named in a pod, and the
victim pair verdict reads `YES` (the victim pair crosses the spine). If it says `NO` or
`UNKNOWN`, photograph the summary and stop before the a2a round: the node order or the layout
must change. If LLDP is not visible on the hosts, the summary says so: the leaf wiring then has to
come from the switch reader (a later phase), and the rest of the output still holds.

**`pingpong`** removes the old `*.json` of `$WORK/pingpong`, generates the ping-pong set into it
(`--set pingpong` with the per-card profile and `--all-ports` the every-port one; one `NET`
and one `PATHS`, `RUNS` runs, 1000 iterations up to 128 MiB and 200 at 512 MiB) and submits
its 4 jobs: the pairs node 1 with nodes 2, 3 and 4, each with three variants (`md` with the
campaign's UCX settings, `default` with no UCX variable, `dual` with both ports of the card and
two rails), and `pw8`, the node total: a pairwise permutation of every rank of node 1 against
node 3, one port per card. Each submit adds a line to `$WORK/run_log.tsv`. All 4 jobs
are submitted at once; node 1 is in every pair and every config asks for `--exclusive`, so
Slurm runs them one after another, about 1.5 to 2 hours in total. `bringup.sh status` shows the queue and the last submits.

**`analyze pingpong`** runs `analyze.py` on the data directories `$DATA_ROOT/*/pp_*` into
`$WORK/results_pingpong/` and prints the first 60 lines of its `summary.md`.

A good result: `md` and `default` agree, `dual` is about twice `md` at large sizes (two rails),
and the `pw8` node total is about 8 times the md bandwidth of the pair n1-n3 (one port per card,
every card busy).

**`pw N...`** repeats the node total for other numbers of ranks per node, one `N` at a time (for
example `pw 4 8`). For each `N` it generates the ping-pong set with `--ranks N` into
`$WORK/pw/ppn<N>` (the old `*.json` there are removed) and submits only the node-total file
`pp_n1n3_pw<N>_*.json`, with `N` ranks per node: a pairwise permutation of the ranks of node 1
against node 3, on every `ppn/N`-th card of the per-card profile (`pw4` on 8 cards uses cards
0, 2, 4 and 6). The file holds two experiments: `pw<N>` with the campaign's UCX settings and
`pw<N>d` with no UCX variable. It is one run of many consecutive iterations rather than several
short runs: `ITER` iterations at every size (default 10000), `WARMUP` warmup iterations (default
200) and `PW_RUNS` runs (default 1); above 1000 iterations the apps also get `-maxsamples`, so
Blink keeps every sample. `NET` and `PATHS` work as for `pingpong`. Every submit adds a line to
`$WORK/run_log.tsv`; the jobs queue one after another as `--exclusive` jobs on the same nodes.

**`analyze pw`** runs `analyze.py` on the data directories that `$WORK/run_log.tsv` records for
the configs under `$WORK/pw/` (the latest submit of each config file; older `pp_*` directories are
left out) into `$WORK/results_pw/` and prints the first 60 lines of its `summary.md`. The ceilings
table has, for each `N`, `pw<N>/r` (per rank) and `pw<N>/n` (per node: per rank times `N`), and
the same with `d` for the run with no UCX variable. The lines stay within 120 columns.

`bringup.sh check` marks the `dual` experiments `CHECK`: they select the NICs through
`UCX_NET_DEVICES` instead of the per-rank shim, so there are no "local rank -> UCX_NET_DEVICES"
lines to count. That is expected.

## Diagnose binding and the bandwidth cap

```bash
examples/nplb/diagnose.sh
```

One command, no arguments, for the H100 cluster. Part A finds which `mpirun` flags pin rank i to
the i-th core of the profile's cpu list (8 ranks on one node, `--report-bindings`). Part B finds
where the per-node bandwidth cap comes from: it runs `nic_check.sh` on subsets of the NICs and on
two node pairs. Every step runs in its own `salloc`, is echoed first, and a failing step is
recorded as `FAILED` while the script goes on. The full output goes to `$WORK/diagnose_<time>.log`,
and a summary of at most 20 lines to `$WORK/diagnose_<time>_summary.txt`. It needs `profile8.json`
and `profile16.json` in `PROFILE_DIR` and at least 3 nodes in `PARTITION`. It exits 0 when every
step ran, 1 when a step failed and 2 when the setup is missing.

## Smoke test on another cluster

Before the real campaign, the whole chain can run on any Slurm cluster with RDMA NICs and at least
4 nodes (the configs need 4 nodes; 3 also works):

```bash
srun -N4 --ntasks-per-node=1 --exclusive -A <account> -p <partition> -t 00:05:00 \
     .venv/bin/python examples/nplb/probe.py --json > probe.jsonl
.venv/bin/python examples/nplb/probe.py --merge probe.jsonl --out profile.json
.venv/bin/python examples/nplb/make_configs.py profile.json --out smoke/ --smoke
for f in smoke/smoke_md_p0_C0_P1.json smoke/smoke_md_p1_C0_P1.json \
         smoke/smoke_md_p2_perm_C0_P1.json smoke/smoke_md_p2_perm_C2_P1.json \
         smoke/smoke_col_a2a_C0_P1.json; do
    .venv/bin/crab run -p <preset> "$f"
done
# when the five jobs are done:
.venv/bin/python examples/nplb/analyze.py data/<preset>/smoke_* --out smoke-results/
```

C0 and C2 mean nothing on such a cluster; running both only exercises the C0-vs-C2 tables. A good
result: every job completes, each `bindings_app_*.log` shows one card per local rank, and
`summary.md` has bandwidth, spread and slowdown values for every experiment.

## GPU comm-only suites

`gpu_set.py` writes two configs, one per victim, with Blink's communication-only programs running
on GPU memory (`-gpu`, the CUDA-aware build under `~/crab/lab/blink-gpu/build-cuda/bin`). It uses
the standard library only and does not read the cluster profile. The configs it wrote for the
H100 nodes are in [`h100/`](h100/README.md).

- `gpu_a2a_comm.json`: victim `alltoall_comm_only`.
- `gpu_ag_comm.json`: victim `allgather_comm_only`.

Each file has 8 experiments: `ppn{1,2,4,8}_alone` (the victim alone, the reference) and
`ppn{1,2,4,8}_vs_a2a` (the same victim while an aggressor runs). A victim is a chain of 11 apps,
one per message size, 1 KiB to 256 MiB in steps of ×4 and then 512 MiB, each starting when the
previous one ends (1000 iterations and 100 warm-up iterations below 32 MiB, 100 and 50 from there
up). The aggressor is `alltoall_comm_only -endl` without `-blength`, so it is steady; it is not
collected, starts 3 seconds before the victim and stops when the last size ends. The 4 nodes are
split in two interleaved partitions of 50%: the victim runs on Slurm nodes 0 and 2, the aggressor
on nodes 1 and 3.

```bash
.venv/bin/python examples/nplb/gpu_set.py --out examples/nplb/h100/            # the two lab files
.venv/bin/python examples/nplb/gpu_set.py --out smoke/ --smoke                 # a short version of both
.venv/bin/python examples/nplb/gpu_set.py --out local/ --local                 # laptop files, no GPUs
.venv/bin/python examples/nplb/gpu_set.py --out examples/nplb/h100/ --aggr-ppn 4 --aggr-msgsize 1048576
```

- `--out DIR` (required): the folder for the two files; it is created when missing.
- `--smoke`: files named `smoke_*`, with only `ppn` 1 and 8, two sizes (1 MiB and 64 MiB),
  `-iter 20 -warmup 5` and a 30 minute walltime. Run it first to check the launch before the
  6 hour full files.
- `--local`: files named `local_*`, only to drive the flow (generate, check, launch, parse) on a
  laptop without GPUs or InfiniBand: `ppn` 1 and 2, sizes 1 KiB and 1 MiB, `-iter 5 -warmup 1`, no
  `-gpu`, `binary`, `nics`, `cpus` or UCX variables. The layout is 2 nodes (`numnodes` "2", `ppn`
  "2"), one per partition, so it needs a preset with two hosts (see
  [Running the laptop files](#running-the-laptop-files)). It cannot be combined with `--smoke`.
- `--aggr-ppn`: the aggressor's ranks per node: 1, 2, 4, 8 or `same` (the default, the victim's
  `ppn`). A comma list is allowed.
- `--aggr-msgsize`: the aggressor's message size in bytes (default 2 MiB = 2097152). A comma list
  is allowed.

With more than one (aggressor `ppn`, size) pair, every victim `ppn` gets one `vs` experiment per
pair, named `ppn{p}_vs_a2a_ap{aggressor ppn}_as{size}` instead of `ppn{p}_vs_a2a`.

**Pinning.** The ranks of a node use the first `ppn` entries of the `ppn` 8 layout: local rank
*i* runs on core 0, 1, 2, 3, 40, 41, 42, 43 (the *i*-th of these), on port `mlx5_0:1`,
`mlx5_2:1`, `mlx5_4:1`, `mlx5_8:1`, `mlx5_10:1`, `mlx5_12:1`, `mlx5_14:1`, `mlx5_16:1` (the *i*-th
of these), and on GPU *i* (Blink's `-gpu` picks the GPU by local rank, and
`CUDA_DEVICE_ORDER=PCI_BUS_ID` fixes the device order). The apps also set
`UCX_MAX_RNDV_RAILS=1` and `UCX_IB_ROCE_REACHABILITY_MODE=all`. The cores and ports are those of
the H100 test cluster; on other hardware change `CORES` and `NICS` in `gpu_set.py`.

**Preset.** The configs launch through the CUDA-aware Open MPI, so run them with the preset
`nplb_h100_gpu` (see [`presets/README.md`](presets/README.md)), not `nplb_h100`:

```bash
.venv/bin/crab run -p nplb_h100_gpu examples/nplb/h100/smoke_gpu_a2a_comm.json   # smoke first
.venv/bin/crab run -p nplb_h100_gpu examples/nplb/h100/gpu_a2a_comm.json         # then the full file
```

**Open choice.** The aggressor's `ppn` and message size are not settled. The committed files use
the defaults (the victim's `ppn`, 2 MiB). When the choice is made, regenerate with `--aggr-ppn` and
`--aggr-msgsize` and commit the new files.

### Running the laptop files

The `local_*` files ask for 2 nodes, and the `local` preset has one host, so CRAB refuses them
(`numnodes 2 is more than the preset's 1 host(s)`). Give CRAB a preset whose two hosts are
both this machine:

```json
{
    "local2": {
        "scheduler": "local",
        "launcher": "mpirun",
        "hosts": ["localhost:6", "127.0.0.1:6"]
    }
}
```

- `"local2"`: the preset name, used as `-p local2`.
- `"scheduler": "local"`: run on this machine as a detached process, with no Slurm.
- `"launcher": "mpirun"`: start each app with Open MPI's `mpirun`, which places the victim ranks on
  one host and the aggressor ranks on the other.
- `"hosts"`: two distinct names (host names must be distinct) that both reach this machine, each
  with 6 cores. The victim partition gets the first host and the aggressor partition the second.

Save it as a file (for example `local2.json`) in `local/presets/` in a checkout, or in the folder
that `CRAB_PATH_PRESETS` names (it must be a folder, not a file). Then check it and run a file:

```bash
.venv/bin/crab presets show local2                         # the preset as CRAB reads it
.venv/bin/crab run -p local2 local/local_gpu_a2a_comm.json  # the generated file, from the --local command above
```

The local files do not write `binary`, so the apps need a Blink receipt (`crab setup`) or a
`binary` you add to each app. The run's worker is a detached process: start it from a normal shell
session, not from a command that kills its children when it ends.

## The two test sets

The **md set** follows the campaign plan; the **colleague set** keeps the earlier Leonardo
congestion configs and adapts them to this hardware. Where the two differ, both are run.

| | md set (`md_*`) | colleague set (`col_*`) |
|---|---|---|
| Victims | `pairwise_nb -mode rot`, `alltoall_nb`, `allgather_nb` | `alltoall_comm_only`, `allgather_comm_only`, `allreduce_nb` |
| Sizes | 1 B to 128 MiB in steps of ×8, plus 512 MiB | 8 B to 16 MiB in steps of ×8 |
| Iterations | 1000 up to 16 MiB, 100 at 128 MiB, 25 at 512 MiB | 1000 |
| Aggressors | `alltoall_nb` steady, `alltoall_nb` bursts, `incast_nb` (2 MiB) | `alltoall_man` bursts (2 MiB) |
| Bursts | on {0.001, 0.01, 0.1} s × off {1e-6, 1e-4, 1e-2} s | same grid |
| Baseline | `<victim>_alone`, same partitions, no aggressor | none |
| Runs | 10 to 20 (20 to 40 for C0) | 1 |
| Aggressor start | 3 s before the victim | with the victim |
| Networks | C0, C2 (+ C3 phase 1) | C0, C2 |

Message sizes of one test run one after the other in the same experiment (a chain), so the
aggressor runs once for the whole sweep and stops when the last size ends.

### md set phases

- **Phase 0, ceilings** (`md_p0_*`, 2 ranks, one per node): `p0_cross` pingpong between Server1
  and Server3 (crosses the spines); `p0_local` pingpong between Server1 and Server2, the host and
  NIC ceiling. `p0_local` assumes the same NIC index on Server1 and Server2 reaches the same leaf:
  check it against the wiring.
- **Phase 1, isolated** (`md_p1_*`, all ranks): `p1_perm` (every rank sends to the matching rank
  in the other pod), `p1_a2a`, `p1_ag`. With 3 nodes, `p1_perm` runs on Server1+Server3 only.
- **Phase 2, victim vs aggressor** (`md_p2_<victim>_*`): the victim on the victim partition alone,
  then against each aggressor on the aggressor partition. With 3 nodes the aggressor is a
  pairwise rotation on Server2 (`-offset ppn/2`, so each card on one leaf sends to a card on the
  other) with `UCX_TLS=rc,self`, so its traffic goes through the NICs instead of shared memory.

## Choices to confirm

The generator makes these choices where the campaign plan leaves them open. Change them with the
flags, or in the scripts, if the test owners decide otherwise:

- The aggressor message size is 2 MiB and the burst grid is the one of the earlier Leonardo
  configs; the incast aggressor also uses 2 MiB.
- Iterations at 128 MiB and 512 MiB (100 and 25).
- `maxruns` is twice `minruns` in the md set.
- A size is skipped when its buffers need more than half the node's memory.
- `p0_local` uses Server1 ↔ Server2 with the same NIC index.
- The 3-node aggressor offset is half the ranks per node.

## Run log

Keep one line per job, next to the results:

| Date and time | Network (C0/C2/C3) | Paths | Config file | Job id | Data dir | Notes |
|---|---|---|---|---|---|---|
| | | | | | | |

Also record once: node list and order, NIC link speeds and ports (from the probe), NIC-to-leaf
wiring, message sizes skipped (from the generator summary), and any anomaly.
