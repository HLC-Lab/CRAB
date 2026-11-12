#!/usr/bin/env bash
# Save the running configuration of the lab switches, then compare the leaves.
#   nplb net dump [NAME...]    ssh to each switch (a password prompt each) and save `display current-configuration`
#                              as <WORK>/switch_cfg/<NAME>.cfg; default: Leaf1..Leaf6 Spine1 Spine2
#   nplb net diff              what Leaf3 and Leaf4 have that the other leaves lack, and the reverse
# The management addresses are those of the lab sheet (80.3.149.x); the user is SWITCH_USER (default huawei).
# Env: SWITCH_SSH (default ssh), SWITCH_USER, WORK (default <checkout>/local/nplb-work), SWITCH_ODD (default Leaf3,Leaf4).
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CHECKOUT=$(cd "$HERE/../.." && pwd)
WORK=${WORK:-${NPLB_WORK:-$CHECKOUT/local/nplb-work}}
DIR=$WORK/switch_cfg
USER_NAME=${SWITCH_USER:-huawei}
read -ra SSH_CMD <<<"${SWITCH_SSH:-ssh -o StrictHostKeyChecking=accept-new}"
declare -A ADDR=([Leaf1]=80.3.149.77 [Leaf2]=80.3.149.78 [Leaf3]=80.3.149.75 [Leaf4]=80.3.149.176
    [Leaf5]=80.3.149.204 [Leaf6]=80.3.149.205 [Spine1]=80.3.149.79 [Spine2]=80.3.149.80)

note() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

cmd_dump() {
    local -a names=("$@")
    ((${#names[@]} > 0)) || names=(Leaf1 Leaf2 Leaf3 Leaf4 Leaf5 Leaf6 Spine1 Spine2)
    mkdir -p "$DIR" || exit 2
    local name addr tmp lines bad=0
    for name in "${names[@]}"; do
        addr=${ADDR[$name]:-}
        if [[ -z $addr ]]; then
            echo "switch_dump: unknown switch $name (known: ${!ADDR[*]})" >&2
            exit 2
        fi
        note "$name ($addr): type the switch password"
        tmp=$(mktemp)
        if "${SSH_CMD[@]}" "$USER_NAME@$addr" "display current-configuration" >"$tmp"; then
            lines=$(wc -l <"$tmp")
            if ((lines > 20)); then
                mv "$tmp" "$DIR/$name.cfg"
                note "$name: saved $lines lines"
                continue
            fi
        fi
        rm -f "$tmp"
        note "$name: FAILED (wrong password, or no config came back)"
        bad=$((bad + 1))
    done
    echo "dumped: $((${#names[@]} - bad)) of ${#names[@]} switches into $DIR"
    ((bad == 0))
}

cmd_diff() {
    local py="$CHECKOUT/.venv/bin/python" out rc
    [[ -x $py ]] || py=python3
    # shellcheck source=lib/pages.sh
    . "$HERE/lib/pages.sh"
    out=$("$py" "$HERE/switch_diff.py" "$DIR" --odd "${SWITCH_ODD:-Leaf3,Leaf4}")
    rc=$?
    if [[ -n $out ]]; then
        printf '%s\n' "$out" | nplb_paged net-diff || ((rc != 0)) || rc=1
    fi
    return "$rc"
}

sub=${1:-}
[[ $# -gt 0 ]] && shift
case $sub in
    dump) cmd_dump "$@" ;;
    diff) cmd_diff ;;
    *)
        echo "usage: switch_dump.sh dump [NAME...] | diff" >&2
        exit 2
        ;;
esac
