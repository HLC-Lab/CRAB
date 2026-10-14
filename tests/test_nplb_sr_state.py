"""examples/nplb/sr_state.sh, run with bash against a fake ssh, a fake mlxreg and a fake sysfs per
host in tmp_path. Nothing here calls a real ssh, sinfo or mlxreg."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "examples" / "nplb" / "sr_state.sh"

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

# Prints the register table of the device in <host dir>/regs/<pci>, fails for an unknown device.
FAKE_MLXREG = r"""#!/usr/bin/env bash
[[ $1 == -d && $3 == --reg_name && $4 == ROCE_ACCL && $5 == -get ]] || { echo "bad args: $*" >&2; exit 2; }
f="$(dirname "$0")/../regs/$2"
if [[ ! -f $f ]]; then
    echo "-E- Failed to open device: $2"
    echo "-E- second line"
    exit 1
fi
cat "$f"
"""

FAKE_SINFO = r"""#!/usr/bin/env bash
[[ "$*" == "-h -N -p p -o %N" ]] || { echo "unexpected sinfo args: $*" >&2; exit 1; }
printf 'n2\nn1\nn1\n'
"""


def table(sr: int, ar: int = 0, sr_name: str = "selective_repeat_forced_en") -> str:
    return (
        "Field Name                        | Data\n"
        "=============================================\n"
        f"adaptive_routing_forced_en        | 0x{ar:08x}\n"
        f"{sr_name:<34}| 0x{sr:08x}\n"
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
    """Hosts n1 and n2, each with four active devices that all report SR 1."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, text in (("ssh", FAKE_SSH), ("sinfo", FAKE_SINFO)):
        (bindir / name).write_text(text)
        (bindir / name).chmod(0o755)
    hosts = tmp_path / "hosts"
    for host in ("n1", "n2"):
        (hosts / host / "bin").mkdir(parents=True)
        (hosts / host / "bin" / "fake-mlxreg").write_text(FAKE_MLXREG)
        (hosts / host / "bin" / "fake-mlxreg").chmod(0o755)
        for name, pci in DEVICES:
            add_device(hosts / host, name, pci, "4: ACTIVE\n", table(1))
    return {"bin": bindir, "hosts": hosts, "log": tmp_path / "log"}


def run_sr(world: dict, args: list | None = None, **env) -> subprocess.CompletedProcess:
    full_env = {
        **os.environ,
        "PATH": f"{world['bin']}:{os.environ['PATH']}",
        "SR_SSH": str(world["bin"] / "ssh"),
        "MLXREG": "fake-mlxreg",
        "FAKE_LOG": str(world["log"]),
        "FAKE_HOSTS": str(world["hosts"]),
    }
    full_env.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT), *(["n1", "n2"] if args is None else args)],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def device_lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if "SR=" in ln]


def test_all_devices_sr_on_prints_one_line_each_and_exit_0(world):
    result = run_sr(world, ["n1", "n2"])
    assert result.returncode == 0, result.stderr
    lines = device_lines(result.stdout)
    assert len(lines) == 8
    for host in ("n1", "n2"):
        for name, pci in DEVICES:
            assert any(
                re.search(rf"\b{host}\b.*\b{name}\b.*\b{re.escape(pci)}\b.*SR=1\b.*AR=0\b", ln)
                for ln in lines
            ), (host, name)
    assert result.stdout.splitlines()[-1] == "SR 1 on all 8 devices of 2 hosts"


def test_one_device_with_sr_off_is_mixed_and_exit_1(world):
    (world["hosts"] / "n2" / "regs" / "0000:3b:00.1").write_text(table(0, ar=1))
    result = run_sr(world, ["n1", "n2"])
    assert result.returncode == 1
    lines = device_lines(result.stdout)
    assert any("n2" in ln and "mlx5_3" in ln and "SR=0" in ln and "AR=1" in ln for ln in lines)
    summary = result.stdout.splitlines()[-1]
    assert "mixed" in summary
    assert "n2/mlx5_3" in summary
    assert "n1/mlx5_0" not in summary


def test_device_with_every_port_down_is_skipped_and_not_counted(world):
    add_device(world["hosts"] / "n1", "mlx5_4", "0000:5e:00.0", "1: DOWN\n", None)
    result = run_sr(world, ["n1", "n2"])
    assert result.returncode == 0, result.stderr
    skipped = [ln for ln in result.stdout.splitlines() if "skipped (no active port)" in ln]
    assert len(skipped) == 1
    assert "n1" in skipped[0] and "mlx5_4" in skipped[0]
    assert len(device_lines(result.stdout)) == 8
    assert result.stdout.splitlines()[-1] == "SR 1 on all 8 devices of 2 hosts"


def test_missing_mlxreg_fails_that_host_and_the_others_are_still_read(world):
    (world["hosts"] / "n1" / "bin" / "fake-mlxreg").unlink()
    result = run_sr(world, ["n1", "n2"])
    assert result.returncode == 1
    fails = [ln for ln in result.stdout.splitlines() if "FAIL" in ln]
    assert len(fails) == 1 and "n1" in fails[0] and "fake-mlxreg" in fails[0]
    assert len([ln for ln in device_lines(result.stdout) if "n2" in ln and "SR=1" in ln]) == 4
    assert world["log"].read_text().split() == ["n1", "n2"]


def test_unreachable_host_is_a_fail_line(world):
    result = run_sr(world, ["n1", "gone"])
    assert result.returncode == 1
    fails = [ln for ln in result.stdout.splitlines() if "FAIL" in ln]
    assert len(fails) == 1 and "gone" in fails[0] and "Could not resolve" in fails[0]
    assert len([ln for ln in device_lines(result.stdout) if "n1" in ln]) == 4


def test_mlxreg_failing_for_one_device_shows_unknown_with_its_first_error_line(world):
    (world["hosts"] / "n1" / "regs" / "0000:19:00.1").unlink()
    result = run_sr(world, ["n1", "n2"])
    assert result.returncode == 1
    bad = [ln for ln in device_lines(result.stdout) if "n1" in ln and "mlx5_1" in ln]
    assert len(bad) == 1 and "SR=?" in bad[0]
    assert "Failed to open device: 0000:19:00.1" in bad[0]
    assert "second line" not in result.stdout
    assert len([ln for ln in device_lines(result.stdout) if "SR=1" in ln]) == 7
    assert "n1/mlx5_1" in result.stdout.splitlines()[-1]


def test_set_command_spelling_is_parsed_too(world):
    for host in ("n1", "n2"):
        for _, pci in DEVICES:
            (world["hosts"] / host / "regs" / pci).write_text(
                table(1, sr_name="selective_repeat_force_en")
            )
    result = run_sr(world, ["n1", "n2"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "SR 1 on all 8 devices of 2 hosts"


def test_script_has_no_set_path():
    assert not re.search(r"(^|\s)-set\b", SCRIPT.read_text())


def test_no_hosts_and_no_partition_exit_2_with_usage(world):
    result = run_sr(world, [], PARTITION="")
    assert result.returncode == 2
    assert "sr_state.sh [HOST...]" in result.stderr
    assert not world["log"].exists()


def test_partition_hosts_come_from_sinfo(world):
    result = run_sr(world, [], PARTITION="p")
    assert result.returncode == 0, result.stderr
    assert world["log"].read_text().split() == ["n1", "n2"]
    assert result.stdout.splitlines()[-1] == "SR 1 on all 8 devices of 2 hosts"
