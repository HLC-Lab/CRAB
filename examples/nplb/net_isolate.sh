#!/usr/bin/env bash
# Why does UCX UD fail between node A and node TEST while it works between A and CTRL (the control pair)?
#   nplb net isolate A CTRL TEST [NIC]        e.g. nplb net isolate node110 node114 node116 mlx5_0
# Level 0: per node, the tools present and the GID table of NIC port 1; the RoCE v2 IPv4-mapped GID
# index of each node is picked and the IPv4 address decoded from it. Level 1: for the pairs A-CTRL and
# A-TEST, tests with the server on the peer and the client on A, the client connecting to the peer's NIC
# IPv4 address. A test passes when server and client both exit 0; a perftest
# test also needs a BW average above 0 in the client result line (the server one too for sbw, sbw64, udbw),
# since a run with zero iterations exits 0. Exit code 124
# means "timeout (nothing came back)". Tests (ISO_TESTS selects, in this order):
#   rc ud256 ud1024   ibv_rc_pingpong, ibv_ud_pingpong with 256 and 1024 byte messages
#   udbw              ib_send_bw -c UD
#   wbw               ib_write_bw with the RoCE v2 IPv4 GID index (-x)
#   wbw0              ib_write_bw without -x (which GID does perftest pick? DETAIL keeps its GID lines)
#   wbwtc             wbw with --tclass=ISO_TCLASS
#   udp4791 udp5000   plain UDP from A's NIC address to the peer's, no RDMA, on that port
#   udp4791tc         udp4791 with the sender's IP_TOS set to ISO_TCLASS
# Opt-in tests (not in the default ISO_TESTS):
#   udpsweep          UDP from ISO_SPORTS source ports (from ISO_SPORT_BASE) of A to port 5000 of the peer,
#                     to find source ports (ECMP hash inputs) that get lost; udpsweeprev: peer to A
#   rcrep, wbwrep     rc, wbw ISO_REPS times with fresh ports and ISO_REP_TIMEOUT; pass=k/N
#   wbw64             ib_write_bw -x idx with 64 byte messages
#   sbw, sbw64, rbw   ib_send_bw (RC SEND, 65536 and 64 bytes), ib_read_bw (RC READ), like wbw
#   rcmtu             rc with -m 4096
#   ttl               plain IP: ping from A's NIC address with TTL 1..ISO_TTL_MAX (default 6); the first
#                     TTL that gets a reply is the hop count (informational); DETAIL names the router
#                     that dropped the TTL 1 ping
#   ucxcfg            on A only, once: the RoCE-related lines of ucx_info -c (UCX lines in the report)
# ISO_TEST (singular) is read when ISO_TESTS is not set.
# ISO_COUNTERS=1: around every pair test (not ttl) read the NIC counters of port 1 (hw_counters, counters,
# and ethtool -S of the GID's netdev) on A and X before and after; DETAIL gets "  CNT host name +delta" lines
# (traffic and per-channel noise left out, error, pause, prio and rdma names kept, at most 40 per host) and a FAIL test
# gets a "  -> host: ..." line on page 1 with the receiver's most telling deltas.
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
# ISO_TEST (singular) is taken when ISO_TESTS is not set: a typo the operator makes once
ISO_TESTS=${ISO_TESTS:-${ISO_TEST:-rc ud256 ud1024 udbw wbw wbw0 wbwtc udp4791 udp4791tc udp5000}}
ISO_REPS=${ISO_REPS:-10}
ISO_REP_TIMEOUT=${ISO_REP_TIMEOUT:-6}
ISO_SPORT_BASE=${ISO_SPORT_BASE:-50000}
ISO_SPORTS=${ISO_SPORTS:-64}
ISO_TTL_MAX=${ISO_TTL_MAX:-6}
ISO_COUNTERS=${ISO_COUNTERS:-0}
KNOWN_TESTS="rc ud256 ud1024 udbw wbw wbw0 wbwtc udp4791 udp4791tc udp5000 udpsweep udpsweeprev rcrep wbwrep wbw64 ttl sbw rbw sbw64 rcmtu ucxcfg"

