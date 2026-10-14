#!/usr/bin/env bash
# Set the Selective Repeat (SR) state of every active RDMA device on the given nodes, then read it back.
#   examples/nplb/sr_set.sh VALUE HOST...     (VALUE is 0 or 1)
# Per host: ssh, list the mlx5_* devices with an ACTIVE port, write the selective_repeat field of
# ROCE_ACCL with mlxreg (the field is spelled selective_repeat_forced_en or selective_repeat_force_en
# depending on the mlxreg version: the spelling in the device's -get output is used), read the
# register again and print "<host> <dev> <pci> SR=<read-back> OK|FAIL" per device.
# Exit 0 when every device reads back VALUE, 1 when one does not or a host failed, 2 on bad usage.
# Not persistent across reboots. Companion of sr_state.sh (read only).
# Env: SR_SSH (default ssh), MLXREG (default mlxreg), SR_SYSFS (default /sys, read on the node).
set -uo pipefail

SR_SSH=${SR_SSH:-ssh}
MLXREG=${MLXREG:-mlxreg}

VALUE=${1:-}
HOSTS=("${@:2}")
if [[ $VALUE != 0 && $VALUE != 1 ]] || ((${#HOSTS[@]} == 0)); then
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 2
fi

# Runs on the node (bash -s, $1 = mlxreg command, $2 = value). One line per device:
#   DEV|name|pci|read-back   SKIP|name   ERR|name|pci|message   FAIL|message
REMOTE=$(
    cat <<'REMOTE_EOF'
mlxreg=$1
value=$2
root=${SR_SYSFS:-/sys}/class/infiniband
if ! command -v "$mlxreg" >/dev/null 2>&1; then
    echo "FAIL|$mlxreg not found on the node"
    exit 1
fi
# row OUTPUT REGEX: "name value" of the first register-table row whose name matches, value as a decimal
row() {
    local r n v
    r=$(printf '%s\n' "$1" | awk -F'|' -v re="$2" '{
        n = $1; gsub(/[[:space:]]/, "", n); v = $2; gsub(/[[:space:]]/, "", v)
        if (n ~ re) { print n, v; exit } }')
    n=${r%% *}
    v=${r#* }
    if [[ $v =~ ^0[xX][0-9a-fA-F]+$ ]]; then echo "$n $((v))"; fi
}
sr_re='^selective_repeat_forced?_en$'
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
    read -r field _ <<<"$(row "$out" "$sr_re")"
    if [[ -z $field ]]; then echo "ERR|$name|$pci|no selective_repeat_forced_en in the ROCE_ACCL output"; continue; fi
    if ! out=$("$mlxreg" -d "$pci" --reg_name ROCE_ACCL -set "$field=$value" -y 2>&1); then
        echo "ERR|$name|$pci|set failed: ${out%%$'\n'*}"
        continue
    fi
    if ! out=$("$mlxreg" -d "$pci" --reg_name ROCE_ACCL -get 2>&1); then
        echo "ERR|$name|$pci|read-back failed: ${out%%$'\n'*}"
        continue
    fi
    read -r _ sr <<<"$(row "$out" "$sr_re")"
    if [[ -z $sr ]]; then echo "ERR|$name|$pci|no selective_repeat_forced_en in the read-back"; continue; fi
    echo "DEV|$name|$pci|$sr"
done
REMOTE_EOF
)

ok=0
failed=() # hosts and "host/dev" that failed
for host in "${HOSTS[@]}"; do
    out=$("$SR_SSH" -o BatchMode=yes -o ConnectTimeout=5 "$host" bash -s -- "$MLXREG" "$VALUE" <<<"$REMOTE" 2>&1)
    rc=$?
    seen=0
    while IFS='|' read -r tag a b c; do
        case $tag in
            DEV)
                seen=1
                if [[ $c == "$VALUE" ]]; then
                    printf '%-10s %-8s %-14s SR=%s OK\n' "$host" "$a" "$b" "$c"
                    ok=$((ok + 1))
                else
                    printf '%-10s %-8s %-14s SR=%s FAIL (wanted %s)\n' "$host" "$a" "$b" "$c" "$VALUE"
                    failed+=("$host/$a")
                fi ;;
            SKIP)
                printf '%-10s %-8s %-14s skipped (no active port)\n' "$host" "$a" "-" ;;
            ERR)
                seen=1
                printf '%-10s %-8s %-14s SR=? FAIL %s\n' "$host" "$a" "$b" "$c"
                failed+=("$host/$a") ;;
            FAIL)
                seen=1
                printf '%-10s FAIL %s\n' "$host" "$a"
                failed+=("$host") ;;
        esac
    done <<<"$out"
    if ((!seen)); then
        if ((rc != 0)); then msg="ssh exit $rc: ${out%%$'\n'*}"; else msg="no active RDMA device"; fi
        printf '%-10s FAIL %s\n' "$host" "$msg"
        failed+=("$host")
    fi
done

if ((${#failed[@]})); then
    echo "SR=$VALUE not set everywhere: $ok devices OK; failed: ${failed[*]}"
    exit 1
fi
echo "SR=$VALUE on all $ok devices of ${#HOSTS[@]} hosts"
exit 0
