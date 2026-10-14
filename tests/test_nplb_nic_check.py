"""examples/nplb/nic_check.sh, run with bash against a fake srun, a fake pairwise_nb and a fake
sysfs tree in tmp_path. Nothing here calls a real srun or reads the real /sys."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "nplb" / "nic_check.sh"
NICS = ["mlx5_0:1", "mlx5_1:1", "mlx5_2:1", "mlx5_3:1"]
START = 12345  # counters start above zero: the script must report differences
MSG = 1_000_000
ITER = 1000

# Runs N x ppn copies of the command one after the other, as Slurm would start them; node k reads
# its own sysfs tree.
FAKE_SRUN = r"""#!/usr/bin/env bash
all="$*"; nodes=1; ppn=1
while [[ $# -gt 0 ]]; do
    case $1 in
        -N) nodes=$2; shift 2 ;;
        -N*) nodes=${1#-N}; shift ;;
        --ntasks-per-node=*) ppn=${1#*=}; shift ;;
        -*) shift ;;
        *) break ;;
    esac
done
echo "srun N=$nodes ppn=$ppn :: $all" >>"$FAKE_LOG"
rc=0
for ((node = 0; node < nodes; node++)); do
    for ((local = 0; local < ppn; local++)); do
        SLURM_NODEID=$node SLURM_LOCALID=$local SLURM_PROCID=$((node * ppn + local)) \
            SLURM_NTASKS_PER_NODE=$ppn NIC_SYSFS="$FAKE_SYSFS/node$node" "$@" || rc=$?
    done
done
exit $rc
"""

# Open MPI 4 look-alike: logs its argv (fields joined by 0x1f), then runs the command once per -H
# entry, the way mpirun starts ranks (OMPI_COMM_WORLD_* set, no SLURM_* variables). It prints
# FAKE_UNBOUND "not bound" lines on stderr, like --report-bindings does for an unbound rank.
FAKE_MPIRUN = r"""#!/usr/bin/env bash
(IFS=$'\x1f'; echo "mpirun$IFS$*" >>"$FAKE_LOG")
hosts=""
while [[ $# -gt 0 ]]; do
    case $1 in
        -n | --cpu-set | --cpu-list | --bind-to | --map-by | -x) shift 2 ;;
        -H) hosts=$2; shift 2 ;;
        --mca) shift 3 ;;
        -*) shift ;;
        *) break ;;
    esac
done
IFS=, read -ra slots <<<"$hosts"
if [[ -n ${FAKE_BIND_LINES:-} ]]; then
    IFS='|' read -ra lines <<<"$FAKE_BIND_LINES"
    printf '%s\n' "${lines[@]}" >&2
fi
for ((k = 0; k < ${FAKE_UNBOUND:-0}; k++)); do
    echo "[h:1] MCW rank $k is not bound (or bound to all available processors)" >&2
done
declare -A seen; declare -A node_of; nodes=0; size=${#slots[@]}; rc=0
for h in "${slots[@]}"; do
    [[ -n ${node_of[$h]:-} ]] || node_of[$h]=$((++nodes))
done
for ((rank = 0; rank < size; rank++)); do
    h=${slots[rank]}
    node=$((node_of[$h] - 1)); local=${seen[$h]:-0}; seen[$h]=$((local + 1))
    OMPI_COMM_WORLD_RANK=$rank OMPI_COMM_WORLD_LOCAL_RANK=$local \
        OMPI_COMM_WORLD_LOCAL_SIZE=$((size / nodes)) SLURM_NODEID=$node \
        NIC_SYSFS="$FAKE_SYSFS/node$node" "$@" || rc=$?
done
exit $rc
"""

FAKE_SCONTROL = r"""#!/usr/bin/env bash
[[ $1 == show && $2 == hostnames ]] || exit 1
for h in ${FAKE_HOSTS-gn01 gn02 gn03}; do echo "$h"; done
"""

FAKE_UNAME = '#!/usr/bin/env bash\n[[ ${1:-} == -n ]] && echo "node-$SLURM_NODEID"\n'

# Only rank 0 prints, like Blink. The median is FAKE_MEDIAN, times ranks per node when
# FAKE_SHARED=1 (every rank on one card). Each copy adds msgsize*iter/4 to its device's counter.
FAKE_BLINK = r"""#!/usr/bin/env bash
rank=${OMPI_COMM_WORLD_RANK:-${SLURM_PROCID:-}}
ppn=${OMPI_COMM_WORLD_LOCAL_SIZE:-${SLURM_NTASKS_PER_NODE:-}}
echo "pnb rank=$rank ppn=$ppn dev=${UCX_NET_DEVICES:-} pml=${OMPI_MCA_pml:-}" \
    "rails=${UCX_MAX_RNDV_RAILS:-} args=$*" >>"$FAKE_LOG"
while [[ $# -gt 0 ]]; do
    case $1 in
        -msgsize) msg=$2; shift 2 ;;
        -iter) iter=$2; shift 2 ;;
        *) shift ;;
    esac
