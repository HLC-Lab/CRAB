#!/usr/bin/env bash
# Read-only topology probe. Run it on the node that holds the CRAB checkout:
#   examples/nplb/topology.sh [HOST...]
# With no HOST, the nodes of $PARTITION (sinfo). Over ssh, in parallel, every node reports each of
# its RDMA ports (state, rate, netdev, speed, MAC, bond, IPv4, RoCE v2 GID index, PCI, NUMA) and
# listens for one LLDP frame per netdev (tcpdump), which names the leaf switch and port behind
# it. All raw lines go to $WORK/topology_<time>.tsv; the screen gets a summary of at most 40
# lines x 120 columns (grid of devices x nodes, then pods, victim pair, ports, cards, LLDP).
# Nothing is changed on any node or switch: it only reads sysfs and listens.
# Exit status: 0 when every node answered, 1 when a node failed, 2 when the setup is missing
# (no nodes, no sinfo, no python).
# Env: PARTITION (h100_4nodes), WORK (<checkout>/local/nplb-work), TOPO_SSH (default ssh),
#      LLDP_WAIT (seconds to wait for a frame per netdev, default 40; switches send one every
#      30 s), SYSFS (default /sys), PYTHON (default python3).
set -uo pipefail

usage() { sed -n '2,/^[^#]/{/^#/p}' "$0" | sed 's/^# \{0,1\}//' >&2; }

case ${1:-} in
    -h | --help)
        usage
        exit 0
        ;;
esac

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CHECKOUT=$(cd "$HERE/../.." && pwd)
PARTITION=${PARTITION:-h100_4nodes}
WORK=${WORK:-$CHECKOUT/local/nplb-work}
LLDP_WAIT=${LLDP_WAIT:-40}
SYSFS=${SYSFS:-/sys}
PYTHON=${PYTHON:-python3}
read -ra SSH <<<"${TOPO_SSH:-ssh}"

