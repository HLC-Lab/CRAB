#!/usr/bin/env bash
# Set the Selective Repeat (SR) state of every active RDMA device on the given nodes, then read it back.
#   examples/nplb/sr_set.sh VALUE HOST...     (VALUE is 0 or 1)
# Per host: ssh, list the mlx5_* devices with an ACTIVE port, write the selective_repeat field of
# ROCE_ACCL with mlxreg (the field is spelled selective_repeat_forced_en or selective_repeat_force_en
# depending on the mlxreg version: the spelling in the device's -get output is used), read the
# register again and print "<host> <dev> <pci> SR=<read-back> OK|FAIL" per device.
# Exit 0 when every device reads back VALUE, 1 when one does not or a host failed, 2 on bad usage.
# Not persistent across reboots. Companion of sr_state.sh (read only).
# Env: SR_SSH (default ssh), MLXREG (default mlxreg), SR_SYSFS (default /sys, read on the node),
# ACCL_BIT (sr, the default, or ar: the adaptive_routing field, label AR; ar_set.sh sets it),
# ACCL_USAGE_FROM (the file whose header is printed on bad usage, default this script).
set -uo pipefail

SR_SSH=${SR_SSH:-ssh}
MLXREG=${MLXREG:-mlxreg}

# The ROCE_ACCL field to set: LABEL in the output, FIELD_RE to find its row (both spellings),
# FIELD_NAME in the messages.
case ${ACCL_BIT:-sr} in
    sr)
        LABEL=SR
        FIELD_RE='^selective_repeat_forced?_en$'
        FIELD_NAME=selective_repeat_forced_en ;;
    ar)
        LABEL=AR
        FIELD_RE='^adaptive_routing_forced?_en$'
        FIELD_NAME=adaptive_routing_forced_en ;;
    *)
        echo "ACCL_BIT must be sr or ar" >&2
        exit 2 ;;
esac

VALUE=${1:-}
HOSTS=("${@:2}")
if [[ $VALUE != 0 && $VALUE != 1 ]] || ((${#HOSTS[@]} == 0)); then
    # the leading comment block of the script, without the "# "
    sed -n '2,${/^#/!q;s/^# \{0,1\}//;p}' "${ACCL_USAGE_FROM:-$0}" >&2
    exit 2
fi

# Runs on the node (bash -s, $1 = mlxreg command, $2 = value, $3 = field regex, $4 = field name). One line per device:
#   DEV|name|pci|read-back   SKIP|name   ERR|name|pci|message   FAIL|message
REMOTE=$(
    cat <<'REMOTE_EOF'
mlxreg=$1
value=$2
field_re=$3
field_name=$4
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
    read -r field _ <<<"$(row "$out" "$field_re")"
    if [[ -z $field ]]; then echo "ERR|$name|$pci|no $field_name in the ROCE_ACCL output"; continue; fi
    if ! out=$("$mlxreg" -d "$pci" --reg_name ROCE_ACCL -set "$field=$value" -y 2>&1); then
        echo "ERR|$name|$pci|set failed: ${out%%$'\n'*}"
        continue
    fi
    if ! out=$("$mlxreg" -d "$pci" --reg_name ROCE_ACCL -get 2>&1); then
        echo "ERR|$name|$pci|read-back failed: ${out%%$'\n'*}"
        continue
    fi
    read -r _ got <<<"$(row "$out" "$field_re")"
    if [[ -z $got ]]; then echo "ERR|$name|$pci|no $field_name in the read-back"; continue; fi
    echo "DEV|$name|$pci|$got"
done
REMOTE_EOF
)

ok=0
failed=() # hosts and "host/dev" that failed
for host in "${HOSTS[@]}"; do
    out=$("$SR_SSH" -o BatchMode=yes -o ConnectTimeout=5 "$host" bash -s -- "$MLXREG" "$VALUE" "$FIELD_RE" "$FIELD_NAME" <<<"$REMOTE" 2>&1)
    rc=$?
    seen=0
    while IFS='|' read -r tag a b c; do
        case $tag in
            DEV)
                seen=1
                if [[ $c == "$VALUE" ]]; then
                    printf '%-10s %-8s %-14s %s=%s OK\n' "$host" "$a" "$b" "$LABEL" "$c"
                    ok=$((ok + 1))
                else
                    printf '%-10s %-8s %-14s %s=%s FAIL (wanted %s)\n' "$host" "$a" "$b" "$LABEL" "$c" "$VALUE"
                    failed+=("$host/$a")
                fi ;;
            SKIP)
                printf '%-10s %-8s %-14s skipped (no active port)\n' "$host" "$a" "-" ;;
            ERR)
                seen=1
                printf '%-10s %-8s %-14s %s=? FAIL %s\n' "$host" "$a" "$b" "$LABEL" "$c"
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
    echo "$LABEL=$VALUE not set everywhere: $ok devices OK; failed: ${failed[*]}"
    exit 1
fi
echo "$LABEL=$VALUE on all $ok devices of ${#HOSTS[@]} hosts"
exit 0
