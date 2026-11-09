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
#
#   examples/nplb/node_tools.sh diag HOST         read-only deep dive of one node, paged, saved as node-diag.
#       Its first content line is "VERDICT: OK", "VERDICT: HARDWARE (why)" (take it to the lab staff),
#       "VERDICT: SOFTWARE (why)" (the why says whether `nplb node fix` repairs it) or "VERDICT: UNKNOWN
#       (why)"; then one "OK|WARN|FAIL <item>: <detail>" line per check: ports (ibdev2netdev, else
#       rdma link: the 16 CX8 devices, mlx5_6 and mlx5_7 ignored), netdevs (ip -br link), cx8-pci (lspci),
#       gpus (nvidia-smi -L), speed (ethtool of each Up netdev), mst, dmesg (cable and mlx5 lines),
#       nfs (workers only) and sr (sr_state.sh); the last matching dmesg lines follow as evidence. A
#       missing tool is a WARN line, never a stop. Every command on the node runs under `timeout 20`
#       (through ssh, or directly when HOST is this node, localhost or HEAD); nothing is written.
#       The cable rule: a "Cable unplugged" dmesg line AND a port that is not Up is HARDWARE (the
#       dmesg buffer keeps old lines, so a cable message with all ports Up is only a WARN).
#       Guessed real-world formats (never seen from the lab): the ibdev2netdev and rdma link line shapes,
#       the lspci device string of a CX8 (a line with Mellanox, ConnectX or [15b3: that is not a ConnectX-5,
#       15b3:1017/1019 or a Virtual Function), and the ip -br link columns.
#   examples/nplb/node_tools.sh fix [--sr 0|1] [--ar 0|1] [HOST...]
#       prints the planned actions and asks "Proceed? [y/N]" (stdin must be a terminal; NPLB_YES=1 answers
#       yes; otherwise it declines and changes nothing), then: (1) checks with `exportfs -v` on the head
#       that NFS_PATH is exported (it never edits /etc/exports: a missing export is a FAIL that prints the
#       line for the lab to add, and then no worker is mounted), (2) on every worker (HOST minus HEAD)
#       unmounts NFS_PATH while it is mounted and mounts <head address>:NFS_PATH there (the head address
#       from `getent hosts HEAD`), (3) with --sr / --ar runs sr_set.sh / ar_set.sh with that value on all
#       HOSTS (they read the value back), (4) prints a paged report, saved as node-fix, with the read-back
#       of each step. Its first line is "VERDICT: OK fix" or "VERDICT: FAIL fix (steps)". With no HOST the
#       nodes of $PARTITION (sinfo). Nothing is deleted, rebooted or killed.
# Exit (diag): 0 on OK, 1 on any other verdict, 2 on bad usage. Exit (fix): 0 when every step read back
# fine, 1 on a failed step or when declined, 2 on bad usage or no HOST.
# Env: GPU_PREFLIGHT (default gpu_preflight.sh next to this script), NODE_MIN_GIB (default 20),
#      CRAB_DIR (default: this checkout), HEAD (default uname -n), PARTITION (default h100_4nodes; also
#      passed through to gpu_preflight.sh), NPLB_REPORT_DIR (the pager's folder), SSH (default ssh;
#      diag and fix), NFS_PATH (the exported and mounted folder, default the parent of CRAB_DIR),
#      SR_STATE, SR_SET, AR_SET (sr_state.sh, sr_set.sh, ar_set.sh, default next to this script),
#      NPLB_YES (1 answers yes to the fix question).
set -u

unset PYTHONPATH

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
GPU_PREFLIGHT=${GPU_PREFLIGHT:-$HERE/gpu_preflight.sh}
NODE_MIN_GIB=${NODE_MIN_GIB:-20}
CRAB_DIR=${CRAB_DIR:-$(cd "$HERE/../.." && pwd)}
HEAD=${HEAD:-$(uname -n)}
HEAD=${HEAD%%.*}
PARTITION=${PARTITION:-h100_4nodes}
NFS_PATH=${NFS_PATH:-$(dirname "$CRAB_DIR")}
NFS_PATH=${NFS_PATH%/}
SR_STATE=${SR_STATE:-$HERE/sr_state.sh}
SR_SET=${SR_SET:-$HERE/sr_set.sh}
AR_SET=${AR_SET:-$HERE/ar_set.sh}
read -ra SSH_CMD <<<"${SSH:-ssh}"
LOCAL_NAME=${HOSTNAME:-$(uname -n)}
LOCAL_NAME=${LOCAL_NAME%%.*}
REMOTE_TIMEOUT=20 # seconds, for every command diag and fix run on a node
# The 16 usable CX8 devices of an H100 node (as in gpu_preflight.sh); mlx5_6 and mlx5_7 are a CX5.
EXPECT_PORTS=(mlx5_0 mlx5_1 mlx5_2 mlx5_3 mlx5_4 mlx5_5 mlx5_8 mlx5_9 mlx5_10 mlx5_11 mlx5_12 mlx5_13
    mlx5_14 mlx5_15 mlx5_16 mlx5_17)
