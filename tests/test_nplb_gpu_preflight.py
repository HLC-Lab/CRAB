"""examples/nplb/gpu_preflight.sh, run with bash against a fake ssh and per-host fake tools in tmp_path.
Nothing here runs a real ssh, sinfo, nvidia-smi or mlxreg, and nothing sends a signal.

Every host gets its own directory `hosts/<name>/` holding its fake sysfs and /proc, its lab stack,
its CUDA libraries, its fake system tools and the files those tools print. The fake ssh runs the
remote command locally with that host's PATH and rewrites the head's paths to the host's own tree
(the script passes the head's paths as they are, as on the real cluster where the checkout is NFS).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "nplb" / "gpu_preflight.sh"
HOSTS = ["head", "w1", "w2", "w3"]
PORTS = [f"mlx5_{i}" for i in (0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17)]
CHECKS_WORKER = ["state", "ports", "gpus", "peermem", "mpirun", "ucx", "blink", "nfs", "sr"]
CHECKS_HEAD = [c for c in CHECKS_WORKER if c != "nfs"]
# counter name -> folder under ports/1
COUNTER_DIRS = {
    "np_ecn_marked_roce_packets": "hw_counters",
    "np_cnp_sent": "hw_counters",
    "rp_cnp_handled": "hw_counters",
    "port_xmit_data": "counters",
    "port_rcv_data": "counters",
}
PROXY = (
    "proxy: counters show whether the NICs saw ECN marks/CNPs, not the NPLB setting of the switch"
)

FAKE_SSH = r"""#!/usr/bin/env bash
while [[ ${1:-} == -o ]]; do shift 2; done
host=$1; shift
echo "$host" >>"$FAKE_SSH_LOG"
dir=$FAKE_HOSTS_DIR/$host
if [[ -f $dir/unreachable ]]; then
    echo "ssh: connect to host $host port 22: No route to host" >&2
    exit 255
