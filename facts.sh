#!/usr/bin/env bash
# Runs on one node (diag1.sh starts it with srun).
#   facts.sh ports                 one line per RDMA port: port host dev state link netdev ipv4 numa pci
#   facts.sh routes "DEVS" "PEERS" for each local port in DEVS and each dev=address in PEERS:
#                                  route host ldev pdev OK|NO <ip route get ... oif <netdev> output>
#   facts.sh detail                addresses, rules and routes of this node (for the log)
SYS=${LAB_SYSFS:-/sys}
host=$(hostname -s)

netdev_of() {
    local d
    for d in "$SYS/class/infiniband/$1/device/net/"*; do
        [[ -e $d ]] && { basename "$d"; return; }
    done
    echo -
}

ipv4_of() {
    local a
    a=$(ip -4 -o addr show dev "$1" 2>/dev/null | awk '{print $4; exit}')
    echo "${a:--}"
}

case ${1:-} in
ports)
    for p in "$SYS"/class/infiniband/*/ports/1; do
        [[ -d $p ]] || continue
        dev=$(basename "$(dirname "$(dirname "$p")")")
        state=$(awk '{print $2}' "$p/state" 2>/dev/null)
        link=$(cat "$p/link_layer" 2>/dev/null)
        nd=$(netdev_of "$dev")
        numa=$(cat "$SYS/class/infiniband/$dev/device/numa_node" 2>/dev/null)
        pci=$(basename "$(readlink -f "$SYS/class/infiniband/$dev/device")")
        echo "port $host $dev ${state:--} ${link:--} $nd $(ipv4_of "$nd") ${numa:--} $pci"
    done
    ;;
routes)
    for ldev in $2; do
        nd=$(netdev_of "$ldev")
        for pair in $3; do
            pdev=${pair%%=*} addr=${pair#*=}
            addr=${addr%/*}
            if [[ $addr == - ]]; then
                echo "route $host $ldev $pdev NO no peer address"
            elif out=$(ip route get "$addr" oif "$nd" 2>&1); then
                echo "route $host $ldev $pdev OK $(echo "$out" | head -1)"
            else
                echo "route $host $ldev $pdev NO $(echo "$out" | head -1)"
            fi
        done
    done
    ;;
detail)
    echo "== $host ip -4 -br addr"; ip -4 -br addr 2>&1
    echo "== $host ip rule"; ip rule 2>&1
    echo "== $host ip -4 route show table all (no local/broadcast)"
    ip -4 route show table all 2>&1 | grep -v -E '^(local|broadcast) '
    ;;
*)
    echo "usage: facts.sh ports | routes \"DEVS\" \"dev=addr ...\" | detail" >&2
    exit 2
    ;;
esac