if [[ $# -ne 3 && $# -ne 4 ]]; then
    echo "usage: nplb net isolate A CTRL TEST [NIC]" >&2
    exit 2
fi
for t in $ISO_TESTS; do
    if [[ " $KNOWN_TESTS " != *" $t "* ]]; then
        echo "unknown test $t in ISO_TESTS (known: $KNOWN_TESTS)" >&2
        exit 2
    fi
done
A=$1
CTRL=$2
TEST=$3
NIC=${4:-mlx5_0}
HOSTS=("$A" "$CTRL" "$TEST")

declare -A IDX IP NDEV SKIPWHY MISSING
TOOL_LINES=()
GID_LINES=()
ROUTE_LINES=()
UCX_LINES=()
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
    printf '%s' "for t in ibv_ud_pingpong ibv_rc_pingpong ib_send_bw ib_write_bw ib_read_bw show_gids python3 ping; do command -v \$t >/dev/null 2>&1 || printf '%s ' \$t; done; test -x $ISO_UCX || printf 'ucx_perftest '; echo"
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
                NDEV[$host]=$ndev
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

# commands NAME SRV_HOST CLI_HOST PORT [TIMEOUT]: sets SRV_CMD and CLI_CMD (each side uses its own GID index)
commands() {
    local name=$1 srv=$2 cli=$3 port=$4 sg=${IDX[$2]} cg=${IDX[$3]} to=${5:-$ISO_TIMEOUT} addr=${IP[$2]} base_s base_c xs xc tc rt recv send tos="" bw sz
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
        rt=$((to > 3 ? to - 2 : 1))
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
    wbw64)
        base_s="ib_write_bw -d $NIC -i 1 -x $sg -D 2 -s 64 -p $port"
        base_c="ib_write_bw -d $NIC -i 1 -x $cg -D 2 -s 64 -p $port $addr"
        ;;
    sbw | sbw64 | rbw)
        # RC SEND (sbw), RC SEND with 64 byte messages (sbw64), RC READ (rbw): like wbw, another opcode
        bw=ib_send_bw sz=65536
        [[ $name == rbw ]] && bw=ib_read_bw
        [[ $name == sbw64 ]] && sz=64
        base_s="$bw -d $NIC -i 1 -x $sg -D 2 -s $sz -p $port"
        base_c="$bw -d $NIC -i 1 -x $cg -D 2 -s $sz -p $port $addr"
        ;;
    rcmtu)
        base_s="ibv_rc_pingpong -d $NIC -i 1 -g $sg -p $port -n 100 -m 4096"
        base_c="ibv_rc_pingpong -d $NIC -i 1 -g $cg -p $port -n 100 -m 4096 $addr"
        ;;
    udpsweep | udpsweeprev)
        # one datagram source port per socket, to see which source ports (ECMP hash inputs) get through.
        # The sender binds ISO_SPORTS source ports from ISO_SPORT_BASE on, one socket each (SO_REUSEADDR),
        # 3 datagrams of 512 bytes per port, 2 ms apart. The receiver binds port 5000 and collects until it
        # has every source port or the time is up, then prints "GOT [sorted source ports]".
        rt=$((to > 3 ? to - 2 : 1))
        recv='import os,select,socket,sys,time;sys.excepthook=lambda t,v,tb:(print(t.__name__+": "+str(v)),sys.stdout.flush(),os._exit(2));'
        recv+="s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind((\"$addr\",5000));seen=set();end=time.time()+$rt;"
        recv+="[seen.add(s.recvfrom(2048)[1][1]) if select.select([s],[],[],0.2)[0] else 0 for _ in iter(lambda:len(seen)<$ISO_SPORTS and time.time()<end,False)];"
        recv+='print("GOT",sorted(seen))'
        send="import socket,time;[(lambda s:(s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1),s.bind((\"${IP[$cli]}\",p)),"
        send+="[(s.sendto(b\"x\"*512,(\"$addr\",5000)),time.sleep(0.002)) for _ in range(3)],s.close()))(socket.socket(socket.AF_INET,socket.SOCK_DGRAM)) "
        send+="for p in range($ISO_SPORT_BASE,$((ISO_SPORT_BASE + ISO_SPORTS)))];"
        send+="print(\"sent\",$((3 * ISO_SPORTS)),\"datagrams from ports $ISO_SPORT_BASE-$((ISO_SPORT_BASE + ISO_SPORTS - 1)) to $addr:5000\")"
        base_s="python3 -c '$recv'"
        base_c="python3 -c '$send'"
        ;;
    esac
    SRV_CMD="timeout $to $base_s"
    CLI_CMD="timeout $to $base_c"
}

