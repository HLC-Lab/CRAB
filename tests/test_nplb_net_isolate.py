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
nd=ib0
[[ -n ${FAKE_NO_NDEV:-} ]] && nd=""
if [[ $cmd == *gid_attrs* ]]; then
    printf '0\tRoCE v1\tfe80:0000:0000:0000:0000:0000:0000:000%s\t%s\n' "$n" "$nd"
    printf '1\tRoCE v2\tfe80:0000:0000:0000:0000:0000:0000:000%s\t%s\n' "$n" "$nd"
    printf '2\tRoCE v1\t0000:0000:0000:0000:0000:ffff:0a00:000%s\t%s\n' "$n" "$nd"
    printf '3\tRoCE v2\t0000:0000:0000:0000:0000:ffff:0a00:000%s\t%s\n' "$n" "$nd"
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
if [[ $cmd == *hw_counters* ]]; then
    # counter snapshot: calls alternate before (odd) and after (even) per host; "@" marks an ethtool name
    [[ ${FAKE_SNAP_FAIL:-} == "$host" ]] && exit 1
    f="$FAKE_LOG.snap.$host"
    k=$(($(cat "$f" 2>/dev/null || echo 0) + 1))
    echo "$k" >"$f"
    add=$(((k - 1) % 2))
    emit() { printf '%s %s\n' "$1" "$((($2) + add * $3))"; }
    case $host in
    nodeA)
        emit local_ack_timeout_err 0 1
        emit port_xmit_data 5 100
        emit rx_write_requests 3 0
        ;;
    nodeC)
        emit rx_write_requests 1 1
        ;;
    nodeT)
        emit port_rcv_data 1000 4000
        emit port_xmit_packets 10 10
        emit port_rcv_packets 100 50
        emit port_rcv_errors 0 0
        emit rx_write_requests 7 2
        emit rx_vport_rdma_unicast_packets 1 40
        emit tx_vport_rdma_unicast_packets 0 0
        emit @rx_bytes_phy 1 999
        if [[ -z ${FAKE_NO_ERR:-} ]]; then
            emit rx_icrc_encapsulated 5 12
            emit out_of_sequence 0 3
            emit @rx_discards_phy 0 4
            emit @rx_err_bytes 0 7
        fi
        if [[ -n ${FAKE_MANY:-} ]]; then
            for i in $(seq -w 1 50); do emit "e_err_$i" 0 1; done
        fi
        if [[ -n ${FAKE_NOISE:-} ]]; then
            # per-channel and other ethtool noise (dropped), and names the keep list rescues
            emit @rx0_packets 0 9
            emit @rx12_bytes 0 9
            emit @tx3_xmit_more 0 2
            emit @ch1_arm 0 5
            emit @ch_events 0 6
            emit @rx_cache_reuse 0 3
            emit @rx_csum_unnecessary 0 8
            emit @rx_lro_packets 0 1
            emit @tx_tso_packets 0 1
            emit @rx_prio3_pause 0 20
            emit @rx_xdp_drop 0 4
        fi
        ;;
    esac
    exit 0
