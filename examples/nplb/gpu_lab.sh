#!/usr/bin/env bash
# One-screen lab session for the NPLB H100 GPU suites. Run it on the node that holds the CRAB
# checkout; every step prints at most one screen (38 lines x 100 columns) that can be photographed,
# and keeps the full detail in a log:
#   examples/nplb/gpu_lab.sh status [HOST...]   checkout, preset, crab validate of the three GPU
#                                               configs, and a node x check grid (gpu_preflight.sh)
#   examples/nplb/gpu_lab.sh show STEP          print the full log of the last run of STEP
#   examples/nplb/gpu_lab.sh -h                 this text
# Steps still to come (they print "not available yet" and exit 2): smoke, dual-port, pin, all.
# With no HOST, the nodes of $PARTITION (sinfo). `status` never stops early: it is a dashboard.
# Each run writes $WORK/gpu_lab/<YYYYmmdd_HHMMSS>/<step>.log; $WORK/gpu_lab/latest points at the
# newest run. A screen line is "<what> OK|WARN|FAIL <detail>"; a line cut at 100 columns ends in
# "~" and, when lines were cut, the last line says how many and names `gpu_lab.sh show STEP`.
# Nothing is changed on any node; the only files written are the logs under $WORK.
# Exit status: 0 nothing failed, 1 a check failed, 2 bad usage or missing setup (no hosts, no log).
# Env: PARTITION (h100_4nodes), WORK (<checkout>/local/nplb-work), NODES (reserved for the later
#      steps: the 2 nodes of the diagnostics), CRAB (the crab executable; default
#      <checkout>/.venv/bin/crab when it is executable, else crab), CRAB_PRESETS_DIR (the folder
#      searched for the preset; default: CRAB_PATH_PRESETS, else <checkout>/local/presets and then
#      $XDG_CONFIG_HOME or ~/.config/crab/presets), PREFLIGHT_SSH (how gpu_preflight.sh reaches the
#      nodes, default ssh), GPU_PREFLIGHT (default: gpu_preflight.sh next to this script).
set -uo pipefail

