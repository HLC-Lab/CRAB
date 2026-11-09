#!/usr/bin/env bash
# Node health for the NPLB lab, in one page. Run it through the dispatcher (`nplb node check [HOST...]`)
# or directly:
#   examples/nplb/node_tools.sh check [HOST...]   read-only: run gpu_preflight.sh and show one grid,
#                                                 host x check, with the verdict first
# With no HOST, gpu_preflight.sh resolves the nodes of $PARTITION itself; the grid holds the hosts it
# prints. Its steps narrate live on stderr as "[HH:MM:SS] text"; stdout is the final report, paged by
# the pager of lib/pages.sh and saved whole as node-check. Its first content line is
# "VERDICT: OK|FAIL ...", then the grid (one short cell per check: ok, WARN, FAIL, or -- when the check
# does not apply to that host), then one "<host> <check> WARN|FAIL <detail>" line per problem.
# A host that gpu_preflight.sh could not reach shows FAIL in every column. The extra check "disk" runs
# here, on the head node (the one that holds the CRAB checkout): free space under <checkout>/data
# (else the checkout) below NODE_MIN_GIB is WARN, below 5 GiB is FAIL.
# Exit: 0 on OK or WARN only, 1 on a FAIL, 2 on bad usage or a missing gpu_preflight.sh.
# Env: GPU_PREFLIGHT (default gpu_preflight.sh next to this script), NODE_MIN_GIB (default 20),
#      CRAB_DIR (default: this checkout), HEAD (default uname -n), PARTITION (passed through to
#      gpu_preflight.sh), NPLB_REPORT_DIR (the pager's folder).
set -u

unset PYTHONPATH

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
GPU_PREFLIGHT=${GPU_PREFLIGHT:-$HERE/gpu_preflight.sh}
NODE_MIN_GIB=${NODE_MIN_GIB:-20}
CRAB_DIR=${CRAB_DIR:-$(cd "$HERE/../.." && pwd)}
HEAD=${HEAD:-$(uname -n)}
HEAD=${HEAD%%.*}
HOST_WIDTH=10  # grid column of the host names, cut with "~"
CELL_MIN=4     # narrowest grid column
VERDICT_MAX=90 # longest verdict line before "..."
FAIL_GIB=5
CANON_ORDER=(ssh state ports gpus peermem mpirun ucx blink nfs sr disk)

# shellcheck source=lib/pages.sh
. "$HERE/lib/pages.sh"

HOSTS=()   # hosts in the order gpu_preflight.sh printed them
CHECKS=()  # check names: the known ones in CANON_ORDER, then unknown ones as seen
LINE_HOST=() LINE_CHECK=() LINE_LEVEL=() LINE_DETAIL=() # one entry per WARN/FAIL
declare -A CELL=()   # "<host> <check>" -> OK|WARN|FAIL
declare -A SEEN=()   # host -> 1
declare -A ABSENT=() # host -> 1 when it could not be reached (an ssh FAIL line)

usage() { echo "usage: node_tools.sh check [HOST...]" >&2; }
note() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() {
    echo "node_tools: $*" >&2
    exit 2
}

join_by() {
    local sep=$1 out="" first=1 word
    shift
    for word in "$@"; do
        if ((first)); then
            out=$word
            first=0
        else
            out+="$sep$word"
        fi
    done
    printf '%s' "$out"
}

# fit TEXT MAX: TEXT, or its first MAX-3 characters and "..."
fit() {
    if ((${#1} > $2)); then
        printf '%s...' "${1:0:$(($2 - 3))}"
    else
        printf '%s' "$1"
    fi
}

rank() {
    case $1 in
        FAIL) echo 2 ;;
        WARN) echo 1 ;;
        *) echo 0 ;;
    esac
}

add_host() {
    [[ -n ${SEEN[$1]:-} ]] && return
    SEEN[$1]=1
    HOSTS+=("$1")
}

add_check() {
    local name c
    name=$1
    for c in "${CHECKS[@]}"; do
        [[ $c == "$name" ]] && return
    done
    CHECKS+=("$name")
}

