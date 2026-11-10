#!/usr/bin/env bash
# The network state of the NPLB lab: three independent switches (NPLB on/off and AR = adaptive routing
# on/off, both set by hand on the switch CLI; SR = selective repeat on/off, a per-node NIC setting).
# Run it through the dispatcher (`nplb net VERB`) or directly:
#   examples/nplb/net_tools.sh declare nplb=on|off ar=on|off
#       writes $WORK/net_state.json: what you say the switches are now (nothing on the nodes can read
#       them, so this is a human claim). The first declaration also writes net_state_initial.json, never
#       overwritten, so the state at slot start can be restored.
#   examples/nplb/net_tools.sh set sr=on|off [arnic=on|off] [HOST...]
#       sets the NIC bits on all hosts with sr_set.sh (SR) and ar_set.sh (the NIC adaptive-routing bit,
#       only with arnic=); each script reads its value back. Paged report, saved as net-set; its first
#       line is "VERDICT: OK SR=1 AR(nic)=0 on 2 nodes" or "VERDICT: FAIL <bit> not set (exit N)".
#   examples/nplb/net_tools.sh show [HOST...]
#       reads SR and the NIC AR bit of every node with sr_state.sh and prints the declared switch state
#       with its age and a table host x SR x AR(nic) x devices. Paged, saved as net-show. First line:
#       "VERDICT: OK nplb=on ar=off sr=1 arnic=0", "VERDICT: WARN no switch state declared (...)" or
#       "VERDICT: FAIL SR mixed (node2)" (also AR(nic) mixed, a node that could not be read).
#   examples/nplb/net_tools.sh sheet STATE | --restore
#       the switch commands to paste for STATE (nplb=on ar=off, or base, nplb, ar, nplb+ar), or for the
#       initial state. Paged, saved as net-sheet. The AR switch command is not known yet: the sheet
#       carries a marked placeholder. NET_SWITCHES (space separated switch names) heads the sheet.
#   examples/nplb/net_tools.sh record DIR [HOST...]
#       writes DIR/nplb_state.json (declared switch state, live NIC SR and AR per host, git revisions of
#       CRAB and Blink, time) and prints "recorded: <label> -> <path>". It refuses (exit 1) when no
#       switch state is declared or the nodes disagree or cannot be read; the run command calls it
#       before every submit.
# Steps narrate live on stderr as "[HH:MM:SS] text"; stdout is the paged report (lib/pages.sh), except
# for declare and record, which print one line. With no HOST the nodes of $PARTITION (sinfo).
# Exit: 0 on success (show: OK or WARN), 1 on a FAIL verdict or a refusal, 2 on bad usage.
# Env: WORK (default <checkout>/local/nplb-work), NET_SWITCHES, SR_STATE, SR_SET, AR_SET (sr_state.sh,
#      sr_set.sh, ar_set.sh, default next to this script), CRAB_DIR (default: this checkout), BLINK_DIR
#      (default $NPLB_LAB/blink-gpu), NPLB_LAB (default $HOME/crab/lab), PARTITION (default
#      h100_4nodes), NPLB_REPORT_DIR (the pager's folder). The state logic lives in net_state.py.
set -u

unset PYTHONPATH

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CHECKOUT=$(cd "$HERE/../.." && pwd)
WORK=${WORK:-${NPLB_WORK:-$CHECKOUT/local/nplb-work}}
PARTITION=${PARTITION:-h100_4nodes}
SR_STATE=${SR_STATE:-$HERE/sr_state.sh}
SR_SET=${SR_SET:-$HERE/sr_set.sh}
AR_SET=${AR_SET:-$HERE/ar_set.sh}
CRAB_DIR=${CRAB_DIR:-$CHECKOUT}
NPLB_LAB=${NPLB_LAB:-$HOME/crab/lab}
BLINK_DIR=${BLINK_DIR:-$NPLB_LAB/blink-gpu}
export WORK PARTITION CRAB_DIR BLINK_DIR NPLB_LAB
PY="$CHECKOUT/.venv/bin/python"
[[ -x $PY ]] || PY=python3
STATE_PY="$HERE/net_state.py"