usage() { sed -n '2,/^[^#]/{/^#/p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CHECKOUT=$(cd "$HERE/../.." && pwd)
PARTITION=${PARTITION:-h100_4nodes}
WORK=${WORK:-$CHECKOUT/local/nplb-work}
GPU_PREFLIGHT=${GPU_PREFLIGHT:-$HERE/gpu_preflight.sh}
CRAB=${CRAB:-}
if [[ -z $CRAB ]]; then
    if [[ -x $CHECKOUT/.venv/bin/crab ]]; then CRAB=$CHECKOUT/.venv/bin/crab; else CRAB=crab; fi
fi

readonly SCREEN_LINES=38
readonly SCREEN_COLS=100
readonly HOST_COLS=24 # a host name is cut to this width in the grid and below it
readonly DETAIL_LINES=12
readonly PRESET=nplb_h100_gpu
STEPS="status smoke dual-port pin"

RUN_DIR=
LOG=
SCREEN=()
FAILS=0
WARNS=0
HOSTS=()
SHOWN=
PRESET_FILE=
PRESET_DIRS=()
NROWS=0
CHECKS=()
declare -A CELL=() DETAIL=() # "<host> <check>" -> status, detail of the gpu_preflight.sh rows

# ---- logs ------------------------------------------------------------------------------------

# start_run STEP: the run folder (one per command, shared by the steps `all` chains), the `latest`
# link and an empty STEP.log.
start_run() {
    local base=$WORK/gpu_lab ts dir n=0
    if [[ -z $RUN_DIR ]]; then
        mkdir -p "$base" || {
            echo "gpu_lab: cannot create $base" >&2
            exit 2
        }
        ts=$(date +%Y%m%d_%H%M%S)
        dir=$base/$ts
        until mkdir "$dir" 2>/dev/null; do
            n=$((n + 1))
            if ((n > 99)); then
                echo "gpu_lab: cannot create a run folder under $base" >&2
                exit 2
            fi
            dir=$base/${ts}_$n
        done
        RUN_DIR=$dir
        ln -sfn "${dir##*/}" "$base/latest"
    fi
    LOG=$RUN_DIR/$1.log
    : >"$LOG"
    FAILS=0
    WARNS=0
    SCREEN=()
}

log() { printf '%s\n' "$*" >>"$LOG"; }

log_section() { printf '\n== %s ==\n' "$1" >>"$LOG"; }

# ---- the screen ------------------------------------------------------------------------------

# say LINE: adds a line to the screen. Tabs, carriage returns and newlines become spaces.
say() {
    local line=$1
    line=${line//$'\t'/ }
    line=${line//$'\r'/}
    line=${line//$'\n'/ }
    SCREEN+=("$line")
}

sayf() { # printf-style
    local line
    printf -v line "$@"
    say "$line"
}

# result WHAT OK|WARN|FAIL DETAIL: one "<what> <status> <detail>" line on the screen and, whole, in
# the log; FAIL and WARN lines are counted for the verdict.
result() {
    local line
    printf -v line '%-20s %-4s %s' "$1" "$2" "$3"
    case $2 in
        FAIL) FAILS=$((FAILS + 1)) ;;
        WARN) WARNS=$((WARNS + 1)) ;;
    esac
    log "$line"
    say "$line"
}

# note LINE: a line that belongs to the result above it, on the screen and in the log.
note() {
    log "$1"
    say "$1"
}

# fit_screen STEP: prints the screen. A line wider than SCREEN_COLS ends in "~"; with more than
# SCREEN_LINES lines the last line becomes "... N more lines, see: gpu_lab.sh show STEP".
fit_screen() {
    local step=$1 total=${#SCREEN[@]} keep i line note
    local out=()
    keep=$total
    ((total > SCREEN_LINES)) && keep=$((SCREEN_LINES - 1))
    for ((i = 0; i < keep; i++)); do
        line=${SCREEN[i]}
        line=${line%"${line##*[![:space:]]}"}
        if ((${#line} > SCREEN_COLS)); then
            line="${line:0:SCREEN_COLS-1}~"
        fi
        out+=("$line")
    done
    if ((total > keep)); then
        note="... $((total - keep)) more lines, see: gpu_lab.sh show $step"
        out+=("${note:0:SCREEN_COLS}")
    fi
    printf '%s\n' "${out[@]}"
}

# ---- hosts -----------------------------------------------------------------------------------

# resolve_hosts HOST...: sets HOSTS from the arguments, else from the nodes of $PARTITION.
resolve_hosts() {
    HOSTS=("$@")
    ((${#HOSTS[@]})) && return 0
    if ! command -v sinfo >/dev/null; then
        echo "gpu_lab: no HOST given and no sinfo to list the nodes of $PARTITION" >&2
        return 1
    fi
    mapfile -t HOSTS < <(sinfo -h -N -p "$PARTITION" -o %N | sort -u)
    if ((${#HOSTS[@]} == 0)); then
        echo "gpu_lab: sinfo lists no nodes in partition $PARTITION" >&2
        return 1
    fi
}

# ---- status ----------------------------------------------------------------------------------

# find_preset: sets PRESET_FILE to the *.json file that defines $PRESET (empty when none does) and
# PRESET_DIRS to the folders searched.
find_preset() {
    local d f
    PRESET_FILE=
    if [[ -n ${CRAB_PRESETS_DIR:-} ]]; then
        PRESET_DIRS=("$CRAB_PRESETS_DIR")
    elif [[ -n ${CRAB_PATH_PRESETS:-} ]]; then
        PRESET_DIRS=("$CRAB_PATH_PRESETS")
    else
        PRESET_DIRS=("$CHECKOUT/local/presets" "${XDG_CONFIG_HOME:-$HOME/.config}/crab/presets")
    fi
    for d in "${PRESET_DIRS[@]}"; do
        for f in "$d"/*.json; do
            [[ -f $f && $(<"$f") == *"\"$PRESET\""* ]] || continue
            PRESET_FILE=$f
            return 0
        done
    done
    return 1
}

# short PATH: PATH relative to the checkout when it is inside it.
short() {
    local p=$1
    echo "${p#"$CHECKOUT"/}"
}

# cut_host WIDTH HOST: sets SHOWN to HOST when it fits in WIDTH columns; else the name without its
# domain; else that name with its middle replaced by "~" (the end of a node name tells nodes apart).
cut_host() {
    local width=$1 keep_start keep_end
    SHOWN=$2
    ((${#SHOWN} <= width)) && return 0
    SHOWN=${SHOWN%%.*}
    ((${#SHOWN} <= width)) && return 0
    keep_start=$((width / 2))
    keep_end=$((width - 1 - keep_start))
    SHOWN="${SHOWN:0:keep_start}~${SHOWN: -keep_end}"
}

status_checkout() {
    local branch sha dirty=
    if command -v git >/dev/null && branch=$(git -C "$CHECKOUT" rev-parse --abbrev-ref HEAD 2>/dev/null) &&
        sha=$(git -C "$CHECKOUT" rev-parse --short HEAD 2>/dev/null); then
        git -C "$CHECKOUT" diff --quiet HEAD -- 2>/dev/null || dirty=" (uncommitted changes)"
        result checkout OK "$branch $sha$dirty"
    else
        result checkout WARN "no git or not a git checkout: cannot name the commit"
    fi
}

status_preset() {
    local p dir
    if find_preset; then
        p=$(short "$PRESET_FILE")
        [[ $p == "$HOME"/* ]] && p="~${p#"$HOME"}"
        ((${#p} > 60)) && p=${p##*/}
        result preset OK "$p"
    else
        dir=$(short "${PRESET_DIRS[0]}")
        result preset FAIL "no *.json in ${PRESET_DIRS[*]} defines $PRESET"
        note "  fix: cp examples/nplb/presets/$PRESET.json $dir/   (mkdir -p $dir first if it is missing)"
    fi
}

# status_validate: crab validate of the smoke file and of the two full suites.
status_validate() {
    local item what file out rc first line
    for item in smoke:smoke_gpu_a2a_comm.json a2a:gpu_a2a_comm.json allgather:gpu_ag_comm.json; do
        what=${item%%:*}
        file=${item#*:}
        log_section "crab validate -p $PRESET examples/nplb/h100/$file"
        if ! command -v "$CRAB" >/dev/null; then
            log "crab not found: $CRAB"
            result "validate $what" FAIL "crab not found: $CRAB (set CRAB)"
            continue
        fi
        out=$("$CRAB" validate -p "$PRESET" "$HERE/h100/$file" 2>&1)
        rc=$?
        log "$out"
        log "exit $rc"
        if ((rc == 0)); then
            result "validate $what" OK "$file"
            continue
        fi
        first=
        while IFS= read -r line; do
            if [[ -n ${line//[[:space:]]/} ]]; then
                first=$line
                break
            fi
        done <<<"$out"
        result "validate $what" FAIL "${first:-crab validate exited $rc with no output}"
    done
}

# status_preflight: runs gpu_preflight.sh on HOSTS; sets CELL and DETAIL ("host check" -> value),
# CHECKS (the grid columns) and NROWS.
status_preflight() {
    local out rc h c s d errfile=$RUN_DIR/status.preflight.err first
    NROWS=0
    CHECKS=(state ports gpus peermem mpirun ucx blink nfs sr)
    CELL=()
    DETAIL=()
    log_section "gpu_preflight.sh ${HOSTS[*]}"
    out=$(bash "$GPU_PREFLIGHT" "${HOSTS[@]}" 2>"$errfile")
    rc=$?
    log "$out"
    log_section "gpu_preflight.sh stderr (exit $rc)"
    cat "$errfile" >>"$LOG"
    while read -r h c s d; do
        [[ $s == OK || $s == WARN || $s == FAIL ]] || continue
        NROWS=$((NROWS + 1))
        CELL["$h $c"]=$s
        DETAIL["$h $c"]=$d
        [[ " ${CHECKS[*]} " == *" $c "* ]] || CHECKS+=("$c")
        case $s in
            FAIL) FAILS=$((FAILS + 1)) ;;
            WARN) WARNS=$((WARNS + 1)) ;;
        esac
    done <<<"$out"
    if ((NROWS == 0)); then
        first=
        IFS= read -r first <"$errfile"
        result preflight FAIL "${first:-gpu_preflight.sh exited $rc with no check lines}"
    fi
    rm -f "$errfile"
}

# status_grid: the host x check table, then the detail of every cell that is not OK.
status_grid() {
    local hostw=4 h c w cell line extra=0 shown_details=0
    for h in "${HOSTS[@]}"; do ((${#h} > hostw)) && hostw=${#h}; done
    ((hostw > HOST_COLS)) && hostw=$HOST_COLS
    printf -v line '%-*s' "$hostw" node
    for c in "${CHECKS[@]}"; do
        w=${#c}
        ((w < 4)) && w=4
        printf -v line '%s  %-*s' "$line" "$w" "$c"
    done
    say ""
    say "$line"
    for h in "${HOSTS[@]}"; do
        cut_host "$hostw" "$h"
        printf -v line '%-*s' "$hostw" "$SHOWN"
        for c in "${CHECKS[@]}"; do
            w=${#c}
            ((w < 4)) && w=4
            case ${CELL["$h $c"]:-} in
                OK) cell=ok ;;
                WARN) cell=WARN ;;
                FAIL) cell=FAIL ;;
                *) cell=- ;;
            esac
            printf -v line '%s  %-*s' "$line" "$w" "$cell"
        done
        say "$line"
    done
    for h in "${HOSTS[@]}"; do
        for c in "${CHECKS[@]}"; do
            case ${CELL["$h $c"]:-} in
                WARN | FAIL)
                    if ((shown_details < DETAIL_LINES)); then
                        ((shown_details == 0)) && say ""
                        cut_host "$hostw" "$h"
                        sayf '%-*s %-8s %s' "$hostw" "$SHOWN" "$c" "${DETAIL["$h $c"]}"
                        shown_details=$((shown_details + 1))
                    else
                        extra=$((extra + 1))
                    fi
                    ;;
            esac
        done
    done
    ((extra > 0)) && sayf '... %d more cells not OK, see: gpu_lab.sh show status' "$extra"
    return 0
}

cmd_status() {
    resolve_hosts "$@" || return 2
    start_run status
    local verdict
    sayf 'gpu_lab status  %s  %d hosts  log: gpu_lab/%s/status.log' "${RUN_DIR##*/}" "${#HOSTS[@]}" "${RUN_DIR##*/}"
    log "hosts: ${HOSTS[*]}"
    status_checkout
    status_preset
    status_validate
    status_preflight
    ((NROWS > 0)) && status_grid
    printf -v verdict 'status: %d FAIL, %d WARN' "$FAILS" "$WARNS"
    say ""
    say "$verdict"
    log_section "verdict"
    log "$verdict"
    fit_screen status
    ((FAILS == 0))
}

# ---- show ------------------------------------------------------------------------------------

# cmd_show STEP: prints the log of STEP from the newest run that has one.
cmd_show() {
    local step=${1:-} dirs i
    if (($# != 1)); then
        echo "gpu_lab: show takes one STEP: $STEPS" >&2
        return 2
    fi
    case " $STEPS " in
        *" $step "*) ;;
        *)
            echo "gpu_lab: unknown step '$step' (one of: $STEPS)" >&2
            return 2
            ;;
    esac
    dirs=("$WORK"/gpu_lab/[0-9]*/)
    for ((i = ${#dirs[@]} - 1; i >= 0; i--)); do
        if [[ -f ${dirs[i]}$step.log ]]; then
            cat "${dirs[i]}$step.log"
            return 0
        fi
    done
    echo "gpu_lab: no log of step '$step' under $WORK/gpu_lab (run it first)" >&2
    return 2
}

# ---- dispatch --------------------------------------------------------------------------------

case ${1:-} in
    -h | --help)
        usage
        exit 0
        ;;
    "")
        usage >&2
        exit 2
        ;;
    status)
        shift
        cmd_status "$@"
        exit $?
        ;;
    show)
        shift
        cmd_show "$@"
        exit $?
        ;;
    smoke | dual-port | pin | all)
        echo "gpu_lab: step '$1' is not available yet" >&2
        exit 2
        ;;
    *)
        echo "gpu_lab: unknown subcommand '$1'" >&2
        usage >&2
        exit 2
        ;;
esac
