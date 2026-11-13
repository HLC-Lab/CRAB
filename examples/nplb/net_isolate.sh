#!/usr/bin/env bash
# Why does UCX UD fail between node A and node TEST while it works between A and CTRL (the control pair)?
#   nplb net isolate A CTRL TEST [NIC]        e.g. nplb net isolate node110 node114 node116 mlx5_0
# Level 0: per node, the tools present and the GID table of NIC port 1; the RoCE v2 IPv4-mapped GID
# index of each node is picked and the IPv4 address decoded from it. Level 1: for the pairs A-CTRL and
# A-TEST, plain verbs tests (RC and UD pingpong, UD ib_send_bw) with the server on the peer and the client
# on A, the client connecting to the peer's NIC IPv4 address. A test passes when server and client both
# exit 0 (throughput is not read). Exit code 124 means "timeout (nothing came back)".
# Stdout is the report, paged and saved as net-isolate; first content line "VERDICT: ...", then one
# "OK|FAIL|SKIP A-X NIC test srv=RC cli=RC" line per test, TOOLS, GID and ROUTE lines, and a DETAIL
# section with the last two output lines of each side. Exit 0 only for the OK verdict, 1 otherwise,
# 2 for bad usage.
# Env: SSH (default ssh), ISO_TIMEOUT (seconds per test side, default 10), ISO_UCX (ucx_perftest path on
# the nodes, default $HOME/crab/lab/ucx-cuda/bin/ucx_perftest), ISO_SETTLE (seconds the server gets to
# start before the client, default 1), NPLB_REPORT_DIR.
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/pages.sh
. "$HERE/lib/pages.sh"
read -ra SSH_CMD <<<"${SSH:-ssh}"
ISO_TIMEOUT=${ISO_TIMEOUT:-10}
# the default keeps $HOME literal: the remote shell expands it
# shellcheck disable=SC2016
ISO_UCX=${ISO_UCX:-'$HOME/crab/lab/ucx-cuda/bin/ucx_perftest'}
ISO_SETTLE=${ISO_SETTLE:-1}

