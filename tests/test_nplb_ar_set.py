"""examples/nplb/ar_set.sh, run with bash against a fake ssh, a fake mlxreg and a fake sysfs per
host in tmp_path. Nothing here calls a real ssh or mlxreg."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "nplb" / "ar_set.sh"

# Two cards, two PCI functions each: (mlx5 name, pci address).
DEVICES = [
    ("mlx5_0", "0000:19:00.0"),
    ("mlx5_1", "0000:19:00.1"),
    ("mlx5_2", "0000:3b:00.0"),
    ("mlx5_3", "0000:3b:00.1"),
]

# Runs the remote command locally, as the named host: its own fake mlxreg on PATH and its own sysfs.
FAKE_SSH = r"""#!/usr/bin/env bash
while [[ ${1:-} == -o ]]; do shift 2; done
host=$1; shift
echo "$host" >>"$FAKE_LOG"
if [[ ! -d $FAKE_HOSTS/$host ]]; then
    echo "ssh: Could not resolve hostname $host" >&2
    exit 255
fi
SR_SYSFS="$FAKE_HOSTS/$host/sysfs" PATH="$FAKE_HOSTS/$host/bin:$PATH" exec "$@"
"""

# -get prints the register table of the device in <host dir>/regs/<pci>. -set <field>=<v> -y rewrites
# the value of that row only (so a later -get returns it) unless <host dir>/ignoreset/<pci> exists
# (succeeds, changes nothing). Every call is appended to <host dir>/mlxreg.log.
FAKE_MLXREG = r"""#!/usr/bin/env bash
hd="$(dirname "$0")/.."
echo "$*" >>"$hd/mlxreg.log"
[[ $1 == -d && $3 == --reg_name && $4 == ROCE_ACCL ]] || { echo "bad args: $*" >&2; exit 2; }
f="$hd/regs/$2"
if [[ ! -f $f ]]; then
    echo "-E- Failed to open device: $2"
    exit 1
fi
case $5 in
    -get) cat "$f" ;;
    -set)
        [[ $7 == -y ]] || { echo "bad args: $*" >&2; exit 2; }
        field=${6%%=*}
        val=${6#*=}
        if ! grep -q "^$field " "$f"; then
            echo "-E- Unknown field $field"
            exit 1
        fi
        if [[ ! -e $hd/ignoreset/$2 ]]; then
            sed -i "s/^\($field *| \).*/\10x$(printf '%08x' "$val")/" "$f"
        fi
        ;;
    *) echo "bad args: $*" >&2; exit 2 ;;
esac
"""


def table(ar: int, sr: int = 0, ar_name: str = "adaptive_routing_forced_en") -> str:
    return (
        "Field Name                        | Data\n"
        "=============================================\n"
        f"{ar_name:<34}| 0x{ar:08x}\n"
        f"selective_repeat_forced_en        | 0x{sr:08x}\n"
    )


def add_device(host_dir: Path, name: str, pci: str, state: str, regs: str | None) -> None:
    ib = host_dir / "sysfs" / "class" / "infiniband" / name
    (ib / "ports" / "1").mkdir(parents=True)
    (ib / "ports" / "1" / "state").write_text(state)
    target = host_dir / "sysfs" / "devices" / "pci0000:00" / pci
    target.mkdir(parents=True, exist_ok=True)
    (ib / "device").symlink_to(target)
    if regs is not None:
        (host_dir / "regs").mkdir(exist_ok=True)
        (host_dir / "regs" / pci).write_text(regs)


@pytest.fixture
def world(tmp_path: Path) -> dict:
    """Hosts n1 and n2, each with four active devices that all report AR 0 and SR 1."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "ssh").write_text(FAKE_SSH)
    (bindir / "ssh").chmod(0o755)
    hosts = tmp_path / "hosts"
    for host in ("n1", "n2"):
        (hosts / host / "bin").mkdir(parents=True)
        (hosts / host / "bin" / "fake-mlxreg").write_text(FAKE_MLXREG)
        (hosts / host / "bin" / "fake-mlxreg").chmod(0o755)
        for name, pci in DEVICES:
            add_device(hosts / host, name, pci, "4: ACTIVE\n", table(0, sr=1))
    return {"bin": bindir, "hosts": hosts, "log": tmp_path / "log"}


def run_set(world: dict, args: list) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{world['bin']}:{os.environ['PATH']}",
        "SR_SSH": str(world["bin"] / "ssh"),
        "MLXREG": "fake-mlxreg",
        "FAKE_LOG": str(world["log"]),
        "FAKE_HOSTS": str(world["hosts"]),
    }
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def mlxreg_log(world: dict, host: str) -> list[str]:
    log = world["hosts"] / host / "mlxreg.log"
    return log.read_text().splitlines() if log.exists() else []


def sets(world: dict, host: str) -> list[str]:
    return [ln for ln in mlxreg_log(world, host) if " -set " in ln]


def device_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if "AR=" in ln and not ln.startswith("AR=")]


