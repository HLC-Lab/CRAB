#!/usr/bin/env bash
# One-screen lab session for the NPLB H100 GPU suites. Run it on the node that holds the CRAB
# checkout; every step prints at most one screen (38 lines x 100 columns) that can be photographed,
# and keeps the full detail in a log:
#   examples/nplb/gpu_lab.sh status [HOST...]   checkout, preset, crab validate of the three GPU
#                                               configs, and a node x check grid (gpu_preflight.sh)
#   examples/nplb/gpu_lab.sh smoke [HOST...]    submits the smoke config with a plain `crab run`,
#                                               waits for the job, then shows: did it run, what the
#                                               victim measured, what the full suites will cost,
#                                               and the NIC counters before/after (HOST...: the
#                                               nodes whose counters are read)
#   examples/nplb/gpu_lab.sh dual-port [CARD]   one H100 card (0-7, default 0), GPU memory, 2 nodes:
#                                               Gb/s with one port and with both ports, through UCX
#                                               (Blink pingpong_b -gpu) and raw (ib_write_bw
#                                               --use_cuda), the ratios, the per-port counters and a
#                                               verdict; inside an allocation of 2 nodes, or it asks
#                                               salloc for one (details: gpu_lab_dual.sh)
#   examples/nplb/gpu_lab.sh show STEP          print the full log of the last run of STEP
#   examples/nplb/gpu_lab.sh -h                 this text
# Steps still to come (they print "not available yet" and exit 2): pin, all.
# With no HOST, the nodes of $PARTITION (sinfo). `status` never stops early: it is a dashboard.
# `smoke` submits one real job (the only thing besides read-only checks that this script starts),
# stops before the submit when the preset or the smoke config is not usable, never cancels the job
# (on a timeout it prints how to look at it and how to cancel it yourself) and never touches a node
# setting. Exit status of `smoke`: 0 when the job completed and every victim app has rows.
# Each run writes $WORK/gpu_lab/<YYYYmmdd_HHMMSS>/<step>.log; $WORK/gpu_lab/latest points at the
# newest run. A screen line is "<what> OK|WARN|FAIL <detail>"; a line cut at 100 columns ends in
# "~" and, when lines were cut, a line says how many and names `gpu_lab.sh show STEP`.
# Nothing is changed on any node; the only files written are the logs under $WORK.
# Exit status: 0 nothing failed, 1 a check failed, 2 bad usage or missing setup (no hosts, no log).
# Env: PARTITION (h100_4nodes), WORK (<checkout>/local/nplb-work), NODES ("a b": the 2 nodes of
#      `dual-port`; same-leaf pairs give the cleanest numbers), CRAB (the crab executable; default
#      <checkout>/.venv/bin/crab when it is executable, else crab), CRAB_PRESETS_DIR (the folder
#      searched for the preset; default: CRAB_PATH_PRESETS, else <checkout>/local/presets and then
#      $XDG_CONFIG_HOME or ~/.config/crab/presets), PREFLIGHT_SSH (how gpu_preflight.sh reaches the
#      nodes, default ssh), GPU_PREFLIGHT (default: gpu_preflight.sh next to this script),
#      SMOKE_JSON (the config `smoke` submits; default h100/smoke_gpu_a2a_comm.json, the allgather
#      smoke file works too), SMOKE_POLL (seconds between two `crab status` calls, default 10),
#      SMOKE_TIMEOUT (seconds to wait for the job, default 1800), PYTHON (reads JSON, logs and CSVs;
#      default <checkout>/.venv/bin/python when it is executable, else python3). `dual-port` also
#      reads SALLOC, GPU_LAB_ALLOC, ALLOC_TIMEOUT, STEP_TIMEOUT, SIZE, DUR, DUAL_ITER, DUAL_WARMUP,
#      RAW_SETTLE, LAB, BLINK and CUDA_LIB: gpu_lab_dual.sh lists them.
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
SMOKE_JSON=${SMOKE_JSON:-$HERE/h100/smoke_gpu_a2a_comm.json}
SMOKE_POLL=${SMOKE_POLL:-10}
SMOKE_TIMEOUT=${SMOKE_TIMEOUT:-1800}
readonly UNKNOWN_POLLS=30 # polls in a row without a job state before `smoke` gives up waiting
PYTHON=${PYTHON:-}
if [[ -z $PYTHON ]]; then
    if [[ -x $CHECKOUT/.venv/bin/python ]]; then PYTHON=$CHECKOUT/.venv/bin/python; else PYTHON=python3; fi
fi

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