# record HOST CHECK LEVEL DETAIL: one result; the worst level of a cell wins
record() {
    local host=$1 check=$2 level=$3 detail=$4 key old
    key="$host $check"
    old=${CELL[$key]:-OK}
    add_host "$host"
    add_check "$check"
    if [[ -z ${CELL[$key]:-} ]] || (($(rank "$level") > $(rank "$old"))); then
        CELL[$key]=$level
    fi
    if [[ $level != OK ]]; then
        LINE_HOST+=("$host")
        LINE_CHECK+=("$check")
        LINE_LEVEL+=("$level")
        LINE_DETAIL+=("$detail")
    fi
    [[ $check == ssh && $level == FAIL ]] && ABSENT[$host]=1
}

# ---- gpu_preflight.sh ------------------------------------------------------------------------------

# parse_preflight TEXT: read the "<host> <check> OK|WARN|FAIL <detail>" lines; sets PARSED to their number
parse_preflight() {
    local host check level detail
    PARSED=0
    while read -r host check level detail; do
        [[ -n $host && $host != gpu_preflight:* ]] || continue
        case $level in
            OK | WARN | FAIL) ;;
            *) continue ;;
        esac
        record "$host" "$check" "$level" "$detail"
        PARSED=$((PARSED + 1))
    done <<<"$1"
}

# run_preflight HOST...: sets PREFLIGHT_ERR to its last stderr line when it printed nothing
run_preflight() {
    local out err_file
    err_file=$(mktemp) || die "cannot create a temporary file"
    note "running gpu_preflight${*:+ on $*}"
    out=$(bash "$GPU_PREFLIGHT" "$@" 2>"$err_file")
    PREFLIGHT_ERR=$(tail -n 1 "$err_file")
    rm -f "$err_file"
    parse_preflight "$out"
}

# add_missing_hosts HOST...: a host that was asked for and has no line is as bad as an unreachable one
add_missing_hosts() {
    local host
    for host in "$@"; do
        [[ -n ${SEEN[$host]:-} ]] || record "$host" ssh FAIL "gpu_preflight printed no line for this host"
    done
}

# ---- disk, on the head node ------------------------------------------------------------------------

check_disk() {
    local path=$CRAB_DIR/data line avail gib
    [[ -d $path ]] || path=$CRAB_DIR
    note "checking free disk on $HEAD: $path"
    if ! line=$(df -Pk "$path" 2>/dev/null | tail -n 1) || [[ -z $line ]]; then
        record "$HEAD" disk WARN "df -Pk $path failed"
        return
    fi
    read -r _ _ _ avail _ <<<"$line"
    if ! [[ $avail =~ ^[0-9]+$ ]]; then
        record "$HEAD" disk WARN "df -Pk $path printed no free space"
        return
    fi
    gib=$((avail / 1048576))
    if ((gib < FAIL_GIB)); then
        record "$HEAD" disk FAIL "$gib GiB free on $path"
    elif ((gib < NODE_MIN_GIB)); then
        record "$HEAD" disk WARN "$gib GiB free on $path"
    else
        record "$HEAD" disk OK "$gib GiB free on $path"
    fi
}

# ---- the report ------------------------------------------------------------------------------------

# fill_unreachable: every cell a host could not run is FAIL (disk is the head's own, run here)
fill_unreachable() {
    local host check
    # gpu_preflight.sh prints an ssh line only for a host it could not reach: the others reached it
    for host in "${HOSTS[@]}"; do
        [[ -n ${ABSENT[$host]:-} || -n ${CELL["$host ssh"]:-} ]] && continue
        [[ " ${CHECKS[*]} " == *" ssh "* ]] && CELL["$host ssh"]=OK
    done
    for host in "${!ABSENT[@]}"; do
        for check in "${CHECKS[@]}"; do
            [[ $check == disk ]] && continue
            [[ -n ${CELL["$host $check"]:-} ]] || CELL["$host $check"]=FAIL
        done
    done
}

# order_checks: the known checks in CANON_ORDER, then the unknown ones in the order seen
order_checks() {
    local ordered=() name c
    for name in "${CANON_ORDER[@]}"; do
        for c in "${CHECKS[@]}"; do
            [[ $c == "$name" ]] && ordered+=("$c")
        done
    done
    for c in "${CHECKS[@]}"; do
        [[ " ${CANON_ORDER[*]} " == *" $c "* ]] || ordered+=("$c")
    done
    CHECKS=("${ordered[@]}")
}

