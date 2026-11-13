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
if [[ $cmd == *"python3 -c"* ]]; then
    # plain UDP: the sender always exits 0; the receiver times out (1) on the failing node and
    # cannot bind (2) on FAKE_BIND_FAIL
    if [[ $cmd == *sendto* ]]; then
        echo "sent 20 datagrams"
        exit 0
    fi
    if [[ ${FAKE_BIND_FAIL:-} == "$host" ]]; then
        echo "OSError: [Errno 98] Address already in use"
        exit 2
    fi
    if [[ ${FAKE_FAIL_NODE:-} == "$host" ]]; then
        echo "TimeoutError: timed out"
        exit 1
    fi
    echo "got 512 bytes from 10.0.0.1"
    exit 0
fi
if [[ $cmd == *ib_write_bw* ]]; then
    # perftest prints both addresses, GID included, before the results
    echo " local address: LID 0x01 GID: 00:00:00:00:00:00:00:00:00:00:255:255:10:00:00:0$n"
    echo " remote address: LID 0x01 GID: 00:00:00:00:00:00:00:00:00:00:255:255:10:00:00:0$n"
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
        PAGE_LINES="500",
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


VERBS = "rc ud256 ud1024 udbw"


def ssh_log(tmp_path: Path) -> list[str]:
    return (tmp_path / "ssh.log").read_text(encoding="utf-8").splitlines()


def test_control_ok_and_test_failing_is_a_fail_only_verdict(tmp_path: Path) -> None:
    result = run_isolate(
        tmp_path, "nodeA", "nodeC", "nodeT", FAKE_FAIL_NODE="nodeT", ISO_TESTS=VERBS
    )
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
    # the default ISO_TESTS list is ten tests per pair
    assert result.stdout.count("\nOK nodeA-") == 20


def test_a_failing_control_pair_invalidates_the_verdict(tmp_path: Path) -> None:
    result = run_isolate(
        tmp_path, "nodeA", "nodeC", "nodeT", FAKE_FAIL_NODE="nodeC", ISO_TESTS=VERBS
    )
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


def test_iso_tests_limits_the_run_to_the_listed_tests(tmp_path: Path) -> None:
    result = run_isolate(tmp_path, "nodeA", "nodeC", "nodeT", ISO_TESTS="rc udp5000")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("\nOK nodeA-") == 4
    assert "OK nodeA-nodeT mlx5_0 udp5000 srv=0 cli=0" in result.stdout
    assert "ud256" not in result.stdout
    assert "wbw" not in result.stdout


def test_an_unknown_test_name_is_a_usage_error(tmp_path: Path) -> None:
    result = run_isolate(tmp_path, "nodeA", "nodeC", "nodeT", ISO_TESTS="rc bogus")
    assert result.returncode == 2
    assert "unknown test bogus" in result.stderr


def test_write_bandwidth_tests_use_the_index_and_the_tclass_only_where_asked(
    tmp_path: Path,
) -> None:
    result = run_isolate(
        tmp_path, "nodeA", "nodeC", "nodeT", ISO_TESTS="wbw wbw0 wbwtc", ISO_TCLASS="106"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [line for line in ssh_log(tmp_path) if "ib_write_bw" in line and " timeout 10 " in line]
    assert len(lines) == 12  # three tests, two pairs, two sides
    no_index = [line for line in lines if " -x " not in line]
    assert len(no_index) == 4  # wbw0: no -x on either side of either pair
    assert all("--tclass" not in line for line in no_index)
    with_tclass = [line for line in lines if "--tclass=106" in line]
    assert len(with_tclass) == 4
    assert all("-x 3 -D 2 -s 65536" in line for line in with_tclass)
    plain = [line for line in lines if " -x " in line and "--tclass" not in line]
    assert len(plain) == 4
    assert any(
        "nodeA timeout 10 ib_write_bw -d mlx5_0 -i 1 -x 3 -D 2 -s 65536 -p 18600 10.0.0.2" in line
        for line in plain
    )
    # wbw0 keeps the GID lines of both sides (two per side) in DETAIL; wbw and wbwtc keep none
    assert result.stdout.count("local address: LID 0x01 GID:") == 4
    assert "GID: 00:00:00:00:00:00:00:00:00:00:255:255:10:00:00:03" in result.stdout


def test_udp_tests_bind_the_nic_address_and_the_sender_sets_the_tos(tmp_path: Path) -> None:
    result = run_isolate(
        tmp_path,
        "nodeA",
        "nodeC",
        "nodeT",
        ISO_TESTS="udp4791 udp4791tc udp5000",
        ISO_TCLASS="106",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    py = [line for line in ssh_log(tmp_path) if " timeout 10 python3 -c " in line]
    assert len(py) == 12
    senders = [line for line in py if "sendto" in line]
    receivers = [line for line in py if "recvfrom" in line]
    assert len(senders) == 6 and len(receivers) == 6
    assert all(line.startswith("nodeA ") and 'bind(("10.0.0.1",0))' in line for line in senders)
    tos = [line for line in senders if "IP_TOS,106" in line]
    assert len(tos) == 2  # udp4791tc, one per pair
    assert all('("10.0.0.3",4791)' in line or '("10.0.0.2",4791)' in line for line in tos)
    assert any(
        line.startswith("nodeT ") and 'bind(("10.0.0.3",5000))' in line for line in receivers
    )
    assert any(
        line.startswith("nodeC ") and 'bind(("10.0.0.2",4791))' in line for line in receivers
    )
    # the receiver gives up before the outer timeout so its own message reaches the report
    assert all("settimeout(8)" in line for line in receivers)
    assert all("'" not in line.split("python3 -c ", 1)[1][1:-1] for line in py)


def test_a_receiver_that_cannot_bind_fails_with_its_reason_in_the_detail(tmp_path: Path) -> None:
    result = run_isolate(
        tmp_path, "nodeA", "nodeC", "nodeT", ISO_TESTS="udp4791", FAKE_BIND_FAIL="nodeT"
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL nodeA-nodeT mlx5_0 udp4791 srv=2 cli=0" in result.stdout
    assert "OSError: [Errno 98] Address already in use" in result.stdout
    assert (
        "VERDICT: FAIL only on nodeA-nodeT: udp4791 (control nodeA-nodeC passes)" in result.stdout
    )


def test_a_missing_python3_skips_the_udp_tests(tmp_path: Path) -> None:
    result = run_isolate(
        tmp_path, "nodeA", "nodeC", "nodeT", ISO_TESTS="rc udp5000", FAKE_MISSING="nodeT:python3"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TOOLS nodeT: missing python3" in result.stdout
    assert "SKIP nodeA-nodeT mlx5_0 udp5000: python3 missing on nodeT" in result.stdout
    assert "OK nodeA-nodeC mlx5_0 udp5000 srv=0 cli=0" in result.stdout