# fit_line LINE: LINE without trailing blanks, cut to SCREEN_COLS with a final "~" when too wide.
fit_line() {
    local line=$1
    line=${line%"${line##*[![:space:]]}"}
    if ((${#line} > SCREEN_COLS)); then
        line="${line:0:SCREEN_COLS-1}~"
    fi
    FITTED=$line
}

# fit_screen STEP [TAIL]: prints the screen. A line wider than SCREEN_COLS ends in "~"; with more
# than SCREEN_LINES lines a line before the last TAIL lines (default 0) says "... N more lines,
# see: gpu_lab.sh show STEP", and the TAIL lines (the verdict) are never the ones cut.
fit_screen() {
    local step=$1 tail=${2:-0} total=${#SCREEN[@]} keep i note
    local out=()
    keep=$total
    ((total > SCREEN_LINES)) && keep=$((SCREEN_LINES - 1 - tail))
    for ((i = 0; i < keep; i++)); do
        fit_line "${SCREEN[i]}"
        out+=("$FITTED")
    done
    if ((total > SCREEN_LINES)); then
        note="... $((total - keep - tail)) more lines, see: gpu_lab.sh show $step"
        out+=("${note:0:SCREEN_COLS}")
        for ((i = total - tail; i < total; i++)); do
            fit_line "${SCREEN[i]}"
            out+=("$FITTED")
        done
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

# validate_one WHAT FILE: `crab validate -p $PRESET FILE`, one "validate WHAT" line.
validate_one() {
    local what=$1 file=$2 out rc first line
    log_section "crab validate -p $PRESET $(short "$file")"
    if ! command -v "$CRAB" >/dev/null; then
        log "crab not found: $CRAB"
        result "validate $what" FAIL "crab not found: $CRAB (set CRAB)"
        return
    fi
    out=$("$CRAB" validate -p "$PRESET" "$file" 2>&1)
    rc=$?
    log "$out"
    log "exit $rc"
    if ((rc == 0)); then
        result "validate $what" OK "${file##*/}"
        return
    fi
    first=
    while IFS= read -r line; do
        if [[ -n ${line//[[:space:]]/} ]]; then
            first=$line
            break
        fi
    done <<<"$out"
    result "validate $what" FAIL "${first:-crab validate exited $rc with no output}"
}

# status_validate: crab validate of the smoke file and of the two full suites.
status_validate() {
    local item
    for item in smoke:smoke_gpu_a2a_comm.json a2a:gpu_a2a_comm.json allgather:gpu_ag_comm.json; do
        validate_one "${item%%:*}" "$HERE/h100/${item#*:}"
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

# ---- smoke -----------------------------------------------------------------------------------

JOB=
DATA_DIR=
JOB_STATE=
TIMED_OUT=0
POINTED=0
CNT_FILE=
CNT_OK=0
CNT_WARN=
CNT_LINES=()
WALL=?
ERRLINE=()
TABLE=()
ESTIMATE=()

# first_line TEXT / last_line TEXT: the first / last line of TEXT that is not blank.
first_line() {
    local line
    while IFS= read -r line; do
        if [[ -n ${line//[[:space:]]/} ]]; then
            echo "$line"
            return
        fi
    done <<<"$1"
}

last_line() {
    local line last=
    while IFS= read -r line; do
        [[ -n ${line//[[:space:]]/} ]] && last=$line
    done <<<"$1"
    echo "$last"
}

# fmt_secs SECONDS: 42s, 3m05s, 1h02m.
fmt_secs() {
    local s=$1
    if ((s < 60)); then
        echo "${s}s"
    elif ((s < 3600)); then
        printf '%dm%02ds\n' $((s / 60)) $((s % 60))
    else
        printf '%dh%02dm\n' $((s / 3600)) $((s % 3600 / 60))
    fi
}

# The analysis of a finished run, in python (stdlib only). Arguments: data dir, smoke config, the
# two full configs, 1 when the job completed. It prints tagged lines separated by US (0x1f):
# W wall time of the job, R/Q result lines (screen+log / log only, both counted), M screen note,
# E first error (file, line), T table lines, X estimate lines, L log lines.
IFS= read -r -d '' SMOKE_PY <<'PY' || true
import csv
import glob
import json
import math
import os
import re
import statistics
import sys
from collections import defaultdict

SEP = "\x1f"
OUT = []
ANSI = re.compile(r"\x1b\[[0-9;]*m")
STAMP = re.compile(r"^\[(\d+):(\d+):(\d+)\]")
EVENT = re.compile(
    r"^\[(\d+):(\d+):(\d+)\]\s+\w+\s+CRAB\s+│\s+(\S+)\s+│\s+Run\s+(\d+)\s+│\s+"
    r"App\s+(\d+)\s+(Launched|FINISHED|FAILED)\b(.*)$"
)
ELAPSED = re.compile(r"Engine run finished\s+elapsed=(\d+):(\d+):(\d+)")
SHOWN_PROBLEMS = 5
MAX_ERROR_LINES = 20


def emit(tag, *fields):
    OUT.append(SEP.join([tag] + [re.sub(r"[\x1f\t\r\n]+", " ", str(f)) for f in fields]))


def truthy(value):
    return value is True or str(value).strip().lower() in ("1", "true", "yes", "on")


def flag(args, name):
    found = re.search(r"(?:^|\s)%s\s+(\d+)" % re.escape(name), args or "")
    return int(found.group(1)) if found else 0


def load_suite(path):
    """(runs, {experiment: {kind, apps}}): the apps that collect data, with ppn, size, iterations."""
    with open(path, encoding="utf-8") as handle:
        cfg = json.load(handle)
    opts = cfg.get("global_options", {})
    runs = int(float(opts.get("maxruns", 1) or 1))
    default_ppn = int(float(opts.get("ppn", 1) or 1))
    suite = {}
    for name, exp in cfg.get("experiments", {}).items():
        apps = exp.get("apps", {})
        versus = any(not truthy(a.get("collect", False)) for a in apps.values())
        victims = []
        for app_id, app in apps.items():
            if not truthy(app.get("collect", False)):
                continue
            args = app.get("args", "")
            victims.append(
                {
                    "id": str(app_id),
                    "ppn": int(float(app.get("ppn", default_ppn))),
                    "size": flag(args, "-msgsize"),
                    "iters": flag(args, "-iter") + flag(args, "-warmup"),
                }
            )
        suite[name] = {"kind": "vs" if versus else "alone", "apps": victims}
    return runs, suite


def span(first, last):
    """Seconds from first to last (both seconds since midnight): +24 h when the clock wrapped."""
    delta = last - first
    return delta + 86400 if delta < 0 else delta


def read_log(path):
    """({(experiment, app): {run: {start, end, state, exit}}}, elapsed seconds or None, stamps)."""
    events = {}
    elapsed = None
    stamps = []
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return events, None, stamps
    for raw in text.splitlines():
        line = ANSI.sub("", raw)
        stamp = STAMP.match(line)
        if stamp:
            h, m, s = (int(x) for x in stamp.groups())
            stamps.append(h * 3600 + m * 60 + s)
        done = ELAPSED.search(line)
        if done:
            h, m, s = (int(x) for x in done.groups())
            elapsed = h * 3600 + m * 60 + s
        found = EVENT.match(line)
        if not found:
            continue
        h, m, s, exp, run, app, kind, rest = found.groups()
        now = int(h) * 3600 + int(m) * 60 + int(s)
        entry = events.setdefault((exp, app), {}).setdefault(run, {})
        if kind == "Launched":
            entry["start"] = now
        else:
            entry["end"] = now
            entry["state"] = kind
            code = re.search(r"exit=(-?\d+)", rest)
            entry["exit"] = code.group(1) if code else "?"
    return events, elapsed, stamps


def app_state(events, exp, app):
    """(state, exit code, wall seconds per run): ok, failed, unfinished, or unknown (no lines)."""
    runs = events.get((exp, app))
    if not runs:
        return "unknown", None, []
    walls = []
    state = "ok"
    code = None
    for entry in runs.values():
        if "end" not in entry:
            if state == "ok":
                state = "unfinished"
            continue
        if entry["state"] == "FAILED":
            state = "failed"
            code = entry["exit"]
        if "start" in entry:
            walls.append(span(entry["start"], entry["end"]))
    return state, code, walls


def read_csv(path):
    """None when the file is missing, else {rows, medians (seconds), size}."""
    try:
        handle = open(path, encoding="utf-8", errors="replace", newline="")
    except OSError:
        return None
    with handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        columns = reader.fieldnames or []
    column = next((c for c in columns if c.endswith("_Median-Duration_s")), None)
    medians = []
    size = None
    for row in rows:
        try:
            value = float(row[column]) if column else 0.0
        except (TypeError, ValueError):
            value = 0.0
        if math.isfinite(value) and value > 0:
            medians.append(value)
        if size is None:
            try:
                size = int(float(row["msg_size"]))
            except (KeyError, TypeError, ValueError):
                size = None
    return {"rows": len(rows), "medians": medians, "size": size}


def fmt_time(seconds):
    us = seconds * 1e6
    if us < 999.5:
        return "%.3g us" % us
    ms = seconds * 1e3
    if ms < 999.5:
        return "%.3g ms" % ms
    return "%.3g s" % seconds


def fmt_dur(seconds):
    total = int(round(seconds))
    if total < 60:
        return "%ds" % total
    if total < 3600:
        return "%dm%02ds" % (total // 60, total % 60)
    return "%dh%02dm" % (total // 3600, total % 3600 // 60)


def fmt_size(size):
    for unit, name in ((1 << 30, "GiB"), (1 << 20, "MiB"), (1 << 10, "KiB")):
        if size >= unit:
            value = size / unit
            return ("%d%s" % (value, name)) if size % unit == 0 else ("%.3g%s" % (value, name))
    return "%dB" % size


def error_sources(data_dir):
    """First error line (relative path, line) over the error logs; the logs go to the log whole."""
    files = sorted(glob.glob(os.path.join(data_dir, "*", "error_app_*.log")))
    files.append(os.path.join(data_dir, "slurm_error.log"))
    first = None
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines = [ln.rstrip() for ln in handle if ln.strip()]
        except OSError:
            continue
        if not lines:
            continue
        rel = os.path.relpath(path, data_dir)
        emit("L", "-- %s (first %d non-blank lines) --" % (rel, MAX_ERROR_LINES))
        for line in lines[:MAX_ERROR_LINES]:
            emit("L", line)
        if first is None:
            first = (rel, lines[0])
    if first:
        emit("E", first[0], first[1])


def predict(points, size):
    """Per-iteration time at `size`: log-log between (or beyond) the two nearest measured sizes,
    never below the smallest measured time."""
    sizes = sorted(points)
    floor = min(points.values())
    if len(sizes) == 1 or size <= 0:
        return points[sizes[0]]
    if size <= sizes[0]:
        low, high = sizes[0], sizes[1]
    elif size >= sizes[-1]:
        low, high = sizes[-2], sizes[-1]
    else:
        low, high = next((a, b) for a, b in zip(sizes, sizes[1:]) if a <= size <= b)
    slope = (math.log(points[high]) - math.log(points[low])) / (math.log(high) - math.log(low))
    value = math.exp(math.log(points[low]) + slope * (math.log(size) - math.log(low)))
    return max(value, floor)


def measured_for(measured, ppn, kind):
    """The measured points for (ppn, kind) and the (ppn, kind) they come from: the same ppn when
    smoke ran it, else the nearest ppn (the larger one on a tie) of the same kind."""
    if (ppn, kind) in measured:
        return measured[(ppn, kind)], (ppn, kind)
    keys = [k for k in measured if k[1] == kind] or list(measured)
    best = min(keys, key=lambda k: (abs(math.log(k[0] / ppn)), -k[0]))
    return measured[best], best


def suite_seconds(runs, suite, measured, startup, label):
    total = 0.0
    borrowed = set()
    for name, exp in suite.items():
        seconds = 0.0
        for app in exp["apps"]:
            points, source = measured_for(measured, app["ppn"], exp["kind"])
            if source != (app["ppn"], exp["kind"]):
                borrowed.add((app["ppn"], exp["kind"], source[0]))
            seconds += runs * (app["iters"] * predict(points, app["size"]) + startup)
        emit("L", "  %s %s: ~%.0f s" % (label, name, seconds))
        total += seconds
    for ppn, kind, source in sorted(borrowed):
        emit("L", "  %s: ppn %d (%s) not measured by smoke, uses ppn %d" % (label, ppn, kind, source))
    return total


def minutes(seconds):
    return max(1, int(seconds / 60.0 + 0.5))


def estimate(smoke, measured, startup, a2a_path, ag_path):
    if not measured or startup is None:
        emit("X", "full suites: no estimate (no data)")
        return
    try:
        a2a_runs, a2a = load_suite(a2a_path)
        ag_runs, ag = load_suite(ag_path)
    except (OSError, ValueError, KeyError) as err:
        emit("X", "full suites: no estimate (cannot read the full configs: %s)" % err)
        return
    emit("L", "full suites model: per app, startup + (iter + warmup) x per-iteration time, times")
    emit("L", "  the runs of the config (maxruns); only the victim apps count, the aggressor runs")
    emit("L", "  alongside. startup %d s = the smallest per-app wall time of this run." % startup)
    emit("L", "  Per-iteration time = median of the median-duration column, per (ppn, alone|vs, size);")
    emit("L", "  other sizes by log-log interpolation or extrapolation between the two nearest smoke")
    emit("L", "  sizes, never below the smallest measured time; a ppn that smoke did not run uses the")
    emit("L", "  nearest ppn measured. a2a and allgather share one time model and this run's numbers.")
    for key in sorted(measured):
        for size in sorted(measured[key]):
            emit("L", "  measured ppn %d %s %s: %s" % (key[0], key[1], fmt_size(size), fmt_time(measured[key][size])))
    first = suite_seconds(a2a_runs, a2a, measured, startup, "a2a")
    second = suite_seconds(ag_runs, ag, measured, startup, "allgather")
    smoke_iters = sorted({a["iters"] for e in smoke.values() for a in e["apps"] if a["iters"]})
    full_iters = sorted({a["iters"] for s in (a2a, ag) for e in s.values() for a in e["apps"]})
    ran = "%d" % smoke_iters[0] if smoke_iters[0] == smoke_iters[-1] else "%d..%d" % (smoke_iters[0], smoke_iters[-1])
    emit(
        "X",
        "full suites: ~%d + ~%d min (rough: smoke ran %s iterations per app, full runs %d..%d)"
        % (minutes(first), minutes(second), ran, full_iters[0], full_iters[-1]),
    )


def analyze(data_dir, smoke_path, a2a_path, ag_path, job_ok):
    _, smoke = load_suite(smoke_path)
    events, elapsed, stamps = read_log(os.path.join(data_dir, "slurm_output.log"))
    if elapsed is None and len(stamps) > 1:
        elapsed = span(stamps[0], stamps[-1])
        emit("L", "run time: no 'Engine run finished' line, taken from the first and last log time")
    emit("W", fmt_dur(elapsed) if elapsed is not None else "?")
    emit("L", "wall time per app = time of the FINISHED/FAILED line minus the Launched line of the")
    emit("L", "  same app and run in slurm_output.log (HH:MM:SS, 1 s resolution, +24 h across midnight);")
    emit("L", "  an app with no Launched/FINISHED lines shows ? and is not an error.")
    expected = done = total_rows = 0
    problems = []
    table = []
    measured = defaultdict(lambda: defaultdict(list))
    walls_all = []
    for name, exp in smoke.items():
        exp_dir = os.path.join(data_dir, name)
        present = os.path.isdir(exp_dir)
        if not present and job_ok:
            problems.append((name, "no folder in the data dir (%s)" % name))
        counts, shown_walls, per_size, finished = [], [], defaultdict(list), 0
        for app in exp["apps"]:
            expected += 1
            csv_path = os.path.join(exp_dir, "data_app_%s.csv" % app["id"])
            data = read_csv(csv_path) if present else None
            state, code, walls = app_state(events, name, app["id"])
            rows = data["rows"] if data else 0
            total_rows += rows
            counts.append(str(rows) if data else "-")
            shown_walls.append(fmt_dur(statistics.median(walls)) if walls else "?")
            walls_all.extend(walls)
            if state == "ok" or (state == "unknown" and rows > 0):
                good = rows > 0
            else:
                good = False
            if good:
                finished += 1
            if data and data["medians"]:
                size = data["size"] or app["size"]
                per_size[size].extend(data["medians"])
                measured[(app["ppn"], exp["kind"])][size].extend(data["medians"])
            emit("L", "%s app %s: size %s, %s rows, %s, wall %s, %s" % (
                name, app["id"], fmt_size(data["size"] or app["size"]) if data else fmt_size(app["size"]),
                rows, state + (" exit " + str(code) if code else ""), shown_walls[-1], csv_path))
            if not job_ok or not present:
                continue
            if state == "failed":
                problems.append((name + "/" + app["id"], "exited %s" % code))
            elif state == "unfinished":
                problems.append((name + "/" + app["id"], "launched, never finished"))
            elif data is None:
                problems.append((name + "/" + app["id"], "no data CSV (%s)" % csv_path))
            elif rows == 0:
                problems.append((name + "/" + app["id"], "CSV has 0 rows (%s)" % csv_path))
        done += finished
        table.append((name, "%d/%d" % (finished, len(exp["apps"])), ",".join(counts),
                      ",".join(shown_walls), {s: statistics.median(v) for s, v in per_size.items()}))
    if job_ok:
        if done == expected:
            emit("R", "apps", "OK", "%d/%d victim apps finished, %d rows" % (done, expected, total_rows))
        else:
            emit("R", "apps", "FAIL", "%d/%d victim apps finished" % (done, expected))
    for index, (who, why) in enumerate(problems):
        what = "app " + who if "/" in who else who
        emit("R" if index < SHOWN_PROBLEMS else "Q", what, "FAIL", why)
    if len(problems) > SHOWN_PROBLEMS:
        emit("M", "... %d more problems, see: gpu_lab.sh show smoke" % (len(problems) - SHOWN_PROBLEMS))
    error_sources(data_dir)
    sizes = sorted({s for row in table for s in row[4]})
    name_w = min(max([len("experiment")] + [len(row[0]) for row in table]), 24)
    rows_w = max([len("rows")] + [len(row[2]) for row in table])
    head = "%-*s  %-4s" % (name_w, "experiment", "apps")
    head += "".join("  %9s" % fmt_size(s) for s in sizes)
    emit("T", "%s  %-*s  %s" % (head, rows_w, "rows", "wall/app"))
    for name, apps, counts, walls, medians in table:
        shown = name if len(name) <= name_w else name[: name_w - 1] + "~"
        line = "%-*s  %-4s" % (name_w, shown, apps)
        line += "".join("  %9s" % (fmt_time(medians[s]) if s in medians else "-") for s in sizes)
        emit("T", "%s  %-*s  %s" % (line, rows_w, counts, walls))
    points = {k: {s: statistics.median(v) for s, v in by.items()} for k, by in measured.items()}
    estimate(smoke, points, min(walls_all) if walls_all else None, a2a_path, ag_path)


def main():
    data_dir, smoke_path, a2a_path, ag_path, job_ok = sys.argv[1:6]
    try:
        analyze(data_dir, smoke_path, a2a_path, ag_path, job_ok == "1")
    except (OSError, ValueError, KeyError, TypeError, IndexError) as err:
        emit("R", "analysis", "FAIL", "%s: %s" % (type(err).__name__, err))
    sys.stdout.write("\n".join(OUT) + "\n")


main()
PY

# smoke_gates: what must hold before a job is submitted. Sets nothing; FAILS counts what is wrong.
smoke_gates() {
    if ! command -v "$CRAB" >/dev/null; then
        log "crab not found: $CRAB"
        result crab FAIL "not found: ${CRAB##*/} (set CRAB)"
        return
    fi
    if [[ ! -f $SMOKE_JSON ]]; then
        log "smoke file not found: $SMOKE_JSON"
        result "smoke file" FAIL "not found: ${SMOKE_JSON##*/} (set SMOKE_JSON)"
        return
    fi
    status_preset
    ((FAILS == 0)) && validate_one smoke "$SMOKE_JSON"
}

# smoke_snapshot: the NIC counters before the job, in $RUN_DIR/smoke.counters. A problem is a
# warning (CNT_WARN), never a stop.
smoke_snapshot() {
    local out rc err errfile=$RUN_DIR/smoke.counters.err
    CNT_FILE=$RUN_DIR/smoke.counters
    CNT_OK=0
    CNT_WARN=
    if ((${#HOSTS[@]} == 0)); then
        CNT_WARN="no nodes to read (give HOST... or set PARTITION): counters skipped"
        log "counters: $CNT_WARN"
        return
    fi
    log_section "gpu_preflight.sh counters snapshot (before the job)"
    out=$(bash "$GPU_PREFLIGHT" counters snapshot "$CNT_FILE" "${HOSTS[@]}" 2>"$errfile")
    rc=$?
    err=$(<"$errfile")
    log "$out"
    log "$err"
    log "exit $rc"
    rm -f "$errfile"
    if ((rc == 0)); then
        CNT_OK=1
        return
    fi
    CNT_WARN="snapshot failed: $(first_line "$err")"
    [[ $CNT_WARN == "snapshot failed: " ]] && CNT_WARN="snapshot failed: $(first_line "$out")"
    [[ $CNT_WARN == "snapshot failed: " ]] && CNT_WARN="snapshot failed (exit $rc)"
}

# smoke_diff: the counters again once the job ended; CNT_LINES gets the three summary lines.
smoke_diff() {
    local out rc err line errfile=$RUN_DIR/smoke.counters.err
    CNT_LINES=()
    ((CNT_OK)) || return 0
    log_section "gpu_preflight.sh counters diff (after the job)"
    out=$(bash "$GPU_PREFLIGHT" counters diff "$CNT_FILE" "${HOSTS[@]}" 2>"$errfile")
    rc=$?
    err=$(<"$errfile")
    log "$out"
    log "$err"
    log "exit $rc"
    rm -f "$errfile"
    while IFS= read -r line; do
        case $line in
            "ports with traffic:"* | "ECN/CNP activity:"* | "proxy:"*) CNT_LINES+=("$line") ;;
        esac
    done <<<"$out"
    if ((rc != 0)); then
        CNT_WARN=$(first_line "$err")
        if [[ -z $CNT_WARN ]]; then
            while IFS= read -r line; do
                [[ $line == unreachable* ]] && CNT_WARN=$line
            done <<<"$out"
        fi
        [[ -n $CNT_WARN ]] || CNT_WARN="counters diff exited $rc"
    fi
}

# smoke_submit: crab run --json from the checkout; sets JOB and DATA_DIR, or one FAIL line.
smoke_submit() {
    local out rc err parsed errfile=$RUN_DIR/smoke.submit.err
    log_section "crab run -p $PRESET --json $(short "$SMOKE_JSON")"
    log "$(date '+%F %T') submitted"
    out=$(cd "$CHECKOUT" && "$CRAB" run -p "$PRESET" --json "$SMOKE_JSON" 2>"$errfile")
    rc=$?
    err=$(<"$errfile")
    rm -f "$errfile"
    log "stdout:"
    log "$out"
    log "stderr:"
    log "$err"
    log "exit $rc"
    if ((rc != 0)); then
        result submit FAIL "crab run exited $rc: $(last_line "$err")"
        return 1
    fi
    parsed=$(printf '%s' "$out" | "$PYTHON" -I -c 'import json, sys
d = json.load(sys.stdin)
print(d["job_id"])
print(d["data_dir"])' 2>/dev/null)
    JOB=${parsed%%$'\n'*}
    DATA_DIR=${parsed#*$'\n'}
    if [[ -z $JOB || $DATA_DIR == "$parsed" || -z $DATA_DIR || ! $JOB =~ ^[A-Za-z0-9_.-]+$ ]]; then
        result submit FAIL "crab run printed no job_id and data_dir: $(first_line "$out")"
        return 1
    fi
    [[ $DATA_DIR == /* ]] || DATA_DIR=$CHECKOUT/$DATA_DIR
    log "job $JOB, data dir $DATA_DIR"
}

# smoke_wait: polls `crab status --json JOB` until the job ends. Sets JOB_STATE (the last state seen,
# "no state from crab status" after UNKNOWN_POLLS polls in a row without one) and TIMED_OUT.
smoke_wait() {
    local out tty=0 waited=0 unknown=0 prev=x t0=$SECONDS started=
    [[ -t 1 ]] && tty=1
    JOB_STATE=
    while :; do
        out=$("$CRAB" status --json "$JOB" 2>>"$RUN_DIR/smoke.status.err")
        JOB_STATE=$(printf '%s' "$out" | "$PYTHON" -I -c 'import json, sys
print(json.load(sys.stdin)["jobs"][0]["state"])' 2>/dev/null)
        JOB_STATE=${JOB_STATE%%$'\n'*}
        if [[ $JOB_STATE != "$prev" ]]; then
            log "$(date '+%F %T') job $JOB state ${JOB_STATE:-none}"
            prev=$JOB_STATE
        fi
        case ${JOB_STATE%% *} in
            "" | UNKNOWN)
                unknown=$((unknown + 1))
                if ((unknown >= UNKNOWN_POLLS)); then
                    JOB_STATE="no state from crab status"
                    break
                fi
                ;;
            COMPLETED) break ;;
            PENDING | CONFIGURING | REQUEUED | REQUEUE_* | SUSPENDED | STOPPED) unknown=0 ;;
            RUNNING | COMPLETING | RESIZING | SIGNALING | STAGE_OUT)
                unknown=0
                [[ -n $started ]] || {
                    started=1
                    log "$(date '+%F %T') started (job $JOB first seen as $JOB_STATE)"
                }
                ;;
            *) break ;;
        esac
        waited=$((SECONDS - t0))
        if ((waited >= SMOKE_TIMEOUT)); then
            TIMED_OUT=1
            break
        fi
        ((tty)) && printf '\rwaiting: job %s %s  %s  ' "$JOB" "${JOB_STATE:-?}" "$(fmt_secs "$waited")"
        ((SMOKE_POLL > 0)) && sleep "$SMOKE_POLL"
    done
    ((tty)) && printf '\r%70s\r' ''
    log "$(date '+%F %T') ended (state ${JOB_STATE:-none}, waited $(fmt_secs $((SECONDS - t0))))"
}

# smoke_analyze JOB_OK: runs the python analysis, then shows its results in order.
smoke_analyze() {
    local out rc tag a b c
    WALL=?
    ERRLINE=()
    TABLE=()
    ESTIMATE=()
    log_section "analysis of $DATA_DIR"
    out=$("$PYTHON" -I -c "$SMOKE_PY" "$DATA_DIR" "$SMOKE_JSON" "$HERE/h100/gpu_a2a_comm.json" \
        "$HERE/h100/gpu_ag_comm.json" "$1" 2>"$RUN_DIR/smoke.analyze.err")
    rc=$?
    if ((rc != 0)) || [[ -z $out ]]; then
        log "$(<"$RUN_DIR/smoke.analyze.err")"
        result analysis FAIL "python exited $rc: $(last_line "$(<"$RUN_DIR/smoke.analyze.err")")"
        return
    fi
    while IFS=$'\x1f' read -r tag a b c; do
        [[ $tag == W ]] && WALL=$a
    done <<<"$out"
    SMOKE_RESULTS=()
    while IFS=$'\x1f' read -r tag a b c; do
        case $tag in
            R | Q | M) SMOKE_RESULTS+=("$tag"$'\x1f'"$a"$'\x1f'"$b"$'\x1f'"$c") ;;
            E) ERRLINE=("$a" "$b") ;;
            T) TABLE+=("$a") ;;
            X) ESTIMATE+=("$a") ;;
            L) log "$a" ;;
        esac
    done <<<"$out"
}

# smoke_show_results: the R/Q/M lines of the analysis, after the job line.
smoke_show_results() {
    local entry tag a b c line
    for entry in "${SMOKE_RESULTS[@]}"; do
        IFS=$'\x1f' read -r tag a b c <<<"$entry"
        case $tag in
            R) result "$a" "$b" "$c" ;;
            Q)
                printf -v line '%-20s %-4s %s' "$a" "$b" "$c"
                log "$line"
                FAILS=$((FAILS + 1))
                ;;
            M) say "$a" ;;
        esac
    done
}

# point_to_log: after the first FAIL lines, once, where the rest is.
point_to_log() {
    ((FAILS > 0 && POINTED == 0)) || return 0
    POINTED=1
    note "  details: gpu_lab.sh show smoke"
}

# smoke_finish: the pointer (when not given yet), the verdict, the screen; returns 0 when nothing
# failed.
smoke_finish() {
    local verdict
    point_to_log
    printf -v verdict 'smoke: %d FAIL, %d WARN' "$FAILS" "$WARNS"
    say ""
    say "$verdict"
    log_section "verdict"
    log "$verdict"
    log "$(date '+%F %T') finished"
    fit_screen smoke 1
    ((FAILS == 0))
}

cmd_smoke() {
    local line job_ok=0 detail t_submit
    if ! [[ $SMOKE_POLL =~ ^[0-9]+$ && $SMOKE_TIMEOUT =~ ^[0-9]+$ ]]; then
        echo "gpu_lab: SMOKE_POLL and SMOKE_TIMEOUT are whole seconds (got '$SMOKE_POLL' and '$SMOKE_TIMEOUT')" >&2
        return 2
    fi
    if ! command -v "$PYTHON" >/dev/null; then
        echo "gpu_lab: no python to read the results: $PYTHON (set PYTHON)" >&2
        return 2
    fi
    [[ $SMOKE_JSON == /* ]] || SMOKE_JSON=$PWD/$SMOKE_JSON
    resolve_hosts "$@" 2>/dev/null
    start_run smoke
    sayf 'gpu_lab smoke  %s  %d nodes  log: gpu_lab/%s/smoke.log' "${RUN_DIR##*/}" "${#HOSTS[@]}" "${RUN_DIR##*/}"
    log "$(date '+%F %T') smoke started; config $SMOKE_JSON; nodes for the counters: ${HOSTS[*]:-none}"
    smoke_gates
    ((FAILS > 0)) && {
        smoke_finish
        return
    }
    smoke_snapshot
    smoke_submit || {
        smoke_finish
        return
    }
    t_submit=$SECONDS
    smoke_wait
    if ((TIMED_OUT)); then
        result job FAIL "$JOB still ${JOB_STATE:-unknown} after ${SMOKE_TIMEOUT}s: not cancelled"
        note "  look: crab status $JOB   cancel it yourself, if you want to: crab cancel $JOB"
        smoke_finish
        return
    fi
    [[ $JOB_STATE == COMPLETED ]] && job_ok=1
    smoke_diff
    smoke_analyze "$job_ok"
    if ((job_ok)); then
        detail="$JOB COMPLETED  run $WALL  total $(fmt_secs $((SECONDS - t_submit)))  data ${DATA_DIR##*/}"
        result job OK "$detail"
    else
        result job FAIL "$JOB $JOB_STATE  after $(fmt_secs $((SECONDS - t_submit)))  run $WALL"
    fi
    smoke_show_results
    if ((FAILS > 0)) && ((${#ERRLINE[@]})); then
        note "first error: ${ERRLINE[0]}: ${ERRLINE[1]}"
    fi
    [[ -n $CNT_WARN ]] && result counters WARN "$CNT_WARN"
    point_to_log
    if ((${#TABLE[@]})); then
        say ""
        for line in "${TABLE[@]}"; do note "$line"; done
    fi
    if ((${#CNT_LINES[@]})); then
        say ""
        note "NIC counters, before and after the job:"
        for line in "${CNT_LINES[@]}"; do note "$line"; done
    fi
    if ((${#ESTIMATE[@]})); then
        say ""
        for line in "${ESTIMATE[@]}"; do note "$line"; done
    fi
    smoke_finish
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

# ---- dual-port -------------------------------------------------------------------------------

# shellcheck source=gpu_lab_dual.sh
source "$HERE/gpu_lab_dual.sh"

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
    smoke)
        shift
        cmd_smoke "$@"
        exit $?
        ;;
    dual-port)
        shift
        cmd_dual "$@"
        exit $?
        ;;
    pin | all)
        echo "gpu_lab: step '$1' is not available yet" >&2
        exit 2
        ;;
    *)
        echo "gpu_lab: unknown subcommand '$1'" >&2
        usage >&2
        exit 2
        ;;
esac