abbrev() {
    case $1 in
        peermem) echo peer ;;
        mpirun) echo mpi ;;
        *) echo "${1:0:6}" ;;
    esac
}

short_host() {
    if ((${#1} > HOST_WIDTH)); then
        printf '%s~' "${1:0:$((HOST_WIDTH - 1))}"
    else
        printf '%s' "$1"
    fi
}

cell_text() {
    case ${CELL["$1 $2"]:-} in
        OK) echo ok ;;
        WARN) echo WARN ;;
        FAIL) echo FAIL ;;
        *) echo -- ;;
    esac
}

# grid_lines: the header row and one row per host
grid_lines() {
    local check host row name widths=() i=0
    row=$(printf '%-*s' "$HOST_WIDTH" host)
    for check in "${CHECKS[@]}"; do
        name=$(abbrev "$check")
        widths[i]=$((${#name} > CELL_MIN ? ${#name} : CELL_MIN))
        row+=$(printf ' %-*s' "${widths[i]}" "$name")
        i=$((i + 1))
    done
    printf '%s\n' "$row"
    for host in "${HOSTS[@]}"; do
        row=$(printf '%-*s' "$HOST_WIDTH" "$(short_host "$host")")
        i=0
        for check in "${CHECKS[@]}"; do
            row+=$(printf ' %-*s' "${widths[i]}" "$(cell_text "$host" "$check")")
            i=$((i + 1))
        done
        printf '%s\n' "$row"
    done
}

# verdict_line: sets VERDICT and RC
verdict_line() {
    local parts=() warns=() host i checks
    RC=0
    for host in "${HOSTS[@]}"; do
        checks=()
        for i in "${!LINE_HOST[@]}"; do
            [[ ${LINE_HOST[i]} == "$host" && ${LINE_LEVEL[i]} == FAIL ]] && checks+=("${LINE_CHECK[i]}")
        done
        ((${#checks[@]} > 0)) && parts+=("$host ($(join_by ", " "${checks[@]}"))")
        for i in "${!LINE_HOST[@]}"; do
            [[ ${LINE_HOST[i]} == "$host" && ${LINE_LEVEL[i]} == WARN ]] && warns+=("$host ${LINE_CHECK[i]}")
        done
    done
    if ((${#parts[@]} > 0)); then
        VERDICT=$(fit "VERDICT: FAIL $(join_by "; " "${parts[@]}")" "$VERDICT_MAX")
        RC=1
    elif ((${#warns[@]} > 0)); then
        VERDICT=$(fit "VERDICT: OK, WARN ${#warns[@]} ($(join_by ", " "${warns[@]}"))" "$VERDICT_MAX")
    else
        VERDICT="VERDICT: OK"
    fi
}

detail_lines() {
    local host i first=1
    for host in "${HOSTS[@]}"; do
        for i in "${!LINE_HOST[@]}"; do
            [[ ${LINE_HOST[i]} == "$host" ]] || continue
            if ((first)); then
                echo
                first=0
            fi
            printf '%s %s %s %s\n' "$host" "${LINE_CHECK[i]}" "${LINE_LEVEL[i]}" "${LINE_DETAIL[i]}"
        done
    done
}

finish_empty() {
    local rc=1
    {
        echo "VERDICT: FAIL no output from gpu_preflight"
        [[ -n ${PREFLIGHT_ERR:-} ]] && echo "gpu_preflight said: $PREFLIGHT_ERR"
    } | nplb_paged node-check || rc=1
    exit "$rc"
}

cmd_check() {
    local asked=("$@")
    [[ -f $GPU_PREFLIGHT ]] || die "not found: $GPU_PREFLIGHT (set GPU_PREFLIGHT)"
    run_preflight "$@"
    ((PARSED > 0)) || finish_empty
    add_missing_hosts "${asked[@]}"
    if [[ -n ${SEEN[$HEAD]:-} ]]; then
        check_disk
    else
        note "head node $HEAD is not among the hosts: disk not checked"
    fi
    order_checks
    fill_unreachable
    verdict_line
    {
        printf '%s\n' "$VERDICT"
        grid_lines
        detail_lines
    } | nplb_paged node-check || RC=1
    exit "$RC"
}

verb=${1:-}
[[ $# -gt 0 ]] && shift
case $verb in
    check) cmd_check "$@" ;;
    *)
        usage
        exit 2
        ;;
esac