if [[ $# -ne 3 && $# -ne 4 ]]; then
    echo "usage: nplb net isolate A CTRL TEST [NIC]" >&2
    exit 2
fi
A=$1
CTRL=$2
TEST=$3
NIC=${4:-mlx5_0}
HOSTS=("$A" "$CTRL" "$TEST")

declare -A IDX IP SKIPWHY MISSING
TOOL_LINES=()
GID_LINES=()
ROUTE_LINES=()
TEST_LINES=()
DETAIL=()
FAILED_CTRL=()
FAILED_TEST=()
OK_CTRL=0
OK_TEST=0
SKIPPED=0
PORT=18599

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

# remote HOST COMMAND: run COMMAND on HOST, never reading stdin
remote() {
    local host=$1
    shift
    "${SSH_CMD[@]}" "$host" "$@" 2>&1 </dev/null
}

# ipv4_of GID: the IPv4 address in the last 8 hex digits of an IPv4-mapped GID
ipv4_of() {
    local h=${1//:/}
    h=${h: -8}
    printf '%d.%d.%d.%d' "0x${h:0:2}" "0x${h:2:2}" "0x${h:4:2}" "0x${h:6:2}"
}

# remote command (plain sh): the tools missing on a node, space separated
tools_cmd() {
    printf '%s' "for t in ibv_ud_pingpong ibv_rc_pingpong ib_send_bw show_gids; do command -v \$t >/dev/null 2>&1 || printf '%s ' \$t; done; test -x $ISO_UCX || printf 'ucx_perftest '; echo"
}

# remote command (plain sh): idx TAB type TAB gid TAB ndev for every non-zero GID of port 1
gids_cmd() {
    local p=/sys/class/infiniband/$NIC/ports/1
    printf '%s' "i=0; while [ \$i -lt 64 ] && [ -e $p/gid_attrs/types/\$i ]; do t=\$(cat $p/gid_attrs/types/\$i 2>/dev/null); g=\$(cat $p/gids/\$i 2>/dev/null); d=\$(cat $p/gid_attrs/ndevs/\$i 2>/dev/null); if [ -n \"\$g\" ] && [ \"\$g\" != 0000:0000:0000:0000:0000:0000:0000:0000 ]; then printf '%s\\t%s\\t%s\\t%s\\n' \$i \"\$t\" \"\$g\" \"\$d\"; fi; i=\$((i+1)); done"
}

level0() {
    local host out missing idx type gid ndev
    for host in "${HOSTS[@]}"; do
        say "tools and GIDs on $host"
        out=$(remote "$host" "$(tools_cmd)" | tail -n 1)
        missing=$(tr -s ' ' <<<"$out" | sed 's/^ //; s/ $//')
        MISSING[$host]=" $missing "
        if [[ -z $missing ]]; then
            TOOL_LINES+=("TOOLS $host: ok")
        else
            TOOL_LINES+=("TOOLS $host: missing $missing")
        fi
        IDX[$host]=""
        IP[$host]=""
        while IFS=$'\t' read -r idx type gid ndev; do
            [[ -n $idx && -n $gid ]] || continue
            GID_LINES+=("GID $host $NIC $idx $type $gid $ndev")
            if [[ -z ${IDX[$host]} && $type == "RoCE v2" && $gid == 0000:0000:0000:0000:0000:ffff:* ]]; then
                IDX[$host]=$idx
                IP[$host]=$(ipv4_of "$gid")
            fi
        done < <(remote "$host" "$(gids_cmd)")
        if [[ -z ${IDX[$host]} ]]; then
            SKIPWHY[$host]="no RoCE v2 IPv4 GID"
            GID_LINES+=("GID $host $NIC: no RoCE v2 IPv4-mapped index")
        fi
    done
}

# route_line X: how A reaches X's address, as the kernel sees it
route_line() {
    local x=$1 out
    if [[ -z ${IP[$A]} || -z ${IP[$x]} ]]; then
        ROUTE_LINES+=("ROUTE $A->$x: unknown (no IPv4 GID)")
        return
    fi
    out=$(remote "$A" "ip -o route get ${IP[$x]} from ${IP[$A]}" | head -n 1 | tr -s ' ' | cut -c1-150)
    ROUTE_LINES+=("ROUTE $A->$x: $out")
}

# last2 TEXT: last two non-empty lines, each trimmed to 90 characters
last2() {
    grep -v '^[[:space:]]*$' <<<"$1" | tail -n 2 | cut -c1-90 | sed 's/^ *//'
}

# note RC: what an exit code means
note() {
    case $1 in
    124) printf ' (timeout, nothing came back)' ;;
    255) printf ' (ssh failed)' ;;
    esac
}

# commands NAME SRV_HOST CLI_HOST PORT: sets SRV_CMD and CLI_CMD (each side uses its own GID index)
commands() {
    local name=$1 srv=$2 cli=$3 port=$4 sg=${IDX[$2]} cg=${IDX[$3]} addr=${IP[$2]} base_s base_c
    case $name in
    rc)
        base_s="ibv_rc_pingpong -d $NIC -i 1 -g $sg -p $port -n 100"
        base_c="ibv_rc_pingpong -d $NIC -i 1 -g $cg -p $port -n 100 $addr"
        ;;
    ud256)
        base_s="ibv_ud_pingpong -d $NIC -i 1 -g $sg -p $port -n 100 -s 256"
        base_c="ibv_ud_pingpong -d $NIC -i 1 -g $cg -p $port -n 100 -s 256 $addr"
        ;;
    ud1024)
        base_s="ibv_ud_pingpong -d $NIC -i 1 -g $sg -p $port -n 100 -s 1024"
        base_c="ibv_ud_pingpong -d $NIC -i 1 -g $cg -p $port -n 100 -s 1024 $addr"
        ;;
    udbw)
        base_s="ib_send_bw -c UD -d $NIC -i 1 -x $sg -n 1000 -s 1024 -p $port"
        base_c="ib_send_bw -c UD -d $NIC -i 1 -x $cg -n 1000 -s 1024 -p $port $addr"
        ;;
    esac
    SRV_CMD="timeout $ISO_TIMEOUT $base_s"
    CLI_CMD="timeout $ISO_TIMEOUT $base_c"
}

# needs NAME: the tool a test uses
needs() {
    case $1 in
    rc) echo ibv_rc_pingpong ;;
    ud256 | ud1024) echo ibv_ud_pingpong ;;
    udbw) echo ib_send_bw ;;
    esac
}