fi
cmd="$*"
cmd=${cmd//"$FAKE_HOSTS_DIR/head/"/"$dir/"}
unset LD_LIBRARY_PATH  # a real ssh does not carry it over
export FAKE_HOST_DIR=$dir
export SR_SYSFS=$dir/sysfs  # sr_state.sh reads /sys on the node; here the host's fake tree
source "$dir/env"
exec bash -c "$cmd"
"""
FAKE_SINFO_LOCAL = (
    '#!/usr/bin/env bash\necho "$*" >>"$FAKE_SSH_LOG.sinfo"\nprintf "w3\\nw1\\nhead\\nw2\\nw1\\n"\n'
)
FAKE_SINFO_HOST = '#!/usr/bin/env bash\ncat "$FAKE_HOST_DIR/state"\n'
FAKE_NVIDIA_SMI = (
    '#!/usr/bin/env bash\n[[ -f $FAKE_HOST_DIR/smi.fail ]] && { echo "NVIDIA-SMI has failed" >&2; exit 9; }\n'
    '[[ $1 == -L ]] && cat "$FAKE_HOST_DIR/gpus.out"\n'
)
FAKE_LDD = '#!/usr/bin/env bash\ncat "$FAKE_HOST_DIR/ldd.out"\n'
FAKE_FINDMNT = (
    "#!/usr/bin/env bash\n"
    "if [[ $* == *SOURCE* ]]; then f=source; else f=fstype; fi\n"
    '[[ -f $FAKE_HOST_DIR/$f ]] && cat "$FAKE_HOST_DIR/$f"\nexit 0\n'
)
FAKE_MLXREG = r"""#!/usr/bin/env bash
f=$FAKE_HOST_DIR/sr.$2
[[ -f $f ]] || f=$FAKE_HOST_DIR/sr.default
printf 'selective_repeat_forced_en | 0x%08x\n' "$(<"$f")"
printf 'adaptive_routing_forced_en | 0x00000000\n'
"""
FAKE_OMPI_INFO = '#!/usr/bin/env bash\ncat "$FAKE_HOST_DIR/ompi.out"\n'
FAKE_EXE = "#!/usr/bin/env bash\nexit 0\n"
LDD_OK = "\tlibucp.so.0 => /lab/ucx-cuda/lib/libucp.so.0 (0x1)\n\tlibcudart.so.12 => /cuda/libcudart.so.12 (0x2)\n"

# The only real tools on the fake hosts' PATH: the machine's own sinfo, nvidia-smi, ldd, ... must
# not leak in, or "tool missing" cases could not be built.
REAL_TOOLS = "bash sh env cat sed awk sort uname rm mkdir mktemp dirname tail head grep tr cut wc readlink basename".split()


def put(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode is not None:
        path.chmod(mode)


def counter_file(host: Path, dev: str, name: str) -> Path:
    return host / "sysfs" / "class" / "infiniband" / dev / "ports" / "1" / COUNTER_DIRS[name] / name


def build_host(root: Path, name: str, base_path: str) -> Path:
    """One host with everything healthy: 16 ACTIVE ports, 8 GPUs, the lab stack, an NFS checkout."""
    d = root / "hosts" / name
    ib = d / "sysfs" / "class" / "infiniband"
    for dev in [*PORTS, "mlx5_6", "mlx5_7"]:
        state = "1: DOWN\n" if dev in ("mlx5_6", "mlx5_7") else "4: ACTIVE\n"
        put(ib / dev / "ports" / "1" / "state", state)
        (d / "pci" / f"pci-{dev}").mkdir(parents=True)
        (ib / dev / "device").symlink_to(d / "pci" / f"pci-{dev}")
        for counter in COUNTER_DIRS:
            put(counter_file(d, dev, counter), "1000\n")
    put(d / "proc" / "modules", "nvidia 1 0 - Live 0x0\nnvidia_peermem 16384 0 - Live 0x0\n")
    lab = d / "lab"
    put(lab / "ompi-cuda" / "bin" / "mpirun", FAKE_EXE, 0o755)
    put(lab / "ompi-cuda" / "bin" / "ompi_info", FAKE_OMPI_INFO, 0o755)
    (lab / "ompi-cuda" / "lib").mkdir(parents=True)
    put(lab / "ucx-cuda" / "lib" / "libucp.so.0", "")
    for prog in ("alltoall_comm_only", "allgather_comm_only"):
        put(lab / "blink-gpu" / "build-cuda" / "bin" / prog, FAKE_EXE, 0o755)
    (d / "cudalib").mkdir()
    put(
        d / "gpus.out",
        "".join(f"GPU {i}: NVIDIA H100 80GB HBM3 (UUID: GPU-{i})\n" for i in range(8)),
    )
    put(d / "state", "idle\n")
    put(d / "ompi.out", "mca:mpi:base:param:mpi_built_with_cuda_support:value:true\n")
    put(d / "ldd.out", LDD_OK)
    put(d / "fstype", "nfs4\n")
    put(d / "source", "head:/root/crab\n")
    put(d / "sr.default", "1\n")
    for tool, text in (
        ("nvidia-smi", FAKE_NVIDIA_SMI),
        ("sinfo", FAKE_SINFO_HOST),
        ("ldd", FAKE_LDD),
        ("findmnt", FAKE_FINDMNT),
        ("mlxreg", FAKE_MLXREG),
    ):
        put(d / "bin" / tool, text, 0o755)
    put(d / "env", f'export PATH="{d}/bin:{base_path}"\n')
    return d


@pytest.fixture
def world(tmp_path: Path) -> dict:
    tools = tmp_path / "realbin"
    tools.mkdir()
    for tool in REAL_TOOLS:
        found = shutil.which(tool)
        if found:
            (tools / tool).symlink_to(found)
    gbin = tmp_path / "gbin"
    put(gbin / "ssh", FAKE_SSH, 0o755)
    put(gbin / "sinfo", FAKE_SINFO_LOCAL, 0o755)
    hosts = {n: build_host(tmp_path, n, str(tools)) for n in HOSTS}
    return {"root": tmp_path, "gbin": gbin, "hosts": hosts, "base": str(tools)}


def run_script(world: dict, args: list | None = None, **env) -> subprocess.CompletedProcess:
    head = world["hosts"]["head"]
    full_env = {
        **os.environ,
        "PATH": f"{world['gbin']}:{world['base']}",
        "FAKE_HOSTS_DIR": str(world["root"] / "hosts"),
        "FAKE_SSH_LOG": str(world["root"] / "ssh.log"),
        "PREFLIGHT_SSH": str(world["gbin"] / "ssh"),
        "CRAB_DIR": str(head / "checkout"),
        "LAB": str(head / "lab"),
        "CUDA_LIB": str(head / "cudalib"),
        "HEAD": "head",
        "GPU_PREFLIGHT_SYSFS": str(head / "sysfs"),
        "GPU_PREFLIGHT_PROC": str(head / "proc"),
    }
    full_env.pop("PARTITION", None)
    full_env.update(env)
    if args is None:
        args = HOSTS
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=full_env, capture_output=True, text=True, timeout=60
    )


def parse(out: str) -> dict:
    """{(host, check): (status, detail)} from the table, ignoring the summary line."""
    rows = {}
    for line in out.splitlines():
        if line.startswith("gpu_preflight:"):
            continue
        host, check, status, *detail = line.split(None, 3)
        rows[(host, check)] = (status, detail[0] if detail else "")
    return rows


def statuses(rows: dict, host: str) -> dict:
    return {check: st for (h, check), (st, _) in rows.items() if h == host}


def test_script_is_executable():
    assert os.access(SCRIPT, os.X_OK)


def test_healthy_world_prints_only_ok_and_exits_0(world):
    result = run_script(world)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = parse(result.stdout)
    assert list(statuses(rows, "head")) == CHECKS_HEAD
    for w in ("w1", "w2", "w3"):
        assert list(statuses(rows, w)) == CHECKS_WORKER
    assert {st for st, _ in rows.values()} == {"OK"}
    assert result.stdout.splitlines()[-1] == "gpu_preflight: 0 FAIL, 0 WARN on 4 hosts"
    assert "16 ports" in rows[("w1", "ports")][1]
    assert "8 GPUs" in rows[("w1", "gpus")][1]
    assert "nfs4" in rows[("w1", "nfs")][1]
    assert rows[("w1", "sr")][1] == "SR=1 AR=0 on 16 devices"


def test_columns_are_aligned(world):
    result = run_script(world, ["head", "w1"])
    lines = [ln for ln in result.stdout.splitlines() if not ln.startswith("gpu_preflight:")]
    assert len({ln.index(" OK ") for ln in lines}) == 1


def test_each_host_is_reached_once_for_the_checks_and_once_for_sr(world):
    run_script(world)
    log = (world["root"] / "ssh.log").read_text().split()
    assert sorted(log) == sorted(HOSTS * 2)


def _port_down(w):
    (counter_file(w["hosts"]["w1"], "mlx5_3", "np_cnp_sent").parents[1] / "state").write_text(
        "1: DOWN\n"
    )


def _port_missing(w):
    shutil.rmtree(w["hosts"]["w2"] / "sysfs" / "class" / "infiniband" / "mlx5_5")


def _seven_gpus(w):
    put(w["hosts"]["w3"] / "gpus.out", "".join(f"GPU {i}: H100 (UUID: x)\n" for i in range(7)))


def _no_peermem(w):
    put(w["hosts"]["w1"] / "proc" / "modules", "nvidia 1 0 - Live 0x0\n")


def _ompi_without_cuda(w):
    put(
        w["hosts"]["w2"] / "ompi.out",
        "mca:mpi:base:param:mpi_built_with_cuda_support:value:false\n",
    )


def _no_ucx(w):
    shutil.rmtree(w["hosts"]["w3"] / "lab" / "ucx-cuda")


def _no_blink_binary(w):
    (w["hosts"]["w1"] / "lab" / "blink-gpu" / "build-cuda" / "bin" / "alltoall_comm_only").unlink()


def _unresolved_lib(w):
    put(w["hosts"]["w2"] / "ldd.out", LDD_OK + "\tlibcudart.so.12 => not found\n")


def _node_down(w):
    put(w["hosts"]["w3"] / "state", "down*\n")


def _not_nfs(w):
    put(w["hosts"]["w1"] / "fstype", "ext4\n")


FAILURES: list[tuple[str, Callable[[dict], None], str, str, str]] = [
    ("port_down", _port_down, "w1", "ports", "mlx5_3"),
    ("port_missing", _port_missing, "w2", "ports", "mlx5_5"),
    ("seven_gpus", _seven_gpus, "w3", "gpus", "7 GPUs"),
    ("no_peermem", _no_peermem, "w1", "peermem", "nvidia_peermem"),
    ("ompi_without_cuda", _ompi_without_cuda, "w2", "mpirun", "CUDA"),
    ("no_ucx", _no_ucx, "w3", "ucx", "libucp"),
    ("no_blink_binary", _no_blink_binary, "w1", "blink", "alltoall_comm_only"),
    ("unresolved_lib", _unresolved_lib, "w2", "blink", "libcudart.so.12"),
    ("node_down", _node_down, "w3", "state", "down"),
    ("not_nfs", _not_nfs, "w1", "nfs", "ext4"),
]


@pytest.mark.parametrize(
    "mutate,host,check,needle",
    [pytest.param(m, h, c, n, id=i) for i, m, h, c, n in FAILURES],
)
def test_a_failure_mode_flips_exactly_its_own_check(world, mutate, host, check, needle):
    mutate(world)
    result = run_script(world)
    assert result.returncode == 1, result.stdout + result.stderr
    rows = parse(result.stdout)
    fails = {k for k, (st, _) in rows.items() if st == "FAIL"}
    assert fails == {(host, check)}
    assert needle in rows[(host, check)][1]
    assert {st for k, (st, _) in rows.items() if k != (host, check)} == {"OK"}
    assert result.stdout.splitlines()[-1] == "gpu_preflight: 1 FAIL, 0 WARN on 4 hosts"


def test_a_down_port_and_a_missing_one_are_both_named(world):
    _port_down(world)
    shutil.rmtree(world["hosts"]["w1"] / "sysfs" / "class" / "infiniband" / "mlx5_17")
    detail = parse(run_script(world).stdout)[("w1", "ports")][1]
    assert "missing: mlx5_17" in detail and "not ACTIVE: mlx5_3" in detail


def test_the_cx5_devices_are_not_checked_and_an_unknown_device_warns(world):
    w1 = world["hosts"]["w1"]
    put(w1 / "sysfs" / "class" / "infiniband" / "mlx5_18" / "ports" / "1" / "state", "4: ACTIVE\n")
    result = run_script(world)
    assert result.returncode == 0
    status, detail = parse(result.stdout)[("w1", "ports")]
    assert status == "WARN" and "mlx5_18" in detail and "mlx5_6" not in detail


def test_unreachable_host_gets_one_ssh_line_and_the_script_goes_on(world):
    (world["hosts"]["w1"] / "unreachable").write_text("")
    result = run_script(world)
    assert result.returncode == 1
    rows = parse(result.stdout)
    assert statuses(rows, "w1") == {"ssh": "FAIL"}
    assert "No route to host" in rows[("w1", "ssh")][1]
    assert list(statuses(rows, "w2")) == CHECKS_WORKER
    assert result.stdout.splitlines()[-1] == "gpu_preflight: 1 FAIL, 0 WARN on 4 hosts"


def test_missing_sinfo_on_a_node_only_warns(world):
    (world["hosts"]["w1"] / "bin" / "sinfo").unlink()
    result = run_script(world)
    assert result.returncode == 0
    assert parse(result.stdout)[("w1", "state")][0] == "WARN"


@pytest.mark.parametrize("state", ["drain", "drng", "fail", "maint", "unk*", "drained"])
def test_unusable_slurm_states_fail(world, state):
    put(world["hosts"]["w1"] / "state", f"{state}\n")
    assert parse(run_script(world).stdout)[("w1", "state")][0] == "FAIL"


@pytest.mark.parametrize("state", ["idle", "mix", "alloc", "idle~"])
def test_usable_slurm_states_pass(world, state):
    put(world["hosts"]["w1"] / "state", f"{state}\n")
    assert parse(run_script(world).stdout)[("w1", "state")][0] == "OK"


def test_nvidia_smi_failing_fails_gpus(world):
    (world["hosts"]["w2"] / "smi.fail").write_text("")
    status, detail = parse(run_script(world).stdout)[("w2", "gpus")]
    assert status == "FAIL" and "NVIDIA-SMI has failed" in detail


def test_non_executable_blink_binary_fails(world):
    prog = world["hosts"]["w1"] / "lab" / "blink-gpu" / "build-cuda" / "bin" / "allgather_comm_only"
    prog.chmod(0o644)
    status, detail = parse(run_script(world).stdout)[("w1", "blink")]
    assert (
        status == "FAIL" and "allgather_comm_only" in detail and "alltoall_comm_only" not in detail
    )


def test_ldd_runs_with_the_lab_and_cuda_library_path(world):
    w1 = world["hosts"]["w1"]
    put(
        w1 / "bin" / "ldd",
        '#!/usr/bin/env bash\nfor d in "$FAKE_HOST_DIR/lab/ucx-cuda/lib" "$FAKE_HOST_DIR/cudalib" '
        '"$FAKE_HOST_DIR/lab/ompi-cuda/lib"; do\n'
        '    case ":${LD_LIBRARY_PATH:-}:" in *":$d:"*) ;; *) echo "\\tlibneeds.so => not found" ;; esac\n'
        "done\n",
        0o755,
    )
    result = run_script(world)
    assert result.returncode == 0, result.stdout
    # and it does flag a missing directory
    shutil.rmtree(w1 / "cudalib")
    put(
        w1 / "bin" / "ldd",
        (w1 / "bin" / "ldd").read_text().replace('"$FAKE_HOST_DIR/cudalib"', '"/x/y"'),
    )
    assert parse(run_script(world).stdout)[("w1", "blink")][0] == "FAIL"


def test_missing_ldd_only_warns(world):
    (world["hosts"]["w1"] / "bin" / "ldd").unlink()
    result = run_script(world)
    assert result.returncode == 0
    assert parse(result.stdout)[("w1", "blink")][0] == "WARN"


def test_head_has_no_nfs_line_even_when_its_checkout_is_not_a_mount(world):
    (world["hosts"]["head"] / "fstype").write_text("ext4\n")
    result = run_script(world)
    assert result.returncode == 0
    assert "nfs" not in statuses(parse(result.stdout), "head")


def test_sr_values_differing_across_ports_warn_and_are_printed(world):
    (world["hosts"]["w1"] / "sr.pci-mlx5_3").write_text("0\n")
    result = run_script(world)
    assert result.returncode == 0, result.stdout
    rows = parse(result.stdout)
    status, detail = rows[("w1", "sr")]
    assert status == "WARN" and "SR=1 0" in detail and "w1/mlx5_3" in detail
    assert "0 FAIL, 4 WARN" in result.stdout


def test_sr_read_failure_fails_that_host_only(world):
    (world["hosts"]["w2"] / "bin" / "mlxreg").unlink()
    result = run_script(world)
    assert result.returncode == 1
    rows = parse(result.stdout)
    status, detail = rows[("w2", "sr")]
    assert status == "FAIL" and "mlxreg" in detail
    assert rows[("w1", "sr")][0] == "OK"
    assert "1 FAIL, 0 WARN" in result.stdout


def test_no_hosts_and_no_partition_prints_usage_and_exits_2(world):
    result = run_script(world, [])
    assert result.returncode == 2
    assert "gpu_preflight.sh [HOST...]" in result.stderr
    assert not (world["root"] / "ssh.log").exists()


def test_help_exits_0_and_prints_usage(world):
    result = run_script(world, ["-h"])
    assert result.returncode == 0
    assert "counters snapshot FILE" in result.stderr
    assert "GPU_PREFLIGHT_PROC" in result.stderr
    assert not (world["root"] / "ssh.log").exists()


def test_partition_env_lists_the_hosts_with_sinfo(world):
    result = run_script(world, [], PARTITION="p")
    assert result.returncode == 0, result.stdout + result.stderr
    rows = parse(result.stdout)
    assert {h for h, _ in rows} == set(HOSTS)
    assert "-p p" in (world["root"] / "ssh.log.sinfo").read_text()
    assert result.stdout.splitlines()[-1] == "gpu_preflight: 0 FAIL, 0 WARN on 4 hosts"


def test_counters_with_a_bad_subcommand_or_no_file_exit_2(world):
    assert run_script(world, ["counters"]).returncode == 2
    assert run_script(world, ["counters", "bogus", "f"]).returncode == 2
    assert run_script(world, ["counters", "snapshot"]).returncode == 2
    assert run_script(world, ["counters", "snapshot", str(world["root"] / "s")]).returncode == 2
    assert not (world["root"] / "ssh.log").exists()


def snapshot(
    world: dict, hosts: list | None = None, **env
) -> tuple[subprocess.CompletedProcess, Path]:
    path = world["root"] / "snap.txt"
    result = run_script(world, ["counters", "snapshot", str(path), *(hosts or HOSTS)], **env)
    return result, path


def read_snapshot(path: Path) -> dict:
    rows = {}
    for line in path.read_text().splitlines():
        host, dev, name, value = line.split(" ")
        rows[(host, dev, name)] = value
    return rows


def test_snapshot_writes_one_line_per_host_port_and_counter(world):
    result, path = snapshot(world)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = read_snapshot(path)
    assert len(rows) == 4 * 16 * 5
    assert {dev for _, dev, _ in rows} == set(PORTS)
    assert {name for _, _, name in rows} == set(COUNTER_DIRS)
    assert set(rows.values()) == {"1000"}


def test_snapshot_records_a_missing_counter_as_na_and_still_succeeds(world):
    counter_file(world["hosts"]["w2"], "mlx5_4", "np_cnp_sent").unlink()
    result, path = snapshot(world)
    assert result.returncode == 0
    rows = read_snapshot(path)
    assert rows[("w2", "mlx5_4", "np_cnp_sent")] == "NA"
    assert rows[("w2", "mlx5_4", "np_ecn_marked_roce_packets")] == "1000"


def test_snapshot_uses_partition_hosts_and_reads_each_host_once(world):
    path = world["root"] / "snap.txt"
    result = run_script(world, ["counters", "snapshot", str(path)], PARTITION="p")
    assert result.returncode == 0
    assert {h for h, _, _ in read_snapshot(path)} == set(HOSTS)
    assert sorted((world["root"] / "ssh.log").read_text().split()) == sorted(HOSTS)


def test_snapshot_with_an_unreachable_host_exits_1_and_keeps_the_others(world):
    (world["hosts"]["w1"] / "unreachable").write_text("")
    result, path = snapshot(world)
    assert result.returncode == 1
    assert "w1 ssh FAIL" in result.stderr
    assert {h for h, _, _ in read_snapshot(path)} == {"head", "w2", "w3"}


def bump(host: Path, dev: str, name: str, by: int) -> None:
    f = counter_file(host, dev, name)
    f.write_text(f"{int(f.read_text()) + by}\n")


def test_diff_names_the_ports_with_traffic_and_reports_no_ecn_activity(world):
    _, path = snapshot(world)
    bump(world["hosts"]["w1"], "mlx5_2", "port_xmit_data", 500)
    bump(world["hosts"]["w3"], "mlx5_16", "port_rcv_data", 7)
    result = run_script(world, ["counters", "diff", str(path), *HOSTS])
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert any(re.fullmatch(r"w1\s+mlx5_2\s+port_xmit_data\s+\+500", ln) for ln in lines)
    assert any(re.fullmatch(r"w3\s+mlx5_16\s+port_rcv_data\s+\+7", ln) for ln in lines)
    assert "ports with traffic: w1/mlx5_2 w3/mlx5_16" in lines
    assert "ECN/CNP activity: no" in lines
    assert lines[-1] == PROXY
    assert not any("mlx5_0 " in ln for ln in lines)


def test_diff_reports_ecn_activity_when_a_cnp_counter_moved(world):
    _, path = snapshot(world)
    bump(world["hosts"]["w2"], "mlx5_8", "np_cnp_sent", 3)
    bump(world["hosts"]["w2"], "mlx5_8", "port_xmit_data", 10)
    result = run_script(world, ["counters", "diff", str(path), *HOSTS])
    lines = result.stdout.splitlines()
    assert any(re.fullmatch(r"w2\s+mlx5_8\s+np_cnp_sent\s+\+3", ln) for ln in lines)
    assert "ECN/CNP activity: yes" in lines
    assert "ports with traffic: w2/mlx5_8" in lines


def test_diff_with_nothing_moved(world):
    _, path = snapshot(world)
    result = run_script(world, ["counters", "diff", str(path), *HOSTS])
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert "ports with traffic: none" in lines
    assert "ECN/CNP activity: no" in lines
    assert lines[-1] == PROXY
    assert not any(re.search(r"\s[+-]\d+$", ln) for ln in lines)


def test_diff_says_unknown_when_no_ecn_counter_is_readable(world):
    for host in world["hosts"].values():
        shutil.rmtree(
            host / "sysfs" / "class" / "infiniband" / "mlx5_0" / "ports" / "1" / "hw_counters"
        )
    for host in world["hosts"].values():
        for dev in PORTS:
            for name in ("np_ecn_marked_roce_packets", "np_cnp_sent", "rp_cnp_handled"):
                counter_file(host, dev, name).unlink(missing_ok=True)
    _, path = snapshot(world)
    bump(world["hosts"]["w1"], "mlx5_2", "port_xmit_data", 1)
    result = run_script(world, ["counters", "diff", str(path), *HOSTS])
    assert result.returncode == 0
    assert "ECN/CNP activity: unknown" in result.stdout.splitlines()
    assert "ports with traffic: w1/mlx5_2" in result.stdout.splitlines()


def test_diff_without_hosts_uses_the_hosts_in_the_file(world):
    _, path = snapshot(world, ["w1", "w2"])
    bump(world["hosts"]["w2"], "mlx5_3", "port_rcv_data", 2)
    bump(world["hosts"]["w3"], "mlx5_3", "port_rcv_data", 2)
    result = run_script(world, ["counters", "diff", str(path)])
    assert result.returncode == 0
    assert "ports with traffic: w2/mlx5_3" in result.stdout.splitlines()


def test_diff_with_a_host_missing_from_the_file_exits_2(world):
    _, path = snapshot(world, ["w1"])
    result = run_script(world, ["counters", "diff", str(path), "w1", "w2"])
    assert result.returncode == 2
    assert "w2" in result.stderr


def test_diff_with_an_unreachable_host_exits_1_after_the_summary(world):
    _, path = snapshot(world)
    (world["hosts"]["w1"] / "unreachable").write_text("")
    bump(world["hosts"]["w2"], "mlx5_3", "port_rcv_data", 2)
    result = run_script(world, ["counters", "diff", str(path), *HOSTS])
    assert result.returncode == 1
    assert "w1 ssh FAIL" in result.stderr
    assert "ports with traffic: w2/mlx5_3" in result.stdout.splitlines()
    assert "unreachable, not in this diff: w1" in result.stdout


def test_diff_with_a_missing_file_exits_2(world):
    result = run_script(world, ["counters", "diff", str(world["root"] / "nope.txt"), *HOSTS])
    assert result.returncode == 2
    assert "nope.txt" in result.stderr
    assert not (world["root"] / "ssh.log").exists()


@pytest.mark.parametrize(
    "text",
    ["", "w1 mlx5_0 np_cnp_sent\n", "w1 mlx5_0 np_cnp_sent abc\n", "w1 mlx5_0 np_cnp_sent 1 2\n"],
    ids=["empty", "three_fields", "not_a_number", "five_fields"],
)
def test_diff_with_a_malformed_file_exits_2(world, text):
    path = world["root"] / "bad.txt"
    path.write_text(text)
    result = run_script(world, ["counters", "diff", str(path), "w1"])
    assert result.returncode == 2
    assert not (world["root"] / "ssh.log").exists()


def test_snapshot_and_diff_leave_every_fake_node_unchanged(world):
    def tree(root: Path) -> dict:
        return {str(p.relative_to(root)): p.read_text() for p in root.rglob("*") if p.is_file()}

    before = {n: tree(h) for n, h in world["hosts"].items()}
    _, path = snapshot(world)
    run_script(world, ["counters", "diff", str(path), *HOSTS])
    run_script(world)
    assert {n: tree(h) for n, h in world["hosts"].items()} == before


# ---- read-only guard -------------------------------------------------------------------------
# Commands that change a node. The script may write only the snapshot FILE on this machine.
WRITE_PATTERNS = {
    "mlxreg -s": r"mlxreg\s+-s\b",
    "mlxconfig": r"mlxconfig",
    "tee": r"\btee\b",
    "redirect into /sys or /proc": r">>?\s*[\"']?(/sys|/proc|\$\{?(sysfs|SYSFS|proc|PROC)\b)",
    "modprobe": r"\b(modprobe|insmod|rmmod)\b",
    "mount command": r"(^|[;&|`(]|\$\()\s*(mount|umount)\s",
    "systemctl": r"systemctl",
    "kill": r"kill",
}


@pytest.mark.parametrize("name", WRITE_PATTERNS)
def test_the_script_holds_no_command_that_writes_to_a_node(name):
    hits = [
        ln
        for ln in SCRIPT.read_text().splitlines()
        if re.search(WRITE_PATTERNS[name], ln, re.IGNORECASE)
    ]
    assert hits == []


@pytest.mark.parametrize(
    "bad",
    [
        "mlxreg -s -d x",
        "mlxconfig -d x set",
        "echo 1 | tee /x",
        'echo 1 > "$sysfs/x"',
        "echo 1 >/proc/sys/x",
        "modprobe nvidia",
        "  mount -t nfs a b",
        "x && umount /y",
        "systemctl stop x",
        "kill -9 1",
    ],
)
def test_the_read_only_guard_does_catch_the_bad_commands(bad):
    assert any(re.search(p, bad, re.IGNORECASE) for p in WRITE_PATTERNS.values())
