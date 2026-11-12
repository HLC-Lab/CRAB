#!/usr/bin/env bash
# Does plain IP traffic pass between two nodes, NIC by NIC?
#   nplb net ping HOST_A HOST_B
# For every UP netdev of HOST_A that has an IPv4 address (the one holding the default route is skipped),
# pings the address of the netdev of the same name on HOST_B from that netdev (2 packets, 3 s limit),
# then once more with a PING_BIG byte payload that must not be fragmented (RoCE sends 4 KiB packets, so
# a path that drops them while small pings pass is a MTU problem).
# Stdout is the report, paged and saved as net-ping; first content line "VERDICT: OK|FAIL ...", then one
# "OK|FAIL|FAIL-BIG|SKIP dev A-address -> B-address" line per netdev. Exit 0 when all pass, 1 otherwise,
# 2 for bad usage.
# Env: SSH (default ssh), PING_BIG (default 4096), NPLB_REPORT_DIR.
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/pages.sh
. "$HERE/lib/pages.sh"
read -ra SSH_CMD <<<"${SSH:-ssh}"

if [[ $# -ne 2 ]]; then
    echo "usage: nplb net ping HOST_A HOST_B" >&2
    exit 2
fi
A=$1
B=$2
BIG=${PING_BIG:-4096}

# ips_of HOST: "dev address" per UP netdev with a global IPv4 address
ips_of() {
    local idx dev fam addr rest
    "${SSH_CMD[@]}" "$1" 'ip -4 -o addr show up scope global' 2>/dev/null |
        while read -r idx dev fam addr rest; do
            echo "$dev ${addr%/*}"
        done
}

default_dev() {
    local dev
    dev=$("${SSH_CMD[@]}" "$1" 'ip route show default' 2>/dev/null | head -n 1 | cut -d' ' -f5)
    echo "$dev"
}

mapfile -t MINE < <(ips_of "$A")
mapfile -t THEIRS < <(ips_of "$B")
SKIPDEV=$(default_dev "$A")
if ((${#MINE[@]} == 0)); then
    echo "net_ping: no IPv4 netdev found on $A (ssh failed?)" >&2
    exit 2
fi

LINES=()
FAILS=0
TESTED=0
for entry in "${MINE[@]}"; do
    read -r dev addr <<<"$entry"
    [[ $dev == "$SKIPDEV" ]] && continue
    peer=""
    for other in "${THEIRS[@]}"; do
        read -r odev oaddr <<<"$other"
        [[ $odev == "$dev" ]] && peer=$oaddr
    done
    if [[ -z $peer ]]; then
        LINES+=("SKIP $dev $addr -> (no such netdev with an address on $B)")
        continue
    fi
    TESTED=$((TESTED + 1))
    printf '[%s] ping %s %s -> %s\n' "$(date +%H:%M:%S)" "$dev" "$addr" "$peer" >&2
    if ! "${SSH_CMD[@]}" "$A" "ping -c 2 -W 1 -w 3 -I $dev $peer" >/dev/null 2>&1 </dev/null; then
        LINES+=("FAIL $dev $addr -> $peer")
        FAILS=$((FAILS + 1))
    elif ! "${SSH_CMD[@]}" "$A" "ping -c 2 -W 1 -w 3 -M do -s $BIG -I $dev $peer" >/dev/null 2>&1 </dev/null; then
        LINES+=("FAIL-BIG $dev $addr -> $peer (a $BIG byte packet does not pass, small ones do)")
        FAILS=$((FAILS + 1))
    else
        LINES+=("OK $dev $addr -> $peer")
    fi
done

{
    if ((FAILS == 0)); then
        echo "VERDICT: OK $A reaches $B on all $TESTED NICs"
    else
        echo "VERDICT: FAIL $FAILS of $TESTED NICs of $A cannot reach $B"
    fi
    printf '%s\n' "${LINES[@]}"
} | nplb_paged net-ping
((FAILS == 0))