done
if [[ ${FAKE_FAIL_PML:-} == 1 && ${OMPI_MCA_pml:-} == ucx ]] ||
   [[ ${FAKE_FAIL_PPN:-} == "$ppn" ]]; then
    echo "pml selection failed" >&2
    exit 3
fi
dev=${UCX_NET_DEVICES%%:*}; port=${UCX_NET_DEVICES##*:}
f=$NIC_SYSFS/$dev/ports/$port/counters/port_xmit_data
[[ -f $f ]] && echo $(($(cat "$f") + msg * iter / 4)) >"$f"
if [[ $rank == 0 ]]; then
    mult=1; [[ ${FAKE_SHARED:-} == 1 ]] && mult=$ppn
    echo "Average,Minimum,Maximum,Median,MainRank"
    for k in 1.5 1 0.5 0.6 2; do
        awk -v m="$FAKE_MEDIAN" -v k="$k" -v x="$mult" \
            'BEGIN { printf "9.9,9.9,9.9,%.9f,9.9\n", m * k * x }'
    done
    echo "Ran $iter iterations. Measured 5 iterations."
fi
"""


@pytest.fixture
def world(tmp_path: Path) -> dict:
    """Fake bin dir, Blink dir, profile and sysfs trees (one per node) under tmp_path."""
    bindir = tmp_path / "bin"
    blink = tmp_path / "blink"
    bindir.mkdir()
    blink.mkdir()
    for path, text in (
        (bindir / "srun", FAKE_SRUN),
        (bindir / "uname", FAKE_UNAME),
        (bindir / "mpirun", FAKE_MPIRUN),
        (bindir / "scontrol", FAKE_SCONTROL),
        (blink / "pairwise_nb", FAKE_BLINK),
    ):
        path.write_text(text)
        path.chmod(0o755)
    sysfs = tmp_path / "sysfs"
    for node in (0, 1):
        for nic in NICS:
            dev, port = nic.split(":")
            counter = sysfs / f"node{node}" / dev / "ports" / port / "counters" / "port_xmit_data"
            counter.parent.mkdir(parents=True)
            counter.write_text(str(START))
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"ppn": 4, "nics": NICS, "cpus": "8,24,40,56"}))
    return {
        "bin": bindir,
        "blink": blink,
        "sysfs": sysfs,
        "profile": profile,
        "log": tmp_path / "log",
    }


def run_check(world: dict, args: list | None = None, **env) -> subprocess.CompletedProcess:
    full_env = {
        **os.environ,
        "PATH": f"{world['bin']}:{os.environ['PATH']}",
        "FAKE_LOG": str(world["log"]),
        "FAKE_SYSFS": str(world["sysfs"]),
        "FAKE_MEDIAN": "0.0001",
        "MSG": str(MSG),
        "ITER": str(ITER),
    }
    full_env.update(env)
    if args is None:
        args = [str(world["profile"]), str(world["blink"])]
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=full_env, capture_output=True, text=True, timeout=60
    )


def block(out: str, name: str) -> list[str]:
    """The line of a placement and the indented lines that continue it."""
    lines = out.splitlines()
    start = next(i for i, ln in enumerate(lines) if re.match(rf"{re.escape(name)}\s", ln))
    end = start + 1
    while end < len(lines) and lines[end].startswith(" "):
        end += 1
    return lines[start:end]


def rank_lines(world: dict) -> list[str]:
    return [ln for ln in world["log"].read_text().splitlines() if ln.startswith("pnb ")]


def test_four_placements_print_rate_and_gb_per_nic(world):
    result = run_check(world)
    assert result.returncode == 0, result.stderr
    # 1e6 B * 8 / 1e-4 s = 80 Gb/s; sent GB per NIC = ranks on the NIC * 1e6 B * 1000 iterations
    one = block(result.stdout, "one-rank")
    assert re.search(r"\b80\.0 Gb/s per rank", one[0])
    assert "node-0: mlx5_0 1.00 mlx5_1 0.00 mlx5_2 0.00 mlx5_3 0.00 GB" in one[0]
    assert "node-1: mlx5_0 1.00 mlx5_1 0.00 mlx5_2 0.00 mlx5_3 0.00 GB" in one[1]
    per_rank = block(result.stdout, "per-rank")
    assert re.search(r"\b80\.0 Gb/s per rank", per_rank[0])
    assert "node-0: mlx5_0 1.00 mlx5_1 1.00 mlx5_2 1.00 mlx5_3 1.00 GB" in per_rank[0]
    assert "node-1: mlx5_0 1.00 mlx5_1 1.00 mlx5_2 1.00 mlx5_3 1.00 GB" in per_rank[1]
    first = block(result.stdout, "first-nic")
    assert "node-0: mlx5_0 4.00 mlx5_1 0.00 mlx5_2 0.00 mlx5_3 0.00 GB" in first[0]
    assert "node-1: mlx5_0 4.00 mlx5_1 0.00 mlx5_2 0.00 mlx5_3 0.00 GB" in first[1]
    ucx = block(result.stdout, "per-rank-ucx")
    assert "node-0: mlx5_0 1.00 mlx5_1 1.00 mlx5_2 1.00 mlx5_3 1.00 GB" in ucx[0]
    assert "stop and find out why" in result.stdout


def test_rate_uses_the_median_of_the_median_column(world):
    # every rank on one card: the median is 4 x 1e-4 s, so 20 Gb/s with 4 ranks per node
    result = run_check(world, FAKE_SHARED="1")
    assert re.search(r"\b80\.0 Gb/s per rank", block(result.stdout, "one-rank")[0])
    assert re.search(r"\b20\.0 Gb/s per rank", block(result.stdout, "per-rank")[0])
    assert re.search(r"\b20\.0 Gb/s per rank", block(result.stdout, "first-nic")[0])


def test_ranks_get_their_own_nic_and_binding(world):
    result = run_check(world, SRUN_FLAGS="--mpi=pmix")
    assert result.returncode == 0, result.stderr
    lines = rank_lines(world)
    assert len(lines) == 2 + 8 + 8 + 8
    one, per_rank, first, ucx = lines[:2], lines[2:10], lines[10:18], lines[18:]
    assert [re.search(r"dev=(\S+)", ln).group(1) for ln in one] == ["mlx5_0:1"] * 2
    assert [re.search(r"dev=(\S+)", ln).group(1) for ln in per_rank] == NICS * 2
    assert [re.search(r"dev=(\S+)", ln).group(1) for ln in first] == ["mlx5_0:1"] * 8
    assert [re.search(r"dev=(\S+)", ln).group(1) for ln in ucx] == NICS * 2
    assert all("rails=1" in ln for ln in lines)
    assert all("pml=ucx" in ln for ln in ucx)
    assert not any("pml=ucx" in ln for ln in lines[:18])
    assert all("-mode rot -offset 1 " in ln for ln in one)
    assert all("-mode rot -offset 4 " in ln for ln in lines[2:])
    assert all(f"-msgsize {MSG} -iter {ITER}" in ln for ln in lines)
    runs = [
        ln
        for ln in world["log"].read_text().splitlines()
        if "pairwise_nb" in ln and ln.startswith("srun")
    ]
    assert "--cpu-bind=map_cpu:8 " in runs[0] and "--ntasks-per-node=1" in runs[0]
    assert all(
        "--cpu-bind=map_cpu:8,24,40,56 " in ln and "--ntasks-per-node=4" in ln for ln in runs[1:]
    )
    assert all("--mpi=pmix" in ln for ln in runs)


def test_failed_run_is_reported_and_the_next_one_goes_on(world):
    result = run_check(world, FAKE_FAIL_PPN="1")
    assert result.returncode == 0
    assert re.search(r"^one-rank\s+failed \(exit 3\)", result.stdout, re.M)
    assert "pml selection failed" in result.stdout
    assert re.search(r"\b80\.0 Gb/s per rank", block(result.stdout, "per-rank")[0])
    assert "per-rank-ucx" in result.stdout


def test_ucx_pml_failure_is_a_finding(world):
    result = run_check(world, FAKE_FAIL_PML="1")
    assert re.search(r"^per-rank-ucx\s+failed \(exit 3\)", result.stdout, re.M)
    assert "per-rank " in result.stdout


def test_unreadable_counters_print_na(world, tmp_path):
    result = run_check(world, NIC_SYSFS="/nonexistent", FAKE_SYSFS=str(tmp_path / "no-sysfs"))
    assert result.returncode == 0, result.stderr
    line = block(result.stdout, "per-rank")[0]
    assert re.search(r"\b80\.0 Gb/s per rank", line)
    assert "node-0: mlx5_0 n/a mlx5_1 n/a mlx5_2 n/a mlx5_3 n/a GB" in line


@pytest.mark.parametrize("which", ["no-args", "missing-profile", "missing-blink"])
def test_bad_arguments_exit_2(world, tmp_path, which):
    if which == "no-args":
        args: list = []
    elif which == "missing-profile":
        args = [str(tmp_path / "none.json"), str(world["blink"])]
    else:
        args = [str(world["profile"]), str(tmp_path / "no-blink")]
    result = run_check(world, args)
    assert result.returncode == 2
    assert not world["log"].exists()
    assert result.stderr


# ---- LAUNCHER=mpirun ----

MPI = {"LAUNCHER": "mpirun", "SLURM_JOB_NODELIST": "gn[01-03]"}


def mpirun_calls(world: dict) -> list[list[str]]:
    """Argv (after the program name) of every fake mpirun call, in order."""
    return [
        ln.split("\x1f")[1:]
        for ln in world["log"].read_text().splitlines()
        if ln.startswith("mpirun\x1f")
    ]


def test_mpirun_prints_three_cases_with_rate_and_gb(world):
    result = run_check(world, **MPI)
    assert result.returncode == 0, result.stderr
    assert not re.search(r"^per-rank-ucx", result.stdout, re.M)
    one = block(result.stdout, "one-rank")
    assert re.search(r"\b80\.0 Gb/s per rank", one[0])
    assert "node-0: mlx5_0 1.00 mlx5_1 0.00 mlx5_2 0.00 mlx5_3 0.00 GB" in one[0]
    assert "node-1: mlx5_0 1.00 mlx5_1 0.00 mlx5_2 0.00 mlx5_3 0.00 GB" in one[1]
    per_rank = block(result.stdout, "per-rank")
    assert re.search(r"\b80\.0 Gb/s per rank", per_rank[0])
    assert "node-0: mlx5_0 1.00 mlx5_1 1.00 mlx5_2 1.00 mlx5_3 1.00 GB" in per_rank[0]
    first = block(result.stdout, "first-nic")
    assert "node-1: mlx5_0 4.00 mlx5_1 0.00 mlx5_2 0.00 mlx5_3 0.00 GB" in first[1]
    assert len(mpirun_calls(world)) == 3
    assert "stop and find out why" in result.stdout


def test_mpirun_argv_per_rank_and_one_rank(world):
    result = run_check(world, MPIRUN_FLAGS="--allow-run-as-root --mca pml ucx", **MPI)
    assert result.returncode == 0, result.stderr
    one, per_rank, first = mpirun_calls(world)
    # Open MPI 4: --cpu-list with cpu-list:ordered binds rank i to the i-th core; --cpu-set only
    # sets an envelope (ranks stay unbound) and --map-by conflicts with the list
    assert all(
        "--map-by" not in call and "--cpu-set" not in call for call in (one, per_rank, first)
    )
    fixed = [
        "--bind-to",
        "cpu-list:ordered",
        "--report-bindings",
        "-x",
        "NICS",
        "-x",
        "UCX_MAX_RNDV_RAILS",
        "--allow-run-as-root",
        "--mca",
        "pml",
        "ucx",
        "bash",
        "-c",
    ]
    hosts4 = ",".join(["gn01"] * 4 + ["gn02"] * 4)
    head4 = ["-n", "8", "-H", hosts4, "--cpu-list", "8,24,40,56"]
    assert per_rank[: 6 + len(fixed)] == head4 + fixed
    tail = per_rank[6 + len(fixed) :]  # WRAP, _, BIN, args
    assert tail[1] == "_" and tail[2].endswith("/pairwise_nb")
    assert tail[3:] == ["-mode", "rot", "-offset", "4", "-msgsize", str(MSG), "-iter", str(ITER)]
    head1 = ["-n", "2", "-H", "gn01,gn02", "--cpu-list", "8"]
    assert one[: 6 + len(fixed)] == head1 + fixed
    assert one[6 + len(fixed) + 3 :][:4] == ["-mode", "rot", "-offset", "1"]
    assert first[: 6 + len(fixed)] == head4 + fixed
    # counters stay on plain srun, one task per node, no MPI flags
    snaps = [ln for ln in world["log"].read_text().splitlines() if ln.startswith("srun")]
    assert len(snaps) == 6 and all("N=2 ppn=1" in ln for ln in snaps)
    assert not any("pairwise_nb" in ln for ln in snaps)


def test_mpirun_ranks_pick_their_nic_from_the_local_rank(world):
    profile = json.loads(world["profile"].read_text())
    profile["nics"] = NICS[:2]
    world["profile"].write_text(json.dumps(profile))
    result = run_check(world, **MPI)
    assert result.returncode == 0, result.stderr
    lines = rank_lines(world)
    assert len(lines) == 2 + 8 + 8
    devs = [re.search(r"dev=(\S+)", ln).group(1) for ln in lines]
    assert devs[:2] == ["mlx5_0:1"] * 2
    assert devs[2:10] == ["mlx5_0:1", "mlx5_1:1"] * 4  # local rank r -> nics[r % 2]
    assert devs[10:] == ["mlx5_0:1"] * 8
    assert all("rails=1" in ln for ln in lines)
    assert "rank=5 ppn=4" in lines[7]


def test_mpirun_counts_unbound_ranks(world):
    result = run_check(world, FAKE_UNBOUND="3", **MPI)
    assert result.returncode == 0, result.stderr
    for name in ("one-rank", "per-rank", "first-nic"):
        assert "unbound ranks: 3" in "\n".join(block(result.stdout, name))
    clean = run_check(world, **MPI)
    for name in ("one-rank", "per-rank", "first-nic"):
        assert "unbound ranks: 0" in "\n".join(block(clean.stdout, name))


def test_srun_mode_prints_no_unbound_line(world):
    assert "unbound" not in run_check(world).stdout


def test_mpirun_failed_case_is_reported_and_the_next_goes_on(world):
    result = run_check(world, FAKE_FAIL_PPN="1", **MPI)
    assert result.returncode == 0
    assert re.search(r"^one-rank\s+failed \(exit 3\)", result.stdout, re.M)
    assert "pml selection failed" in result.stdout
    assert re.search(r"\b80\.0 Gb/s per rank", block(result.stdout, "per-rank")[0])


@pytest.mark.parametrize("hosts", ["", "gn01"])
def test_mpirun_needs_two_hosts(world, hosts):
    result = run_check(world, FAKE_HOSTS=hosts, **MPI)
    assert result.returncode == 2
    assert "2-node allocation" in result.stderr
    assert not world["log"].exists()


def test_mpirun_without_a_nodelist_exits_2(world):
    result = run_check(world, LAUNCHER="mpirun", SLURM_JOB_NODELIST="")
    assert result.returncode == 2
    assert "2-node allocation" in result.stderr
    assert not world["log"].exists()


def test_unknown_launcher_exits_2(world):
    result = run_check(world, LAUNCHER="bogus")
    assert result.returncode == 2
    assert "srun|mpirun" in result.stderr
    assert not world["log"].exists()


BINDINGS = (
    "[gn01:11] MCW rank 10 bound to socket 1[core 60[hwt 0-1]]: [../..]"
    "|[gn01:11] MCW rank 2 bound to socket 0[core 40[hwt 0-1]]: [../..]"
    "|[gn01:11] MCW rank 1 bound to socket 0[core 24[hwt 0-1]]: [../..]"
    "|[gn01:11] MCW rank 1 bound to socket 0[core 24[hwt 0-1]]: [../..]"
    "|[gn01:11] MCW rank 0 bound to socket 0[core 8[hwt 0-1]]: [../..]"
)


def test_mpirun_per_rank_prints_the_bindings_sorted_by_rank(world):
    result = run_check(world, FAKE_BIND_LINES=BINDINGS, **MPI)
    assert result.returncode == 0, result.stderr
    per_rank = block(result.stdout, "per-rank")
    text = "\n".join(per_rank)
    assert "unbound ranks: 0" in text and "bindings:" in text
    ranks = [int(m.group(1)) for ln in per_rank if (m := re.search(r"MCW rank (\d+) bound", ln))]
    assert ranks == [0, 1, 2, 10]  # numeric order, the repeated line printed once
    assert all(ln.startswith(" ") for ln in per_rank[1:])
    assert text.index("unbound ranks") < text.index("bindings:")
    for name in ("one-rank", "first-nic"):
        assert "MCW rank" not in "\n".join(block(result.stdout, name))
        assert "bindings:" not in "\n".join(block(result.stdout, name))


def test_mpirun_failure_tail_drops_the_binding_lines(world):
    result = run_check(world, FAKE_BIND_LINES=BINDINGS, FAKE_FAIL_PPN="1", **MPI)
    failed = result.stdout.split("per-rank ")[0]
    assert re.search(r"^one-rank\s+failed \(exit 3\)", failed, re.M)
    assert "pml selection failed" in failed
    assert "MCW rank" not in failed
