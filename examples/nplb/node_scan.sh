#!/usr/bin/env bash
# Every Slurm node of every partition, to find spare GPU nodes beyond $PARTITION. Read-only.
#   nplb node scan [HOST...]
# With no HOST the nodes come from `sinfo -a -N` (all partitions). Each node is asked over ssh (BatchMode,
# 5 s connect timeout, all nodes in parallel) for its GPU count, its ACTIVE RDMA ports, whether /root/crab/CRAB
# is visible (the NFS checkout the runs need) and its 1-minute load. Stdout is the report, paged and saved as
# node-scan: "VERDICT: OK|WARN ...", then one line per node: host, partitions, Slurm state, ssh, gpus, rdma
# ports up, crab, load. A node that answers ssh with GPUs and RDMA ports up is a candidate: probe its leaf with
# `nplb topo HOST...` (set WORK to a scratch folder to keep the campaign's node_map.json) and its link with
# `nplb build try A B`. Exit 0 when at least one node answered, 1 when none did, 2 when there is no node list.
# Env: SSH (default ssh), SINFO (default sinfo), NPLB_REPORT_DIR.
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/pages.sh
. "$HERE/lib/pages.sh"
read -ra SSH_CMD <<<"${SSH:-ssh}"
SINFO=${SINFO:-sinfo}

note() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

# host -> "partition,partition" and "state" from sinfo (one line per node and partition)
declare -A PARTS STATE
if (($# > 0)); then
    HOSTS=("$@")
else
    HOSTS=()
    while read -r host part state; do
        [[ -z $host ]] && continue
        part=${part%\*}
        if [[ -z ${PARTS[$host]:-} ]]; then
            HOSTS+=("$host")
            PARTS[$host]=$part
            STATE[$host]=$state
        elif [[ ,${PARTS[$host]}, != *",$part,"* ]]; then
            PARTS[$host]+=",$part"
        fi
    done < <("$SINFO" -a -h -N -o '%N %P %t' 2>/dev/null | sort -u)
fi
if ((${#HOSTS[@]} == 0)); then
    echo "node_scan: no HOST given and sinfo lists no nodes" >&2
    exit 2
fi

# The remote probe prints one line: "gpus=N rdma=N crab=yes|no load=X"
PROBE='g=$(nvidia-smi -L 2>/dev/null | grep -c GPU); r=$(cat /sys/class/infiniband/*/ports/*/state 2>/dev/null | grep -c ACTIVE); c=no; [ -d /root/crab/CRAB ] && c=yes; l=$(cut -d" " -f1 /proc/loadavg); echo "gpus=$g rdma=$r crab=$c load=$l"'

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
note "asking ${#HOSTS[@]} nodes over ssh (5 s timeout each, in parallel)"
for host in "${HOSTS[@]}"; do
    "${SSH_CMD[@]}" -o BatchMode=yes -o ConnectTimeout=5 "$host" "$PROBE" >"$TMP/$host" 2>/dev/null </dev/null &
done
wait

UP=0
CAND=0
LINES=()
for host in "${HOSTS[@]}"; do
    out=$(grep -m1 '^gpus=' "$TMP/$host" 2>/dev/null)
    if [[ -n $out ]]; then
        UP=$((UP + 1))
        ssh_ok=yes
        read -r g r c l <<<"$out"
        g=${g#gpus=} r=${r#rdma=} c=${c#crab=} l=${l#load=}
        [[ $g != 0 && $r != 0 ]] && CAND=$((CAND + 1))
    else
        ssh_ok=no g=- r=- c=- l=-
    fi
    LINES+=("$(printf '%-12s %-24s %-6s %-3s %-4s %-4s %-4s %s' "$host" "${PARTS[$host]:--}" \
        "${STATE[$host]:--}" "$ssh_ok" "$g" "$r" "$c" "$l")")
done

{
    if ((UP == 0)); then
        echo "VERDICT: WARN none of ${#HOSTS[@]} nodes answered ssh"
    else
        echo "VERDICT: OK $UP of ${#HOSTS[@]} nodes answered ssh, $CAND with GPUs and RDMA ports up"
    fi
    printf '%-12s %-24s %-6s %-3s %-4s %-4s %-4s %s\n' host partitions state ssh gpus rdma crab load
    printf '%s\n' "${LINES[@]}"
    echo "next: nplb topo HOST... (leaf of each candidate), then nplb build try A B"
} | nplb_paged node-scan
((UP > 0))
