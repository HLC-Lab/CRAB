"""examples/nplb/gpu_lab_dual.sh (`gpu_lab.sh dual-port [CARD]`), run with bash against fakes.

Everything the step launches is a fake: `salloc`, `scontrol`, `ssh` (runs the remote command in a
local bash), `mpirun` (prints a Blink CSV for the Gb/s it is told to simulate and logs its argv and
environment), `taskset`, `ip`, `ib_write_bw` (prints a perftest BW table; the client tells a one-port
run from a two-port run by the count of counter snapshots so far) and a fake
GPU_PREFLIGHT that answers `counters snapshot|diff` from canned lines. Nothing real runs and nothing
sends a signal. The world helpers come from tests/test_nplb_gpu_lab.py.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_nplb_gpu_lab import (
    SCREEN_COLS,
    SCREEN_LINES,
    SCRIPT,
    WRITE_PATTERNS,
    make_world,
    put,
    run_lab,
    runs,
    screen_limits,
)

DUAL = SCRIPT.parent / "gpu_lab_dual.sh"
SIZE = 134217728
CARDS = [  # card -> (f0, f1, gpu, core)
    ("mlx5_0", "mlx5_1", 0, 0),
    ("mlx5_2", "mlx5_3", 1, 1),
    ("mlx5_4", "mlx5_5", 2, 2),
    ("mlx5_8", "mlx5_9", 3, 3),
    ("mlx5_10", "mlx5_11", 4, 40),
    ("mlx5_12", "mlx5_13", 5, 41),
    ("mlx5_14", "mlx5_15", 6, 42),
    ("mlx5_16", "mlx5_17", 7, 43),
]
POINTER = "gpu_lab.sh show dual-port"

FAKE_SSH = r"""#!/usr/bin/env bash
while [[ ${1:-} == -o ]]; do shift 2; done
host=$1; shift
echo "$host $*" >>"$FAKE_DUAL_DIR/ssh.log"
if [[ -e $FAKE_DUAL_DIR/unreachable.$host ]]; then
    echo "ssh: connect to host $host port 22: No route to host" >&2
    exit 255
fi
export FAKE_NODE=$host
exec bash -c "$*"
"""

# argv and environment of every call go to mpirun.<n>.argv / .env; the Blink CSV it prints follows
# FAKE_UCX_SINGLE / FAKE_UCX_DUAL (Gb/s; the rails of the call tell which). Columns: the Maximum
# column holds the time of the figure, Average and Minimum hold other values, one sample is an outlier.
FAKE_MPIRUN = r"""#!/usr/bin/env bash
d=$FAKE_DUAL_DIR
n=$(($(cat "$d/mpirun.count" 2>/dev/null || echo 0) + 1))
echo "$n" >"$d/mpirun.count"
printf '%s\n' "$@" >"$d/mpirun.$n.argv"
env >"$d/mpirun.$n.env"
args=("$@")
rails=
size=0
for ((i = 0; i < ${#args[@]}; i++)); do
    case ${args[i]} in
        -x) [[ ${args[i + 1]} == UCX_MAX_RNDV_RAILS=* ]] && rails=${args[i + 1]#*=} ;;
        -msgsize) size=${args[i + 1]} ;;
    esac
done
long=$(printf '%0250d' 0)
case ${FAKE_MPIRUN_MODE:-ok} in
    fail)
        echo "[w1:123] Error: failed to create endpoint: Unsupported operation" >&2
        echo "second line of the failure"
        exit 1 ;;
    empty) echo "something unrelated"; exit 0 ;;
    hang) sleep 30 ;;
    long)
        for k in 1 2 3 4 5 6; do echo "Error line $k $long-END-$k" >&2; done
        exit 1 ;;
esac
g=${FAKE_UCX_SINGLE:-375}
[[ $rails == 2 ]] && g=${FAKE_UCX_DUAL:-700}
t=$(awk -v s="$size" -v g="$g" 'BEGIN { printf "%.9f", s * 8 / 1e9 / g }')
echo "Ping-pong with 2 processes, msg-size: $size, test iterations: 50."
echo "Average,Minimum,Maximum,Median,MainRank"
for k in 1 2 3 4 5; do
    mx=$t
    [[ $k == 3 ]] && mx=$(awk -v t="$t" 'BEGIN { printf "%.9f", t * 3 }')
    printf '0.000100000,0.000050000,%s,0.000090000,%s\n' "$mx" "$mx"
done
echo "Ran 60 iterations. Measured 5 iterations."
"""

FAKE_TASKSET = r"""#!/usr/bin/env bash
echo "$FAKE_NODE $*" >>"$FAKE_DUAL_DIR/taskset.log"
shift 2
exec "$@"
"""

# `ib_write_bw --help`, a server (no address argument: prints nothing useful and ends) and a client
# (the last argument is an IPv4 address): the BW table. The client tells the one-port run from the
# two-port run by the number of counter snapshots so far (the runs go ucx1, ucx2, raw1, raw2: the
# fourth snapshot starts the two-port raw run).
FAKE_IBW = r"""#!/usr/bin/env bash
d=$FAKE_DUAL_DIR
if [[ ${1:-} == --help ]]; then
    echo "Usage:"
    echo "  -d, --ib-dev=<dev>  Use IB device <dev>"
    [[ -n ${FAKE_NO_CUDA:-} ]] || echo "      --use_cuda=<index>  Use CUDA lib for GPU-Direct RDMA support"
    exit 0
