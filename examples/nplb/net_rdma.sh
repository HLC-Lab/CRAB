#!/usr/bin/env bash
# Does RDMA pass between two nodes, NIC by NIC, without UCX or MPI?
#   nplb net rdma HOST_A HOST_B
# For every NIC of RDMA_NICS (default: the eight the smoke test binds) runs two perftest measurements
# from HOST_A to HOST_B on that NIC of both nodes, 2 seconds each: RC (ib_write_bw) and UD (ib_send_bw
# -c UD, 2048 byte messages). An RC result counts when the client prints a "BW average" line; a UD result
# only when the server on HOST_B reports a received rate above 0 (a UD sender reports a rate anyway).
# Stdout is the report, paged and saved as net-rdma; first content line "VERDICT: OK|FAIL ...", then one
# "OK|FAIL <rc|ud> <nic>" line per measurement. Exit 0 when all pass, 1 otherwise, 2 for bad usage.
# Env: SSH (default ssh), RDMA_NICS, RDMA_TIMEOUT (seconds per client, default 15), NPLB_REPORT_DIR.
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/pages.sh
. "$HERE/lib/pages.sh"
read -ra SSH_CMD <<<"${SSH:-ssh}"
RDMA_NICS=${RDMA_NICS:-mlx5_0 mlx5_2 mlx5_4 mlx5_8 mlx5_10 mlx5_12 mlx5_14 mlx5_16}
RDMA_TIMEOUT=${RDMA_TIMEOUT:-15}

if [[ $# -ne 2 ]]; then
    echo "usage: nplb net rdma HOST_A HOST_B" >&2
    exit 2
fi
A=$1
B=$2

LINES=()
FAILS=0
TESTED=0

# measure NAME NIC SERVER_CMD CLIENT_CMD: server on B (in the background), client on A
measure() {
    local name=$1 nic=$2 server=$3 client=$4 out sfile received=1
    sfile=$(mktemp)
    TESTED=$((TESTED + 1))
    printf '[%s] %s %s %s -> %s\n' "$(date +%H:%M:%S)" "$name" "$nic" "$A" "$B" >&2
    "${SSH_CMD[@]}" "$B" "timeout $((RDMA_TIMEOUT + 5)) $server" >"$sfile" 2>&1 </dev/null &
    sleep 2
    out=$("${SSH_CMD[@]}" "$A" "timeout $RDMA_TIMEOUT $client" 2>&1 </dev/null)
    wait
    # a UD sender reports its rate even when nothing arrives: only the receiver's rate proves delivery
    if [[ $name == ud ]]; then
        received=0
        awk '/^ *[0-9]+ +[0-9]+ +[0-9.]+ +[0-9.]+ +[0-9.]+ *$/ && $4 + 0 > 0 {f = 1} END {exit !f}' "$sfile" && received=1
    fi
    rm -f "$sfile"
    if grep -q 'BW average' <<<"$out" && ((received)); then
        LINES+=("OK $name $nic")
    elif ((received)); then
        FAILS=$((FAILS + 1))
        LINES+=("FAIL $name $nic: $(grep -i -m 1 'error\|fail\|unable\|couldn' <<<"$out" | cut -c1-70)")
    else
        FAILS=$((FAILS + 1))
        LINES+=("FAIL $name $nic: sent, but $B received nothing")
    fi
}

for nic in $RDMA_NICS; do
    measure rc "$nic" "ib_write_bw -d $nic -D 2 -s 65536" "ib_write_bw -d $nic -D 2 -s 65536 $B"
    measure ud "$nic" "ib_send_bw -c UD -d $nic -D 2 -s 2048" "ib_send_bw -c UD -d $nic -D 2 -s 2048 $B"
done

{
    if ((FAILS == 0)); then
        echo "VERDICT: OK RC and UD work from $A to $B on all NICs ($TESTED runs)"
    else
        echo "VERDICT: FAIL $FAILS of $TESTED RDMA runs from $A to $B failed"
    fi
    printf '%s\n' "${LINES[@]}"
} | nplb_paged net-rdma
((FAILS == 0))