def test_ar_set_writes_the_adaptive_routing_field_only(world):
    (world["hosts"] / "n1" / "regs" / "0000:3b:00.0").write_text(
        table(0, sr=1, ar_name="adaptive_routing_force_en")
    )
    result = run_set(world, ["1", "n1"])
    assert result.returncode == 0, result.stderr
    got = sets(world, "n1")
    assert len(got) == 4
    assert not any("selective_repeat" in s for s in got)
    assert sum("-set adaptive_routing_forced_en=1 -y" in s for s in got) == 3
    assert any("-d 0000:3b:00.0 " in s and "-set adaptive_routing_force_en=1 -y" in s for s in got)
    for _, pci in DEVICES:
        regs = (world["hosts"] / "n1" / "regs" / pci).read_text()
        assert re.search(r"^selective_repeat_forced_en\s*\| 0x00000001$", regs, re.M)
        assert re.search(r"^adaptive_routing_forced?_en\s*\| 0x00000001$", regs, re.M)
    lines = device_lines(result.stdout)
    assert len(lines) == 4
    for name, pci in DEVICES:
        assert any(
            re.search(rf"\bn1\b.*\b{name}\b.*\b{re.escape(pci)}\b.*AR=1\b.*\bOK$", ln)
            for ln in lines
        ), name
    assert "SR=" not in result.stdout
    assert result.stdout.splitlines()[-1] == "AR=1 on all 4 devices of 1 hosts"


def test_ar_set_zero(world):
    for _, pci in DEVICES:
        (world["hosts"] / "n1" / "regs" / pci).write_text(table(1, sr=1))
    result = run_set(world, ["0", "n1"])
    assert result.returncode == 0, result.stderr
    assert len(device_lines(result.stdout)) == 4
    assert all("-set adaptive_routing_forced_en=0 -y" in s for s in sets(world, "n1"))
    assert result.stdout.splitlines()[-1] == "AR=0 on all 4 devices of 1 hosts"


def test_ar_set_reports_a_failed_readback(world):
    (world["hosts"] / "n2" / "ignoreset").mkdir()
    (world["hosts"] / "n2" / "ignoreset" / "0000:3b:00.0").write_text("")
    result = run_set(world, ["1", "n1", "n2"])
    assert result.returncode == 1
    bad = [ln for ln in device_lines(result.stdout) if "n2" in ln and "mlx5_2" in ln]
    assert len(bad) == 1 and "AR=0 FAIL (wanted 1)" in bad[0]
    last = result.stdout.splitlines()[-1]
    assert last.startswith("AR=1 not set everywhere: 7 devices OK; failed: ")
    assert "n2/mlx5_2" in last


def test_ar_set_without_the_field_is_an_error_line(world):
    (world["hosts"] / "n1" / "regs" / "0000:19:00.1").write_text(
        "Field Name | Data\n=====\nselective_repeat_forced_en | 0x00000000\n"
    )
    result = run_set(world, ["1", "n1", "n2"])
    assert result.returncode == 1
    bad = [ln for ln in device_lines(result.stdout) if "mlx5_1" in ln and "n1" in ln]
    assert len(bad) == 1
    assert "AR=? FAIL no adaptive_routing_forced_en in the ROCE_ACCL output" in bad[0]
    assert not any("0000:19:00.1" in s for s in sets(world, "n1"))
    assert len(sets(world, "n1")) == 3 and len(sets(world, "n2")) == 4
    summary = result.stdout.splitlines()[-1]
    assert "n1/mlx5_1" in summary and "n2" not in summary


@pytest.mark.parametrize("args", [["2", "n1"], ["", "n1"], ["x", "n1"], ["1"], []])
def test_ar_set_usage(world, args):
    result = run_set(world, args)
    assert result.returncode == 2
    assert "ar_set.sh VALUE HOST..." in result.stderr
    assert "sr_set.sh VALUE HOST..." not in result.stderr
    # the env names SR_SSH and SR_SYSFS are shared with sr_set.sh and do appear in the header
    assert "SR=" not in result.stderr
    assert "Selective" not in result.stderr
    assert "AR" in result.stderr
    assert not world["log"].exists()


def test_ar_set_skips_devices_without_an_active_port(world):
    add_device(world["hosts"] / "n1", "mlx5_4", "0000:5e:00.0", "1: DOWN\n", table(0))
    result = run_set(world, ["1", "n1", "n2"])
    assert result.returncode == 0, result.stderr
    skipped = [ln for ln in result.stdout.splitlines() if "skipped (no active port)" in ln]
    assert len(skipped) == 1
    assert "n1" in skipped[0] and "mlx5_4" in skipped[0]
    assert not any("0000:5e:00.0" in s for s in mlxreg_log(world, "n1"))
    assert result.stdout.splitlines()[-1] == "AR=1 on all 8 devices of 2 hosts"


def test_ar_set_unknown_host_fails_cleanly(world):
    result = run_set(world, ["1", "n1", "gone"])
    assert result.returncode == 1
    fails = [ln for ln in result.stdout.splitlines() if "FAIL" in ln]
    assert len(fails) == 1 and "gone" in fails[0] and "Could not resolve" in fails[0]
    assert len(sets(world, "n1")) == 4
    assert "gone" in result.stdout.splitlines()[-1]
