#!/usr/bin/env bash
# Why does UCX UD fail between node A and node TEST while it works between A and CTRL (the control pair)?
#   nplb net isolate A CTRL TEST [NIC]        e.g. nplb net isolate node110 node114 node116 mlx5_0
# Level 0: per node, the tools present and the GID table of NIC port 1; the RoCE v2 IPv4-mapped GID
# index of each node is picked and the IPv4 address decoded from it. Level 1: for the pairs A-CTRL and
# A-TEST, tests with the server on the peer and the client on A, the client connecting to the peer's NIC
# IPv4 address. A test passes when server and client both exit 0 (throughput is not read). Exit code 124
# means "timeout (nothing came back)". Tests (ISO_TESTS selects, in this order):
#   rc ud256 ud1024   ibv_rc_pingpong, ibv_ud_pingpong with 256 and 1024 byte messages
#   udbw              ib_send_bw -c UD
#   wbw               ib_write_bw with the RoCE v2 IPv4 GID index (-x)
#   wbw0              ib_write_bw without -x (which GID does perftest pick? DETAIL keeps its GID lines)
#   wbwtc             wbw with --tclass=ISO_TCLASS
#   udp4791 udp5000   plain UDP from A's NIC address to the peer's, no RDMA, on that port
#   udp4791tc         udp4791 with the sender's IP_TOS set to ISO_TCLASS
# Stdout is the report, paged and saved as net-isolate; first content line "VERDICT: ...", then one
# "OK|FAIL|SKIP A-X NIC test srv=RC cli=RC" line per test, TOOLS, ROUTE and GID lines, and a DETAIL
# section with the last two output lines of each side. Exit 0 only for the OK verdict, 1 otherwise,
# 2 for bad usage.
# Env: SSH (default ssh), ISO_TIMEOUT (seconds per test side, default 10), ISO_TESTS (the tests to run),
# ISO_TCLASS (traffic class, default 106 = DSCP 26), ISO_UCX (ucx_perftest path on the nodes, default
# $HOME/crab/lab/ucx-cuda/bin/ucx_perftest), ISO_SETTLE (seconds the server gets to start before the
# client, default 1), NPLB_REPORT_DIR.
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
ISO_TCLASS=${ISO_TCLASS:-106}
ISO_TESTS=${ISO_TESTS:-rc ud256 ud1024 udbw wbw wbw0 wbwtc udp4791 udp4791tc udp5000}

if [[ $# -ne 3 && $# -ne 4 ]]; then
    echo "usage: nplb net isolate A CTRL TEST [NIC]" >&2
    exit 2
fi
for t in $ISO_TESTS; do
    case $t in
    rc | ud256 | ud1024 | udbw | wbw | wbw0 | wbwtc | udp4791 | udp4791tc | udp5000) ;;
    *)
        echo "unknown test $t in ISO_TESTS (known: rc ud256 ud1024 udbw wbw wbw0 wbwtc udp4791 udp4791tc udp5000)" >&2
        exit 2
        ;;
    esac
done
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
    printf '%s' "for t in ibv_ud_pingpong ibv_rc_pingpong ib_send_bw ib_write_bw show_gids python3; do command -v \$t >/dev/null 2>&1 || printf '%s ' \$t; done; test -x $ISO_UCX || printf 'ucx_perftest '; echo"
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

# gid_lines TEXT: the lines that name a GID (perftest prints local and remote address), at most four
gid_lines() {
    grep GID <<<"$1" | head -n 4 | cut -c1-90 | sed 's/^ */  GID line: /'
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
    local name=$1 srv=$2 cli=$3 port=$4 sg=${IDX[$2]} cg=${IDX[$3]} addr=${IP[$2]} base_s base_c xs xc tc rt recv send tos=""
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
    wbw | wbw0 | wbwtc)
        # wbw0 has no -x: perftest picks the GID itself; wbwtc sets the traffic class on both sides
        xs="-x $sg " xc="-x $cg " tc=""
        [[ $name == wbw0 ]] && xs="" xc=""
        [[ $name == wbwtc ]] && tc=" --tclass=$ISO_TCLASS"
        base_s="ib_write_bw -d $NIC -i 1 ${xs}-D 2 -s 65536 -p $port$tc"
        base_c="ib_write_bw -d $NIC -i 1 ${xc}-D 2 -s 65536 -p $port$tc $addr"
        ;;
    udp4791 | udp4791tc | udp5000)
        # plain UDP, python code without single quotes: the remote login shell reads it inside '...'.
        # The receiver exits 1 on a timeout (its own, before the outer one) and 2 on any other error
        # (a bind failure, say), printing the error; the sender binds the NIC address so the source IP is
        # the NIC's and sends 20 datagrams of 512 bytes, 0.1 s apart.
        rt=$((ISO_TIMEOUT > 3 ? ISO_TIMEOUT - 2 : 1))
        [[ $name == udp4791tc ]] && tos="s.setsockopt(socket.IPPROTO_IP,socket.IP_TOS,$ISO_TCLASS);"
        recv='import os,socket,sys;sys.excepthook=lambda t,v,tb:(print(t.__name__+": "+str(v)),sys.stdout.flush(),os._exit(1 if issubclass(t,socket.timeout) else 2));'
        recv+="s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout($rt);s.bind((\"$addr\",$port));"
        recv+='d,a=s.recvfrom(2048);print("got",len(d),"bytes from",a[0])'
        send="import socket,time;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind((\"${IP[$cli]}\",0));$tos"
        send+="[s.sendto(b\"x\"*512,(\"$addr\",$port)) or time.sleep(0.1) for _ in range(20)];"
        send+="print(\"sent 20 datagrams from\",s.getsockname()[0],\"to $addr:$port\")"
        base_s="python3 -c '$recv'"
        base_c="python3 -c '$send'"
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
    wbw | wbw0 | wbwtc) echo ib_write_bw ;;
    udp*) echo python3 ;;
    esac
}

# run_test KEY X NAME: server on X, client on A; KEY is ctrl or test
run_test() {
    local key=$1 x=$2 name=$3 label="$A-$2" why="" tool sfile sout cout srv_rc cli_rc spid verdict=OK port
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
    case $name in
    udp*) port=${name%tc} port=${port#udp} ;;
    *)
        PORT=$((PORT + 1))
        port=$PORT
        ;;
    esac
    commands "$name" "$x" "$A" "$port"
    say "$label $name (port $port)"
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
    [[ $name == wbw0 ]] && DETAIL+=("$(gid_lines "$sout")")
    DETAIL+=("client on $A, rc=$cli_rc$(note "$cli_rc"):")
    DETAIL+=("$(last2 "$cout" | sed 's/^/  /')")
    [[ $name == wbw0 ]] && DETAIL+=("$(gid_lines "$cout")")
}

# join ARGS...: the arguments separated by spaces
join() {
    local IFS=' '
    echo "$*"
}

level0
route_line "$CTRL"
route_line "$TEST"
for name in $ISO_TESTS; do
    run_test ctrl "$CTRL" "$name"
done
for name in $ISO_TESTS; do
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
    printf '%s\n' "${ROUTE_LINES[@]}"
    printf '%s\n' "${GID_LINES[@]}"
    echo "DETAIL"
    printf '%s\n' "${DETAIL[@]}"
} | nplb_paged net-isolate
[[ $VERDICT == "VERDICT: OK"* ]]