EXPECT_CX8=16
EXPECT_GPUS=8
DMESG_RE='cable unplugged|netdev watchdog|mlx5_core.*(error|fail|timeout)|link down' # on a lowercased line
PCI_RE='[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9]'
CX_RE='mellanox|connectx|\[15b3:'
NOT_CX8_RE='connectx-5|\[15b3:101[79]\]|virtual function'
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

usage() {
    {
        echo "usage: node_tools.sh check [HOST...]"
        echo "       node_tools.sh diag HOST"
        echo "       node_tools.sh fix [--sr 0|1] [--ar 0|1] [HOST...]"
    } >&2
}
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

# ---- diag and fix: helpers ---------------------------------------------------------------------------

REPORT=()    # "OK|WARN|FAIL <item>: <detail>" lines and raw lines of a diag or fix report
EVIDENCE=()  # diag: the last matching dmesg lines
FIX_FAILS=() # fix: names of the failed steps

# item LEVEL NAME DETAIL: one result line
item() { REPORT+=("$1 $2: $3"); }

# short_join SEP MAX WORD...: the first MAX words joined by SEP, then " +N more"
short_join() {
    local sep=$1 max=$2
    shift 2
    if (($# > max)); then
        printf '%s +%d more' "$(join_by "$sep" "${@:1:max}")" $(($# - max))
    else
        join_by "$sep" "$@"
    fi
}

is_local() { [[ $1 == localhost || $1 == "$LOCAL_NAME" || $1 == "$HEAD" ]]; }

# run_on HOST CMD: run CMD on HOST (ssh), or here when HOST is this node. Sets RUN_OUT (stdout and
# stderr) and RUN_RC (127: the tool is missing; 255 from ssh: the node cannot be reached).
run_on() {
    local host=$1 cmd=$2
    if is_local "$host"; then
        RUN_OUT=$(bash -c "$cmd" 2>&1 </dev/null)
    else
        RUN_OUT=$("${SSH_CMD[@]}" -o BatchMode=yes -o ConnectTimeout=10 "$host" "$cmd" 2>&1 </dev/null)
    fi
    RUN_RC=$?
}

# run_tool HOST CMD...: run_on with the command under `timeout`
run_tool() {
    local host=$1
    shift
    run_on "$host" "timeout $REMOTE_TIMEOUT $*"
}

tool_missing() { ((RUN_RC == 127)); }

first_line() { printf '%s' "${1%%$'\n'*}"; }

quote() {
    local out
    printf -v out '%q' "$1"
    printf '%s' "$out"
}

# nfs_state HOST: sets NFS_STATE to mounted, unmounted or unknown, and NFS_DETAIL
nfs_state() {
    local host=$1 fstype src mnt type line
    NFS_STATE=unknown
    NFS_DETAIL=""
    run_tool "$host" findmnt -n -o FSTYPE,SOURCE "$(quote "$NFS_PATH")"
    if tool_missing; then
        run_tool "$host" cat /proc/mounts
        if ((RUN_RC != 0)); then
            NFS_DETAIL="neither findmnt nor /proc/mounts could be read"
            return
        fi
        NFS_STATE=unmounted
        while read -r src mnt type _; do
            if [[ $mnt == "$NFS_PATH" && $type == nfs* ]]; then
                NFS_STATE=mounted
                NFS_DETAIL="$type from $src (/proc/mounts)"
            fi
        done <<<"$RUN_OUT"
        return
    fi
    if ((RUN_RC == 255)); then
        NFS_DETAIL="cannot reach $host: $(first_line "$RUN_OUT")"
        return
    fi
    read -r fstype src <<<"$RUN_OUT"
    if ((RUN_RC == 0)) && [[ $fstype == nfs* ]]; then
        NFS_STATE=mounted
        NFS_DETAIL="$fstype from ${src:-?}"
    else
        NFS_STATE=unmounted
        line=$(first_line "$RUN_OUT")
        NFS_DETAIL="${line:-no mount at $NFS_PATH}"
    fi
}

# ---- diag: one collector per check -------------------------------------------------------------------

declare -A PORT_STATE=()  # mlx5 device -> Up|Down
declare -A PORT_NETDEV=() # mlx5 device -> its netdev ("" when it has none)
PORTS_KNOWN=0
PORTS_NOT_UP=() # expected devices that are Down or not listed
CABLE_N=0       # ports named by "Cable unplugged" dmesg lines
CX8_COUNT=-1    # -1: lspci did not tell
GPU_COUNT=-1
HW_WHY=() SW_WHY=() UNKNOWN_WHY=() NEXT_PARTS=()

# parse_ibdev2netdev TEXT: lines like "mlx5_0 port 1 ==> ens1f0np0 (Up)"; a port without a netdev has none
parse_ibdev2netdev() {
    local line re='^(mlx5_[0-9]+) port [0-9]+ ==> *([^ ]*) *\((Up|Down)\)'
    while IFS= read -r line; do
        if [[ $line =~ $re ]]; then
            PORT_STATE[${BASH_REMATCH[1]}]=${BASH_REMATCH[3]}
            PORT_NETDEV[${BASH_REMATCH[1]}]=${BASH_REMATCH[2]}
        fi
    done <<<"$1"
}

# parse_rdma_link TEXT: lines like "link mlx5_0/1 state ACTIVE physical_state LINK_UP netdev ens1f0np0"
parse_rdma_link() {
    local line dev state re='^link (mlx5_[0-9]+)/[0-9]+ state ([A-Z_]+)' net='netdev ([^ ]+)'
    while IFS= read -r line; do
        [[ $line =~ $re ]] || continue
        dev=${BASH_REMATCH[1]}
        state=Down
        [[ ${BASH_REMATCH[2]} == ACTIVE ]] && state=Up
        PORT_STATE[$dev]=$state
        PORT_NETDEV[$dev]=""
        [[ $line =~ $net ]] && PORT_NETDEV[$dev]=${BASH_REMATCH[1]}
    done <<<"$1"
}

collect_ports() {
    local host=$1 src="" dev up=0 down=() unlisted=() detail
    run_tool "$host" ibdev2netdev
    if tool_missing; then
        item WARN ibdev2netdev "not installed"
    elif ((RUN_RC == 0)); then
        parse_ibdev2netdev "$RUN_OUT"
        ((${#PORT_STATE[@]} > 0)) && src=ibdev2netdev
    fi
    run_tool "$host" rdma link show
    if tool_missing; then
        item WARN rdma "not installed"
    elif ((RUN_RC == 0)) && [[ -z $src ]]; then
        parse_rdma_link "$RUN_OUT"
        ((${#PORT_STATE[@]} > 0)) && src="rdma link"
    fi
    if [[ -z $src ]]; then
        item WARN ports "neither ibdev2netdev nor rdma link answered"
        return
    fi
    PORTS_KNOWN=1
    for dev in "${EXPECT_PORTS[@]}"; do
        case ${PORT_STATE[$dev]:-} in
            Up) up=$((up + 1)) ;;
            Down) down+=("$dev") ;;
            *) unlisted+=("$dev") ;;
        esac
    done
    PORTS_NOT_UP=("${down[@]}" "${unlisted[@]}")
    if ((${#PORTS_NOT_UP[@]} == 0)); then
        item OK ports "$up/${#EXPECT_PORTS[@]} Up ($src; mlx5_6, mlx5_7 ignored)"
        return
    fi
    detail="$up/${#EXPECT_PORTS[@]} Up ($src)"
    ((${#down[@]} > 0)) && detail+="; Down: $(short_join " " 6 "${down[@]}")"
    ((${#unlisted[@]} > 0)) && detail+="; not listed: $(short_join " " 6 "${unlisted[@]}")"
    item FAIL ports "$detail"
}

# collect_netdevs HOST: the netdevs of the 16 ports, as `ip -br link` shows them
collect_netdevs() {
    local host=$1 dev name state total=0 present=0 missing=() notup=() detail
    declare -A LINK=()
    ((PORTS_KNOWN)) || return
    run_tool "$host" ip -br link
    if tool_missing; then
        item WARN netdevs "ip not installed (netdev state not checked)"
        return
    elif ((RUN_RC != 0)); then
        item WARN netdevs "ip -br link failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
        return
    fi
    while read -r name state _; do
        LINK[${name%%@*}]=$state
    done <<<"$RUN_OUT"
    for dev in "${EXPECT_PORTS[@]}"; do
        name=${PORT_NETDEV[$dev]:-}
        [[ -n $name ]] || continue
        total=$((total + 1))
        if [[ -z ${LINK[$name]:-} ]]; then
            missing+=("$name")
        else
            present=$((present + 1))
            [[ ${LINK[$name]} == UP ]] || notup+=("$name")
        fi
    done
    if ((total == 0)); then
        item WARN netdevs "the port tool names no netdev for the 16 ports"
    elif ((${#missing[@]} == 0 && ${#notup[@]} == 0)); then
        item OK netdevs "$total/$total in ip -br link, all UP"
    else
        detail="$((total - ${#missing[@]} - ${#notup[@]}))/$total UP in ip -br link"
        ((${#missing[@]} > 0)) && detail+="; missing: $(short_join " " 6 "${missing[@]}")"
        ((${#notup[@]} > 0)) && detail+="; not UP: $(short_join " " 6 "${notup[@]}")"
        item WARN netdevs "$detail"
    fi
}

# collect_dmesg HOST: the cable and mlx5 error lines; the last five go to EVIDENCE
collect_dmesg() {
    local host=$1 line low n=0 cable=0 shown first=0 matched=()
    declare -A ADDR=()
    run_tool "$host" dmesg
    if tool_missing; then
        item WARN dmesg "not installed"
        return
    elif ((RUN_RC != 0)); then
        item WARN dmesg "cannot be read (exit $RUN_RC): $(first_line "$RUN_OUT")"
        return
    fi
    while IFS= read -r line; do
        low=${line,,}
        [[ $low =~ $DMESG_RE ]] || continue
        matched+=("$line")
        n=$((n + 1))
        if [[ $low == *"cable unplugged"* ]]; then
            cable=$((cable + 1))
            [[ $low =~ $PCI_RE ]] && ADDR[${BASH_REMATCH[0]}]=1
        fi
    done <<<"$RUN_OUT"
    CABLE_N=${#ADDR[@]}
    ((CABLE_N == 0)) && CABLE_N=$cable
    if ((n == 0)); then
        item OK dmesg "no cable or mlx5 error line"
        return
    fi
    ((n > 5)) && first=$((n - 5))
    for shown in "${matched[@]:first}"; do
        EVIDENCE+=("  dmesg: $shown")
    done
    if ((CABLE_N > 0 && ${#PORTS_NOT_UP[@]} > 0)); then
        item FAIL dmesg "$n matching lines, cable unplugged on $CABLE_N ports (last ones below)"
    elif ((CABLE_N > 0)); then
        item WARN dmesg "$n matching lines, cable unplugged on $CABLE_N ports earlier, all ports are Up now"
    else
        item WARN dmesg "$n matching lines (last ones below)"
    fi
}

# collect_pci HOST: the CX8 functions in lspci (a CX5 and virtual functions do not count)
collect_pci() {
    local host=$1 line low n=0
    run_tool "$host" lspci -nn
    if tool_missing; then
        item WARN cx8-pci "lspci not installed"
        return
    elif ((RUN_RC != 0)); then
        item WARN cx8-pci "lspci failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
        return
    fi
    while IFS= read -r line; do
        low=${line,,}
        [[ $low =~ $CX_RE ]] || continue
        [[ $low =~ $NOT_CX8_RE ]] && continue
        n=$((n + 1))
    done <<<"$RUN_OUT"
    if ((n == 0)); then
        item WARN cx8-pci "lspci shows no Mellanox/ConnectX line (no card, or a device string this script does not know)"
        return
    fi
    CX8_COUNT=$n
    if ((n == EXPECT_CX8)); then
        item OK cx8-pci "$n/$EXPECT_CX8 CX8 functions in lspci"
    elif ((n < EXPECT_CX8)); then
        item FAIL cx8-pci "$n/$EXPECT_CX8 CX8 functions in lspci"
    else
        item WARN cx8-pci "$n CX8 functions in lspci, expected $EXPECT_CX8"
    fi
}

collect_gpus() {
    local host=$1 line n=0
    run_tool "$host" nvidia-smi -L
    if tool_missing; then
        item WARN gpus "nvidia-smi not installed"
        return
    elif ((RUN_RC != 0)); then
        item FAIL gpus "nvidia-smi -L failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
        SW_WHY+=("nvidia-smi -L fails: driver state, the lab's call")
        return
    fi
    while IFS= read -r line; do
        [[ $line == "GPU "* ]] && n=$((n + 1))
    done <<<"$RUN_OUT"
    GPU_COUNT=$n
    if ((n == EXPECT_GPUS)); then
        item OK gpus "$n/$EXPECT_GPUS GPUs (nvidia-smi -L)"
    else
        item FAIL gpus "$n/$EXPECT_GPUS GPUs (nvidia-smi -L)"
    fi
}

# collect_speed HOST: ethtool speed of every Up netdev, in one remote command
collect_speed() {
    local host=$1 dev name speed nets=() detail="" tally_name
    local script='command -v ethtool >/dev/null 2>&1 || exit 127; for n in "$@"; do s=$(ethtool "$n" 2>/dev/null | while read -r k v; do [ "$k" = "Speed:" ] && echo "$v"; done); echo "$n ${s:-?}"; done'
    declare -A SPEEDS=()
    for dev in "${EXPECT_PORTS[@]}"; do
        name=${PORT_NETDEV[$dev]:-}
        [[ ${PORT_STATE[$dev]:-} == Up && $name =~ ^[A-Za-z0-9_.:-]+$ ]] && nets+=("$name")
    done
    if ((${#nets[@]} == 0)); then
        item WARN speed "no Up netdev to ask"
        return
    fi
    run_tool "$host" "sh -c '$script' sh ${nets[*]}"
    if tool_missing; then
        item WARN ethtool "not installed (link speed not checked)"
        return
    elif ((RUN_RC != 0)); then
        item WARN speed "ethtool failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
        return
    fi
    while read -r name speed; do
        [[ -n $name ]] && SPEEDS[$speed]=$((${SPEEDS[$speed]:-0} + 1))
    done <<<"$RUN_OUT"
    while read -r tally_name; do
        detail+="${detail:+, }${SPEEDS[$tally_name]} x $tally_name"
    done < <(printf '%s\n' "${!SPEEDS[@]}" | sort)
    if ((${#SPEEDS[@]} == 1)) && [[ -z ${SPEEDS["?"]:-} && -z ${SPEEDS["Unknown!"]:-} ]]; then
        item OK speed "$detail (ethtool)"
    else
        item WARN speed "$detail (ethtool)"
    fi
}

collect_mst() {
    local host=$1 line n=0 shown=()
    run_tool "$host" mst status -v
    if tool_missing; then
        item WARN mst "not installed"
        return
    elif ((RUN_RC != 0)); then
        item WARN mst "mst status -v failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
        return
    fi
    while IFS= read -r line; do
        if [[ $line =~ [A-Za-z] ]] && ((n < 2)); then
            shown+=("$line")
            n=$((n + 1))
        fi
    done <<<"$RUN_OUT"
    item OK mst "mst status -v: ${shown[*]:-no text}"
}

collect_nfs() {
    local host=$1
    if [[ $host == "$HEAD" ]]; then
        item OK nfs "head node: it exports $NFS_PATH, no mount expected"
        return
    fi
    nfs_state "$host"
    case $NFS_STATE in
        mounted) item OK nfs "$NFS_PATH mounted ($NFS_DETAIL)" ;;
        unmounted)
            item FAIL nfs "$NFS_PATH is not mounted (${NFS_DETAIL})"
            SW_WHY+=("NFS $NFS_PATH not mounted, run: nplb node fix")
            NEXT_PARTS+=("nplb node fix")
            ;;
        *) item WARN nfs "cannot tell: $NFS_DETAIL" ;;
    esac
}

# tally_text NAME: "0 x8, 1 x8" from the associative array of that name (value -> count), values sorted
tally_text() {
    local -n counts=$1
    local v out=""
    while read -r v; do
        out+="${out:+, }$v x${counts[$v]}"
    done < <(printf '%s\n' "${!counts[@]}" | sort -n)
    printf '%s' "$out"
}

# collect_sr HOST: SR and AR of every active device, as sr_state.sh reads them
collect_sr() {
    local host=$1 out rc line sr_n=0 re='SR=([0-9]+) AR=([0-9?]+)$' last problems=0 sr_val ar_val
    declare -A SR_COUNT=() AR_COUNT=()
    if [[ ! -f $SR_STATE ]]; then
        item WARN sr "not found: $SR_STATE (set SR_STATE)"
        return
    fi
    note "reading SR and AR of $host with sr_state.sh"
    out=$(timeout 120 bash "$SR_STATE" "$host" 2>&1 </dev/null)
    rc=$?
    while IFS= read -r line; do
        if [[ $line =~ $re ]]; then
            sr_n=$((sr_n + 1))
            SR_COUNT[${BASH_REMATCH[1]}]=$((${SR_COUNT[${BASH_REMATCH[1]}]:-0} + 1))
            [[ ${BASH_REMATCH[2]} == "?" ]] || AR_COUNT[${BASH_REMATCH[2]}]=$((${AR_COUNT[${BASH_REMATCH[2]}]:-0} + 1))
        fi
    done <<<"$out"
    last=${out##*$'\n'}
    if ((sr_n == 0)); then
        item WARN sr "sr_state.sh read no device (exit $rc): ${last:-no output}"
        return
    fi
    if ((${#SR_COUNT[@]} > 1)); then
        problems=1
        SW_WHY+=("SR values differ ($(tally_text SR_COUNT)), run: nplb node fix --sr 0|1")
    fi
    if ((${#AR_COUNT[@]} > 1)); then
        problems=1
        SW_WHY+=("AR values differ ($(tally_text AR_COUNT)), run: nplb node fix --ar 0|1")
    fi
    if ((problems)); then
        NEXT_PARTS+=("nplb node fix")
        item FAIL sr "SR: $(tally_text SR_COUNT); AR: $(tally_text AR_COUNT) (sr_state.sh)"
    elif ((rc != 0)); then
        item WARN sr "sr_state.sh exit $rc: ${last:-no output}"
    else
        sr_val=${!SR_COUNT[*]}
        ar_val=${!AR_COUNT[*]}
        item OK sr "SR=$sr_val AR=${ar_val:-?} on $sr_n devices (sr_state.sh)"
    fi
}

# diag_verdict: sets VERDICT and RC from what the collectors found
diag_verdict() {
    local down_text
    RC=1
    if ((PORTS_KNOWN && ${#PORTS_NOT_UP[@]} > 0 && CABLE_N > 0)); then
        HW_WHY+=("cable unplugged on $CABLE_N ports")
    fi
    if ((CX8_COUNT >= 0 && CX8_COUNT < EXPECT_CX8)); then
        HW_WHY+=("$((EXPECT_CX8 - CX8_COUNT)) of $EXPECT_CX8 CX8 functions missing from lspci")
    fi
    if ((GPU_COUNT >= 0 && GPU_COUNT < EXPECT_GPUS)); then
        HW_WHY+=("$GPU_COUNT of $EXPECT_GPUS GPUs listed by nvidia-smi -L")
    fi
    if ((${#HW_WHY[@]} == 0 && PORTS_KNOWN && ${#PORTS_NOT_UP[@]} > 0)); then
        down_text=$(short_join ", " 3 "${PORTS_NOT_UP[@]}")
        if ((CX8_COUNT >= 0)); then
            SW_WHY=("$down_text not Up, cards in lspci, no cable message: driver or link state" "${SW_WHY[@]}")
            NEXT_PARTS+=("check the dmesg lines below; reloading mlx5_core is the lab's call")
        else
            UNKNOWN_WHY+=("$down_text not Up and lspci gave no CX8 count")
        fi
    fi
    if ((${#HW_WHY[@]} > 0)); then
        VERDICT="VERDICT: HARDWARE ($(join_by "; " "${HW_WHY[@]}"))"
        NEXT_PARTS=("take this report to the lab staff (cables, card or GPUs); nothing here repairs it")
    elif ((${#SW_WHY[@]} > 0)); then
        VERDICT="VERDICT: SOFTWARE ($(join_by "; " "${SW_WHY[@]}"))"
    elif ((${#UNKNOWN_WHY[@]} > 0)); then
        VERDICT="VERDICT: UNKNOWN ($(join_by "; " "${UNKNOWN_WHY[@]}"))"
        NEXT_PARTS=("fix the WARN or FAIL lines below, then run diag again")
    else
        VERDICT="VERDICT: OK"
        RC=0
    fi
}

cmd_diag() {
    local host=${1:-} how=worker via=ssh
    if (($# != 1)) || [[ $host == -* ]]; then
        usage
        exit 2
    fi
    [[ $host == "$HEAD" ]] && how=head
    is_local "$host" && via=local
    note "diagnosing $host ($how, $via)"
    run_tool "$host" uname -n
    if ((RUN_RC != 0)); then
        VERDICT="VERDICT: UNKNOWN (cannot reach $host)"
        item FAIL "$via" "exit $RUN_RC: $(first_line "$RUN_OUT")"
        NEXT_PARTS=("check that ssh to $host works, then run diag again")
        RC=1
    else
        note "ports (ibdev2netdev, rdma link)"
        collect_ports "$host"
        note "netdevs, dmesg, lspci"
        collect_netdevs "$host"
        collect_dmesg "$host"
        collect_pci "$host"
        note "GPUs, link speed, mst"
        collect_gpus "$host"
        collect_speed "$host"
        collect_mst "$host"
        note "NFS and SR/AR"
        collect_nfs "$host"
        collect_sr "$host"
        diag_verdict
    fi
    {
        printf '%s\n' "$VERDICT"
        ((${#NEXT_PARTS[@]} > 0)) && printf 'next: %s\n' "$(join_by "; " "${NEXT_PARTS[@]}")"
        printf 'host: %s (%s, %s)\n' "$host" "$how" "$via"
        printf '%s\n' "${REPORT[@]}"
        ((${#EVIDENCE[@]} > 0)) && printf '%s\n' "${EVIDENCE[@]}"
    } | nplb_paged node-diag || RC=1
    exit "$RC"
}

# ---- fix ---------------------------------------------------------------------------------------------

# resolve_addr NAME: sets ADDR to the first IPv4 address `getent hosts` gives (else its first one), or ""
resolve_addr() {
    local first="" any="" re='^[0-9]+(\.[0-9]+){3}$'
    ADDR=""
    while read -r first _; do
        [[ -n $any ]] || any=$first
        if [[ $first =~ $re ]]; then
            ADDR=$first
            return
        fi
    done < <(getent hosts "$1" 2>/dev/null)
    ADDR=$any
}

# confirm: the "Proceed? [y/N]" question; NPLB_YES=1 yes, a terminal asks, anything else is no
confirm() {
    local ans=""
    if [[ ${NPLB_YES:-} == 1 ]]; then
        echo "Proceed? [y/N] yes (NPLB_YES=1)" >&2
        return 0
    fi
    if [[ -t 0 ]]; then
        read -r -p "Proceed? [y/N] " ans
        [[ $ans == [yY] || $ans == [yY][eE][sS] ]]
        return
    fi
    echo "Proceed? [y/N] no (stdin is not a terminal; set NPLB_YES=1 to answer yes)" >&2
    return 1
}

# print_plan: the actions fix will take, on stderr
print_plan() {
    local step=1
    {
        echo "planned actions:"
        echo "  $step. $HEAD (head): check that $NFS_PATH is exported (exportfs -v, read only; /etc/exports is never edited)"
        step=$((step + 1))
        if ((${#FIX_WORKERS[@]} == 0)); then
            echo "  $step. no worker to mount"
        elif [[ -z $HEAD_ADDR ]]; then
            echo "  $step. workers ${FIX_WORKERS[*]}: not mounted, getent hosts $HEAD gives no address"
        else
            echo "  $step. ${FIX_WORKERS[*]}: umount $NFS_PATH while it is mounted, then mount -t nfs $HEAD_ADDR:$NFS_PATH $NFS_PATH"
        fi
        step=$((step + 1))
        [[ -n $FIX_SR ]] && echo "  $step. ${FIX_HOSTS[*]}: set SR=$FIX_SR with sr_set.sh, read it back" && step=$((step + 1))
        [[ -n $FIX_AR ]] && echo "  $step. ${FIX_HOSTS[*]}: set AR=$FIX_AR with ar_set.sh, read it back"
    } >&2
}

# export_line: the /etc/exports line the lab has to add, one address per worker
export_line() {
    local w parts=()
    for w in "${FIX_WORKERS[@]}"; do
        resolve_addr "$w"
        parts+=("${ADDR:-$w}(rw,sync,no_root_squash,no_subtree_check)")
    done
    printf '%s %s' "$NFS_PATH" "${parts[*]}"
}

# fix_export: checks the export on the head; sets EXPORT_OK to 1 unless it is missing
fix_export() {
    local first listed=0
    EXPORT_OK=1
    note "checking the export of $NFS_PATH on $HEAD"
    run_tool "$HEAD" exportfs -v
    if tool_missing; then
        item WARN "head export" "exportfs is not installed on $HEAD: the export is not verified"
        return
    elif ((RUN_RC != 0)); then
        item WARN "head export" "exportfs -v failed (exit $RUN_RC): $(first_line "$RUN_OUT"); the export is not verified"
        return
    fi
    while read -r first _; do
        [[ $first == "$NFS_PATH" ]] && listed=1
    done <<<"$RUN_OUT"
    if ((listed)); then
        item OK "head export" "$NFS_PATH is exported"
        return
    fi
    EXPORT_OK=0
    item FAIL "head export" "$NFS_PATH is not exported; add this line to /etc/exports (lab-owned, not edited here): $(export_line) then run: exportfs -ra"
    FIX_FAILS+=("head export missing")
    ((${#FIX_WORKERS[@]} > 0)) && FIX_FAILS+=("workers not mounted")
}

# fix_mount WORKER: unmount while mounted, mount from the head, read it back
fix_mount() {
    local w=$1 inner q_path q_inner
    q_path=$(quote "$NFS_PATH")
    inner="while findmnt -n $q_path >/dev/null 2>&1; do umount $q_path || exit 3; done; mount -t nfs $(quote "$HEAD_ADDR:$NFS_PATH") $q_path"
    q_inner=$(quote "$inner")
    note "remounting $NFS_PATH on $w"
    run_tool "$w" "sh -c $q_inner"
    if ((RUN_RC == 3)); then
        item FAIL "$w mount" "umount $NFS_PATH failed, a process may use it: $(first_line "$RUN_OUT")"
        FIX_FAILS+=("mount $w")
        return
    elif ((RUN_RC != 0)); then
        item FAIL "$w mount" "exit $RUN_RC: $(first_line "$RUN_OUT")"
        FIX_FAILS+=("mount $w")
        return
    fi
    nfs_state "$w"
    if [[ $NFS_STATE == mounted ]]; then
        item OK "$w mounted" "$NFS_DETAIL"
    else
        item FAIL "$w read-back" "$NFS_PATH is not mounted after the mount command: $NFS_DETAIL"
        FIX_FAILS+=("mount $w")
    fi
}

fix_mounts() {
    local w
    ((${#FIX_WORKERS[@]} > 0)) || return
    if ((!EXPORT_OK)); then
        return
    elif [[ -z $HEAD_ADDR ]]; then
        item FAIL "head address" "getent hosts $HEAD printed no address: the workers are not mounted"
        FIX_FAILS+=("workers not mounted")
        return
    fi
    for w in "${FIX_WORKERS[@]}"; do
        fix_mount "$w"
    done
}

# fix_bit NAME VALUE SCRIPT: sr_set.sh / ar_set.sh on all hosts; its last line is the read-back
fix_bit() {
    local name=$1 value=$2 script=$3 out rc last line shown=0
    if [[ ! -f $script ]]; then
        item FAIL "$name" "not found: $script"
        FIX_FAILS+=("$name")
        return
    fi
    note "setting ${name^^}=$value on ${FIX_HOSTS[*]}"
    out=$(timeout 300 bash "$script" "$value" "${FIX_HOSTS[@]}" 2>&1 </dev/null)
    rc=$?
    last=${out##*$'\n'}
    if ((rc == 0)); then
        item OK "$name" "${last:-no output}"
        return
    fi
    item FAIL "$name" "${last:-no output (exit $rc)}"
    FIX_FAILS+=("$name")
    while IFS= read -r line; do
        [[ $line == *FAIL* && $line != "$last" ]] || continue
        REPORT+=("  $line")
        shown=$((shown + 1))
        ((shown >= 3)) && break
    done <<<"$out"
}

# fix_hosts_default: HOST... from sinfo
fix_hosts_default() {
    command -v sinfo >/dev/null 2>&1 || die "no HOST given and no sinfo to list the nodes of $PARTITION"
    mapfile -t FIX_HOSTS < <(sinfo -h -N -p "$PARTITION" -o %N 2>/dev/null | sort -u)
    ((${#FIX_HOSTS[@]} > 0)) || die "no HOST given and sinfo lists no nodes in $PARTITION"
}

cmd_fix() {
    local h rc=0 verdict
    FIX_SR="" FIX_AR="" FIX_HOSTS=() FIX_WORKERS=()
    while (($# > 0)); do
        case $1 in
            --sr | --ar)
                if [[ ${2:-} != 0 && ${2:-} != 1 ]]; then
                    usage
                    exit 2
                fi
                if [[ $1 == --sr ]]; then FIX_SR=$2; else FIX_AR=$2; fi
                shift 2
                ;;
            -*)
                usage
                exit 2
                ;;
            *)
                FIX_HOSTS+=("$1")
                shift
                ;;
        esac
    done
    ((${#FIX_HOSTS[@]} > 0)) || fix_hosts_default
    for h in "${FIX_HOSTS[@]}"; do
        [[ $h == "$HEAD" ]] || FIX_WORKERS+=("$h")
    done
    resolve_addr "$HEAD"
    HEAD_ADDR=$ADDR
    print_plan
    if ! confirm; then
        echo "declined: nothing changed" >&2
        exit 1
    fi
    fix_export
    fix_mounts
    [[ -n $FIX_SR ]] && fix_bit sr "$FIX_SR" "$SR_SET"
    [[ -n $FIX_AR ]] && fix_bit ar "$FIX_AR" "$AR_SET"
    if ((${#FIX_FAILS[@]} > 0)); then
        verdict="VERDICT: FAIL fix ($(join_by ", " "${FIX_FAILS[@]}"))"
        rc=1
    else
        verdict="VERDICT: OK fix"
    fi
    {
        printf '%s\n' "$verdict"
        printf 'head: %s (%s), workers: %s\n' "$HEAD" "${HEAD_ADDR:-no address}" "${FIX_WORKERS[*]:-none}"
        printf '%s\n' "${REPORT[@]}"
    } | nplb_paged node-fix || rc=1
    exit "$rc"
}

verb=${1:-}
[[ $# -gt 0 ]] && shift
case $verb in
    check) cmd_check "$@" ;;
    diag) cmd_diag "$@" ;;
    fix) cmd_fix "$@" ;;
    *)
        usage
        exit 2
        ;;
esac
