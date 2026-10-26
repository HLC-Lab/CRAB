#!/usr/bin/env bash
# Runs on one node (diag1.sh starts it with srun).
#   facts.sh ports                 one line per RDMA port: port host dev state link netdev ipv4 numa pci
#   facts.sh routes "DEVS" "PEERS" for each local port in DEVS and each dev=address in PEERS:
#                                  route host ldev pdev OK <prefix> | NO <prefixes on that interface>
#   facts.sh table "DEVS"          per port: interface and the main-table routes on it
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

# the destinations (first field) of the main-table routes on an interface
prefixes_on() { ip -4 route show table main dev "$1" 2>/dev/null | awk '{print $1}'; }

# does address $1 fall in prefix $2 (default, a.b.c.d/n or a.b.c.d)
in_prefix() {
    awk -v a="$1" -v p="$2" '
        function num(x,  q) { split(x, q, "."); return ((q[1] * 256 + q[2]) * 256 + q[3]) * 256 + q[4] }
        BEGIN {
            if (p == "default") exit 0
            n = split(p, s, "/"); len = (n > 1) ? s[2] : 32; m = 2 ^ (32 - len)
            exit !(int(num(a) / m) == int(num(s[1]) / m))
        }'
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
                continue
            fi
            # a main-table route on this interface whose prefix holds the address (not
            # `ip route get ... oif`: with oif the kernel assumes on-link when nothing matches)
            hit=$(prefixes_on "$nd" | while read -r p; do
                in_prefix "$addr" "$p" && { echo "$p"; break; }
            done)
            if [[ -n $hit ]]; then
                echo "route $host $ldev $pdev OK $hit"
            else
                echo "route $host $ldev $pdev NO none of: $(prefixes_on "$nd" | tr '\n' ' ')"
            fi
        done
    done
    ;;
table)
    # per port: interface and the main-table routes on it
    for ldev in $2; do
        nd=$(netdev_of "$ldev")
        echo "table $host $ldev $nd $(prefixes_on "$nd" | tr '\n' ' ')"
    done
    ;;
detail)
    echo "== $host ip -4 -br addr"; ip -4 -br addr 2>&1
    echo "== $host ip rule"; ip rule 2>&1
    echo "== $host ip -4 route show table all (no local/broadcast)"
    ip -4 route show table all 2>&1 | grep -v -E '^(local|broadcast) '
    ;;
*)
    echo "usage: facts.sh ports | routes \"DEVS\" \"dev=addr ...\" | table \"DEVS\" | detail" >&2
    exit 2
    ;;
esac