fi
if [[ $cmd == ping* ]]; then
    # a reply needs a TTL of at least 1 towards nodeC and FAKE_TTL_MIN_T (default 2) towards nodeT; below
    # that a router (10.0.9.9) answers "Time to live exceeded"
    re='-t ([0-9]+) '
    [[ $cmd =~ $re ]]
    min=1
    [[ ${cmd##* } == 10.0.0.3 ]] && min=${FAKE_TTL_MIN_T:-2}
    if ((BASH_REMATCH[1] >= min)); then
        echo "64 bytes from ${cmd##* }: icmp_seq=1 ttl=63 time=0.1 ms"
        exit 0
    fi
    echo "PING ${cmd##* } 56(84) bytes of data."
    echo "From 10.0.9.9 icmp_seq=1 Time to live exceeded"
    echo "2 packets transmitted, 0 received, +2 errors, 100% packet loss"
    exit 1
fi
if [[ $cmd == *ucx_info* ]]; then
    if [[ -n ${FAKE_NO_UCX_INFO:-} ]]; then
        echo UCX_INFO_MISSING
        exit 0
    fi
    echo "UCX_LOG_LEVEL=warn"
    echo "UCX_IB_GID_INDEX=auto"
    echo "UCX_IB_TRAFFIC_CLASS=auto"
    echo "UCX_IB_SL=0"
    echo "UCX_IB_ADDR_TYPE=auto"
    echo "UCX_RC_MLX5_HOP_LIMIT=255"
    for i in 1 2 3 4 5 6 7 8 9 10; do echo "UCX_IB_ROCE_OPT$i=y"; done
    exit 0
fi
if [[ $cmd == *"python3 -c"* ]]; then
    # plain UDP: the sender always exits 0; the receiver times out (1) on the failing node and
    # cannot bind (2) on FAKE_BIND_FAIL
    if [[ $cmd == *sendto* ]]; then
        echo "sent 20 datagrams"
        exit 0
    fi
    if [[ $cmd == *'"GOT"'* ]]; then
        # sweep receiver: sees every source port except FAKE_LOSS when it runs on FAKE_LOSS_ON
        re='len\(seen\)<([0-9]+)'
        [[ $cmd =~ $re ]]
        list=""
        b=${FAKE_SWEEP_BASE:-50000}
        for ((p = b; p < b + BASH_REMATCH[1]; p++)); do
            if [[ ${FAKE_LOSS_ON:-} == "$host" && " ${FAKE_LOSS:-} " == *" $p "* ]]; then continue; fi
            list+="${list:+, }$p"
        done
        echo "GOT [$list]"
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
if [[ $cmd == *ib_write_bw* || $cmd == *ib_send_bw* || $cmd == *ib_read_bw* ]]; then
    if [[ $cmd == *ib_write_bw* ]]; then
        # perftest prints both addresses, GID included, before the results
        echo " local address: LID 0x01 GID: 00:00:00:00:00:00:00:00:00:00:255:255:10:00:00:0$n"
        echo " remote address: LID 0x01 GID: 00:00:00:00:00:00:00:00:00:00:255:255:10:00:00:0$n"
    fi
    # the result line: bytes, iterations, BW peak, BW average, MsgRate. FAKE_ZERO_BW=cli: the client aimed
    # at nodeT reports zero iterations; =srv: the server on nodeT does (both exit 0); =none: that client
    # prints no result line
    l=${cmd##* }
    zero=0
    [[ ${FAKE_ZERO_BW:-} == cli && $l == 10.0.0.3 ]] && zero=1
    [[ ${FAKE_ZERO_BW:-} == srv && $host == nodeT && ! $l =~ ^10\. ]] && zero=1
    echo " #bytes     #iterations    BW peak[MB/sec]    BW average[MB/sec]   MsgRate[Mpps]"
    if [[ ${FAKE_ZERO_BW:-} == none && $l == 10.0.0.3 ]]; then
        :  # no result line at all
    elif ((zero)); then
        echo " 65536      0                0.00               0.00               0.000000"
    else
        echo " 65536      1000             11.50              11.40              0.17"
    fi
fi
if [[ $cmd == *ibv_rc_pingpong* ]]; then
    # FAKE_FAIL_PORTS: runs on these -p ports time out on both sides
    for fp in ${FAKE_FAIL_PORTS:-}; do
        [[ $cmd == *"-p $fp "* ]] && exit 124
    done
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


def test_a_sweep_that_loses_source_ports_fails_with_the_count_and_the_lost_list(
    tmp_path: Path,
) -> None:
    result = run_isolate(
        tmp_path,
        "nodeA",
        "nodeC",
        "nodeT",
        ISO_TESTS="udpsweep",
        FAKE_LOSS_ON="nodeT",
        FAKE_LOSS="50003 50010 50063",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "OK nodeA-nodeC mlx5_0 udpsweep srv=0 cli=0 delivered=64/64" in result.stdout
    assert "FAIL nodeA-nodeT mlx5_0 udpsweep srv=0 cli=0 delivered=61/64" in result.stdout
    assert "lost sports: 50003 50010 50063" in result.stdout
    assert (
        "VERDICT: FAIL only on nodeA-nodeT: udpsweep (control nodeA-nodeC passes)" in result.stdout
    )
    py = [line for line in ssh_log(tmp_path) if " timeout 10 python3 -c " in line]
    receivers = [line for line in py if '"GOT"' in line]
    senders = [line for line in py if "SO_REUSEADDR" in line]
    assert [line.split()[0] for line in receivers] == ["nodeC", "nodeT"]
    assert all('bind(("10.0.0.3",5000))' in line for line in receivers if line.startswith("nodeT"))
    assert all("settimeout" not in line and "len(seen)<64" in line for line in receivers)
    assert all(line.startswith("nodeA ") for line in senders)
    assert all('s.bind(("10.0.0.1",p))' in line for line in senders)
    assert all("range(50000,50064)" in line and "range(3)" in line for line in senders)
    assert all("'" not in line.split("python3 -c ", 1)[1][1:-1] for line in py)


def test_a_long_lost_list_is_cut_at_forty_ports(tmp_path: Path) -> None:
    result = run_isolate(
        tmp_path,
        "nodeA",
        "nodeC",
        "nodeT",
        ISO_TESTS="udpsweep",
        FAKE_LOSS_ON="nodeT",
        FAKE_LOSS=" ".join(str(p) for p in range(50000, 50045)),
    )
    assert "delivered=19/64" in result.stdout
    assert "50039" in result.stdout and "50040" not in result.stdout
    assert "(+5 more)" in result.stdout


def test_the_reverse_sweep_puts_the_receiver_on_a(tmp_path: Path) -> None:
    result = run_isolate(
        tmp_path,
        "nodeA",
        "nodeC",
        "nodeT",
        ISO_TESTS="udpsweeprev",
        ISO_SPORT_BASE="40000",
        FAKE_SWEEP_BASE="40000",
        ISO_SPORTS="8",
        FAKE_LOSS_ON="nodeT",
        FAKE_LOSS="50001",
    )
    # nothing is lost when the receiver is on A: the fake only loses on nodeT
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK nodeA-nodeT mlx5_0 udpsweeprev srv=0 cli=0 delivered=8/8" in result.stdout
    py = [line for line in ssh_log(tmp_path) if " timeout 10 python3 -c " in line]
    receivers = [line for line in py if '"GOT"' in line]
    senders = [line for line in py if "SO_REUSEADDR" in line]
    assert [line.split()[0] for line in receivers] == ["nodeA", "nodeA"]
    assert all('bind(("10.0.0.1",5000))' in line for line in receivers)
    assert [line.split()[0] for line in senders] == ["nodeC", "nodeT"]
    assert 's.bind(("10.0.0.3",p))' in senders[1] and '("10.0.0.1",5000)' in senders[1]
    assert "range(40000,40008)" in senders[1]


def test_a_reverse_sweep_losing_on_a_invalidates_the_control(tmp_path: Path) -> None:
    result = run_isolate(
        tmp_path,
        "nodeA",
        "nodeC",
        "nodeT",
        ISO_TESTS="udpsweeprev",
        FAKE_LOSS_ON="nodeA",
        FAKE_LOSS="50007",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL nodeA-nodeC mlx5_0 udpsweeprev srv=0 cli=0 delivered=63/64" in result.stdout
    assert "VERDICT: INVALID control pair nodeA-nodeC failed: udpsweeprev" in result.stdout


def test_rcrep_counts_the_passing_runs_with_fresh_ports_and_a_short_timeout(
    tmp_path: Path,
) -> None:
    result = run_isolate(
        tmp_path,
        "nodeA",
        "nodeC",
        "nodeT",
        ISO_TESTS="rcrep",
        ISO_REPS="5",
        FAKE_FAIL_PORTS="18606 18608",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "OK nodeA-nodeC mlx5_0 rcrep pass=5/5" in result.stdout
    assert "FAIL nodeA-nodeT mlx5_0 rcrep pass=3/5" in result.stdout
    assert "VERDICT: FAIL only on nodeA-nodeT: rcrep (control nodeA-nodeC passes)" in result.stdout
    runs = [line for line in ssh_log(tmp_path) if " timeout 6 ibv_rc_pingpong " in line]
    assert len(runs) == 20  # five runs, two sides, two pairs
    ports = sorted({int(re.search(r"-p (\d+)", line).group(1)) for line in runs})  # type: ignore[union-attr]
    assert ports == list(range(18600, 18610))


def test_wbwrep_and_wbw64_use_the_gid_index(tmp_path: Path) -> None:
    result = run_isolate(
        tmp_path, "nodeA", "nodeC", "nodeT", ISO_TESTS="wbwrep wbw64", ISO_REPS="3"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK nodeA-nodeT mlx5_0 wbwrep pass=3/3" in result.stdout
    assert "OK nodeA-nodeT mlx5_0 wbw64 srv=0 cli=0" in result.stdout
    lines = [line for line in ssh_log(tmp_path) if "ib_write_bw" in line and " timeout " in line]
    reps = [line for line in lines if " timeout 6 " in line]
    assert len(reps) == 12 and all("-x 3 -D 2 -s 65536 -p " in line for line in reps)
    small = [line for line in lines if " -s 64 " in line]
    assert len(small) == 4 and all(" timeout 10 " in line and " -x 3 " in line for line in small)


def test_iso_test_singular_is_taken_when_iso_tests_is_not_set(tmp_path: Path) -> None:
    result = run_isolate(tmp_path, "nodeA", "nodeC", "nodeT", ISO_TEST="rc")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("\nOK nodeA-") == 2
    assert "ud256" not in result.stdout


def test_ttl_finds_the_first_ttl_that_gets_a_reply_and_keeps_the_dropping_router(
    tmp_path: Path,
) -> None:
    result = run_isolate(tmp_path, "nodeA", "nodeC", "nodeT", ISO_TESTS="ttl")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK nodeA-nodeC mlx5_0 ttl first_reply_ttl=1" in result.stdout
    assert "OK nodeA-nodeT mlx5_0 ttl first_reply_ttl=2" in result.stdout
    assert "From 10.0.9.9 icmp_seq=1 Time to live exceeded" in result.stdout
    pings = [line for line in ssh_log(tmp_path) if line.startswith("nodeA ping ")]
    assert pings == [
        "nodeA ping -c 2 -W 1 -t 1 -I 10.0.0.1 10.0.0.2",
        "nodeA ping -c 2 -W 1 -t 1 -I 10.0.0.1 10.0.0.3",
        "nodeA ping -c 2 -W 1 -t 2 -I 10.0.0.1 10.0.0.3",
    ]


def test_ttl_without_any_reply_fails_up_to_the_limit(tmp_path: Path) -> None:
    result = run_isolate(
        tmp_path, "nodeA", "nodeC", "nodeT", ISO_TESTS="ttl", ISO_TTL_MAX="3", FAKE_TTL_MIN_T="9"
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL nodeA-nodeT mlx5_0 ttl no reply up to ttl 3" in result.stdout
    assert "VERDICT: FAIL only on nodeA-nodeT: ttl (control nodeA-nodeC passes)" in result.stdout
    pings = [line for line in ssh_log(tmp_path) if line.endswith(" 10.0.0.3") and " ping " in line]
    assert len(pings) == 3


def test_send_and_read_bandwidth_tests_use_the_index(tmp_path: Path) -> None:
    result = run_isolate(tmp_path, "nodeA", "nodeC", "nodeT", ISO_TESTS="sbw rbw sbw64")
    assert result.returncode == 0, result.stdout + result.stderr
    log = [line for line in ssh_log(tmp_path) if " timeout 10 " in line]
    sbw = [line for line in log if "ib_send_bw -d mlx5_0 -i 1 -x 3 -D 2 -s 65536 -p " in line]
    rbw = [line for line in log if "ib_read_bw -d mlx5_0 -i 1 -x 3 -D 2 -s 65536 -p " in line]
    small = [line for line in log if "ib_send_bw -d mlx5_0 -i 1 -x 3 -D 2 -s 64 -p " in line]
    assert (len(sbw), len(rbw), len(small)) == (4, 4, 4)  # two sides, two pairs
    assert all("-c UD" not in line for line in sbw + small)
    assert [line.endswith(" 10.0.0.3") for line in rbw if line.startswith("nodeA ")] == [
        False,
        True,
    ]
    assert "OK nodeA-nodeT mlx5_0 rbw srv=0 cli=0" in result.stdout


def test_rcmtu_is_rc_with_a_4096_mtu(tmp_path: Path) -> None:
    result = run_isolate(tmp_path, "nodeA", "nodeC", "nodeT", ISO_TESTS="rcmtu")
    assert result.returncode == 0, result.stdout + result.stderr
    log = [line for line in ssh_log(tmp_path) if "ibv_rc_pingpong" in line and " timeout " in line]
    assert len(log) == 4
    assert all(" -g 3 " in line and " -n 100 -m 4096" in line for line in log)
    assert [line for line in log if line.startswith("nodeA ")][1].endswith(" -m 4096 10.0.0.3")
    assert "OK nodeA-nodeT mlx5_0 rcmtu srv=0 cli=0" in result.stdout


def test_ucxcfg_lists_the_matching_ucx_settings_once(tmp_path: Path) -> None:
    result = run_isolate(tmp_path, "nodeA", "nodeC", "nodeT", ISO_TESTS="rc ucxcfg")
    assert result.returncode == 0, result.stdout + result.stderr
    ucx = [line for line in result.stdout.splitlines() if line.startswith("UCX ")]
    assert ucx[:5] == [
        "UCX UCX_IB_GID_INDEX=auto",
        "UCX UCX_IB_TRAFFIC_CLASS=auto",
        "UCX UCX_IB_SL=0",
        "UCX UCX_IB_ADDR_TYPE=auto",
        "UCX UCX_RC_MLX5_HOP_LIMIT=255",
    ]
    assert len(ucx) == 12  # cut at twelve
    assert "UCX_LOG_LEVEL" not in result.stdout
    calls = [line for line in ssh_log(tmp_path) if "ucx_info" in line]
    assert len(calls) == 1 and calls[0].startswith("nodeA ")
    assert "dirname $HOME/crab/lab/ucx-cuda/bin/ucx_perftest" in calls[0]
    # it is no pair test: only rc shows in the test lines
    assert result.stdout.count("\nOK nodeA-") == 2
    assert result.stdout.index("\nROUTE ") < result.stdout.index("\nUCX ")


def test_ucxcfg_says_so_when_ucx_info_is_missing(tmp_path: Path) -> None:
    result = run_isolate(
        tmp_path, "nodeA", "nodeC", "nodeT", ISO_TESTS="ucxcfg", FAKE_NO_UCX_INFO="1"
    )
    assert "UCX ucx_info missing" in result.stdout
    assert "UNKNOWN" in result.stdout  # no pair test ran


def counter_run(tmp_path: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return run_isolate(
        tmp_path, "nodeA", "nodeC", "nodeT", ISO_TESTS="rc", ISO_COUNTERS="1", **extra
    )


def snapshot_calls(tmp_path: Path) -> list[str]:
    return [line for line in ssh_log(tmp_path) if "hw_counters" in line]


def test_counter_deltas_are_listed_per_host_under_the_test(tmp_path: Path) -> None:
    result = counter_run(tmp_path, FAKE_FAIL_NODE="nodeT")
    assert result.returncode == 1, result.stdout + result.stderr
    out = result.stdout
    assert "  CNT nodeA local_ack_timeout_err +1" in out
    assert "  CNT nodeC rx_write_requests +1" in out
    # nodeT, sorted by name, with the delta of the second snapshot over the first
    cnt_t = [line for line in out.splitlines() if line.startswith("  CNT nodeT ")]
    assert cnt_t == [
        "  CNT nodeT out_of_sequence +3",
        "  CNT nodeT rx_discards_phy +4",
        "  CNT nodeT rx_err_bytes +7",
        "  CNT nodeT rx_icrc_encapsulated +12",
        "  CNT nodeT rx_vport_rdma_unicast_packets +40",
        "  CNT nodeT rx_write_requests +2",
    ]
    # the deltas sit under their test in DETAIL: nodeA-nodeT's block comes after nodeA-nodeC's
    assert out.index("== nodeA-nodeC rc") < out.index("CNT nodeC") < out.index("== nodeA-nodeT rc")
    assert out.index("== nodeA-nodeT rc") < out.index("CNT nodeT out_of_sequence")
    # one snapshot per host before and one after each test: A, X, A, X
    hosts = [line.split()[0] for line in snapshot_calls(tmp_path)]
    assert hosts == ["nodeA", "nodeC", "nodeA", "nodeC", "nodeA", "nodeT", "nodeA", "nodeT"]
    assert all("ethtool -S ib0" in line for line in snapshot_calls(tmp_path))
    assert all(
        "ports/1/hw_counters" in line and "ports/1/counters" in line
        for line in snapshot_calls(tmp_path)
    )
    assert all("/sys/class/infiniband/mlx5_0/" in line for line in snapshot_calls(tmp_path))


def test_the_filter_drops_traffic_counters_and_keeps_the_error_names(tmp_path: Path) -> None:
    result = counter_run(tmp_path, FAKE_FAIL_NODE="nodeT")
    for dropped in (
        "port_rcv_data",  # traffic
        "port_xmit_packets",  # traffic
        "port_rcv_packets",  # traffic
        "port_xmit_data",  # traffic (nodeA)
        "rx_bytes_phy",  # ethtool _bytes
        "port_rcv_errors",  # zero delta
        "tx_vport_rdma_unicast_packets",  # zero delta
    ):
        assert f"CNT nodeT {dropped}" not in result.stdout, dropped
        assert f"CNT nodeA {dropped}" not in result.stdout, dropped
    # an ethtool name with _bytes stays when it names an error; rdma_unicast is kept
    assert "CNT nodeT rx_err_bytes +7" in result.stdout
    assert "CNT nodeT rx_vport_rdma_unicast_packets +40" in result.stdout


def test_a_failing_test_gets_a_summary_line_of_the_receivers_telling_counters(
    tmp_path: Path,
) -> None:
    result = counter_run(tmp_path, FAKE_FAIL_NODE="nodeT")
    lines = result.stdout.splitlines()
    i = lines.index("FAIL nodeA-nodeT mlx5_0 rc srv=124 cli=124")
    assert lines[i + 1] == (
        "  -> nodeT: rx_icrc_encapsulated+12 rx_err_bytes+7 rx_discards_phy+4 out_of_sequence+3"
    )
    # a passing test gets none
    j = lines.index("OK nodeA-nodeC mlx5_0 rc srv=0 cli=0")
    assert not lines[j + 1].startswith("  ->")


def test_the_summary_falls_back_to_the_rdma_unicast_counters(tmp_path: Path) -> None:
    result = counter_run(tmp_path, FAKE_FAIL_NODE="nodeT", FAKE_NO_ERR="1")
    assert "  -> nodeT: rx_vport_rdma_unicast_packets+40" in result.stdout


def test_at_most_40_counter_lines_per_host_then_a_count(tmp_path: Path) -> None:
    result = counter_run(tmp_path, FAKE_FAIL_NODE="nodeT", FAKE_MANY="1")
    cnt_t = [line for line in result.stdout.splitlines() if line.startswith("  CNT nodeT ")]
    assert len(cnt_t) == 41  # 40 names and the count of the rest
    assert cnt_t[-1] == "  CNT nodeT (+16 more)"  # 56 names in all


def test_an_unreadable_snapshot_is_reported(tmp_path: Path) -> None:
    result = counter_run(tmp_path, FAKE_SNAP_FAIL="nodeT")
    assert "  CNT nodeT unreadable" in result.stdout
    assert "  CNT nodeC rx_write_requests +1" in result.stdout


def test_without_a_netdev_ethtool_is_not_asked_and_counters_are_off_by_default(
    tmp_path: Path,
) -> None:
    result = counter_run(tmp_path, FAKE_NO_NDEV="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot_calls(tmp_path)
    assert all("ethtool" not in line for line in snapshot_calls(tmp_path))
    other = tmp_path / "off"
    other.mkdir()
    off = run_isolate(other, "nodeA", "nodeC", "nodeT", ISO_TESTS="rc")
    assert off.returncode == 0, off.stdout + off.stderr
    assert snapshot_calls(other) == [] and "CNT" not in off.stdout


def test_the_counter_filter_drops_per_channel_noise_and_keeps_pause_prio_and_rdma(
    tmp_path: Path,
) -> None:
    result = counter_run(tmp_path, FAKE_FAIL_NODE="nodeT", FAKE_NOISE="1")
    cnt_t = [line for line in result.stdout.splitlines() if line.startswith("  CNT nodeT ")]
    names = [line.split()[2] for line in cnt_t]
    for noise in (
        "rx0_packets",
        "rx12_bytes",
        "tx3_xmit_more",
        "ch1_arm",
        "ch_events",
        "rx_cache_reuse",
        "rx_csum_unnecessary",
        "rx_lro_packets",
        "tx_tso_packets",
    ):
        assert noise not in names, noise
    assert "  CNT nodeT rx_prio3_pause +20" in cnt_t
    assert "  CNT nodeT rx_vport_rdma_unicast_packets +40" in cnt_t
    assert "  CNT nodeT rx_xdp_drop +4" in cnt_t  # _xdp is noise, but drop is on the keep list


def test_the_summary_adds_pause_prio_and_rdma_names_after_the_error_names(tmp_path: Path) -> None:
    result = counter_run(tmp_path, FAKE_FAIL_NODE="nodeT", FAKE_NO_ERR="1", FAKE_NOISE="1")
    assert (
        "  -> nodeT: rx_xdp_drop+4 rx_vport_rdma_unicast_packets+40 rx_prio3_pause+20"
        in result.stdout
    )


def bw_run(tmp_path: Path, tests: str, **extra: str) -> subprocess.CompletedProcess[str]:
    return run_isolate(tmp_path, "nodeA", "nodeC", "nodeT", ISO_TESTS=tests, **extra)


def test_a_perftest_with_zero_iterations_fails_even_when_both_sides_exit_0(tmp_path: Path) -> None:
    result = bw_run(tmp_path, "wbw", FAKE_ZERO_BW="cli")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL nodeA-nodeT mlx5_0 wbw srv=0 cli=0 bw=0" in result.stdout
    assert "OK nodeA-nodeC mlx5_0 wbw srv=0 cli=0 bw=11.40" in result.stdout
    assert "VERDICT: FAIL only on nodeA-nodeT: wbw (control nodeA-nodeC passes)" in result.stdout


def test_a_perftest_with_a_rate_passes_and_shows_the_bandwidth(tmp_path: Path) -> None:
    result = bw_run(tmp_path, "wbw wbw0 wbwtc wbw64 sbw sbw64 rbw udbw")
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ("wbw", "wbw0", "wbwtc", "wbw64", "sbw", "sbw64", "rbw", "udbw"):
        assert f"OK nodeA-nodeT mlx5_0 {name} srv=0 cli=0 bw=11.40" in result.stdout, name


def test_a_send_test_needs_a_rate_on_the_server_too(tmp_path: Path) -> None:
    result = bw_run(tmp_path, "sbw", FAKE_ZERO_BW="srv")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL nodeA-nodeT mlx5_0 sbw srv=0 cli=0 bw=0" in result.stdout
    # a write test does not look at the server's line
    other = tmp_path / "w"
    other.mkdir()
    write = bw_run(other, "wbw", FAKE_ZERO_BW="srv")
    assert write.returncode == 0, write.stdout + write.stderr
    assert "OK nodeA-nodeT mlx5_0 wbw srv=0 cli=0 bw=11.40" in write.stdout


def test_wbwrep_counts_a_zero_iteration_run_as_a_failure(tmp_path: Path) -> None:
    result = bw_run(tmp_path, "wbwrep", ISO_REPS="3", FAKE_ZERO_BW="cli")
    assert "OK nodeA-nodeC mlx5_0 wbwrep pass=3/3" in result.stdout
    assert "FAIL nodeA-nodeT mlx5_0 wbwrep pass=0/3" in result.stdout


def test_pingpong_rules_are_unchanged_and_a_missing_result_line_fails_a_perftest(
    tmp_path: Path,
) -> None:
    # pingpong output has no perftest result line, and its rules are unchanged
    result = bw_run(tmp_path, "rc ud256")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK nodeA-nodeT mlx5_0 rc srv=0 cli=0\n" in result.stdout
    other = tmp_path / "none"
    other.mkdir()
    missing = bw_run(other, "rbw", FAKE_ZERO_BW="none")
    assert missing.returncode == 1, missing.stdout + missing.stderr
    assert "FAIL nodeA-nodeT mlx5_0 rbw srv=0 cli=0 bw=0" in missing.stdout
