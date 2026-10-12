# NPLB campaign

Tools to measure how a fabric's per-packet load balancing (NPLB) compares with ECMP, with Blink
microbenchmarks run through CRAB. Every number comes from Blink timings: no switch or NIC counters
are read.

| File | What it does |
|------|--------------|
| `probe.py` | Reads each node's RDMA NICs (ports, speed, NUMA node, netdev, memory, GPU layout) and writes a cluster profile with the `nics` and `cpus` lists to use. |
| `make_configs.py` | Writes the CRAB configs of both test sets from the profile (`md_set.py`, `colleague_set.py`, `naming.py`). |
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

Put a preset for the cluster in `local/presets.json` (git-ignored, merged over the shipped presets;
see `docs/using/presets.md`). A starting point for a Slurm cluster with Open MPI built with PMIx:

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

The probe cannot see which leaf each NIC is cabled to. Get the NIC-to-leaf wiring from the
network team and record it; the 3-node layout and `p0_local` depend on it.

## 4. Generate the configs

```bash
.venv/bin/python examples/nplb/make_configs.py profile.json --out configs/
```

Defaults: both test sets, C0 and C2 with 1 and 4 paths, plus C3 for phase 1 with 1 path. The
summary lists every file, its experiments, the message sizes skipped for memory, and the
assumptions it made. Useful flags: `--set md|colleague|both`, `--net C0 C2`, `--paths 1`,
`--minruns`, `--maxruns`, `--walltime`, `--timeout`, `--mem-gib`, `--iter-small`,
`--iter-128m`, `--iter-512m`. `--smoke` writes a short version (3 sizes, 50 iterations, one run)
to test the whole chain.

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

`results/summary.csv` has one row per data directory, experiment and message size;
`results/summary.md` has the tables, including **C0 vs C2** side by side. The metrics:

| Column | Meaning |
|--------|---------|
| `bw_gbps` | per-rank bandwidth: message size × granularity / median time (one-way for pingpong) |
| `spread_med`, `spread_p99` | max / min time across ranks, per iteration (median and p99): the collision signal |
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
