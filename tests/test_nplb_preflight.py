"""examples/nplb/preflight.sh, run with bash against a fake ssh, per-host fake tools and a fake
checkout tree in tmp_path. Nothing here runs a real ssh, sinfo or touches the network.

Every host gets its own directory `hosts/<name>/` holding its checkout, its MPI install, its fake
system tools and the files those tools print. The fake ssh runs the remote command locally with
that host's PATH and rewrites the head's paths to the host's own tree (the real clusters mount the
head's checkout at the same path everywhere, so the script passes the head's paths as they are)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "nplb" / "preflight.sh"
PROGRAMS = [
    "pingpong_b",
    "pairwise_nb",
    "alltoall_nb",
    "allgather_nb",
    "incast_nb",
    "alltoall_man",
    "alltoall_comm_only",
    "allgather_comm_only",
    "allreduce_nb",
]
CHECKS_WORKER = [
    "ssh",
    "checkout",
    "mount",
    "data",
    "blink",
    "mpirun",
    "ldd",
    "ompi",
    "python",
    "ucx",
    "route",
]
CHECKS_HEAD = [c for c in CHECKS_WORKER if c != "route"]
OMPI = "Open MPI v4.1.6"
UCX = "# Library version: 1.18.0"
HEAD_IP = "10.0.0.1"

# Generic fake tools: each reads what it prints from files in $FAKE_HOST_DIR.
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
source "$dir/env"
exec bash -c "$cmd"
"""
FAKE_FINDMNT = (
    '#!/usr/bin/env bash\n[[ -f $FAKE_HOST_DIR/fstype ]] && cat "$FAKE_HOST_DIR/fstype"\nexit 0\n'
)
FAKE_LDD = '#!/usr/bin/env bash\ncat "$FAKE_HOST_DIR/ldd.out"\n'
FAKE_UCX_INFO = '#!/usr/bin/env bash\ncat "$FAKE_HOST_DIR/ucx.out"\n'
FAKE_IP = (
    '#!/usr/bin/env bash\n[[ $1 == route && $2 == get ]] || exit 1\ncat "$FAKE_HOST_DIR/route"\n'
)
FAKE_GETENT = (
    '#!/usr/bin/env bash\n[[ $1 == hosts && $2 == head ]] && echo "10.0.0.1 head"\nexit 0\n'
)
FAKE_EXPORTFS = '#!/usr/bin/env bash\ncat "$FAKE_HOST_DIR/exports"\n'
FAKE_OMPI_INFO = '#!/usr/bin/env bash\ncat "$FAKE_HOST_DIR/ompi.out"\n'
FAKE_MPIRUN = "#!/usr/bin/env bash\necho fake mpirun\n"
FAKE_BLINK = "#!/usr/bin/env bash\nexit 0\n"
FAKE_SINFO = '#!/usr/bin/env bash\necho "$*" >>"$FAKE_SSH_LOG.sinfo"\nprintf "w2\\nw1\\nw2\\n"\n'