fi
echo "$FAKE_NODE ib_write_bw $* ORDER=${CUDA_DEVICE_ORDER:-} VISIBLE=${CUDA_VISIBLE_DEVICES-unset}" >>"$d/ibw.log"
args=("$@")
dev=
size=0
for ((i = 0; i < ${#args[@]}; i++)); do
    case ${args[i]} in
        -d) dev=${args[i + 1]} ;;
        -s) size=${args[i + 1]} ;;
    esac
done
last=${args[${#args[@]} - 1]}
if ! [[ $last =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo " Waiting for client to connect... *"
    exit 0
fi
case ${FAKE_IBW_FAIL:-} in
    client) echo "Unable to connect to $last: Connection refused" >&2
            echo "second line of the failure" >&2
            exit 1 ;;
    long) for k in 1 2 3 4 5 6; do echo "Error line $k $(printf '%0250d' 0)-END-$k" >&2; done
          exit 1 ;;
    hang) sleep 30 ;;
esac
bw=${FAKE_IBW_SINGLE:-390}
[[ $(cat "$d/snapno") -ge 4 ]] && bw=${FAKE_IBW_DUAL_EACH:-390}
[[ -n ${FAKE_IBW_GARBAGE:-} ]] && bw=N/A
echo "---------------------------------------------------------------------------------------"
echo "                    RDMA_Write BW Test"
echo " Device         : $dev"
echo "---------------------------------------------------------------------------------------"
echo " #bytes     #iterations    BW peak[Gb/sec]    BW average[Gb/sec]   MsgRate[Mpps]"
printf ' %-10s %-14s %-18s %-20s %s\n' "$size" 1000 399.1 "$bw" 0.000364
echo "---------------------------------------------------------------------------------------"
"""

FAKE_IP = r"""#!/usr/bin/env bash
nd=${*: -1}
[[ -n $nd ]] || exit 1
n=${nd##*_}
echo "3: $nd    inet 10.1.0.$((n + 1))/24 brd 10.1.0.255 scope global $nd"
"""

FAKE_SALLOC = r"""#!/usr/bin/env bash
d=$FAKE_DUAL_DIR
echo "$*" >"$d/salloc.args"
echo x >>"$d/salloc.calls"
env | grep '^GPU_LAB_' >"$d/salloc.env"
if [[ ${FAKE_SALLOC_MODE:-ok} == timeout ]]; then
    echo "salloc: Pending job allocation 99" >&2
    echo "salloc: error: Unable to allocate resources: Immediate execution impossible, insufficient resources" >&2
    exit 1
fi
while [[ $1 != -- ]]; do shift; done
shift
export SLURM_JOB_ID=777 SLURM_JOB_NODELIST="w[1-2]"
exec "$@"
"""

FAKE_SCONTROL = r"""#!/usr/bin/env bash
echo "$*" >>"$FAKE_DUAL_DIR/scontrol.log"
for n in ${FAKE_NODES-w1 w2}; do echo "$n"; done
"""

# counters: `snapshot FILE` creates FILE (or fails when snapshot_fail exists); `diff FILE` fails when
# FILE is missing and prints diff.<n> for its n-th call (diff_fail makes it fail).
FAKE_PREFLIGHT = r"""#!/usr/bin/env bash
d=$FAKE_DUAL_DIR
echo "$*" >>"$d/preflight.log"
[[ $1 == counters ]] || exit 0
mode=$2
file=$3
case $mode in
    snapshot)
        echo $(($(cat "$d/snapno" 2>/dev/null || echo 0) + 1)) >"$d/snapno"
        if [[ -e $d/snapshot_fail ]]; then
            echo "w1 ssh FAIL ssh: connect to host w1 port 22: No route to host" >&2
            exit 1
        fi
        : >"$file"
        echo "gpu_preflight: wrote 0 counter lines for 2 of 2 hosts to $file" ;;
    diff)
        n=$(($(cat "$d/diffno" 2>/dev/null || echo 0) + 1))
        echo "$n" >"$d/diffno"
        if [[ -e $d/diff_fail ]]; then
            echo "w2 ssh FAIL no route" >&2
            exit 1
        fi
        if [[ ! -f $file ]]; then
            echo "gpu_preflight: cannot read $file" >&2
            exit 2
        fi
        cat "$d/diff.$n" ;;
esac
"""


class Dual:
    """A fake world with the dual-port fakes installed; `run` calls `gpu_lab.sh dual-port`."""

    def __init__(self, root: Path) -> None:
        self.w = make_world(root, ["head", "w1", "w2"])
        self.dir = root / "dual"
        self.dir.mkdir()
        gbin = self.w["gbin"]
        for tool in ("timeout", "sleep"):
            found = shutil.which(tool)
            assert found
            (Path(self.w["base"]) / tool).symlink_to(found)
        head = self.w["hosts"]["head"]
        for dev in [f"mlx5_{i}" for i in (0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17)]:
            (head / "pci" / f"pci-{dev}" / "net" / f"eth_{dev}").mkdir(parents=True)
        put(head / "lab" / "ompi-cuda" / "bin" / "mpirun", FAKE_MPIRUN, 0o755)
        for name, text in (
            ("ssh", FAKE_SSH),
            ("taskset", FAKE_TASKSET),
            ("ib_write_bw", FAKE_IBW),
            ("ip", FAKE_IP),
            ("salloc", FAKE_SALLOC),
            ("scontrol", FAKE_SCONTROL),
        ):
            put(gbin / name, text, 0o755)
        put(root / "fake_preflight_dual.sh", FAKE_PREFLIGHT, 0o755)
        self.traffic()

    def traffic(self, runs_: list[str] | None = None, ecn: str = "no", card: int = 0) -> None:
        """Write the canned diffs: the tokens of `ports with traffic:` per run (4 runs)."""
        f0, f1 = CARDS[card][0], CARDS[card][1]
        single = f"w1/{f0} w2/{f0}"
        both = f"w1/{f0} w1/{f1} w2/{f0} w2/{f1}"
        for n, tokens in enumerate(runs_ or [single, both, single, both], start=1):
            put(
                self.dir / f"diff.{n}",
                f"w1       {f0}   port_xmit_data  +5\nports with traffic: {tokens}\n"
                f"ECN/CNP activity: {ecn}\nproxy: counters are a proxy\n",
            )

    def run(self, args: list | tuple = (), **env) -> subprocess.CompletedProcess:
        full = {
            "FAKE_DUAL_DIR": str(self.dir),
            "GPU_PREFLIGHT": str(self.w["root"] / "fake_preflight_dual.sh"),
            "GPU_LAB_ALLOC": "0",
            "SLURM_JOB_NODELIST": "w[1-2]",
            "SLURM_JOB_ID": "",
            "GPU_LAB_IN_ALLOC": "",
            "GPU_LAB_ALLOC_FLAG": "",
            "SALLOC": "",
            "UCX_LOG_LEVEL": "",
            "RAW_SETTLE": "0",
            "DUR": "2",
            "SIZE": str(SIZE),
            "STEP_TIMEOUT": "20",
            "ALLOC_TIMEOUT": "600",
            "DUAL_ITER": "50",
            "DUAL_WARMUP": "10",
            "PARTITION": "h100_4nodes",
            "NODES": "",
            **env,
        }
        return run_lab(self.w, ["dual-port", *args], **full)

    def log(self, name: str) -> str:
        return (self.dir / name).read_text()

    def calls(self) -> list[dict]:
        """The mpirun calls in order: argv list and environment."""
        out = []
        for argv in sorted(self.dir.glob("mpirun.*.argv"), key=lambda p: int(p.name.split(".")[1])):
            n = argv.name.split(".")[1]
            env = dict(
                ln.split("=", 1)
                for ln in (self.dir / f"mpirun.{n}.env").read_text().splitlines()
                if "=" in ln
            )
            out.append({"argv": argv.read_text().splitlines(), "env": env})
        return out


@pytest.fixture
def dw(tmp_path: Path) -> Dual:
    return Dual(tmp_path)


@pytest.fixture(scope="module")
def healthy(tmp_path_factory):
    d = Dual(tmp_path_factory.mktemp("dual"))
    return d, d.run(UCX_LOG_LEVEL="info")


def line(out: str, what: str) -> str:
    """The first screen line that starts with `what` followed by blanks."""
    found = [ln for ln in out.splitlines() if re.match(rf"{re.escape(what)}\s", ln)]
    assert found, f"no '{what}' line in:\n{out}"
    return found[0]


def x_value(argv: list[str], name: str) -> str:
    """The value of `-x name=value` in an mpirun argv."""
    hits = [
        argv[i + 1] for i, a in enumerate(argv) if a == "-x" and argv[i + 1].startswith(name + "=")
    ]
    assert len(hits) == 1, (name, argv)
    return hits[0].split("=", 1)[1]


# ---- red: the step exists ---------------------------------------------------------------------


def test_script_files_parse_and_the_step_is_wired():
    assert subprocess.run(["bash", "-n", str(SCRIPT), str(DUAL)]).returncode == 0
    assert "gpu_lab_dual.sh" in SCRIPT.read_text()


# ---- 1. healthy -------------------------------------------------------------------------------


def test_a_healthy_dual_port_screen_has_every_part_and_exits_0(healthy):
    d, result = healthy
    out = result.stdout
    assert result.returncode == 0, out + result.stderr
    screen_limits(out)
    lines = out.splitlines()
    assert lines[0].startswith("gpu_lab dual-port")
    assert re.search(
        r"^nodes w1 -> w2\s+card 0: mlx5_0 \+ mlx5_1\s+GPU 0\s+core 0\s+message 128 MiB$", out, re.M
    )
    assert re.match(r"ucx 1 port\s+OK\s+375 Gb/s\s", line(out, "ucx 1 port"))
    assert re.match(r"ucx 2 ports\s+OK\s+700 Gb/s\s", line(out, "ucx 2 ports"))
    assert re.match(r"raw 1 port\s+OK\s+390 Gb/s\s", line(out, "raw 1 port"))
    assert re.match(r"raw 2 ports\s+OK\s+780 Gb/s\s", line(out, "raw 2 ports"))
    assert re.search(r"^ratio 2 ports / 1 port:\s+ucx 1\.87x\s+raw 2\.00x$", out, re.M)
    assert re.search(r"^reference:.*PCIe Gen5 x16.*418-435 Gb/s", out, re.M)
    assert re.search(r"^units: Gb/s = 1e9 bit/s", out, re.M)
    assert lines[-1] == "dual-port: 0 FAIL, 0 WARN"
    assert "verdict ucx: second port adds bandwidth" in out
    assert "verdict raw: second port adds bandwidth" in out


def test_the_blink_figure_is_the_slowest_rank_median_not_another_column(healthy):
    # the fake's Average column would give 1.3 Gb/s-scale numbers: only the Maximum column gives 375
    d, result = healthy
    assert re.match(r"ucx 1 port\s+OK\s+375 Gb/s", line(result.stdout, "ucx 1 port"))


def test_the_screen_lists_the_ports_of_the_card_per_run_and_the_log_keeps_the_detail(healthy):
    d, result = healthy
    out = result.stdout
    assert re.search(r"^\s+ucx 1 port\s+mlx5_0 yes\s+mlx5_1 no\s+ECN/CNP no$", out, re.M), out
    assert re.search(r"^\s+ucx 2 ports\s+mlx5_0 yes\s+mlx5_1 yes\s+ECN/CNP no$", out, re.M)
    assert re.search(r"^\s+raw 1 port\s+mlx5_0 yes\s+mlx5_1 no\s+ECN/CNP no$", out, re.M)
    assert re.search(r"^\s+raw 2 ports\s+mlx5_0 yes\s+mlx5_1 yes\s+ECN/CNP no$", out, re.M)
    (run,) = runs(d.w)
    log = (run / "dual-port.log").read_text()
    assert "Average,Minimum,Maximum,Median,MainRank" in log
    assert "RDMA_Write BW Test" in log and "Waiting for client to connect" in log
    assert "ports with traffic: w1/mlx5_0 w1/mlx5_1 w2/mlx5_0 w2/mlx5_1" in log
    assert (d.w["work"] / "gpu_lab" / "latest").resolve() == run.resolve()
    assert sorted(p.name for p in run.iterdir()) == [
        "dual-port.counters.raw1",
        "dual-port.counters.raw2",
        "dual-port.counters.ucx1",
        "dual-port.counters.ucx2",
        "dual-port.log",
    ]


def test_show_dual_port_prints_the_log(healthy):
    d, _ = healthy
    shown = run_lab(d.w, ["show", "dual-port"])
    (run,) = runs(d.w)
    assert shown.returncode == 0
    assert shown.stdout == (run / "dual-port.log").read_text()


# ---- 2. verdict wording -----------------------------------------------------------------------

CAP = "consistent with a shared PCIe x16 cap"
VERDICTS = [  # (one port, two ports, wording, cap note expected)
    (375, 700, "second port adds bandwidth", False),
    (375, 600, "second port adds bandwidth", False),  # ratio 1.60 exactly
    (375, 590, "partial gain", False),  # 1.57, above the ceiling
    (375, 430, "partial gain", True),  # 1.15, within 5% of the ceiling
    (375, 413, "partial gain", True),  # 1.10 exactly
    (375, 410, "no gain from the second port", True),  # 1.09
    (390, 392, "no gain from the second port", True),
    (200, 300, "partial gain", True),  # below the ceiling
    (375, 480, "partial gain", False),  # 1.28, above 435 * 1.05
    (400, 456, "partial gain", True),  # 456 <= 456.75
    (400, 458, "partial gain", False),  # 458 > 456.75
    (500, 1000, "second port adds bandwidth", False),
]


@pytest.mark.parametrize("batch", [VERDICTS[i : i + 2] for i in range(0, len(VERDICTS), 2)])
def test_the_verdict_wording_follows_the_ratio_and_the_ceiling(dw, batch):
    ucx, raw = batch
    result = dw.run(
        FAKE_UCX_SINGLE=str(ucx[0]),
        FAKE_UCX_DUAL=str(ucx[1]),
        FAKE_IBW_SINGLE=str(raw[0]),
        FAKE_IBW_DUAL_EACH=str(raw[1] / 2),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for which, (_, two, wording, cap) in (("ucx", ucx), ("raw", raw)):
        verdict = line(result.stdout, f"verdict {which}:")
        assert f"verdict {which}: {wording}" in verdict, verdict
        assert (CAP in verdict) is cap, verdict
        assert re.search(rf"\(\d\.\d\dx, {two} Gb/s\)$", verdict), verdict


def test_the_verdict_never_says_pass_or_fail_about_the_ratio(healthy):
    _, result = healthy
    verdicts = [ln for ln in result.stdout.splitlines() if ln.startswith("verdict")]
    assert len(verdicts) == 2
    for ln in verdicts:
        assert not re.search(r"\b(pass|fail|ok)\b", ln, re.I), ln


@pytest.mark.parametrize(
    ("single", "dual", "text"),
    [
        (450, 700, "above the 400 Gb/s line rate"),
        (375, 900, "above the 800 Gb/s line rate"),
        (390, 300, "two ports gave less than one"),
    ],
)
def test_values_that_cannot_be_right_are_a_warning_not_a_failure(dw, single, dual, text):
    result = dw.run(FAKE_UCX_SINGLE=str(single), FAKE_UCX_DUAL=str(dual))
    assert result.returncode == 0
    assert re.search(rf"^ucx \d ports?\s+WARN\s+.*{text}", result.stdout, re.M), result.stdout
    assert result.stdout.splitlines()[-1] == "dual-port: 0 FAIL, 1 WARN"


# ---- 3. the commands --------------------------------------------------------------------------


def test_the_ucx_runs_carry_exactly_the_variables_of_the_lab(healthy):
    d, _ = healthy
    single, dual = d.calls()
    lab = str(d.w["hosts"]["head"] / "lab")
    for call, devs, rails in ((single, "mlx5_0:1", "1"), (dual, "mlx5_0:1,mlx5_1:1", "2")):
        argv, env = call["argv"], call["env"]
        assert x_value(argv, "UCX_NET_DEVICES") == devs
        assert x_value(argv, "UCX_MAX_RNDV_RAILS") == rails
        assert x_value(argv, "UCX_IB_ROCE_REACHABILITY_MODE") == "all"
        assert x_value(argv, "CUDA_DEVICE_ORDER") == "PCI_BUS_ID"
        assert x_value(argv, "CUDA_VISIBLE_DEVICES") == "0"
        assert "-gpu" in argv and argv[argv.index("-n") + 1] == "2"
        assert argv[argv.index("-H") + 1] == "w1,w2"
        assert argv[argv.index("--cpu-list") + 1] == "0"
        assert argv[argv.index("--bind-to") + 1] == "cpu-list:ordered"
        assert argv[argv.index("-msgsize") + 1] == str(SIZE)
        assert argv[argv.index("-iter") + 1] == "50" and argv[argv.index("-warmup") + 1] == "10"
        assert argv[argv.index("--mca") + 1 :][:2] == ["pml", "ucx"]
        assert "--allow-run-as-root" in argv
        assert any(a.endswith("/pingpong_b") for a in argv)
        assert "-x" in argv and "LD_LIBRARY_PATH" in argv  # forwarded by name
        paths = env["LD_LIBRARY_PATH"].split(":")
        assert f"{lab}/ucx-cuda/lib" in paths and f"{lab}/ompi-cuda/lib" in paths
        assert str(d.w["hosts"]["head"] / "cudalib") in paths
        assert env["PATH"].split(":")[0] == f"{lab}/ompi-cuda/bin"
        assert not any("UCX_LOG_LEVEL" in a for a in argv)
        assert "UCX_LOG_LEVEL" not in env  # even when the caller had it set
        assert env["LC_ALL"] == "C"  # decimal points in the numbers the script sorts and divides


def test_mpirun_is_the_cuda_one_of_the_lab(healthy):
    d, _ = healthy
    (run,) = runs(d.w)
    log = (run / "dual-port.log").read_text()
    assert f"{d.w['hosts']['head'] / 'lab'}/ompi-cuda/bin/mpirun -n 2" in log


def test_the_raw_runs_use_cuda_memory_one_core_per_process_and_the_gbit_report(healthy):
    d, _ = healthy
    lines = (d.dir / "ibw.log").read_text().splitlines()
    servers = [
        ln for ln in lines if ln.startswith("w2 ") and not re.search(r" 10\.1\.0\.\d+ ", ln + " ")
    ]
    clients = [
        ln for ln in lines if ln.startswith("w1 ") and re.search(r" 10\.1\.0\.\d+ ORDER", ln)
    ]
    assert len(servers) == 3 and len(clients) == 3  # one port, then two ports
    for ln in servers + clients:
        assert "-R" in ln.split() and "--use_cuda=0" in ln and "--report_gbits" in ln
        assert f"-s {SIZE}" in ln and "-D 2" in ln
        assert "ORDER=PCI_BUS_ID" in ln and "VISIBLE=unset" in ln
    devs = sorted(
        re.search(r"-d (\S+)", ln).group(1) for ln in clients
    )  # processes of a run overlap
    assert devs == ["mlx5_0", "mlx5_0", "mlx5_1"]
    assert {re.search(r"-p (\d+)", ln).group(1) for ln in clients} == {"18500", "18501"}
    # the addresses of the ports on B: f0 -> .1, f1 -> .2 (the fake `ip`)
    assert any("-d mlx5_0 " in ln and ln.split()[-3] == "10.1.0.1" for ln in clients)
    assert any("-d mlx5_1 " in ln and ln.split()[-3] == "10.1.0.2" for ln in clients)
    cores = [ln.split() for ln in (d.dir / "taskset.log").read_text().splitlines()]
    # (node, core): the first process of a run on the card's core, the second 4 cores up
    assert sorted((c[0], c[2]) for c in cores) == [
        ("w1", "0"), ("w1", "0"), ("w1", "4"), ("w2", "0"), ("w2", "0"), ("w2", "4"),
    ]  # fmt: skip


def test_the_only_commands_started_on_a_node_are_the_benchmark_processes(healthy):
    d, _ = healthy
    for ln in (d.dir / "ssh.log").read_text().splitlines():
        host, cmd = ln.split(" ", 1)
        assert host in ("w1", "w2")
        assert re.match(
            r"(bash -s -- \S+ mlx5_0 mlx5_1|ib_write_bw --help"
            r"|timeout -k 5 \d+ env CUDA_DEVICE_ORDER=PCI_BUS_ID taskset -c \d+ ib_write_bw )",
            cmd,
        ), ln


def test_the_settings_reach_the_commands(dw):
    result = dw.run(["3"], SIZE="1048576", DUR="7", DUAL_ITER="12", DUAL_WARMUP="3")
    assert result.returncode == 0, result.stdout
    argv = dw.calls()[0]["argv"]
    assert argv[argv.index("-msgsize") + 1] == "1048576"
    assert argv[argv.index("-iter") + 1] == "12" and argv[argv.index("-warmup") + 1] == "3"
    assert (
        "-s 1048576" in (dw.dir / "ibw.log").read_text()
        and "-D 7" in (dw.dir / "ibw.log").read_text()
    )
    assert "message 1 MiB" in result.stdout


# ---- 4. the card argument ---------------------------------------------------------------------


@pytest.mark.parametrize("card", range(8))
def test_each_card_maps_to_its_pair_gpu_and_core(dw, card):
    f0, f1, gpu, core = CARDS[card]
    dw.traffic(card=card)
    result = dw.run(
        [str(card)], FAKE_NO_CUDA="1"
    )  # the raw part is skipped: one card per run is quick
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"card {card}: {f0} + {f1}  GPU {gpu}  core {core}" in result.stdout
    single, dual = dw.calls()
    assert x_value(single["argv"], "UCX_NET_DEVICES") == f"{f0}:1"
    assert x_value(dual["argv"], "UCX_NET_DEVICES") == f"{f0}:1,{f1}:1"
    for call in (single, dual):
        assert x_value(call["argv"], "CUDA_VISIBLE_DEVICES") == str(gpu)
        assert call["argv"][call["argv"].index("--cpu-list") + 1] == str(core)


def test_the_raw_runs_of_a_late_card_use_its_ports_gpu_and_core(dw):
    f0, f1, gpu, core = CARDS[5]
    dw.traffic(card=5)
    result = dw.run(["5"])
    assert result.returncode == 0, result.stdout + result.stderr
    ibw = (dw.dir / "ibw.log").read_text()
    assert f"-d {f0} " in ibw and f"-d {f1} " in ibw and f"--use_cuda={gpu}" in ibw
    assert f"-p {18500 + 12}" in ibw and f"-p {18500 + 13}" in ibw
    cores = {
        (c.split()[0], c.split()[2]) for c in (dw.dir / "taskset.log").read_text().splitlines()
    }
    assert cores == {
        ("w1", str(core)),
        ("w2", str(core)),
        ("w1", str(core + 4)),
        ("w2", str(core + 4)),
    }


def test_the_default_card_is_0(dw):
    result = dw.run(FAKE_NO_CUDA="1")
    assert "card 0: mlx5_0 + mlx5_1  GPU 0  core 0" in result.stdout


@pytest.mark.parametrize("bad", [["8"], ["-1"], ["x"], ["1", "2"], [""], ["01x"]])
def test_an_invalid_card_exits_2_with_one_line_and_starts_nothing(dw, bad):
    result = dw.run(
        bad, GPU_LAB_ALLOC="", SALLOC=str(dw.w["gbin"] / "salloc"), SLURM_JOB_NODELIST=""
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert len(result.stderr.strip().splitlines()) == 1, result.stderr
    assert "CARD is a number from 0 to 7" in result.stderr or "at most one CARD" in result.stderr
    assert not (dw.dir / "salloc.calls").exists() and dw.calls() == []
    assert not dw.w["work"].exists()


@pytest.mark.parametrize(
    "setting",
    [
        ("SIZE", "big"),
        ("DUR", "0"),
        ("DUAL_ITER", "x"),
        ("DUAL_WARMUP", "-1"),
        ("STEP_TIMEOUT", "0"),
        ("ALLOC_TIMEOUT", "soon"),
        ("RAW_SETTLE", "1.5"),
    ],
)
def test_a_bad_setting_exits_2_with_one_line_and_starts_nothing(dw, setting):
    name, value = setting
    result = dw.run(**{name: value})
    assert result.returncode == 2
    assert result.stdout == "" and name in result.stderr
    assert len(result.stderr.strip().splitlines()) == 1
    assert dw.calls() == [] and not dw.w["work"].exists()


# ---- 5. perftest without CUDA -----------------------------------------------------------------


def test_a_perftest_without_cuda_skips_the_raw_part_with_a_warning(dw):
    result = dw.run(FAKE_NO_CUDA="1")
    out = result.stdout
    assert result.returncode == 0, out
    assert re.search(r"^raw\s+WARN\s+skipped: ib_write_bw on w1 has no --use_cuda", out, re.M)
    assert re.match(r"ucx 1 port\s+OK\s+375 Gb/s", line(out, "ucx 1 port"))
    assert re.match(r"ucx 2 ports\s+OK\s+700 Gb/s", line(out, "ucx 2 ports"))
    assert "verdict ucx: second port adds bandwidth" in out
    assert re.search(r"^verdict raw: skipped: ib_write_bw on w1 has no --use_cuda", out, re.M)
    assert "ratio 2 ports / 1 port:  ucx 1.87x   raw n/a" in out
    assert out.splitlines()[-1] == "dual-port: 0 FAIL, 1 WARN"
    assert not (dw.dir / "ibw.log").exists()
    assert (dw.dir / "preflight.log").read_text().count("snapshot") == 2


# ---- 6. counters ------------------------------------------------------------------------------


def test_counters_are_snapshotted_before_and_diffed_after_each_of_the_four_runs(healthy):
    d, _ = healthy
    calls = (d.dir / "preflight.log").read_text().splitlines()
    assert [c.split()[1] for c in calls] == ["snapshot", "diff"] * 4
    names = [Path(c.split()[2]).name for c in calls]
    assert names == [
        f"dual-port.counters.{t}"
        for t in ("ucx1", "ucx1", "ucx2", "ucx2", "raw1", "raw1", "raw2", "raw2")
    ]
    assert all(c.split()[3:] == ["w1", "w2"] for c in calls)


def test_a_second_port_without_traffic_in_a_two_port_run_is_a_warning(dw):
    f0 = "mlx5_0"
    single = f"w1/{f0} w2/{f0}"
    dw.traffic([single, single, single, f"w1/{f0} w1/mlx5_1 w2/{f0}"])
    result = dw.run()
    out = result.stdout
    assert result.returncode == 0, out
    assert re.search(
        r"^ctr ucx 2 ports\s+WARN\s+second port carried no traffic \(mlx5_1\)$", out, re.M
    )
    assert "ctr raw 2 ports" not in out  # one node showed traffic on the second port
    assert re.search(r"^\s+ucx 2 ports\s+mlx5_0 yes\s+mlx5_1 no", out, re.M)
    assert out.splitlines()[-1] == "dual-port: 0 FAIL, 1 WARN"


def test_traffic_on_the_second_port_of_a_one_port_run_and_none_on_the_first_are_warnings(dw):
    both = "w1/mlx5_0 w1/mlx5_1 w2/mlx5_0 w2/mlx5_1"
    dw.traffic(["w1/mlx5_1 w2/mlx5_1", both, "none", both])
    result = dw.run()
    out = result.stdout
    assert result.returncode == 0
    assert re.search(
        r"^ctr ucx 1 port\s+WARN\s+first port carried no traffic \(mlx5_0\)", out, re.M
    )
    assert re.search(
        r"^ctr ucx 1 port\s+WARN\s+the second port carried traffic in a one-port run", out, re.M
    )
    assert re.search(r"^ctr raw 1 port\s+WARN\s+first port carried no traffic", out, re.M)
    assert out.splitlines()[-1] == "dual-port: 0 FAIL, 3 WARN"


def test_ecn_activity_is_shown_per_run(dw):
    dw.traffic(ecn="yes")
    result = dw.run()
    assert len(re.findall(r"ECN/CNP yes$", result.stdout, re.M)) == 4


def test_a_failing_snapshot_is_one_warning_and_the_runs_still_count(dw):
    (dw.dir / "snapshot_fail").write_text("")
    result = dw.run()
    out = result.stdout
    assert result.returncode == 0, out
    assert re.search(
        r"^counters\s+WARN\s+no counters in 4 of 4 runs: snapshot failed: w1 ssh FAIL",
        out,
        re.M,
    )
    assert len(re.findall(r"^  (ucx|raw) \d ports?\s+n/a$", out, re.M)) == 4
    assert re.match(r"ucx 2 ports\s+OK\s+700 Gb/s", line(out, "ucx 2 ports"))
    assert out.splitlines()[-1] == "dual-port: 0 FAIL, 1 WARN"
    assert "diff" not in (dw.dir / "preflight.log").read_text()  # no diff without a snapshot


def test_a_failing_diff_is_a_warning_with_its_reason(dw):
    (dw.dir / "diff_fail").write_text("")
    result = dw.run()
    assert result.returncode == 0
    assert re.search(
        r"^counters\s+WARN\s+no counters in 4 of 4 runs: diff failed: w2 ssh FAIL no route",
        result.stdout,
        re.M,
    )


# ---- 7. failure paths -------------------------------------------------------------------------


def test_mpirun_failing_fails_the_ucx_part_and_leaves_the_raw_part_standing(dw):
    result = dw.run(FAKE_MPIRUN_MODE="fail")
    out = result.stdout
    assert result.returncode == 1
    screen_limits(out)
    assert re.search(
        r"^ucx 1 port\s+FAIL\s+mpirun exited 1: \[w1:123\] Error: failed to create endpoint",
        out,
        re.M,
    )
    assert re.search(r"^ucx 2 ports\s+FAIL\s+mpirun exited 1", out, re.M)
    assert re.match(r"raw 1 port\s+OK\s+390 Gb/s", line(out, "raw 1 port"))
    assert re.match(r"raw 2 ports\s+OK\s+780 Gb/s", line(out, "raw 2 ports"))
    assert "verdict ucx: not measured" in out
    assert "verdict raw: second port adds bandwidth" in out
    assert re.search(r"^ratio 2 ports / 1 port:\s+ucx n/a\s+raw 2\.00x$", out, re.M)
    assert out.splitlines()[-1] == "dual-port: 2 FAIL, 0 WARN"
    assert POINTER in out


def test_mpirun_without_result_rows_is_a_fail(dw):
    result = dw.run(FAKE_MPIRUN_MODE="empty")
    assert result.returncode == 1
    assert re.search(
        r"^ucx 1 port\s+FAIL\s+no result rows in the Blink output: something unrelated",
        result.stdout,
        re.M,
    )


def test_a_missing_mpirun_is_a_fail(dw):
    (dw.w["hosts"]["head"] / "lab" / "ompi-cuda" / "bin" / "mpirun").unlink()
    result = dw.run()
    assert result.returncode == 1
    assert re.search(
        r"^ucx 1 port\s+FAIL\s+mpirun not found: LAB/ompi-cuda/bin/mpirun, LAB=\S+",
        result.stdout,
        re.M,
    )
    assert re.match(r"raw 2 ports\s+OK", line(result.stdout, "raw 2 ports"))


def test_a_hanging_mpirun_is_bounded_by_the_step_timeout(dw):
    result = dw.run(FAKE_MPIRUN_MODE="hang", STEP_TIMEOUT="1")
    assert result.returncode == 1
    assert re.search(r"^ucx 1 port\s+FAIL\s+mpirun timed out after 1s", result.stdout, re.M)
    assert re.search(r"^ucx 2 ports\s+FAIL\s+mpirun timed out after 1s", result.stdout, re.M)


def test_an_ib_write_bw_that_cannot_connect_fails_the_raw_part_and_leaves_ucx_standing(dw):
    result = dw.run(FAKE_IBW_FAIL="client")
    out = result.stdout
    assert result.returncode == 1
    assert re.search(
        r"^raw 1 port\s+FAIL\s+mlx5_0: ib_write_bw exited 1: Unable to connect to 10\.1\.0\.1",
        out,
        re.M,
    )
    assert len(re.findall(r"^raw 2 ports\s+FAIL\s+mlx5_[01]: ib_write_bw exited 1", out, re.M)) == 2
    assert re.match(r"ucx 2 ports\s+OK\s+700 Gb/s", line(out, "ucx 2 ports"))
    assert "verdict raw: not measured" in out
    assert out.splitlines()[-1] == "dual-port: 3 FAIL, 0 WARN"


def test_a_hanging_ib_write_bw_is_bounded_by_the_step_timeout(dw):
    result = dw.run(FAKE_IBW_FAIL="hang", STEP_TIMEOUT="1")
    assert result.returncode == 1
    assert re.search(
        r"^raw 1 port\s+FAIL\s+mlx5_0: ib_write_bw timed out after 1s", result.stdout, re.M
    )


def test_an_unreachable_node_b_is_one_raw_fail_and_the_ucx_part_still_ran(dw):
    (dw.dir / "unreachable.w2").write_text("")
    result = dw.run()
    out = result.stdout
    assert result.returncode == 1
    assert re.search(
        r"^raw\s+FAIL\s+cannot read the port addresses on w2: ssh: connect to host w2", out, re.M
    )
    assert re.match(r"ucx 2 ports\s+OK\s+700 Gb/s", line(out, "ucx 2 ports"))
    assert out.splitlines()[-1] == "dual-port: 1 FAIL, 0 WARN"


def test_an_unreachable_node_a_fails_the_raw_part_with_one_line(dw):
    (dw.dir / "unreachable.w1").write_text("")
    result = dw.run()
    assert result.returncode == 1
    assert re.search(
        r"^raw\s+FAIL\s+cannot run ib_write_bw on w1: ssh: connect to host w1", result.stdout, re.M
    )


def test_unparsable_numbers_are_a_fail_naming_the_value(dw):
    result = dw.run(FAKE_IBW_GARBAGE="1")
    assert result.returncode == 1
    assert re.search(
        r"^raw 1 port\s+FAIL\s+mlx5_0: unparsable BW average 'N/A'", result.stdout, re.M
    )


def test_a_blink_table_with_only_zero_times_is_not_a_number(dw):
    put(
        dw.w["hosts"]["head"] / "lab" / "ompi-cuda" / "bin" / "mpirun",
        "#!/usr/bin/env bash\necho 'Average,Minimum,Maximum,Median,MainRank'\n"
        "echo '0.1,0.1,0.000000000,0.1,0.1'\n",
        0o755,
    )
    result = dw.run()
    assert result.returncode == 1
    assert re.search(r"^ucx 1 port\s+FAIL\s+no result rows", result.stdout, re.M)


@pytest.mark.parametrize(("nodes", "count"), [("w1 w2 w3", 3), ("w1", 1), ("", 0)])
def test_an_allocation_without_exactly_two_nodes_is_one_fail(dw, nodes, count):
    result = dw.run(FAKE_NODES=nodes)
    assert result.returncode == 1
    assert re.search(
        rf"^nodes\s+FAIL\s+{count} nodes in w\[1-2\], the step needs exactly 2$",
        result.stdout,
        re.M,
    )
    assert result.stdout.splitlines()[-1] == "dual-port: 1 FAIL, 0 WARN"
    assert dw.calls() == []


def test_no_allocation_and_no_nodes_exits_2_with_one_line(dw):
    result = dw.run(SLURM_JOB_NODELIST="")
    assert result.returncode == 2 and result.stdout == ""
    assert len(result.stderr.strip().splitlines()) == 1
    assert "no allocation (SLURM_JOB_NODELIST is empty)" in result.stderr


def test_the_nodes_setting_names_the_pair_when_there_is_no_nodelist(dw):
    result = dw.run(SLURM_JOB_NODELIST="", NODES="w2 w1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "nodes w2 -> w1" in result.stdout
    assert dw.calls()[0]["argv"][dw.calls()[0]["argv"].index("-H") + 1] == "w2,w1"


# ---- 8. the allocation ------------------------------------------------------------------------


def salloc_env(dw, **extra):
    return {
        "GPU_LAB_ALLOC": "",
        "SALLOC": str(dw.w["gbin"] / "salloc"),
        "SLURM_JOB_NODELIST": "",
        **extra,
    }


def test_outside_an_allocation_the_step_runs_again_under_salloc_with_the_marker(dw):
    dw.traffic(card=3)
    result = dw.run(["3"], **salloc_env(dw))
    assert result.returncode == 0, result.stdout + result.stderr
    args = (dw.dir / "salloc.args").read_text().strip()
    here = SCRIPT.parent
    assert (
        args
        == f"-N 2 -p h100_4nodes --exclusive --immediate=600 -- bash {here}/gpu_lab.sh dual-port 3"
    )
    env = dict(ln.split("=", 1) for ln in (dw.dir / "salloc.env").read_text().splitlines())
    assert env["GPU_LAB_IN_ALLOC"] == "1"
    assert (dw.dir / "salloc.calls").read_text().count(
        "x"
    ) == 1  # the wrapped run does not wrap again
    assert "card 3: mlx5_8 + mlx5_9" in result.stdout
    assert result.stdout.count("gpu_lab dual-port") == 1
    assert (
        "ucx 2 ports" in result.stdout
        and result.stdout.splitlines()[-1] == "dual-port: 0 FAIL, 0 WARN"
    )
    assert (dw.dir / "scontrol.log").read_text().strip() == "show hostnames w[1-2]"
    assert [p.name for p in (dw.w["work"] / "gpu_lab").iterdir() if p.name.startswith(".")] == []


def test_the_partition_and_the_nodes_go_to_salloc(dw):
    result = dw.run(**salloc_env(dw, PARTITION="gpu", NODES="w1 w2", ALLOC_TIMEOUT="45"))
    assert result.returncode == 0, result.stdout + result.stderr
    args = (dw.dir / "salloc.args").read_text().split()
    assert args[:7] == ["-N", "2", "-p", "gpu", "--exclusive", "--immediate=45", "--nodelist=w1,w2"]
    assert args[7] == "--"


def test_the_exit_status_of_the_wrapped_run_is_the_exit_status(dw):
    result = dw.run(**salloc_env(dw), FAKE_MPIRUN_MODE="fail")
    assert result.returncode == 1
    assert "mpirun exited 1" in result.stdout


@pytest.mark.parametrize(
    "inside",
    [
        {"SLURM_JOB_ID": "5", "SLURM_JOB_NODELIST": "w[1-2]"},
        {"GPU_LAB_ALLOC": "0"},
        {"GPU_LAB_IN_ALLOC": "1"},
    ],
)
def test_inside_an_allocation_salloc_is_not_called(dw, inside):
    env = {
        "SALLOC": str(dw.w["gbin"] / "salloc"),
        "GPU_LAB_ALLOC": "",
        "SLURM_JOB_NODELIST": "w[1-2]",
        **inside,
    }
    result = dw.run(**env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (dw.dir / "salloc.calls").exists()


def test_a_salloc_that_gives_no_allocation_in_time_is_one_fail_line_and_exit_1(dw):
    result = dw.run(**salloc_env(dw, FAKE_SALLOC_MODE="timeout", ALLOC_TIMEOUT="3"))
    out = result.stdout
    assert result.returncode == 1
    assert re.search(
        r"^allocation\s+FAIL\s+no allocation of 2 nodes in h100_4nodes within 3s \(salloc exit 1\)$",
        out,
        re.M,
    )
    assert re.search(
        r"^  salloc: error: Unable to allocate resources: Immediate execution impossible", out, re.M
    )
    assert "--immediate=3" in (dw.dir / "salloc.args").read_text()
    assert out.splitlines()[-1] == "dual-port: 1 FAIL, 0 WARN"
    screen_limits(out)
    assert dw.calls() == []
    (run,) = runs(dw.w)
    assert "Pending job allocation 99" in (run / "dual-port.log").read_text()
    assert [p.name for p in (dw.w["work"] / "gpu_lab").iterdir() if p.name.startswith(".")] == []


def test_a_missing_salloc_is_the_same_one_fail_line(dw):
    result = dw.run(**salloc_env(dw, SALLOC=str(dw.dir / "no-such-salloc")))
    assert result.returncode == 1
    assert re.search(
        r"^allocation\s+FAIL\s+no allocation of 2 nodes in h100_4nodes within 600s \(salloc exit 127\)$",
        result.stdout,
        re.M,
    )
    assert "no-such-salloc" in result.stdout


# ---- 9. the screen ----------------------------------------------------------------------------


def test_a_stress_run_is_cut_to_the_screen_says_how_much_and_keeps_the_verdict(dw):
    long_dir = "x" * 200
    result = dw.run(
        FAKE_MPIRUN_MODE="long", FAKE_IBW_FAIL="long", WORK=str(dw.w["work"] / long_dir)
    )
    out = result.stdout
    lines = out.splitlines()
    assert result.returncode == 1
    screen_limits(out)
    assert len(lines) == SCREEN_LINES
    m = re.search(r"^\.\.\. (\d+) more lines, see: gpu_lab\.sh show dual-port$", out, re.M)
    assert m and int(m.group(1)) >= 1
    assert "~" in out
    assert re.fullmatch(r"dual-port: \d+ FAIL, \d+ WARN", lines[-1]), lines[-1]
    assert lines[-2] == ""
    assert (
        POINTER in out and "verdict ucx: not measured" in out and "verdict raw: not measured" in out
    )
    assert (
        out.index("verdict ucx")
        < out.index("verdict raw")
        < out.index(POINTER, out.index("verdict raw"))
    )
    (run,) = runs_in(dw.w["work"] / long_dir)
    log = (run / "dual-port.log").read_text()
    for k in range(1, 7):
        assert f"Error line {k} " + "0" * 250 + f"-END-{k}" in log
    shown = run_lab(dw.w, ["show", "dual-port"], WORK=str(dw.w["work"] / long_dir))
    assert shown.stdout == log


def runs_in(base: Path) -> list[Path]:
    return sorted(p for p in (base / "gpu_lab").iterdir() if p.is_dir() and not p.is_symlink())


def test_the_healthy_screen_is_well_inside_the_limits(healthy):
    _, result = healthy
    lines = result.stdout.splitlines()
    assert len(lines) <= SCREEN_LINES - 8 and max(map(len, lines)) <= SCREEN_COLS
    assert "~" not in result.stdout and "more lines" not in result.stdout


def test_long_node_names_are_shortened_on_the_nodes_line(dw):
    long_a, long_b = "n" * 40 + "a", "n" * 40 + "b"
    result = dw.run(FAKE_NODES=f"{long_a} {long_b}")
    screen_limits(result.stdout)
    assert re.search(r"^nodes n+~\S* -> n+~\S* ", result.stdout, re.M)


# ---- 10. read-only guard ----------------------------------------------------------------------


@pytest.mark.parametrize("name", WRITE_PATTERNS)
def test_the_dual_script_holds_no_command_that_writes_to_a_node(name):
    hits = [
        ln
        for ln in DUAL.read_text().splitlines()
        if re.search(WRITE_PATTERNS[name], ln, re.IGNORECASE)
    ]
    assert hits == []


def test_every_server_and_client_ends_by_itself_and_ucx_log_level_is_never_set():
    text = DUAL.read_text()
    assert "timeout -k 5 $((DUR + 30))" in text  # the remote end of every ib_write_bw
    assert text.count('timeout -k 5 "$STEP_TIMEOUT"') >= 5  # every launched command, locally
    uses = [ln for ln in text.splitlines() if "UCX_LOG_LEVEL" in ln]
    assert uses == ["    unset UCX_LOG_LEVEL"]


def test_the_script_writes_only_under_work(healthy):
    d, _ = healthy
    assert [p.name for p in d.w["work"].iterdir()] == ["gpu_lab"]