# needs NAME: the tool a test uses
needs() {
    case $1 in
    rc | rcrep | rcmtu) echo ibv_rc_pingpong ;;
    ud256 | ud1024) echo ibv_ud_pingpong ;;
    udbw) echo ib_send_bw ;;
    wbw | wbw0 | wbwtc | wbwrep | wbw64) echo ib_write_bw ;;
    sbw | sbw64) echo ib_send_bw ;;
    rbw) echo ib_read_bw ;;
    ttl) echo ping ;;
    udp*) echo python3 ;;
    esac
}

# execute NAME SRV_HOST CLI_HOST PORT TIMEOUT: the server on SRV_HOST in the background, then the client
# on CLI_HOST; sets EX_SRV_RC, EX_CLI_RC, EX_SOUT (server output) and EX_COUT (client output)
execute() {
    local sfile spid
    commands "$1" "$2" "$3" "$4" "$5"
    sfile=$(mktemp)
    "${SSH_CMD[@]}" "$2" "$SRV_CMD" >"$sfile" 2>&1 </dev/null &
    spid=$!
    sleep "$ISO_SETTLE"
    EX_COUT=$(remote "$3" "$CLI_CMD")
    EX_CLI_RC=$?
    wait "$spid"
    EX_SRV_RC=$?
    EX_SOUT=$(cat "$sfile")
    rm -f "$sfile"
}

# tally KEY NAME VERDICT: count one result for the verdict; KEY is ctrl or test
tally() {
    if [[ $3 == FAIL ]]; then
        if [[ $1 == ctrl ]]; then
            FAILED_CTRL+=("$2")
        else
            FAILED_TEST+=("$2")
        fi
    elif [[ $1 == ctrl ]]; then
        OK_CTRL=$((OK_CTRL + 1))
    else
        OK_TEST=$((OK_TEST + 1))
    fi
}

