#!/usr/bin/env bash
# Leftover benchmark processes on the nodes: they send traffic and congest the fabric.
#   nplb node procs [HOST...]       read-only: list the Blink, perftest, mpirun and orted processes on each node
#   nplb node stop [HOST...]        stop them (SIGTERM, then SIGKILL after 3 s) after "Stop N processes? [y/N]"
# With no HOST the nodes of $PARTITION (sinfo) are used. Stdout of `procs` is the report, paged and saved as
# node-procs; its first content line is "VERDICT: OK|WARN ...", then one "host pid age command" line per
# process (the command cut at 70 columns). `stop` prints one result line. A process is matched when its command
# line has blink-gpu/build-cuda/bin, ib_write_bw, ib_send_bw, orted or mpirun, so stop it only when no run of
# yours is meant to be going. Exit 0 (procs: nothing found, stop: all gone), 1 otherwise, 2 for bad usage.
# Env: SSH (default ssh), PARTITION (h100_4nodes), NPLB_YES=1 (no question), NPLB_REPORT_DIR.
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib/pages.sh
. "$HERE/lib/pages.sh"
read -ra SSH_CMD <<<"${SSH:-ssh}"
PARTITION=${PARTITION:-h100_4nodes}
PATTERN='[b]link-gpu/build-cuda/bin|[i]b_write_bw|[i]b_send_bw|[o]rted|[m]pirun'

note() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

sub=${1:-}
[[ $# -gt 0 ]] && shift
case $sub in
    procs | stop) ;;
    *)
        echo "usage: nplb node procs [HOST...] | nplb node stop [HOST...]" >&2
        exit 2
        ;;
esac
HOSTS=("$@")
if ((${#HOSTS[@]} == 0)); then
    mapfile -t HOSTS < <(sinfo -h -p "$PARTITION" -N -o %n 2>/dev/null | sort -u)
fi
if ((${#HOSTS[@]} == 0)); then
    echo "node_procs: no HOST given and sinfo lists no nodes in $PARTITION" >&2
    exit 2
fi

# found: "host pid age command" lines of all hosts, this script's own ssh excluded by the [x] pattern
FOUND=()
for host in "${HOSTS[@]}"; do
    note "listing on $host"
    while IFS= read -r line; do
        [[ -n $line ]] && FOUND+=("$host $line")
    done < <("${SSH_CMD[@]}" "$host" "ps -eo pid,etimes,args | grep -E '$PATTERN'" 2>/dev/null </dev/null)
done

if [[ $sub == procs ]]; then
    {
        if ((${#FOUND[@]} == 0)); then
            echo "VERDICT: OK no leftover benchmark process on ${HOSTS[*]}"
        else
            echo "VERDICT: WARN ${#FOUND[@]} benchmark processes on ${HOSTS[*]}: they can congest the fabric"
            echo "host pid age(s) command"
            for entry in "${FOUND[@]}"; do
                echo "${entry:0:96}"
            done
        fi
    } | nplb_paged node-procs
    ((${#FOUND[@]} == 0))
    exit
fi

if ((${#FOUND[@]} == 0)); then
    echo "nothing to stop on ${HOSTS[*]}"
    exit 0
fi
printf '%s\n' "${FOUND[@]}" | cut -c1-96 >&2
if [[ ${NPLB_YES:-0} != 1 ]]; then
    read -r -p "Stop ${#FOUND[@]} processes? [y/N] " answer
    if [[ $answer != y && $answer != Y ]]; then
        echo "declined: nothing changed"
        exit 1
    fi
fi
for host in "${HOSTS[@]}"; do
    "${SSH_CMD[@]}" "$host" "pkill -TERM -f '$PATTERN'; sleep 3; pkill -KILL -f '$PATTERN'" >/dev/null 2>&1 </dev/null
done
LEFT=0
for host in "${HOSTS[@]}"; do
    n=$("${SSH_CMD[@]}" "$host" "ps -eo args | grep -c -E '$PATTERN'" 2>/dev/null </dev/null)
    LEFT=$((LEFT + ${n:-0}))
done
echo "stopped: ${#FOUND[@]} processes sent a signal, $LEFT left on ${HOSTS[*]}"
((LEFT == 0))
