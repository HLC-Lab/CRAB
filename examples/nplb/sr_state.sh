#!/usr/bin/env bash
# Read the Selective Repeat (SR) state of every active RDMA device on each node. Read only.
#   examples/nplb/sr_state.sh [HOST...]       (or PARTITION=name: the nodes of that Slurm partition)
# Per host: ssh, list the mlx5_* devices with an ACTIVE port, read ROCE_ACCL with mlxreg and print
# selective_repeat_forced_en (SR) and adaptive_routing_forced_en (AR) as decimals.
# Exit 0 when every device was read and all SR values are equal, 1 when they differ or a read failed,
# 2 on bad usage.
# Env: SR_SSH (default ssh), MLXREG (default mlxreg), SR_SYSFS (default /sys, read on the node).
set -uo pipefail

SR_SSH=${SR_SSH:-ssh}
MLXREG=${MLXREG:-mlxreg}

HOSTS=("$@")
if ((${#HOSTS[@]} == 0)) && [[ -n ${PARTITION:-} ]]; then
    mapfile -t HOSTS < <(sinfo -h -N -p "$PARTITION" -o %N | sort -u)
fi
if ((${#HOSTS[@]} == 0)); then
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 2
fi

# Runs on the node (bash -s, $1 = mlxreg command). One line per device:
#   DEV|name|pci|sr|ar   SKIP|name   ERR|name|pci|message   FAIL|message
REMOTE=$(
    cat <<'REMOTE_EOF'
mlxreg=$1
root=${SR_SYSFS:-/sys}/class/infiniband
if ! command -v "$mlxreg" >/dev/null 2>&1; then
    echo "FAIL|$mlxreg not found on the node"
    exit 1
fi
# field OUTPUT REGEX: the first value of a register-table row whose name matches, as a decimal
field() {
    local v
    v=$(printf '%s\n' "$1" | awk -F'|' -v re="$2" '{
        n = $1; gsub(/[[:space:]]/, "", n); v = $2; gsub(/[[:space:]]/, "", v)
        if (n ~ re) { print v; exit } }')
    if [[ $v =~ ^0[xX][0-9a-fA-F]+$ ]]; then echo $((v)); fi
}
for d in "$root"/*; do
    [[ -e $d ]] || continue
    name=${d##*/}
    active=0
    for s in "$d"/ports/*/state; do
        if [[ -r $s && $(<"$s") == *ACTIVE* ]]; then active=1; fi
    done
    if ((!active)); then echo "SKIP|$name"; continue; fi
    pci=$(basename "$(readlink -f "$d/device")")
    if ! out=$("$mlxreg" -d "$pci" --reg_name ROCE_ACCL -get 2>&1); then
        echo "ERR|$name|$pci|${out%%$'\n'*}"
        continue
    fi
    sr=$(field "$out" '^selective_repeat_forced?_en$')
    ar=$(field "$out" '^adaptive_routing_forced?_en$')
    if [[ -z $sr ]]; then echo "ERR|$name|$pci|no selective_repeat_forced_en in the ROCE_ACCL output"; continue; fi
    echo "DEV|$name|$pci|$sr|${ar:-?}"
done
REMOTE_EOF
)

ids=()    # "host/dev" of every device that was read, parallel to vals
vals=()   # its SR value
unread=() # hosts and devices that could not be read
for host in "${HOSTS[@]}"; do
    out=$("$SR_SSH" -o BatchMode=yes -o ConnectTimeout=5 "$host" bash -s -- "$MLXREG" <<<"$REMOTE" 2>&1)
    rc=$?
    seen=0
    while IFS='|' read -r tag a b c d; do
        case $tag in
            DEV)
                printf '%-10s %-8s %-14s SR=%s AR=%s\n' "$host" "$a" "$b" "$c" "$d"
                ids+=("$host/$a"); vals+=("$c"); seen=1 ;;
            SKIP)
                printf '%-10s %-8s %-14s skipped (no active port)\n' "$host" "$a" "-" ;;
            ERR)
                printf '%-10s %-8s %-14s SR=? AR=? %s\n' "$host" "$a" "$b" "$c"
                unread+=("$host/$a"); seen=1 ;;
            FAIL)
                printf '%-10s FAIL %s\n' "$host" "$a"
                unread+=("$host"); seen=1 ;;
        esac
    done <<<"$out"
    if ((!seen)); then
        if ((rc != 0)); then msg="ssh exit $rc: ${out%%$'\n'*}"; else msg="no active RDMA device"; fi
        printf '%-10s FAIL %s\n' "$host" "$msg"
        unread+=("$host")
    fi
done

# Summary: distinct SR values, the odd devices are those not on the most common value.
declare -A count=()
for v in "${vals[@]+"${vals[@]}"}"; do count[$v]=$((${count[$v]:-0} + 1)); done
top=""
for v in "${!count[@]}"; do
    if [[ -z $top || ${count[$v]} -gt ${count[$top]} || (${count[$v]} -eq ${count[$top]} && $v -lt $top) ]]; then top=$v; fi
done
status=0
if ((${#vals[@]} == 0)); then
    summary="SR n/a: no device could be read"
elif ((${#count[@]} == 1)); then
    if ((${#unread[@]})); then
        summary="SR $top on ${#vals[@]} devices read"
    else
        summary="SR $top on all ${#vals[@]} devices of ${#HOSTS[@]} hosts"
    fi
else
    parts=""
    for v in $(printf '%s\n' "${!count[@]}" | sort -n); do parts+="${parts:+, }${count[$v]} with SR=$v"; done
    odd=()
    for i in "${!vals[@]}"; do
        if [[ ${vals[i]} != "$top" ]]; then odd+=("${ids[i]}"); fi
    done
    summary="SR mixed: $parts; odd: ${odd[*]}"
    status=1
fi
if ((${#unread[@]})); then
    summary+="; unread: ${unread[*]}"
    status=1
fi
echo "$summary"
exit "$status"