# remote command (plain sh): "name value" for every file of hw_counters/ and counters/ of port 1, then
# "@name value" for the numeric lines of ethtool -S NETDEV when the netdev of HOST is known
snap_cmd() {
    local p=/sys/class/infiniband/$NIC/ports/1 nd=${NDEV[$1]:-} cmd
    cmd='for f in @P@/hw_counters/* @P@/counters/*; do [ -f "$f" ] && printf "%s %s\n" "${f##*/}" "$(cat "$f" 2>/dev/null)"; done'
    cmd=${cmd//@P@/$p}
    if [[ -n $nd ]]; then
        cmd+="; ethtool -S $nd 2>/dev/null | sed -n 's/^ *\\([A-Za-z0-9_.-]*\\): *\\([0-9][0-9]*\\) *\$/@\\1 \\2/p'"
    fi
    printf '%s; true' "$cmd"
}

# cnt_snap before|after X: with ISO_COUNTERS=1, one snapshot of A and one of X (one ssh call each); a call
# that fails leaves UNREADABLE in the file
cnt_snap() {
    local w=$1 x=$2 k h
    [[ $ISO_COUNTERS == 1 ]] || return 0
    for k in a x; do
        h=$A
        [[ $k == x ]] && h=$x
        remote "$h" "$(snap_cmd "$h")" >"$CNT_DIR/$w.$k" || echo UNREADABLE >"$CNT_DIR/$w.$k"
    done
}

# cnt_deltas BEFORE AFTER: "name delta" lines sorted by name, or UNREADABLE. Only increases are shown. Byte
# and packet traffic counters are left out (port_xmit_data port_rcv_data port_xmit_packets port_rcv_packets,
# ethtool names with _bytes) and per-channel and other ethtool noise (ch*_, rx<N>_, tx<N>_, _cache_, csum,
# _xdp, _lro, _gro, _tso, _wake, _arm, _poll, _events) are left out, unless the name also says discard drop
# err seq nak retry timeout rnr duplicate icrc cnp ecn oos out_of pause prio pfc rdma roce buffer storm.
cnt_deltas() {
    if grep -q UNREADABLE "$1" "$2"; then
        echo UNREADABLE
        return 0
    fi
    awk '
        NR == FNR { b[$1] = $2; next }
        {
            n = $1
            if (!(n in b) || $2 !~ /^[0-9]+$/ || b[n] !~ /^[0-9]+$/) next
            d = $2 - b[n]
            if (d <= 0) next
            eth = (substr(n, 1, 1) == "@")
            if (eth) n = substr(n, 2)
            keep = (n ~ /discard|drop|err|seq|nak|retry|timeout|rnr|duplicate|icrc|cnp|ecn|oos|out_of|pause|prio|pfc|rdma|roce|buffer|storm/)
            traffic = (n == "port_xmit_data" || n == "port_rcv_data" || n == "port_xmit_packets" || n == "port_rcv_packets")
            bytes = (eth && n ~ /_bytes/)
            noise = (n ~ /^ch[0-9]*_|^ch_|^rx[0-9]+_|^tx[0-9]+_|_cache_|csum|_xdp|_lro|_gro|_tso|_wake|_arm|_poll|_events/)
            if (keep || !(traffic || bytes || noise)) printf "%s %.0f\n", n, d
        }' "$1" "$2" | LC_ALL=C sort
}

# cnt_after X: the second snapshot, then CNT_A and CNT_X (cnt_deltas of A and of X)
cnt_after() {
    [[ $ISO_COUNTERS == 1 ]] || return 0
    cnt_snap after "$1"
    CNT_A=$(cnt_deltas "$CNT_DIR/before.a" "$CNT_DIR/after.a")
    CNT_X=$(cnt_deltas "$CNT_DIR/before.x" "$CNT_DIR/after.x")
}

# cnt_emit HOST RAW: the DETAIL lines "  CNT host name +delta", at most 40 per host
cnt_emit() {
    local h=$1 raw=$2 k=0 name delta
    [[ $ISO_COUNTERS == 1 ]] || return 0
    if [[ $raw == UNREADABLE ]]; then
        DETAIL+=("  CNT $h unreadable")
        return 0
    fi
    if [[ -z $raw ]]; then
        DETAIL+=("  CNT $h (no change)")
        return 0
    fi
    while read -r name delta; do
        k=$((k + 1))
        if ((k <= 40)); then
            DETAIL+=("  CNT $h $name +$delta")
        fi
    done <<<"$raw"
    if ((k > 40)); then
        DETAIL+=("  CNT $h (+$((k - 40)) more)")
    fi
}

# cnt_summary HOST RAW: the page 1 line after a FAIL test: up to four most telling deltas of the receiver
# (error-like names by size, else the rdma unicast packet counters)
cnt_summary() {
    local h=$1 raw=$2 top
    [[ $ISO_COUNTERS == 1 ]] || return 0
    if [[ $raw == UNREADABLE ]]; then
        TEST_LINES+=("  -> $h: counters unreadable")
        return 0
    fi
    # error-like names first (largest delta first), then pause, prio and rdma names, four in all
    top=$(awk '
        $1 ~ /discard|drop|err|seq|nak|retry|rnr|icrc|oos|out_of/ { print 1, $2, $1; next }
        $1 ~ /pause|prio|rdma/ { print 2, $2, $1 }' <<<"$raw" | sort -k1,1n -k2,2nr -k3,3 | head -n 4 | awk '{ printf "%s%s+%s", (NR > 1 ? " " : ""), $3, $2 }')
    TEST_LINES+=("  -> $h: ${top:-no error counter moved}")
}

# bw_average TEXT: the BW average (4th field, as printed) of the perftest result line of TEXT: a line of a
# numeric message size, then iterations, BW peak, BW average and MsgRate; "none" when there is no such line
bw_average() {
    awk 'function num(s) { return s ~ /^[0-9]+(\.[0-9]+)?$/ }
        $1 ~ /^[0-9]+$/ && num($2) && num($3) && num($4) && num($5) { print $4; found = 1; exit }
        END { if (!found) print "none" }' <<<"$1"
}

# positive VALUE: true when VALUE is a number above zero
positive() {
    awk -v v="$1" 'BEGIN { exit !(v ~ /^[0-9.]+$/ && v + 0 > 0) }'
}

# perftest_check NAME SERVER_OUTPUT CLIENT_OUTPUT: for the perftest tests, a run that exits 0 still fails
# when the client has no result line with a BW average above 0 (zero iterations is a false pass), and, for
# the send tests, when the server has none either. Sets PT_OK (1 or 0) and PT_BW (the client's BW average
# as printed, 0 when the check failed; empty for a test that is no perftest) and PT_CLI, PT_SRV (the two
# averages, "none" when missing).
perftest_check() {
    PT_OK=1 PT_BW="" PT_CLI="" PT_SRV=""
    case $1 in
    wbw | wbw0 | wbwtc | wbw64 | sbw | sbw64 | rbw | udbw) ;;
    *) return 0 ;;
    esac
    PT_CLI=$(bw_average "$3")
    PT_SRV=$(bw_average "$2")
    positive "$PT_CLI" || PT_OK=0
    case $1 in
    sbw | sbw64 | udbw) positive "$PT_SRV" || PT_OK=0 ;;
    esac
    PT_BW=$PT_CLI
    ((PT_OK)) || PT_BW=0
}