def put(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode is not None:
        path.chmod(mode)


def build_host(root: Path, name: str, base_path: str) -> Path:
    """One host with everything consistent: NFS checkout, MPI, Blink, UCX, a non-RDMA route."""
    d = root / "hosts" / name
    checkout = d / "checkout"
    blink = checkout / "benchmarks" / "blink"
    mpi = d / "opt" / "mpi"
    for prog in PROGRAMS:
        put(blink / prog, FAKE_BLINK, 0o755)
    # the head's python also answers the receipt lookup with the head's Blink dir
    head_blink = root / "hosts" / "head" / "checkout" / "benchmarks" / "blink"
    put(
        checkout / ".venv" / "bin" / "python",
        f'#!/bin/sh\ncase "$2" in *get_receipt*) echo "{head_blink}" ;; esac\nexit 0\n',
        0o755,
    )
    put(mpi / "bin" / "mpirun", FAKE_MPIRUN, 0o755)
    put(mpi / "bin" / "ompi_info", FAKE_OMPI_INFO, 0o755)
    for tool, text in (
        ("findmnt", FAKE_FINDMNT),
        ("ldd", FAKE_LDD),
        ("ucx_info", FAKE_UCX_INFO),
        ("ip", FAKE_IP),
        ("getent", FAKE_GETENT),
    ):
        put(d / "bin" / tool, text, 0o755)
    put(d / "fstype", "nfs4\n")
    put(d / "ompi.out", f"{OMPI}\n  more text\n")
    put(d / "ucx.out", f"{UCX}\n# Library path: /x\n")
    put(
        d / "ldd.out",
        f"\tlibmpi.so.40 => {mpi}/lib/libmpi.so.40 (0x00007f)\n\tlibc.so.6 => /lib/libc.so.6 (0x1)\n",
    )
    put(d / "route", f"{HEAD_IP} dev eth0 src 10.0.0.9 uid 1000\n")
    put(d / "env", f'export PATH="{d}/bin:{mpi}/bin:{base_path}"\n')
    return d


# The only real tools on the fake hosts' PATH: the machine's own exportfs, ucx_info, ldd, ... must
# not leak in, or "tool missing" cases could not be built.
REAL_TOOLS = (
    "bash sh env cat sed awk sort uname rm mkdir mktemp dirname tail head grep tr cut".split()
)


def real_tool_dir(root: Path) -> str:
    tools = root / "realbin"
    tools.mkdir()
    for name in REAL_TOOLS:
        found = shutil.which(name)
        if found:
            (tools / name).symlink_to(found)
    return str(tools)


@pytest.fixture
def world(tmp_path: Path) -> dict:
    base = real_tool_dir(tmp_path)
    gbin = tmp_path / "gbin"
    put(gbin / "ssh", FAKE_SSH, 0o755)
    put(gbin / "sinfo", FAKE_SINFO, 0o755)
    hosts = {n: build_host(tmp_path, n, base) for n in ("head", "w1", "w2")}
    head = hosts["head"]
    put(head / "bin" / "exportfs", FAKE_EXPORTFS, 0o755)
    put(head / "exports", f"{head}\t<world>(sync,wdelay)\n")
    sysfs = tmp_path / "sysfs"
    (sysfs / "class" / "net" / "eth0" / "device").mkdir(parents=True)
    (sysfs / "class" / "net" / "eth1" / "device" / "infiniband" / "mlx5_3").mkdir(parents=True)
    return {"root": tmp_path, "gbin": gbin, "hosts": hosts, "sysfs": sysfs, "base": base}


def run_preflight(world: dict, args: list | None = None, **env) -> subprocess.CompletedProcess:
    head = world["hosts"]["head"]
    full_env = {
        **os.environ,
        "PATH": f"{world['gbin']}:{head}/bin:{head}/opt/mpi/bin:{world['base']}",
        "FAKE_HOST_DIR": str(head),
        "FAKE_HOSTS_DIR": str(world["root"] / "hosts"),
        "FAKE_SSH_LOG": str(world["root"] / "ssh.log"),
        "PREFLIGHT_SSH": str(world["gbin"] / "ssh"),
        "CRAB_DIR": str(head / "checkout"),
        "HEAD": "head",
        "PREFLIGHT_SYSFS": str(world["sysfs"]),
    }
    full_env.pop("PARTITION", None)
    full_env.update(env)
    if args is None:
        args = ["head", "w1", "w2"]
    return subprocess.run(
        ["bash", str(SCRIPT), *args], env=full_env, capture_output=True, text=True, timeout=60
    )


def parse(out: str) -> dict:
    """{(host, check): (status, detail)} from the table, ignoring the summary line."""
    rows = {}
    for line in out.splitlines():
        if line.startswith("preflight:"):
            continue
        host, check, status, *detail = line.split(None, 3)
        rows[(host, check)] = (status, detail[0] if detail else "")
    return rows


def statuses(rows: dict, host: str) -> dict:
    return {check: st for (h, check), (st, _) in rows.items() if h == host}


def test_script_is_executable():
    assert os.access(SCRIPT, os.X_OK)


def test_consistent_cluster_prints_only_ok_and_exits_0(world):
    result = run_preflight(world)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = parse(result.stdout)
    assert list(statuses(rows, "head")) == CHECKS_HEAD
    for w in ("w1", "w2"):
        assert list(statuses(rows, w)) == CHECKS_WORKER
    assert {st for st, _ in rows.values()} == {"OK"}
    assert "preflight: 0 FAIL, 0 WARN on 3 hosts" in result.stdout
    assert rows[("head", "mount")][1].startswith("exported")
    assert rows[("w1", "mount")][1].startswith("nfs")
    assert "eth0" in rows[("w1", "route")][1]


def test_columns_are_aligned(world):
    result = run_preflight(world, ["head", "w1"])
    lines = [ln for ln in result.stdout.splitlines() if not ln.startswith("preflight:")]
    assert len({ln.index(" OK ") for ln in lines}) == 1


def test_worker_checkout_not_on_nfs_fails_and_the_rest_is_checked(world):
    (world["hosts"]["w1"] / "fstype").write_text("ext4\n")
    result = run_preflight(world)
    assert result.returncode == 1
    rows = parse(result.stdout)
    assert rows[("w1", "mount")][0] == "FAIL"
    assert "ext4" in rows[("w1", "mount")][1]
    assert statuses(rows, "w2")["mount"] == "OK"
    assert list(statuses(rows, "w2")) == CHECKS_WORKER
    assert "preflight: 1 FAIL" in result.stdout


def test_worker_with_no_mount_at_all_fails(world):
    (world["hosts"]["w1"] / "fstype").unlink()
    result = run_preflight(world)
    assert result.returncode == 1
    assert parse(result.stdout)[("w1", "mount")][0] == "FAIL"


def test_head_checkout_not_exported_fails(world):
    (world["hosts"]["head"] / "exports").write_text("/srv/other\t<world>(rw)\n")
    result = run_preflight(world)
    assert result.returncode == 1
    assert parse(result.stdout)[("head", "mount")][0] == "FAIL"


def test_head_without_exportfs_only_warns(world):
    (world["hosts"]["head"] / "bin" / "exportfs").unlink()
    result = run_preflight(world)
    assert result.returncode == 0
    assert parse(result.stdout)[("head", "mount")][0] == "WARN"


def test_missing_blink_program_fails_and_names_it(world):
    blink = world["hosts"]["w1"] / "checkout" / "benchmarks" / "blink"
    (blink / "incast_nb").unlink()
    result = run_preflight(world)
    assert result.returncode == 1
    status, detail = parse(result.stdout)[("w1", "blink")]
    assert status == "FAIL" and "incast_nb" in detail
    assert "alltoall_nb" not in detail
    assert statuses(parse(result.stdout), "w2")["blink"] == "OK"


def test_non_executable_blink_program_fails(world):
    blink = world["hosts"]["w1"] / "checkout" / "benchmarks" / "blink"
    (blink / "pingpong_b").chmod(0o644)
    result = run_preflight(world)
    status, detail = parse(result.stdout)[("w1", "blink")]
    assert status == "FAIL" and "pingpong_b" in detail


def test_heads_mpirun_path_missing_on_a_worker_fails(world):
    (world["hosts"]["w2"] / "opt" / "mpi" / "bin" / "mpirun").unlink()
    result = run_preflight(world)
    assert result.returncode == 1
    status, detail = parse(result.stdout)[("w2", "mpirun")]
    assert status == "FAIL" and "mpirun" in detail
    assert statuses(parse(result.stdout), "w1")["mpirun"] == "OK"


def test_different_mpirun_on_path_but_heads_path_exists_warns(world):
    w1 = world["hosts"]["w1"]
    put(w1 / "alt" / "bin" / "mpirun", FAKE_MPIRUN, 0o755)
    put(w1 / "env", f'export PATH="{w1}/alt/bin:{w1}/bin:{w1}/opt/mpi/bin:{world["base"]}"\n')
    result = run_preflight(world)
    assert result.returncode == 0
    status, detail = parse(result.stdout)[("w1", "mpirun")]
    assert status == "WARN"
    assert f"{w1}/alt/bin/mpirun" in detail


def test_unresolved_library_in_ldd_fails(world):
    w1 = world["hosts"]["w1"]
    (w1 / "ldd.out").write_text(
        f"\tlibmpi.so.40 => {w1}/opt/mpi/lib/libmpi.so.40 (0x1)\n\tlibucp.so.0 => not found\n"
    )
    result = run_preflight(world)
    assert result.returncode == 1
    status, detail = parse(result.stdout)[("w1", "ldd")]
    assert status == "FAIL" and "libucp.so.0" in detail


def test_libmpi_resolving_elsewhere_than_on_the_head_fails(world):
    (world["hosts"]["w1"] / "ldd.out").write_text("\tlibmpi.so.40 => /usr/lib/libmpi.so.40 (0x1)\n")
    result = run_preflight(world)
    assert result.returncode == 1
    assert parse(result.stdout)[("w1", "ldd")][0] == "FAIL"


def test_ompi_version_differing_from_the_head_fails(world):
    (world["hosts"]["w1"] / "ompi.out").write_text("Open MPI v4.1.5\n")
    result = run_preflight(world)
    assert result.returncode == 1
    status, detail = parse(result.stdout)[("w1", "ompi")]
    assert status == "FAIL" and "4.1.5" in detail and "4.1.6" in detail


def test_import_crab_failing_fails(world):
    put(
        world["hosts"]["w1"] / "checkout" / ".venv" / "bin" / "python",
        '#!/bin/sh\necho "ModuleNotFoundError: No module named crab" >&2\nexit 1\n',
        0o755,
    )
    result = run_preflight(world)
    assert result.returncode == 1
    status, detail = parse(result.stdout)[("w1", "python")]
    assert status == "FAIL" and "crab" in detail
    assert statuses(parse(result.stdout), "w2")["python"] == "OK"


def test_ucx_version_differing_fails_and_missing_ucx_info_warns(world):
    (world["hosts"]["w1"] / "ucx.out").write_text("# Library version: 1.17.0\n")
    (world["hosts"]["w2"] / "bin" / "ucx_info").unlink()
    result = run_preflight(world)
    rows = parse(result.stdout)
    assert rows[("w1", "ucx")][0] == "FAIL"
    assert rows[("w2", "ucx")][0] == "WARN"


def test_route_over_a_netdev_with_an_rdma_device_warns(world):
    (world["hosts"]["w1"] / "route").write_text(f"{HEAD_IP} dev eth1 src 10.0.0.3 uid 1000\n")
    result = run_preflight(world)
    assert result.returncode == 0
    rows = parse(result.stdout)
    status, detail = rows[("w1", "route")]
    assert status == "WARN" and "eth1" in detail and "mlx5_3" in detail
    assert rows[("w2", "route")][0] == "OK"
    assert "preflight: 0 FAIL, 1 WARN" in result.stdout


def test_unreachable_host_gets_one_ssh_line_and_the_script_goes_on(world):
    (world["hosts"]["w1"] / "unreachable").write_text("")
    result = run_preflight(world)
    assert result.returncode == 1
    rows = parse(result.stdout)
    assert statuses(rows, "w1") == {"ssh": "FAIL"}
    assert list(statuses(rows, "w2")) == CHECKS_WORKER
    assert "preflight: 1 FAIL, 0 WARN on 3 hosts" in result.stdout


def test_no_hosts_and_no_partition_prints_usage_and_exits_2(world):
    result = run_preflight(world, [])
    assert result.returncode == 2
    assert "preflight.sh [HOST...]" in result.stderr
    assert not (world["root"] / "ssh.log").exists()


def test_partition_env_lists_the_hosts_with_sinfo(world):
    result = run_preflight(world, [], PARTITION="p")
    assert result.returncode == 0, result.stdout + result.stderr
    rows = parse(result.stdout)
    assert {h for h, _ in rows} == {"w1", "w2"}
    assert "-p p" in (world["root"] / "ssh.log.sinfo").read_text()
    assert "preflight: 0 FAIL, 0 WARN on 2 hosts" in result.stdout


def test_receipt_lookup_failure_exits_2_with_a_message(world):
    put(
        world["hosts"]["head"] / "checkout" / ".venv" / "bin" / "python",
        "#!/bin/sh\nexit 1\n",
        0o755,
    )
    result = run_preflight(world)
    assert result.returncode == 2
    assert "receipt" in result.stderr


def fake_ldd_needing(libdir: str, host: Path) -> None:
    """A worker whose libmpi only resolves when LD_LIBRARY_PATH holds libdir."""
    put(
        host / "bin" / "ldd",
        '#!/usr/bin/env bash\ncase ":${LD_LIBRARY_PATH:-}:" in\n'
        f'    *:{libdir}:*) printf "\\tlibmpi.so.40 => {host}/opt/mpi/lib/libmpi.so.40 (0x1)\\n" ;;\n'
        '    *) printf "\\tlibmpi.so.40 => not found\\n" ;;\nesac\n',
        0o755,
    )


def test_ldd_runs_with_the_heads_ld_library_path(world):
    fake_ldd_needing("/opt/special/lib", world["hosts"]["w1"])
    ok = run_preflight(world, LD_LIBRARY_PATH="/other:/opt/special/lib")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert parse(ok.stdout)[("w1", "ldd")][0] == "OK"


def test_ldd_fails_when_the_heads_ld_library_path_is_empty(world):
    fake_ldd_needing("/opt/special/lib", world["hosts"]["w1"])
    result = run_preflight(world, LD_LIBRARY_PATH="")
    assert result.returncode == 1
    status, detail = parse(result.stdout)[("w1", "ldd")]
    assert status == "FAIL" and "libmpi.so.40" in detail
    assert parse(result.stdout)[("w2", "ldd")][0] == "OK"


def test_data_dir_is_created_and_probed_without_leaving_files(world):
    result = run_preflight(world)
    rows = parse(result.stdout)
    assert {rows[(h, "data")][0] for h in ("head", "w1", "w2")} == {"OK"}
    for host in world["hosts"].values():
        assert list((host / "checkout" / "data").iterdir()) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the directory mode")
def test_read_only_data_dir_fails(world):
    data = world["hosts"]["w1"] / "checkout" / "data"
    data.mkdir()
    data.chmod(0o555)
    try:
        result = run_preflight(world)
    finally:
        data.chmod(0o755)
    assert result.returncode == 1
    status, detail = parse(result.stdout)[("w1", "data")]
    assert status == "FAIL" and ".preflight." in detail
    assert parse(result.stdout)[("w2", "data")][0] == "OK"