HOSTS=("$@")
if ((${#HOSTS[@]} == 0)); then
    if ! command -v sinfo >/dev/null; then
        echo "topology: no HOST given and no sinfo to list the nodes of $PARTITION" >&2
        exit 2
    fi
    mapfile -t HOSTS < <(sinfo -h -N -p "$PARTITION" -o %N | sort -u)
    if ((${#HOSTS[@]} == 0)); then
        echo "topology: sinfo lists no nodes in partition $PARTITION" >&2
        exit 2
    fi
fi
if ! command -v "$PYTHON" >/dev/null; then
    echo "topology: no $PYTHON to render the summary" >&2
    exit 2
fi
mkdir -p "$WORK" || {
    echo "topology: cannot create $WORK" >&2
    exit 2
}

# ---- the script each host runs ---------------------------------------------------------------
# Positional args: host sysfs lldp_wait. It prints one TSV line per RDMA port (17 fields, see
# HEADER below) and "#WARN <tab> host <tab> text" records for what it could not do.
REMOTE=$(
    cat <<'REMOTE_SCRIPT'
set -u
host=$1 sysfs=$2 wait_s=$3
tab=$'\t'

clean() { local v=${1//$'\t'/ }; v=${v//$'\n'/ }; v=${v//$'\r'/}; printf '%s' "${v:--}"; }
rd() { local v=; { read -r v <"$1"; } 2>/dev/null; clean "$v"; }
warn() { printf '#WARN\t%s\t%s\n' "$host" "$1"; }

tmp=$(mktemp -d) || exit 1
trap 'rm -rf "$tmp"' EXIT

lines=()
netdevs=()
for devdir in "$sysfs"/class/infiniband/mlx5_*; do
    [[ -d $devdir ]] || continue
    dev=${devdir##*/}
    nd=-
    for n in "$devdir"/device/net/*; do
        [[ -e $n ]] && { nd=${n##*/}; break; }
    done
    pci=-
    [[ -e $devdir/device ]] && pci=$(readlink -f "$devdir/device" 2>/dev/null)
    pci=$(clean "${pci##*/}")
    numa=$(rd "$devdir/device/numa_node")
    oper=- speed=- mac=- bond=- ip4=-
    if [[ $nd != - ]]; then
        netdevs+=("$nd")
        net=$sysfs/class/net/$nd
        oper=$(rd "$net/operstate")
        speed=$(rd "$net/speed")
        mac=$(rd "$net/address")
        if [[ -L $net/master ]]; then
            master=$(readlink "$net/master" 2>/dev/null)
            bond=$(clean "${master##*/}")
        fi
        if command -v ip >/dev/null; then
            addr=$(ip -4 -o addr show dev "$nd" 2>/dev/null)
            addr=${addr%%$'\n'*}
            read -ra words <<<"$addr"
            for ((w = 0; w + 1 < ${#words[@]}; w++)); do
                [[ ${words[w]} == inet ]] && { ip4=${words[w + 1]}; break; }
            done
        fi
    fi
    for pdir in "$devdir"/ports/*; do
        [[ -d $pdir ]] || continue
        port=${pdir##*/}
        gid=-
        for ((i = 0; i < 512; i++)); do
            [[ -e $pdir/gid_attrs/types/$i ]] || break
            [[ $(rd "$pdir/gid_attrs/types/$i") == "RoCE v2" ]] || continue
            g=$(rd "$pdir/gids/$i")
            if [[ $g != - && ! $g =~ ^[0:]*$ ]]; then
                gid=$i
                break
            fi
        done
        lines+=("$(printf '%s\t' "$host" "$dev" "$port" "$(rd "$pdir/state")" "$(rd "$pdir/rate")" \
            "$(rd "$pdir/link_layer")" "$nd" "$oper" "$speed" "$mac" "$bond" "$ip4" "$gid" "$pci" \
            "$numa")${nd}")
    done
done

if ((${#lines[@]} == 0)); then
    warn "no mlx5 device under $sysfs/class/infiniband"
    exit 0
fi

# one LLDP frame per netdev, all netdevs listening at once
can_listen=1
if ! command -v tcpdump >/dev/null; then
    warn "tcpdump not found, no LLDP from this host"
    can_listen=0
elif ! command -v timeout >/dev/null; then
    warn "timeout not found, no LLDP from this host"
    can_listen=0
fi
if ((can_listen)); then
    for nd in "${netdevs[@]}"; do
        [[ -e $tmp/$nd.out ]] && continue
        : >"$tmp/$nd.out"
        timeout -k 2 "$wait_s" tcpdump -i "$nd" -nn -v -c 1 'ether proto 0x88cc' \
            >"$tmp/$nd.out" 2>"$tmp/$nd.err" &
    done
    wait
    for nd in "${netdevs[@]}"; do
        err=
        [[ -r $tmp/$nd.err ]] && err=$(<"$tmp/$nd.err")
        if [[ $err == *[Pp]ermission* || $err == *"not permitted"* ]]; then
            warn "tcpdump: permission denied (LLDP needs root)"
            break
        fi
    done
fi

# the second last field is the LLDP system name, the last one the port ID
for line in "${lines[@]}"; do
    nd=${line##*"$tab"}
    sys=- lport=-
    if [[ -s $tmp/$nd.out ]]; then
        sys=$(sed -n 's/^[[:space:]]*System Name TLV[^:]*: *//p' "$tmp/$nd.out" | sed -n 1p)
        lport=$(sed -n 's/^[[:space:]]*Subtype Interface Name (5): *//p' "$tmp/$nd.out" | sed -n 1p)
        [[ -n $lport ]] || lport=$(sed -n 's/^[[:space:]]*Subtype Local (7): *//p' "$tmp/$nd.out" | sed -n 1p)
        [[ -n $lport ]] || lport=$(sed -n 's/^[[:space:]]*Port Description TLV[^:]*: *//p' "$tmp/$nd.out" | sed -n 1p)
        sys=$(clean "${sys%"${sys##*[![:space:]]}"}")
        lport=$(clean "${lport%"${lport##*[![:space:]]}"}")
    fi
    printf '%s\t%s\t%s\n' "${line%"$tab$nd"}" "$sys" "$lport"
done
exit 0
REMOTE_SCRIPT
)

tab=$'\t'
HEADER="host${tab}dev${tab}port${tab}state${tab}rate${tab}link_layer${tab}netdev${tab}operstate${tab}speed_mbps${tab}mac${tab}bond${tab}ipv4${tab}gid${tab}pci${tab}numa${tab}lldp_sys${tab}lldp_port"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# ---- one ssh per host, all at once -------------------------------------------------------------
for i in "${!HOSTS[@]}"; do
    host=${HOSTS[i]}
    # ssh joins its arguments into one remote command line: quote each value for the remote shell
    remote_cmd=$(printf '%q ' bash -s -- "$host" "$SYSFS" "$LLDP_WAIT")
    (
        "${SSH[@]}" -o BatchMode=yes -o ConnectTimeout=5 "$host" "$remote_cmd" \
            <<<"$REMOTE" >"$TMP/$i.out" 2>"$TMP/$i.err"
        echo $? >"$TMP/$i.rc"
    ) &
done
wait

TS=$(date +%Y%m%d-%H%M%S)
TSV=$WORK/topology_$TS.tsv
fails=0
{
    printf '%s\n' "$HEADER"
    for i in "${!HOSTS[@]}"; do
        host=${HOSTS[i]}
        rc=$(<"$TMP/$i.rc")
        if [[ $rc != 0 ]]; then
            detail=$(tail -n 1 "$TMP/$i.err")
            detail=${detail//$'\t'/ }
            printf '#FAIL\t%s\tssh exited %s: %s\n' "$host" "$rc" "${detail:-no message}"
            fails=$((fails + 1))
        elif [[ ! -s $TMP/$i.out ]]; then
            printf '#FAIL\t%s\tno output from the remote probe\n' "$host"
            fails=$((fails + 1))
        else
            cat "$TMP/$i.out"
        fi
    done
} >"$TSV"

"$PYTHON" "$HERE/topology_summary.py" "$TSV" --log-label "$TSV" || exit 2
((fails == 0))
