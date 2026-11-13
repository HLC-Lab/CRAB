"""examples/nplb/net_isolate.sh (`nplb net isolate`): control pair versus test pair, with a fake ssh."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"
BASH = shutil.which("bash") or "/bin/bash"

# fake ssh: HOST COMMAND. nodeA, nodeC (control) and nodeT (test) have IPv4 10.0.0.1, .2 and .3. Every
# call is logged to $FAKE_LOG. A pingpong or ib_send_bw command whose last word is an IPv4 address is a
# client. With FAKE_FAIL_NODE=<host>, the server on that host and every client aimed at its address
# exit 124 (timeout). FAKE_MISSING=<host>:<tool> reports that tool missing on that host.
FAKE_SSH = r"""#!/bin/bash
host=$1
shift
cmd="$*"
printf '%s %s\n' "$host" "$cmd" >> "$FAKE_LOG"
case $host in
nodeA) n=1 ;;
nodeC) n=2 ;;
nodeT) n=3 ;;
*) exit 255 ;;
esac
if [[ $cmd == *gid_attrs* ]]; then
    printf '0\tRoCE v1\tfe80:0000:0000:0000:0000:0000:0000:000%s\tib0\n' "$n"
    printf '1\tRoCE v2\tfe80:0000:0000:0000:0000:0000:0000:000%s\tib0\n' "$n"
    printf '2\tRoCE v1\t0000:0000:0000:0000:0000:ffff:0a00:000%s\tib0\n' "$n"
    printf '3\tRoCE v2\t0000:0000:0000:0000:0000:ffff:0a00:000%s\tib0\n' "$n"
    exit 0
fi
if [[ $cmd == *"command -v"* ]]; then
    if [[ ${FAKE_MISSING:-} == "$host:"* ]]; then
        echo "${FAKE_MISSING#*:} "
    else
        echo
    fi
    exit 0
fi
if [[ $cmd == "ip -o route get"* ]]; then
    echo "${cmd#ip -o route get }  dev ib0   src 10.0.0.$n  uid 1000"
    echo "    cache"
    exit 0
fi
last=${cmd##* }
if [[ $last =~ ^10\.0\.0\.([0-9])$ ]]; then
    echo "client sees a peer"
    [[ ${FAKE_FAIL_NODE:-} == nodeT && $last == 10.0.0.3 ]] && exit 124
    [[ ${FAKE_FAIL_NODE:-} == nodeC && $last == 10.0.0.2 ]] && exit 124
    exit 0
fi
echo "server waits"
[[ ${FAKE_FAIL_NODE:-} == "$host" ]] && exit 124
echo "server done"
exit 0
"""


def run_isolate(tmp_path: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    ssh = tmp_path / "fake_ssh"
    ssh.write_text(FAKE_SSH, encoding="utf-8")
    ssh.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "NPLB_WORK")}
    env.update(
        SSH=str(ssh),
        FAKE_LOG=str(tmp_path / "ssh.log"),
        ISO_SETTLE="0",
        NPLB_REPORT_DIR=str(tmp_path / "reports"),
    )
    env.update(extra)
    return subprocess.run(
        [BASH, str(NPLB / "net_isolate.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def ssh_log(tmp_path: Path) -> list[str]:
    return (tmp_path / "ssh.log").read_text(encoding="utf-8").splitlines()


def test_control_ok_and_test_failing_is_a_fail_only_verdict(tmp_path: Path) -> None:
    result = run_isolate(tmp_path, "nodeA", "nodeC", "nodeT", FAKE_FAIL_NODE="nodeT")
    assert result.returncode == 1, result.stdout + result.stderr
    first = result.stdout.splitlines()[1]  # line 0 is the pager header
    assert (
        first
        == "VERDICT: FAIL only on nodeA-nodeT: rc ud256 ud1024 udbw (control nodeA-nodeC passes)"
    )
    assert "OK nodeA-nodeC mlx5_0 rc srv=0 cli=0" in result.stdout
    assert "FAIL nodeA-nodeT mlx5_0 udbw srv=124 cli=124" in result.stdout
    assert "TOOLS nodeA: ok" in result.stdout
    assert "GID nodeT mlx5_0 3 RoCE v2 0000:0000:0000:0000:0000:ffff:0a00:0003 ib0" in result.stdout
    assert "ROUTE nodeA->nodeT: 10.0.0.3 from 10.0.0.1" in result.stdout
    # the RoCE v2 IPv4 index (3) is used on both sides, and the client connects to the IPv4 address
    tests = [line for line in ssh_log(tmp_path) if " timeout 10 " in line]
    assert len(tests) == 16
    for line in tests:
        assert re.search(r"-[gx] 3 ", line), line
        assert "-g 1 " not in line and "-g 2 " not in line
    clients_to_test = [
        line for line in tests if line.startswith("nodeA ") and line.endswith(" 10.0.0.3")
    ]
    assert len(clients_to_test) == 4
    assert any(
        "timeout 10 ibv_rc_pingpong -d mlx5_0 -i 1 -g 3 -p 18600 -n 100" in line for line in tests
    )
    assert any(
        "ib_send_bw -c UD -d mlx5_0 -i 1 -x 3 -n 1000 -s 1024 -p 18607 10.0.0.3" in line
        for line in tests
    )


def test_all_pass_is_an_ok_verdict(tmp_path: Path) -> None:
    result = run_isolate(tmp_path, "nodeA", "nodeC", "nodeT")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VERDICT: OK verbs pass on both pairs: problem is above verbs (UCX)" in result.stdout
    assert result.stdout.count("\nOK nodeA-") == 8


def test_a_failing_control_pair_invalidates_the_verdict(tmp_path: Path) -> None:
    result = run_isolate(tmp_path, "nodeA", "nodeC", "nodeT", FAKE_FAIL_NODE="nodeC")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "VERDICT: INVALID control pair nodeA-nodeC failed: rc ud256 ud1024 udbw" in result.stdout


def test_a_missing_tool_skips_only_the_tests_that_need_it(tmp_path: Path) -> None:
    result = run_isolate(
        tmp_path, "nodeA", "nodeC", "nodeT", "mlx5_2", FAKE_MISSING="nodeT:ibv_ud_pingpong"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TOOLS nodeT: missing ibv_ud_pingpong" in result.stdout
    assert "SKIP nodeA-nodeT mlx5_2 ud256: ibv_ud_pingpong missing on nodeT" in result.stdout
    assert "OK nodeA-nodeT mlx5_2 rc srv=0 cli=0" in result.stdout


def test_wrong_number_of_hosts_is_a_usage_error(tmp_path: Path) -> None:
    result = run_isolate(tmp_path, "nodeA", "nodeC")
    assert result.returncode == 2
    assert "usage: nplb net isolate" in result.stderr