# run_test KEY X NAME: server on X, client on A; KEY is ctrl or test
run_test() {
    local key=$1 x=$2 name=$3 label="$A-$2" why="" tool sfile sout cout srv_rc cli_rc spid verdict=OK
    tool=$(needs "$name")
    if [[ -n ${SKIPWHY[$A]:-} ]]; then
        why="${SKIPWHY[$A]} on $A"
    elif [[ -n ${SKIPWHY[$x]:-} ]]; then
        why="${SKIPWHY[$x]} on $x"
    elif [[ ${MISSING[$A]} == *" $tool "* ]]; then
        why="$tool missing on $A"
    elif [[ ${MISSING[$x]} == *" $tool "* ]]; then
        why="$tool missing on $x"
    fi
    if [[ -n $why ]]; then
        SKIPPED=$((SKIPPED + 1))
        TEST_LINES+=("SKIP $label $NIC $name: $why")
        return
    fi
    PORT=$((PORT + 1))
    commands "$name" "$x" "$A" "$PORT"
    say "$label $name (port $PORT)"
    sfile=$(mktemp)
    "${SSH_CMD[@]}" "$x" "$SRV_CMD" >"$sfile" 2>&1 </dev/null &
    spid=$!
    sleep "$ISO_SETTLE"
    cout=$(remote "$A" "$CLI_CMD")
    cli_rc=$?
    wait "$spid"
    srv_rc=$?
    sout=$(cat "$sfile")
    rm -f "$sfile"
    if ((srv_rc != 0 || cli_rc != 0)); then
        verdict=FAIL
        if [[ $key == ctrl ]]; then
            FAILED_CTRL+=("$name")
        else
            FAILED_TEST+=("$name")
        fi
    elif [[ $key == ctrl ]]; then
        OK_CTRL=$((OK_CTRL + 1))
    else
        OK_TEST=$((OK_TEST + 1))
    fi
    TEST_LINES+=("$verdict $label $NIC $name srv=$srv_rc cli=$cli_rc")
    DETAIL+=("== $label $name: $verdict")
    DETAIL+=("server on $x, rc=$srv_rc$(note "$srv_rc"):")
    DETAIL+=("$(last2 "$sout" | sed 's/^/  /')")
    DETAIL+=("client on $A, rc=$cli_rc$(note "$cli_rc"):")
    DETAIL+=("$(last2 "$cout" | sed 's/^/  /')")
}

# join ARGS...: the arguments separated by spaces
join() {
    local IFS=' '
    echo "$*"
}

level0
route_line "$CTRL"
route_line "$TEST"
for name in rc ud256 ud1024 udbw; do
    run_test ctrl "$CTRL" "$name"
done
for name in rc ud256 ud1024 udbw; do
    run_test test "$TEST" "$name"
done

if ((${#FAILED_CTRL[@]} > 0)); then
    VERDICT="VERDICT: INVALID control pair $A-$CTRL failed: $(join "${FAILED_CTRL[@]}")"
elif ((${#FAILED_TEST[@]} > 0)); then
    VERDICT="VERDICT: FAIL only on $A-$TEST: $(join "${FAILED_TEST[@]}") (control $A-$CTRL passes)"
elif ((OK_CTRL > 0 && OK_TEST > 0)); then
    VERDICT="VERDICT: OK verbs pass on both pairs: problem is above verbs (UCX)"
    ((SKIPPED == 0)) || VERDICT="$VERDICT ($SKIPPED tests skipped)"
elif ((OK_CTRL + OK_TEST == 0)); then
    VERDICT="VERDICT: UNKNOWN every test was skipped (see SKIP lines)"
elif ((OK_CTRL == 0)); then
    VERDICT="VERDICT: UNKNOWN no test ran on the control pair $A-$CTRL"
else
    VERDICT="VERDICT: UNKNOWN no test ran on $A-$TEST"
fi

{
    echo "$VERDICT"
    printf '%s\n' "${TEST_LINES[@]}"
    echo "(rc 124 = timeout, nothing came back; 255 = ssh failed)"
    printf '%s\n' "${TOOL_LINES[@]}"
    printf '%s\n' "${GID_LINES[@]}"
    printf '%s\n' "${ROUTE_LINES[@]}"
    echo "DETAIL"
    printf '%s\n' "${DETAIL[@]}"
} | nplb_paged net-isolate
[[ $VERDICT == "VERDICT: OK"* ]]
