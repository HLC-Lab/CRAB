#!/usr/bin/env bash
# Save the running configuration of the lab switches, then compare the leaves.
#   nplb net dump [NAME...]    ssh to each switch (a password prompt each) and save `display current-configuration`
#                              as <WORK>/switch_cfg/<NAME>.cfg; default: Leaf1..Leaf6 Spine1 Spine2
#   nplb net diff [WORD]       what Leaf3 and Leaf4 have that the other leaves lack, and the reverse; WORD also shows the lines around it
# The management addresses are those of the lab sheet (80.3.149.x); the user is SWITCH_USER (default huawei).
# Env: SWITCH_SSH (default ssh), SWITCH_USER, WORK (default <checkout>/local/nplb-work), SWITCH_ODD (default Leaf3,Leaf4).
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CHECKOUT=$(cd "$HERE/../.." && pwd)
WORK=${WORK:-${NPLB_WORK:-$CHECKOUT/local/nplb-work}}
DIR=$WORK/switch_cfg
USER_NAME=${SWITCH_USER:-huawei}
read -ra SSH_CMD <<<"${SWITCH_SSH:-ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR}"
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
        # an interactive session fed with the commands, as typed by hand; the password is read from the terminal
        printf 'screen-length 0 temporary\ndisplay current-configuration\nquit\n' |
            "${SSH_CMD[@]}" -tt "$USER_NAME@$addr" 2>"$tmp.err" | tr -d '\r' >"$tmp.raw"
        # the configuration: from the line after the echoed command to the closing `return`
        awk '/display current-configuration/ {on = 1; next} on {print} /^return/ {exit}' "$tmp.raw" >"$tmp"
        lines=$(wc -l <"$tmp")
        if ((lines > 20)); then
            mv "$tmp" "$DIR/$name.cfg"
            rm -f "$tmp.raw" "$tmp.err"
            note "$name: saved $lines lines"
            continue
        fi
        note "$name: FAILED, $lines config lines came back; ssh said: $(tail -n 1 "$tmp.err")"
        note "$name: the first lines of the session: $(head -c 200 "$tmp.raw" | tr '\n' ' ')"
        rm -f "$tmp" "$tmp.raw" "$tmp.err"
        bad=$((bad + 1))
    done
    echo "dumped: $((${#names[@]} - bad)) of ${#names[@]} switches into $DIR"
    ((bad == 0))
}

cmd_diff() {
    local -a show=()
    [[ -n ${1:-} ]] && show=(--show "$1")
    local py="$CHECKOUT/.venv/bin/python" out rc
    [[ -x $py ]] || py=python3
    # shellcheck source=lib/pages.sh
    . "$HERE/lib/pages.sh"
    out=$("$py" "$HERE/switch_diff.py" "$DIR" --odd "${SWITCH_ODD:-Leaf3,Leaf4}" "${show[@]}")
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
    diff) cmd_diff "$@" ;;
    *)
        echo "usage: switch_dump.sh dump [NAME...] | diff" >&2
        exit 2
        ;;
esac
