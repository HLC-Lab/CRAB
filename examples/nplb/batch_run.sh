#!/usr/bin/env bash
# Run campaign rows one after the other in the background, so the console can log out.
#   nplb batch start [--repeat K] ROW...   ROW is N or A-B; returns at once, the runner survives the logout;
#                              --repeat K runs the whole row list K times (rounds 2..K pass --force, so DONE
#                              rows run again: more samples of the same cells)
#   nplb batch status          paged report: alive or not, one line per row, the tail of the log
#   nplb batch stop            asks [y/N], then stops the runner after killing its process group;
#                              the Slurm job of the current row keeps running (nplb cancel stops it)
# The runner (setsid + nohup, stdin from /dev/null) calls `nplb run N` for each row in the given order: that
# checks the gates, submits the block, follows the job to its end and updates campaign_runs.tsv, exactly as
# by hand. A refused or failed row is logged and the next row starts anyway. After the last row it writes
# `nplb report` into the log. Files go to $WORK/batch: batch_<time>.log (everything the rows print),
# batch_<time>.tsv (row, start, end, exit code, one line per finished row), runner.pid, current (the name of
# the newest batch). Only one runner at a time: start refuses while the pid in runner.pid is a live batch
# runner (checked in /proc, no signal sent). The status verdict is RUNNING, FINISHED or DIED (the runner is
# gone and did not write its end line, e.g. the node rebooted).
# Exit: 0, 1 (refused, declined, nothing to report), 2 bad usage.
# Env: WORK (else NPLB_WORK, default <checkout>/local/nplb-work), NPLB_CMD (default the nplb dispatcher next
# to this file, run with bash), NPLB_YES=1 (stop asks nothing), NPLB_REPORT_DIR.
set -u

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CHECKOUT=$(cd "$HERE/../.." && pwd)
# shellcheck source=lib/pages.sh
. "$HERE/lib/pages.sh"
WORK=${WORK:-${NPLB_WORK:-$CHECKOUT/local/nplb-work}}
BATCH_DIR=$WORK/batch
PID_FILE=$BATCH_DIR/runner.pid
read -ra NPLB_RUN <<<"${NPLB_CMD:-bash $HERE/nplb}"
TAIL=25

now() { date '+%Y-%m-%d %H:%M:%S'; }
log() { printf '[%s] batch: %s\n' "$(now)" "$*"; }

usage() {
    echo "usage: nplb batch start [--repeat K] ROW... (N or A-B) | nplb batch status | nplb batch stop" >&2
    exit 2
}

# runner_pid: the pid of runner.pid when it is a live batch runner, else nothing (no signal is sent)
runner_pid() {
    local pid
    pid=$(cat "$PID_FILE" 2>/dev/null) || return 1
    [[ $pid =~ ^[0-9]+$ ]] || return 1
    [[ -r /proc/$pid/cmdline ]] || return 1
    tr '\0' ' ' 2>/dev/null <"/proc/$pid/cmdline" | grep -q 'batch_run.sh _loop' || return 1
    echo "$pid"
}

# expand_rows ROW...: N or A-B into one number per line; bad input is a usage error
expand_rows() {
    local arg a b i
    for arg in "$@"; do
        if [[ $arg =~ ^[0-9]+$ ]]; then
            echo "$arg"
        elif [[ $arg =~ ^([0-9]+)-([0-9]+)$ ]]; then
            a=${BASH_REMATCH[1]} b=${BASH_REMATCH[2]}
            if ((a > b)); then
                echo "batch: range $arg goes backwards" >&2
                return 2
            fi
            for ((i = a; i <= b; i++)); do echo "$i"; done
        else
            echo "batch: '$arg' is not a row (N or A-B)" >&2
            return 2
        fi
    done
}

