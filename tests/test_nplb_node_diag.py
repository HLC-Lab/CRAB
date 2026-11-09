"""examples/nplb/node_tools.sh (`nplb node diag` and `nplb node fix`), run with bash against fakes.

Everything lives under tmp_path. Each fake node has a directory hosts/<name>/ with a bin/ of fake tools
(ibdev2netdev, rdma, ip, dmesg, lspci, ethtool, nvidia-smi, mst, findmnt, mount, umount, exportfs) that
print canned data and log every call to calls.log, and a state file `mounted`. The fake ssh runs the
remote command locally with that node's bin/ first on PATH and logs it. The process under test is
the head node: its own bin/ is first on its PATH, and the head counts as local (no ssh). No real ssh,
mount, mlxreg or network.

The pager cuts a line at 100 columns, so details are asserted on the report the pager saves whole."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
SCRIPT = NPLB / "node_tools.sh"
BASH = shutil.which("bash") or "/bin/bash"
NOW = "2026-11-09T14:07:00"
HEAD = "node110"
WORKERS = ["node112", "node114"]
NFS = "/root/crab"
ADDRS = {"node110": "10.0.0.110", "node112": "10.0.0.112", "node114": "10.0.0.114"}
EXPORT_OPTS = "(rw,sync,no_root_squash,no_subtree_check)"
CX8_PORTS = [f"mlx5_{n}" for n in (0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17)]
TOOLS = (
    "bash sh awk sed sort grep head tail cat date mkdir mv dirname basename uname python3 timeout "
    "find tr cut wc ls stat env readlink mktemp touch chmod ln tee cp id sleep rm true false"
).split()
REMOTE_TOOLS = (
    "ibdev2netdev rdma ip dmesg lspci ethtool nvidia-smi mst findmnt mount umount exportfs mlxreg"
).split()

# Prints data/<tool>.out, exits with data/<tool>.rc (default 0); logs "<tool> <args>".
GENERIC = r"""#!/usr/bin/env bash
hd="$(cd "$(dirname "$0")/.." && pwd)"
echo "TOOL $*" >>"$hd/calls.log"
cat "$hd/data/TOOL.out" 2>/dev/null
exit "$(cat "$hd/data/TOOL.rc" 2>/dev/null || echo 0)"
"""
FAKE_FINDMNT = r"""#!/usr/bin/env bash
hd="$(cd "$(dirname "$0")/.." && pwd)"
echo "findmnt $*" >>"$hd/calls.log"
src=$(cat "$hd/mounted" 2>/dev/null)
if [[ -n $src && ${src#*:} == "${*: -1}" ]]; then
    echo "nfs4 $src"
    exit 0
fi
exit 1
"""
FAKE_MOUNT = r"""#!/usr/bin/env bash
hd="$(cd "$(dirname "$0")/.." && pwd)"
echo "mount $*" >>"$hd/calls.log"
[[ $1 == -t && $2 == nfs && $3 == *:* ]] || { echo "bad mount args: $*" >&2; exit 32; }
[[ -e $hd/mount_fails ]] && { echo "mount.nfs: access denied by server" >&2; exit 32; }
echo "$3" >"$hd/mounted"
"""
FAKE_UMOUNT = r"""#!/usr/bin/env bash
hd="$(cd "$(dirname "$0")/.." && pwd)"
echo "umount $*" >>"$hd/calls.log"
rm -f "$hd/mounted"
"""
FAKE_ETHTOOL = r"""#!/usr/bin/env bash
hd="$(cd "$(dirname "$0")/.." && pwd)"
echo "ethtool $*" >>"$hd/calls.log"
echo "Settings for $1:"
printf '\tSpeed: %s\n' "$(cat "$hd/data/speed" 2>/dev/null || echo 400000Mb/s)"
"""
# Runs the remote command locally as the named host, with its own bin first on PATH.
FAKE_SSH = r"""#!/usr/bin/env bash
while [[ ${1:-} == -o ]]; do shift 2; done
host=$1; shift
echo "$host $*" >>"$FAKE_SSH_LOG"
if [[ ! -d $FAKE_HOSTS/$host ]]; then
    echo "ssh: Could not resolve hostname $host" >&2
    exit 255
fi
PATH="$FAKE_HOSTS/$host/bin:$FAKE_TOOLS" exec "$FAKE_BASH" -c "$*"
"""
FAKE_GETENT = r"""#!/usr/bin/env bash
echo "$*" >>"$FAKE_GETENT_LOG"
[[ $1 == hosts ]] || exit 2
grep -E " $2\$" "$FAKE_ETC_HOSTS" || exit 2
"""
# sr_state.sh stand-in: prints the canned text, logs its arguments.
FAKE_SR_STATE = r"""#!/usr/bin/env bash
echo "$*" >>"$FAKE_SR_STATE_LOG"
cat "$FAKE_SR_STATE_OUT"
exit "${FAKE_SR_STATE_RC:-0}"
"""
# sr_set.sh / ar_set.sh stand-in: logs "<value> <hosts...>"; the label is the script's own name.
FAKE_SET = r"""#!/usr/bin/env bash
label=$(basename "$0" .sh)
label=${label%_set}
echo "$*" >>"$FAKE_SET_LOG.$label"
echo "node110 mlx5_0 0000:19:00.0 ${label^^}=$1 OK"
echo "${label^^}=$1 on all 16 devices of $(($# - 1)) hosts"
[[ -e $FAKE_SET_LOG.$label.fail ]] && { echo "${label^^}=$1 not set everywhere: 15 devices OK; failed: node112/mlx5_3"; exit 1; }
exit 0
"""


def make_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def netdev(dev: str) -> str:
    n = int(dev.split("_")[1])
    return f"ens{n // 2 + 1}f{n % 2}np0"


def ibdev2netdev_text(listed: list[str], down: list[str]) -> str:
    lines = []
    for dev in listed:
        state = "Down" if dev in down else "Up"
        lines.append(f"{dev} port 1 ==> {netdev(dev)} ({state})")
    lines.append("mlx5_6 port 1 ==> enp83s0f0np0 (Down)")
    lines.append("mlx5_7 port 1 ==> enp83s0f1np1 (Down)")
    return "\n".join(lines) + "\n"


def ip_text(listed: list[str]) -> str:
    lines = ["lo               UNKNOWN        00:00:00:00:00:00 <LOOPBACK,UP,LOWER_UP>"]
    lines.append(
        "enp86s0f0        UP             aa:bb:cc:00:00:01 <BROADCAST,MULTICAST,UP,LOWER_UP>"
    )
    for dev in listed:
        lines.append(
            f"{netdev(dev):<16} UP             aa:bb:cc:00:10:{int(dev.split('_')[1]):02x} <BROADCAST,MULTICAST,UP,LOWER_UP>"
        )
    return "\n".join(lines) + "\n"


def lspci_text(cx8: int) -> str:
    lines = []
    for i in range(cx8):
        lines.append(
            f"{0x19 + i // 2:02x}:00.{i % 2} Ethernet controller [0200]: NVIDIA Corporation ConnectX-8 Family [15b3:1023]"
        )
    lines.append(
        "53:00.0 Ethernet controller [0200]: Mellanox Technologies MT27800 Family [ConnectX-5] [15b3:1017]"
    )
    lines.append(
        "53:00.1 Ethernet controller [0200]: Mellanox Technologies MT27800 Family [ConnectX-5] [15b3:1017]"
    )
    for i in range(8):
        lines.append(
            f"{0x18 + i:02x}:00.0 3D controller [0302]: NVIDIA Corporation GH100 [H100 SXM5 80GB] [10de:2330]"
        )
    return "\n".join(lines) + "\n"


def dmesg_text(extra: list[str], noise: int = 20) -> str:
    lines = [f"[{i:9.6f}] kernel: boot line {i}" for i in range(noise)]
    lines += extra
    return "\n".join(lines) + "\n"


def cable_lines(n: int) -> list[str]:
    return [
        f"[ 1234.{i:03d}] mlx5_core 0000:{0x19 + i:02x}:00.0: Cable unplugged" for i in range(n)
    ]


def make_host(
    site: dict,
    name: str,
    *,
    down: list[str] | None = None,
    listed: list[str] | None = None,
    cx8: int = 16,
    gpus: int = 8,
    dmesg: list[str] | None = None,
    dmesg_noise: int = 20,
    mounted: bool = True,
    missing: tuple[str, ...] = (),
    speed: str | None = None,
) -> Path:
    """Create the fake node `name`; `missing` tools get no fake in bin/."""
    hd = site["hosts"] / name
    for stale in ("bin", "data"):  # calls.log stays: tests read what several runs did
        shutil.rmtree(hd / stale, ignore_errors=True)
    (hd / "mounted").unlink(missing_ok=True)
    (hd / "data").mkdir(parents=True)
    (hd / "bin").mkdir()
    listed = CX8_PORTS if listed is None else listed
    data = {
        "ibdev2netdev": ibdev2netdev_text(listed, down or []),
        "rdma": "".join(
            f"link {d}/1 state {'DOWN' if d in (down or []) else 'ACTIVE'} physical_state LINK_UP netdev {netdev(d)}\n"
            for d in listed
        ),
        "ip": ip_text(listed),
        "dmesg": dmesg_text(dmesg or [], dmesg_noise),
        "lspci": lspci_text(cx8),
        "nvidia-smi": "".join(
            f"GPU {i}: NVIDIA H100 80GB HBM3 (UUID: GPU-{i:04d})\n" for i in range(gpus)
        ),
        "mst": "MST modules:\n------------\n    MST PCI module is not loaded\n",
        "exportfs": f"{NFS}\t\t{ADDRS['node112']}(sync,wdelay,hide,no_subtree_check,sec=sys,rw,no_root_squash)\n",
    }
    for tool, text in data.items():
        (hd / "data" / f"{tool}.out").write_text(text)
    for tool in REMOTE_TOOLS:
        if tool in missing:
            continue
        make_exe(hd / "bin" / tool, GENERIC.replace("TOOL", tool))
    for tool, body in (
        ("findmnt", FAKE_FINDMNT),
        ("mount", FAKE_MOUNT),
        ("umount", FAKE_UMOUNT),
        ("ethtool", FAKE_ETHTOOL),
    ):
        if tool not in missing:
            make_exe(hd / "bin" / tool, body)
    if speed:
        (hd / "data" / "speed").write_text(speed + "\n")
    if mounted:
        (hd / "mounted").write_text(f"{ADDRS[HEAD]}:{NFS}\n")
    return hd


def calls(site: dict, name: str) -> list[str]:
    log = site["hosts"] / name / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def sr_state_text(sr: list[int], ar: list[int]) -> str:
    lines = [
        f"node112 mlx5_{i} 0000:19:00.{i % 2} SR={s} AR={a}"
        for i, (s, a) in enumerate(zip(sr, ar, strict=True))
    ]
    if len(set(sr)) == 1:
        lines.append(f"SR {sr[0]} on all {len(sr)} devices of 1 hosts")
    else:
        lines.append("SR mixed: odd devices")
    return "\n".join(lines) + "\n"


@pytest.fixture
def site(tmp_path: Path) -> dict:
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in TOOLS:
        real = shutil.which(name)
        if real:
            (tools / name).symlink_to(real)
    site = {
        "tmp": tmp_path,
        "tools": tools,
        "hosts": tmp_path / "hosts",
        "reports": tmp_path / "reports",
        "ssh_log": tmp_path / "ssh.log",
        "getent_log": tmp_path / "getent.log",
        "sr_state_log": tmp_path / "sr_state.log",
        "set_log": tmp_path / "set.log",
    }
    make_exe(tmp_path / "ssh", FAKE_SSH)
    make_exe(tmp_path / "fake_getent" / "getent", FAKE_GETENT)
    make_exe(tmp_path / "sr_state.sh", FAKE_SR_STATE)
    make_exe(tmp_path / "sr_set.sh", FAKE_SET)
    make_exe(tmp_path / "ar_set.sh", FAKE_SET)
    (tmp_path / "etc_hosts").write_text("".join(f"{a} {n}\n" for n, a in ADDRS.items()))
    (tmp_path / "sr_state.out").write_text(sr_state_text([1] * 16, [0] * 16))
    (tmp_path / "home").mkdir()
    (tmp_path / "crab" / "CRAB").mkdir(parents=True)
    for host in (HEAD, *WORKERS):
        make_host(site, host)
    return site


def env_for(site: dict, **extra: str) -> dict:
    tmp = site["tmp"]
    env = {
        "PATH": f"{site['hosts'] / HEAD / 'bin'}:{tmp / 'fake_getent'}:{site['tools']}",
        "HOME": str(tmp / "home"),
        "NPLB_REPORT_DIR": str(site["reports"]),
        "NPLB_NOW": NOW,
        "SSH": str(tmp / "ssh"),
        "FAKE_BASH": BASH,
        "FAKE_TOOLS": f"{tmp / 'fake_getent'}:{site['tools']}",
        "FAKE_HOSTS": str(site["hosts"]),
        "FAKE_SSH_LOG": str(site["ssh_log"]),
        "FAKE_GETENT_LOG": str(site["getent_log"]),
        "FAKE_ETC_HOSTS": str(tmp / "etc_hosts"),
        "FAKE_SR_STATE_LOG": str(site["sr_state_log"]),
        "FAKE_SR_STATE_OUT": str(tmp / "sr_state.out"),
        "FAKE_SET_LOG": str(site["set_log"]),
        "SR_STATE": str(tmp / "sr_state.sh"),
        "SR_SET": str(tmp / "sr_set.sh"),
        "AR_SET": str(tmp / "ar_set.sh"),
        "NFS_PATH": NFS,
        "CRAB_DIR": str(tmp / "crab" / "CRAB"),
        "HEAD": HEAD,
        "PYTHONPATH": "/bad/pythonpath",
    }
    env.update(extra)
    return env


def run(site: dict, *args: str, script: Path = SCRIPT, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(script), *args],
        env=env_for(site, **env),
        cwd=site["tmp"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


def saved(site: dict, name: str) -> list[str]:
    found = sorted(site["reports"].glob(f"*_{name}*.txt"), key=os.path.getmtime)
    assert found, f"no saved report {name}"
    return found[-1].read_text().splitlines()


def diag(
    site: dict, host: str = "node112", **env: str
) -> tuple[subprocess.CompletedProcess, list[str]]:
    result = run(site, "diag", host, **env)
    return result, saved(site, "node-diag")


def line_with(lines: list[str], prefix: str) -> str:
    found = [ln for ln in lines if ln.startswith(prefix)]
    assert found, f"no line starting with {prefix!r} in:\n" + "\n".join(lines)
    return found[0]


# ---- diag ----


def test_diag_ok(site):
    result, lines = diag(site)
    assert result.returncode == 0, result.stdout + result.stderr
    assert lines[0] == "VERDICT: OK"
    assert len(lines) <= 30
    assert line_with(lines, "OK ports:").startswith("OK ports: 16/16 Up (ibdev2netdev")
    assert "mlx5_6" in line_with(lines, "OK ports:")  # said to be ignored
    assert "16/16" in line_with(lines, "OK cx8-pci:")
    assert "8/8" in line_with(lines, "OK gpus:")
    assert "16 x 400000Mb/s" in line_with(lines, "OK speed:")
    assert line_with(lines, "OK dmesg:")
    assert "mounted" in line_with(lines, "OK nfs:")
    assert line_with(lines, "OK netdevs:")
    assert "SR=1 AR=0 on 16 devices" in line_with(lines, "OK sr:")
    assert "mst" in "\n".join(ln for ln in lines if ln.startswith("OK mst"))
    assert site["sr_state_log"].read_text().split() == ["node112"]
    # narrates on stderr, report on stdout
    err = result.stderr.splitlines()
    assert err and all(re.match(r"\[\d\d:\d\d:\d\d\] ", ln) for ln in err), result.stderr
    assert result.stdout.splitlines()[0].startswith("node-diag |")


def test_diag_head_is_local_and_exports(site):
    result, lines = diag(site, HEAD)
    assert result.returncode == 0, result.stdout + result.stderr
    assert lines[0] == "VERDICT: OK"
    assert not site["ssh_log"].exists()
    assert "head" in line_with(lines, "OK nfs:")
    assert not any(c.startswith("findmnt") for c in calls(site, HEAD))


def test_diag_cables_unplugged_is_hardware(site):
    down = CX8_PORTS[:4]
    make_host(site, "node112", down=down, dmesg=cable_lines(4))
    result, lines = diag(site)
    assert result.returncode == 1, result.stdout + result.stderr
    assert lines[0] == "VERDICT: HARDWARE (cable unplugged on 4 ports)"
    assert "FAIL ports: 12/16 Up" in line_with(lines, "FAIL ports:")
    for ln in cable_lines(4):  # at most 5 matches: all are shown
        assert any(ln in shown for shown in lines), ln
    assert len(lines) <= 30


def test_diag_old_cable_message_with_all_ports_up_is_not_hardware(site):
    make_host(site, "node112", dmesg=cable_lines(2))
    result, lines = diag(site)
    assert result.returncode == 0, result.stdout + result.stderr
    assert lines[0] == "VERDICT: OK"
    assert line_with(lines, "WARN dmesg:")


def test_diag_missing_card_is_hardware(site):
    make_host(site, "node112", cx8=14, listed=CX8_PORTS[:14])
    result, lines = diag(site)
    assert result.returncode == 1, result.stdout + result.stderr
    assert lines[0] == "VERDICT: HARDWARE (2 of 16 CX8 functions missing from lspci)"
    assert "14/16" in line_with(lines, "FAIL cx8-pci:")
    assert "not listed: mlx5_16 mlx5_17" in line_with(lines, "FAIL ports:")


def test_diag_pci_present_but_ports_down_without_cable_message_is_software(site):
    make_host(site, "node112", down=["mlx5_4", "mlx5_5"])
    result, lines = diag(site)
    assert result.returncode == 1, result.stdout + result.stderr
    assert lines[0] == (
        "VERDICT: SOFTWARE (mlx5_4, mlx5_5 not Up, cards in lspci, no cable message: driver or link state)"
    )
    assert lines[1] == "next: check the dmesg lines below; reloading mlx5_core is the lab's call"
    assert "nplb node fix" not in "\n".join(lines[:2])
    # a port the tool does not list at all counts the same
    make_host(site, "node112", listed=[p for p in CX8_PORTS if p != "mlx5_3"])
    _, lines = diag(site)
    assert (
        lines[0]
        == "VERDICT: SOFTWARE (mlx5_3 not Up, cards in lspci, no cable message: driver or link state)"
    )


def test_diag_rdma_answers_when_ibdev2netdev_is_missing(site):
    make_host(site, "node112", missing=("ibdev2netdev",), down=["mlx5_4"])
    result, lines = diag(site)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "15/16 Up (rdma link" in line_with(lines, "FAIL ports:")
    assert lines[0].startswith("VERDICT: SOFTWARE (mlx5_4 not Up")


def test_diag_nfs_mount_missing_is_software(site):
    make_host(site, "node112", mounted=False)
    result, lines = diag(site)
    assert result.returncode == 1, result.stdout + result.stderr
    assert lines[0] == f"VERDICT: SOFTWARE (NFS {NFS} not mounted, run: nplb node fix)"
    assert line_with(lines, "FAIL nfs:")


def test_diag_nfs_path_defaults_to_the_parent_of_the_checkout(site):
    env = {k: v for k, v in env_for(site).items() if k != "NFS_PATH"}
    result = subprocess.run(
        [BASH, str(SCRIPT), "diag", "node112"],
        env=env,
        cwd=site["tmp"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    assert result.returncode == 1
    parent = str(site["tmp"] / "crab")
    assert (
        saved(site, "node-diag")[0]
        == f"VERDICT: SOFTWARE (NFS {parent} not mounted, run: nplb node fix)"
    )


def test_diag_sr_mixed_is_software(site):
    (site["tmp"] / "sr_state.out").write_text(sr_state_text([1] * 8 + [0] * 8, [0] * 16))
    result, lines = diag(site, FAKE_SR_STATE_RC="1")
    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        lines[0] == "VERDICT: SOFTWARE (SR values differ (0 x8, 1 x8), run: nplb node fix --sr 0|1)"
    )
    (site["tmp"] / "sr_state.out").write_text(sr_state_text([1] * 16, [0] * 8 + [1] * 8))
    _, lines = diag(site)
    assert (
        lines[0] == "VERDICT: SOFTWARE (AR values differ (0 x8, 1 x8), run: nplb node fix --ar 0|1)"
    )


def test_diag_gpu_count_and_nvidia_smi_failure(site):
    make_host(site, "node112", gpus=7)
    result, lines = diag(site)
    assert result.returncode == 1
    assert lines[0] == "VERDICT: HARDWARE (7 of 8 GPUs listed by nvidia-smi -L)"
    make_host(site, "node112")
    (site["hosts"] / "node112" / "data" / "nvidia-smi.rc").write_text("9\n")
    result, lines = diag(site)
    assert result.returncode == 1
    assert lines[0].startswith("VERDICT: SOFTWARE (nvidia-smi -L fails")


def test_diag_tools_missing_never_crash(site):
    make_host(site, "node112", missing=("rdma", "mst", "ethtool"))
    result, lines = diag(site)
    assert result.returncode == 0, result.stdout + result.stderr
    assert lines[0] == "VERDICT: OK"
    assert "WARN rdma: not installed" in lines
    assert "WARN mst: not installed" in lines
    assert "WARN ethtool: not installed (link speed not checked)" in lines
    assert "Traceback" not in result.stderr + result.stdout and "unbound" not in result.stderr
    # every tool missing: still a verdict, never a crash
    make_host(site, "node112", missing=tuple(REMOTE_TOOLS))
    result, lines = diag(site)
    assert result.returncode == 0 or result.returncode == 1
    assert lines[0].startswith("VERDICT: ")
    assert "unbound" not in result.stderr and "Traceback" not in result.stderr
    assert any(ln.startswith("WARN") for ln in lines)


def test_diag_every_ibdev_tool_missing_with_ports_unknown(site):
    make_host(site, "node112", missing=("ibdev2netdev", "rdma"))
    result, lines = diag(site)
    assert result.returncode == 0
    assert lines[0] == "VERDICT: OK"
    assert "WARN ports: neither ibdev2netdev nor rdma link answered" in lines


def test_diag_unreachable_is_unknown(site):
    result = run(site, "diag", "node999")
    lines = saved(site, "node-diag")
    assert result.returncode == 1, result.stdout + result.stderr
    assert lines[0] == "VERDICT: UNKNOWN (cannot reach node999)"
    assert "unbound" not in result.stderr


def test_diag_lspci_without_a_card_line_is_not_a_hardware_verdict(site):
    (site["hosts"] / "node112" / "data" / "lspci.out").write_text(
        "00:00.0 Host bridge: Some Vendor\n"
    )
    result, lines = diag(site)
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(ln.startswith("WARN cx8-pci:") for ln in lines)
    make_host(site, "node112", down=["mlx5_4"])
    (site["hosts"] / "node112" / "data" / "lspci.out").write_text(
        "00:00.0 Host bridge: Some Vendor\n"
    )
    result, lines = diag(site)
    assert result.returncode == 1
    assert lines[0] == "VERDICT: UNKNOWN (mlx5_4 not Up and lspci gave no CX8 count)"


def test_diag_is_read_only_and_remote_commands_are_bounded(site):
    make_host(site, "node112", down=["mlx5_4"], dmesg=cable_lines(2))
    diag(site)
    diag(site, HEAD)
    sent = site["ssh_log"].read_text().splitlines()
    assert sent
    for entry in sent:
        host, cmd = entry.split(" ", 1)
        assert host == "node112"
        assert cmd.startswith("timeout 20 "), entry
    for host in (HEAD, *WORKERS):
        for call in calls(site, host):
            tool = call.split()[0]
            assert tool not in {"mount", "umount", "exportfs", "mlxreg"}, f"{host}: {call}"
    assert not (site["set_log"].parent / "set.log.sr").exists()


def test_diag_fits_pages(site):
    make_host(site, "node112", down=CX8_PORTS[:4], dmesg=cable_lines(150), dmesg_noise=200)
    result, lines = diag(site)
    out = result.stdout.splitlines()
    assert 0 < len(out) <= 38
    assert max(len(ln) for ln in out) <= 100
    assert len(lines) <= 30
    assert lines[0] == "VERDICT: HARDWARE (cable unplugged on 4 ports)" or lines[0].startswith(
        "VERDICT: HARDWARE"
    )


def test_diag_usage(site):
    for args in (("diag",), ("diag", "a", "b")):
        bad = run(site, *args)
        assert bad.returncode == 2
        assert "usage: node_tools.sh" in bad.stderr and "diag HOST" in bad.stderr
    assert not site["ssh_log"].exists()


# ---- fix ----


def fix(site: dict, *args: str, **env: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    env.setdefault("NPLB_YES", "1")
    result = run(site, "fix", *args, **env)
    return result, (
        saved(site, "node-fix") if result.returncode in (0, 1) and result.stdout else []
    )


def test_fix_asks_first_and_declines_by_default(site):
    result = run(site, "fix", HEAD, *WORKERS, "--sr", "1")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "declined: nothing changed" in result.stderr
    assert "Proceed? [y/N]" in result.stderr
    assert f"mount -t nfs {ADDRS[HEAD]}:{NFS} {NFS}" in result.stderr  # the plan names the command
    for host in (HEAD, *WORKERS):
        assert not [c for c in calls(site, host) if c.split()[0] in {"mount", "umount", "exportfs"}]
    assert not site["ssh_log"].exists()
    assert not list(site["tmp"].glob("set.log.*"))
    assert not site["reports"].exists() or not list(site["reports"].glob("*node-fix*"))


def test_fix_remounts_workers_and_reads_back(site):
    make_host(site, "node112", mounted=True)
    make_host(site, "node114", mounted=False)
    result, lines = fix(site, HEAD, *WORKERS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert lines[0] == "VERDICT: OK fix"
    mount_cmd = f"mount -t nfs {ADDRS[HEAD]}:{NFS} {NFS}"
    assert mount_cmd in calls(site, "node112")
    assert mount_cmd in calls(site, "node114")
    assert [c for c in calls(site, "node112") if c.startswith("umount")] == [f"umount {NFS}"]
    assert not [c for c in calls(site, "node114") if c.startswith("umount")]
    assert any(ln.startswith("OK node112 mounted") for ln in lines)
    assert any(ln.startswith("OK node114 mounted") for ln in lines)
    assert (site["hosts"] / "node114" / "mounted").read_text().strip() == f"{ADDRS[HEAD]}:{NFS}"
    for entry in site["ssh_log"].read_text().splitlines():
        assert entry.split(" ", 1)[1].startswith("timeout 20 "), entry
    assert "hosts node110" in site["getent_log"].read_text()
    assert "Proceed? [y/N]" in result.stderr


def test_fix_reports_a_failed_mount(site):
    make_host(site, "node112", mounted=False)
    (site["hosts"] / "node112" / "mount_fails").write_text("")
    result, lines = fix(site, HEAD, "node112", "node114")
    assert result.returncode == 1, result.stdout + result.stderr
    assert lines[0].startswith("VERDICT: FAIL fix")
    assert "node112" in lines[0]
    assert "access denied" in line_with(lines, "FAIL node112 mount")
    assert any(ln.startswith("OK node114 mounted") for ln in lines)


def test_fix_checks_the_export_on_the_head_and_never_edits_exports(site):
    make_host(site, HEAD)
    (site["hosts"] / HEAD / "data" / "exportfs.out").write_text("/srv/other\t\t10.9.9.9(rw)\n")
    result, lines = fix(site, HEAD, *WORKERS)
    assert result.returncode == 1, result.stdout + result.stderr
    expected = (
        f"FAIL head export: {NFS} is not exported; add this line to /etc/exports (lab-owned, not edited "
        f"here): {NFS} {ADDRS['node112']}{EXPORT_OPTS} {ADDRS['node114']}{EXPORT_OPTS} then run: exportfs -ra"
    )
    assert expected in lines
    assert lines[0] == "VERDICT: FAIL fix (head export missing, workers not mounted)"
    for host in WORKERS:
        assert not calls(site, host)
    assert calls(site, HEAD) == ["exportfs -v"]
    # the script has no write to /etc/exports
    text = SCRIPT.read_text()
    assert not re.search(r"(>|tee\s|sed -i).*etc/exports", text)
    # when the export is present nothing but `exportfs -v` runs on the head
    make_host(site, HEAD)
    result, lines = fix(site, HEAD, *WORKERS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls(site, HEAD) == ["exportfs -v", "exportfs -v"]  # second run of this test, appended
    assert any(ln.startswith("OK head export:") for ln in lines)


def test_fix_reapplies_sr_and_ar_with_flags(site):
    result, lines = fix(site, "--sr", "1", "--ar", "0", HEAD, *WORKERS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (site["tmp"] / "set.log.sr").read_text().split() == ["1", HEAD, *WORKERS]
    assert (site["tmp"] / "set.log.ar").read_text().split() == ["0", HEAD, *WORKERS]
    assert "OK sr: SR=1 on all 16 devices of 3 hosts" in lines
    assert "OK ar: AR=0 on all 16 devices of 3 hosts" in lines
    assert lines[0] == "VERDICT: OK fix"
    # a failing set script: FAIL verdict, exit 1
    (site["tmp"] / "set.log.ar.fail").write_text("")
    result, lines = fix(site, "--ar", "1", HEAD, *WORKERS)
    assert result.returncode == 1, result.stdout + result.stderr
    assert lines[0].startswith("VERDICT: FAIL fix")
    assert "ar" in lines[0]
    assert "FAIL ar: AR=1 not set everywhere" in "\n".join(lines)


def test_fix_without_flags_does_not_call_the_set_scripts(site):
    result, _ = fix(site, HEAD, *WORKERS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(site["tmp"].glob("set.log.*"))


def test_fix_default_hosts_come_from_sinfo(site):
    sinfo = site["tmp"] / "fake_getent" / "sinfo"
    make_exe(
        sinfo,
        '#!/usr/bin/env bash\necho "$*" >"$FAKE_SET_LOG.sinfo"\nprintf "node114\\nnode110\\nnode112\\nnode110\\n"\n',
    )
    result, lines = fix(site, "--sr", "1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "-p h100_4nodes" in (site["tmp"] / "set.log.sinfo").read_text()
    assert (site["tmp"] / "set.log.sr").read_text().split() == [
        "1",
        "node110",
        "node112",
        "node114",
    ]
    nothing = site["tmp"] / "fake_getent" / "sinfo"
    make_exe(nothing, "#!/usr/bin/env bash\nexit 0\n")
    bad = run(site, "fix")
    assert bad.returncode == 2
    assert "no HOST given" in bad.stderr


def test_fix_usage(site):
    for args in (
        ("--sr", "2"),
        ("--ar", "x"),
        ("--sr",),
        ("--frob",),
        ("--sr", "1", "--bogus", HEAD),
    ):
        bad = run(site, "fix", *args, NPLB_YES="1")
        assert bad.returncode == 2, args
        assert "usage: node_tools.sh" in bad.stderr
        assert "fix [--sr 0|1] [--ar 0|1] [HOST...]" in bad.stderr
    assert not site["ssh_log"].exists()
    assert not list(site["tmp"].glob("set.log.*"))
    for host in (HEAD, *WORKERS):
        assert not calls(site, host)


# ---- the dispatcher ----


@pytest.fixture
def checkout(site) -> Path:
    chk = site["tmp"] / "chk"
    nplb_dir = chk / "examples" / "nplb"
    (nplb_dir / "lib").mkdir(parents=True)
    for rel in ("nplb", "node_tools.sh", "pages.py", "lib/pages.sh"):
        shutil.copy(NPLB / rel, nplb_dir / rel)
    return chk


def test_dispatcher_diag_and_fix(site, checkout):
    nplb = checkout / "examples" / "nplb" / "nplb"
    result = run(site, "node", "diag", "node112", script=nplb)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[0].startswith("node-diag |")
    assert saved(site, "node-diag")[0] == "VERDICT: OK"

    declined = run(site, "node", "fix", HEAD, "node112", script=nplb)
    assert declined.returncode == 1
    assert "declined: nothing changed" in declined.stderr

    done = run(site, "node", "fix", "--sr", "1", HEAD, "node112", script=nplb, NPLB_YES="1")
    assert done.returncode == 0, done.stdout + done.stderr
    assert saved(site, "node-fix")[0] == "VERDICT: OK fix"
    assert (site["tmp"] / "set.log.sr").read_text().split() == ["1", HEAD, "node112"]

    for args in ((), ("frob",), ("diag",)):
        bad = run(site, "node", *args, script=nplb)
        assert bad.returncode == 2, args
        assert (
            "usage: nplb node check [HOST...] | diag HOST | fix [--sr 0|1] [--ar 0|1] [HOST...]"
            in bad.stderr
        )

    helped = run(site, "help", script=nplb)
    assert helped.returncode == 0
    lines = helped.stdout.splitlines()
    assert any(ln.startswith("  node check") for ln in lines)
    assert any(ln.startswith("  node diag HOST") for ln in lines)
    assert any(ln.startswith("  node fix") for ln in lines)
    assert 0 < len(lines) <= 38
    assert max(len(ln) for ln in lines) <= 100