# shellcheck source=lib/pages.sh
. "$HERE/lib/pages.sh"

HOSTS=()
TMPD=""

usage() {
    {
        echo "usage: nplb net declare nplb=on|off ar=on|off"
        echo "       nplb net set sr=on|off [arnic=on|off] [HOST...]"
        echo "       nplb net show [HOST...]"
        echo "       nplb net sheet STATE | --restore"
        echo "       nplb net record DIR [HOST...]"
    } >&2
}
note() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() {
    echo "net: $*" >&2
    exit 2
}
join_by() {
    local sep=$1 out="" word
    shift
    for word in "$@"; do out+="${out:+$sep}$word"; done
    printf '%s' "$out"
}

# need_tmp: a scratch folder, removed on exit
need_tmp() {
    [[ -n $TMPD ]] && return
    TMPD=$(mktemp -d) || die "cannot create a temporary folder"
    trap 'rm -rf "$TMPD"' EXIT
}

# resolve_hosts: HOSTS stays as given, else the nodes of $PARTITION
resolve_hosts() {
    ((${#HOSTS[@]} > 0)) && return
    command -v sinfo >/dev/null 2>&1 || die "no HOST given and no sinfo to list the nodes of $PARTITION"
    mapfile -t HOSTS < <(sinfo -h -N -p "$PARTITION" -o %N 2>/dev/null | sort -u)
    ((${#HOSTS[@]} > 0)) || die "no HOST given and sinfo lists no nodes in $PARTITION"
}

# take_hosts ARGS...: HOSTS from plain arguments; an option is bad usage
take_hosts() {
    local arg
    for arg in "$@"; do
        case $arg in
            -*)
                usage
                exit 2
                ;;
            *) HOSTS+=("$arg") ;;
        esac
    done
}

# read_nic: runs sr_state.sh on HOSTS and turns its lines into $NIC_JSON (net_state.py nic)
read_nic() {
    local raw
    need_tmp
    raw=$TMPD/sr_state.txt
    NIC_JSON=$TMPD/nic.json
    note "reading SR and AR(nic) of ${HOSTS[*]} with sr_state.sh"
    if [[ -f $SR_STATE ]]; then
        timeout 300 bash "$SR_STATE" "${HOSTS[@]}" >"$raw" 2>&1 </dev/null
    else
        note "not found: $SR_STATE"
        : >"$raw"
    fi
    "$PY" "$STATE_PY" nic --hosts "$(join_by , "${HOSTS[@]}")" <"$raw" >"$NIC_JSON" || exit 2
}

# page_report NAME FILE CODE: pages FILE, exits CODE (1 when the pager failed)
page_report() {
    local name=$1 file=$2 code=$3
    nplb_paged "$name" <"$file" || code=1
    exit "$code"
}

cmd_declare() {
    exec "$PY" "$STATE_PY" declare "$@"
}

cmd_sheet() {
    local code
    need_tmp
    "$PY" "$STATE_PY" sheet "$@" >"$TMPD/report"
    code=$?
    ((code == 0)) || exit "$code"
    page_report net-sheet "$TMPD/report" 0
}

cmd_show() {
    local code
    take_hosts "$@"
    resolve_hosts
    read_nic
    "$PY" "$STATE_PY" show-report --nic "$NIC_JSON" >"$TMPD/report"
    code=$?
    ((code <= 1)) || exit "$code"
    page_report net-show "$TMPD/report" "$code"
}

cmd_record() {
    local dir
    (($# >= 1)) || {
        usage
        exit 2
    }
    dir=$1
    shift
    [[ -d $dir ]] || die "no such folder: $dir"
    "$PY" "$STATE_PY" declared || exit $?
    take_hosts "$@"
    resolve_hosts
    read_nic
    "$PY" "$STATE_PY" record "$dir" --nic "$NIC_JSON"
    exit $?
}

# set_bit LABEL VALUE SCRIPT: runs the script on HOSTS; sets BIT_RC, BIT_LAST (its last line, the
# read-back) and BIT_FAILS (up to 3 of its FAIL lines)
set_bit() {
    local label=$1 value=$2 script=$3 out line
    BIT_FAILS=()
    note "setting $label=$value on ${HOSTS[*]}"
    if [[ ! -f $script ]]; then
        BIT_RC=1
        BIT_LAST="not found: $script"
        return
    fi
    out=$(timeout 300 bash "$script" "$value" "${HOSTS[@]}" 2>&1 </dev/null)
    BIT_RC=$?
    BIT_LAST=${out##*$'\n'}
    [[ -n $BIT_LAST ]] || BIT_LAST="no output (exit $BIT_RC)"
    ((BIT_RC == 0)) && return
    while IFS= read -r line; do
        [[ $line == *FAIL* && $line != "$BIT_LAST" ]] || continue
        BIT_FAILS+=("  $line")
        ((${#BIT_FAILS[@]} >= 3)) && break
    done <<<"$out"
}

# onoff_bit VALUE: 1 for on, 0 for off, else bad usage
onoff_bit() {
    case $1 in
        on) echo 1 ;;
        off) echo 0 ;;
        *) die "${2}=$1: use on or off" ;;
    esac
}

cmd_set() {
    local sr="" arnic="" arg fails=() report=() verdict code=0
    while (($# > 0)); do
        arg=$1
        shift
        case $arg in
            sr=*)
                [[ -z $sr ]] || die "sr= given twice"
                sr=$(onoff_bit "${arg#*=}" sr) || exit 2
                ;;
            arnic=*)
                [[ -z $arnic ]] || die "arnic= given twice"
                arnic=$(onoff_bit "${arg#*=}" arnic) || exit 2
                ;;
            *=* | -*)
                usage
                die "unknown setting '$arg' (use sr= and arnic=)"
                ;;
            *) HOSTS+=("$arg") ;;
        esac
    done
    [[ -n $sr ]] || {
        usage
        die "missing sr=on|off"
    }
    resolve_hosts
    need_tmp
    set_bit SR "$sr" "$SR_SET"
    [[ $BIT_RC -eq 0 ]] || fails+=("SR not set (exit $BIT_RC)")
    report+=("SR=$sr: $BIT_LAST" "${BIT_FAILS[@]}")
    if [[ -n $arnic ]]; then
        set_bit "AR(nic)" "$arnic" "$AR_SET"
        [[ $BIT_RC -eq 0 ]] || fails+=("AR(nic) not set (exit $BIT_RC)")
        report+=("AR(nic)=$arnic: $BIT_LAST" "${BIT_FAILS[@]}")
    fi
    if ((${#fails[@]} > 0)); then
        verdict="VERDICT: FAIL $(join_by ", " "${fails[@]}")"
        code=1
    else
        verdict="VERDICT: OK SR=$sr${arnic:+ AR(nic)=$arnic} on ${#HOSTS[@]} nodes"
    fi
    {
        printf '%s\n' "$verdict"
        printf 'nodes: %s\n' "${HOSTS[*]}"
        printf '%s\n' "${report[@]}"
        echo "the NIC bits do not survive a reboot of a node; read them back with: nplb net show"
    } >"$TMPD/report"
    page_report net-set "$TMPD/report" "$code"
}

verb=${1:-}
[[ $# -gt 0 ]] && shift
case $verb in
    declare) cmd_declare "$@" ;;
    set) cmd_set "$@" ;;
    show) cmd_show "$@" ;;
    sheet) cmd_sheet "$@" ;;
    record) cmd_record "$@" ;;
    *)
        usage
        exit 2
        ;;
esac