# sweep_check SERVER_OUTPUT: reads the receiver's "GOT [ports]" line; sets SWEEP_NLOST (source ports
# of the sweep that never arrived) and SWEEP_LIST (those ports, cut at 40)
sweep_check() {
    local got lost
    local -a l
    got=$(grep -m 1 '^GOT' <<<"$1" | tr -d '[],' | sed 's/^GOT//')
    lost=$(awk -v b="$ISO_SPORT_BASE" -v n="$ISO_SPORTS" -v g="$got" 'BEGIN { m = split(g, a, " "); for (i = 1; i <= m; i++) seen[a[i]] = 1; for (p = b; p < b + n; p++) if (!(p in seen)) printf "%d ", p }')
    read -ra l <<<"$lost"
    SWEEP_NLOST=${#l[@]}
    SWEEP_LIST="${l[*]:0:40}" SWEEP_MORE=""
    if ((SWEEP_NLOST > 40)); then
        SWEEP_MORE="(+$((SWEEP_NLOST - 40)) more)"
    fi
}

# run_reps KEY X NAME LABEL: the base test (rcrep: rc, wbwrep: wbw) ISO_REPS times, each with a fresh port
# and the shorter ISO_REP_TIMEOUT; passes only when every run does
run_reps() {
    cnt_snap before "$2"
    local key=$1 x=$2 name=$3 label=$4 base=${3%rep} i pass=0 runs="" verdict=OK first=0 fs="" fc=""
    for ((i = 1; i <= ISO_REPS; i++)); do
        PORT=$((PORT + 1))
        say "$label $name $i/$ISO_REPS (port $PORT)"
        execute "$base" "$x" "$A" "$PORT" "$ISO_REP_TIMEOUT"
        perftest_check "$base" "$EX_SOUT" "$EX_COUT"
        runs+=" $EX_SRV_RC/$EX_CLI_RC${PT_BW:+/bw=$PT_BW}"
        if ((EX_SRV_RC == 0 && EX_CLI_RC == 0 && PT_OK == 1)); then
            pass=$((pass + 1))
        elif ((first == 0)); then
            first=$i fs=$EX_SOUT fc=$EX_COUT
        fi
    done
    cnt_after "$x"
    ((pass == ISO_REPS)) || verdict=FAIL
    tally "$key" "$name" "$verdict"
    TEST_LINES+=("$verdict $label $NIC $name pass=$pass/$ISO_REPS")
    [[ $verdict == FAIL ]] && cnt_summary "$x" "${CNT_X:-}"
    DETAIL+=("== $label $name: $verdict")
    DETAIL+=("runs (server rc/client rc[/bw average]):$runs")
    cnt_emit "$A" "${CNT_A:-}"
    cnt_emit "$x" "${CNT_X:-}"
    if ((first > 0)); then
        DETAIL+=("first failure, run $first, server on $x:")
        DETAIL+=("$(last2 "$fs" | sed 's/^/  /')")
        DETAIL+=("first failure, run $first, client on $A:")
        DETAIL+=("$(last2 "$fc" | sed 's/^/  /')")
    fi
}

# run_ttl KEY X LABEL: plain IP, on A only: ping X from A's NIC address with TTL 1, 2, ... up to
# ISO_TTL_MAX; the first TTL that gets a reply is the hop count. Informational: it passes whenever some TTL
# replies. DETAIL keeps the "Time to live exceeded"/"From" lines of the TTL 1 ping, which name the router
# that dropped it.
run_ttl() {
    local key=$1 x=$2 label=$3 t out rc first=0 verdict=OK line="" seen=""
    for ((t = 1; t <= ISO_TTL_MAX; t++)); do
        say "$label ttl $t"
        out=$(remote "$A" "ping -c 2 -W 1 -t $t -I ${IP[$A]} ${IP[$x]}")
        rc=$?
        if ((t == 1)); then
            line=$(grep -E 'Time to live exceeded|From' <<<"$out" | head -n 2 | cut -c1-90 | sed 's/^/  /')
        fi
        if ((rc == 0)); then
            first=$t
            break
        fi
        seen+=" $t=no reply"
    done
    if ((first > 0)); then
        TEST_LINES+=("OK $label $NIC ttl first_reply_ttl=$first")
    else
        verdict=FAIL
        TEST_LINES+=("FAIL $label $NIC ttl no reply up to ttl $ISO_TTL_MAX")
    fi
    tally "$key" ttl "$verdict"
    DETAIL+=("== $label ttl: $verdict")
    DETAIL+=("ping from $A (${IP[$A]}) to $x (${IP[$x]}), TTL 1..$ISO_TTL_MAX:$seen")
    DETAIL+=("TTL 1 answer:")
    DETAIL+=("${line:-  (none)}")
}

# run_ucxcfg: when ISO_TESTS lists ucxcfg, the UCX settings of A that bear on RoCE (hop limit, traffic class,
# GID index, SL, address type), from ucx_info -c next to ISO_UCX, as UCX lines in the report body
run_ucxcfg() {
    local out line
    [[ " $ISO_TESTS " == *" ucxcfg "* ]] || return 0
    say "ucx_info -c on $A"
    out=$(remote "$A" "u=\$(dirname $ISO_UCX)/ucx_info; if [ -x \$u ]; then \$u -c; else echo UCX_INFO_MISSING; fi")
    if [[ $out == *UCX_INFO_MISSING* ]]; then
        UCX_LINES+=("UCX ucx_info missing")
        return 0
    fi
    out=$(grep -iE 'HOP_LIMIT|TRAFFIC_CLASS|GID_INDEX|ROCE|_SL=|ADDR_TYPE' <<<"$out" | head -n 12 | cut -c1-90)
    if [[ -z $out ]]; then
        UCX_LINES+=("UCX no matching line in ucx_info -c")
    else
        while IFS= read -r line; do
            UCX_LINES+=("UCX $line")
        done <<<"$out"
    fi
}

# run_test KEY X NAME: server on X, client on A (udpsweeprev: receiver on A, sender on X); KEY is ctrl or test
run_test() {
    local key=$1 x=$2 name=$3 label="$A-$2" why="" tool verdict=OK port sh=$2 ch=$A extra=""
    [[ $name == ucxcfg ]] && return # not a pair test: run_ucxcfg lists it once
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
    if [[ $name == ttl ]]; then
        run_ttl "$key" "$x" "$label"
        return
    fi
    if [[ $name == rcrep || $name == wbwrep ]]; then
        run_reps "$key" "$x" "$name" "$label"
        return
    fi
    if [[ $name == udpsweeprev ]]; then
        sh=$A ch=$x
    fi
    case $name in
    udpsweep*) port=5000 ;;
    udp*) port=${name%tc} port=${port#udp} ;;
    *)
        PORT=$((PORT + 1))
        port=$PORT
        ;;
    esac
    say "$label $name (port $port)"
    cnt_snap before "$x"
    execute "$name" "$sh" "$ch" "$port" "$ISO_TIMEOUT"
    cnt_after "$x"
    perftest_check "$name" "$EX_SOUT" "$EX_COUT"
    if ((EX_SRV_RC != 0 || EX_CLI_RC != 0 || PT_OK == 0)); then
        verdict=FAIL
    fi
    if [[ $name == udpsweep || $name == udpsweeprev ]]; then
        sweep_check "$EX_SOUT"
        extra=" delivered=$((ISO_SPORTS - SWEEP_NLOST))/$ISO_SPORTS"
        ((SWEEP_NLOST == 0)) || verdict=FAIL
    fi
    tally "$key" "$name" "$verdict"
    TEST_LINES+=("$verdict $label $NIC $name srv=$EX_SRV_RC cli=$EX_CLI_RC$extra${PT_BW:+ bw=$PT_BW}")
    if [[ $verdict == FAIL ]]; then
        if [[ $sh == "$A" ]]; then cnt_summary "$sh" "${CNT_A:-}"; else cnt_summary "$sh" "${CNT_X:-}"; fi
    fi
    DETAIL+=("== $label $name: $verdict")
    DETAIL+=("server on $sh, rc=$EX_SRV_RC$(note "$EX_SRV_RC"):")
    DETAIL+=("$(last2 "$EX_SOUT" | sed 's/^/  /')")
    [[ $name == wbw0 ]] && DETAIL+=("$(gid_lines "$EX_SOUT")")
    DETAIL+=("client on $ch, rc=$EX_CLI_RC$(note "$EX_CLI_RC"):")
    DETAIL+=("$(last2 "$EX_COUT" | sed 's/^/  /')")
    [[ $name == wbw0 ]] && DETAIL+=("$(gid_lines "$EX_COUT")")
    [[ -z $PT_CLI ]] || DETAIL+=("BW average: client $PT_CLI, server $PT_SRV (a send test needs both above 0)")
    cnt_emit "$A" "${CNT_A:-}"
    cnt_emit "$x" "${CNT_X:-}"
    if [[ -n $extra ]]; then
        # wrapped: a page line is cut at 100 columns
        DETAIL+=("$(fold -s -w 88 <<<"lost sports: ${SWEEP_LIST:-none}" | sed '2,$s/^/  /')")
        [[ -z $SWEEP_MORE ]] || DETAIL+=("  $SWEEP_MORE")
    fi
}

# join ARGS...: the arguments separated by spaces
join() {
    local IFS=' '
    echo "$*"
}

if [[ $ISO_COUNTERS == 1 ]]; then
    CNT_DIR=$(mktemp -d)
    trap 'rm -rf "$CNT_DIR"' EXIT
fi
level0
route_line "$CTRL"
route_line "$TEST"
run_ucxcfg
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
    ((${#UCX_LINES[@]} == 0)) || printf '%s\n' "${UCX_LINES[@]}"
    printf '%s\n' "${GID_LINES[@]}"
    echo "DETAIL"
    printf '%s\n' "${DETAIL[@]}"
} | nplb_paged net-isolate
[[ $VERDICT == "VERDICT: OK"* ]]