cmd_start() {
    local rows pid name repeat=1
    if [[ ${1:-} == --repeat ]]; then
        if [[ ! ${2:-} =~ ^[1-9][0-9]*$ ]]; then
            echo "batch: --repeat needs a whole number of rounds, 1 or more" >&2
            exit 2
        fi
        repeat=$2
        shift 2
    fi
    (($# > 0)) || usage
    rows=$(expand_rows "$@") || exit 2
    mkdir -p "$BATCH_DIR" || exit 1
    if pid=$(runner_pid); then
        echo "REFUSED: a batch is already running (pid $pid): nplb batch status, or nplb batch stop"
        exit 1
    fi
    name=batch_$(date +%Y%m%d_%H%M%S)
    echo "$name" >"$BATCH_DIR/current"
    : >"$BATCH_DIR/$name.tsv"
    # shellcheck disable=SC2086 # rows is one number per line, split on purpose
    setsid nohup bash "$HERE/batch_run.sh" _loop "$name" "$repeat" $rows \
        >>"$BATCH_DIR/$name.log" 2>&1 </dev/null &
    echo "$!" >"$PID_FILE"
    # shellcheck disable=SC2086,SC2116 # join the rows on one line
    echo "started: rows $(echo $rows) x $repeat rounds in the background (pid $!), one at a time"
    echo "log $BATCH_DIR/$name.log"
    echo "you can log out now; later: nplb batch status"
}

# _loop NAME K ROW...: the runner itself, started by cmd_start; stdout and stderr are the log
cmd_loop() {
    local name=$1 repeat=$2 round row start rc
    local force=()
    shift 2
    log "start, rows $* x $repeat rounds"
    for ((round = 1; round <= repeat; round++)); do
        ((round > 1)) && force=(--force)
        for row in "$@"; do
            start=$(now)
            log "row $row (round $round of $repeat): nplb run $row ${force[*]}"
            "${NPLB_RUN[@]}" run "$row" "${force[@]}"
            rc=$?
            printf '%s\t%s\t%s\t%s\n' "$row" "$start" "$(now)" "$rc" >>"$BATCH_DIR/$name.tsv"
            log "row $row (round $round of $repeat): ended, exit $rc"
        done
    done
    log "all rows done, nplb report follows"
    "${NPLB_RUN[@]}" report
    log "end"
}

cmd_status() {
    local name pid verdict rows_done=0 row start end rc
    name=$(cat "$BATCH_DIR/current" 2>/dev/null)
    if [[ -z $name || ! -f $BATCH_DIR/$name.log ]]; then
        echo "no batch yet: nplb batch start ROW..."
        exit 1
    fi
    if pid=$(runner_pid); then
        verdict="RUNNING (pid $pid)"
    elif grep -q 'batch: end$' "$BATCH_DIR/$name.log"; then
        verdict=FINISHED
    else
        verdict="DIED (the runner is gone before its end line)"
    fi
    rows_done=$(grep -c . "$BATCH_DIR/$name.tsv" 2>/dev/null)
    {
        echo "VERDICT: $verdict, $name, ${rows_done:-0} rows ended"
        echo "$(grep -m1 'batch: start, rows' "$BATCH_DIR/$name.log" | sed 's/^.*batch: //')"
        echo "row  started              ended                exit"
        while IFS=$'\t' read -r row start end rc; do
            printf '%-4s %-20s %-20s %s%s\n' "$row" "$start" "$end" "$rc" \
                "$( ((rc == 0)) || echo '  (refused or failed: see the log)')"
        done <"$BATCH_DIR/$name.tsv"
        echo "-- last $TAIL lines of $BATCH_DIR/$name.log --"
        tail -n "$TAIL" "$BATCH_DIR/$name.log" | sed 's/\x1b\[[0-9;]*m//g' | cut -c1-100
        echo "more: nplb report, nplb queue, nplb follow"
    } | nplb_paged batch-status
}

cmd_stop() {
    local pid answer
    if ! pid=$(runner_pid); then
        echo "no batch runner is running"
        exit 1
    fi
    if [[ ${NPLB_YES:-0} != 1 ]]; then
        read -r -p "Stop the batch runner (pid $pid)? The current Slurm job keeps running. [y/N] " answer
        if [[ $answer != y && $answer != Y ]]; then
            echo "declined: nothing changed"
            exit 1
        fi
    fi
    # setsid made the runner a process group leader: its group is the runner and its nplb run children
    kill -TERM -- "-$pid" 2>/dev/null
    log "stopped by nplb batch stop" >>"$BATCH_DIR/$(cat "$BATCH_DIR/current").log"
    rm -f "$PID_FILE"
    echo "stopped: the runner is gone; the Slurm job of the current row (if any) still runs: nplb queue,"
    echo "nplb follow to watch it, nplb cancel to stop it"
}

sub=${1:-}
[[ $# -gt 0 ]] && shift
case $sub in
    start) cmd_start "$@" ;;
    _loop) cmd_loop "$@" ;;
    status) cmd_status ;;
    stop) cmd_stop ;;
    *) usage ;;
esac
