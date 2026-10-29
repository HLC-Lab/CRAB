#!/usr/bin/env bash
# Sourced by gpu_lab.sh (it needs that file's screen, log, result and host helpers), not run
# directly: the `smoke` step. `gpu_lab.sh smoke [HOST...]` submits the smoke config with a plain
# `crab run`, waits for the job, then shows what ran, what the victim measured, what the full suites
# will cost and the NIC counters before and after the job. This file holds the step's settings
# (SMOKE_JSON, SMOKE_POLL, SMOKE_TIMEOUT, SMOKE_STALL, PYTHON), the markers that say how a job log
# ends, its state variables, `job_outcome` (how the end of a job is recognised), `cmd_smoke` with
# the `smoke_*` helpers, and the embedded python (SMOKE_PY) that reads the finished run. The usage
# text and the env documentation stay in the header of gpu_lab.sh.

SMOKE_JSON=${SMOKE_JSON:-$HERE/h100/smoke_gpu_a2a_comm.json}
SMOKE_POLL=${SMOKE_POLL:-10}
SMOKE_TIMEOUT=${SMOKE_TIMEOUT:-1800}
SMOKE_STALL=${SMOKE_STALL:-120}
# How the worker ends its log (slurm_output.log, ANSI colour codes stripped): the LAST non-empty line
# decides. All are extended regular expressions. "All experiments finished" is not a success marker:
# the worker prints it after failed experiments too.
readonly SMOKE_OK_MARKERS='Engine run finished'
readonly SMOKE_FAIL_MARKERS='FATAL|Worker fatal error|Worker terminated|Worker interrupted'
readonly SMOKE_STOP_MARKERS='Worker terminated|Worker interrupted' # the part of the above that is a stop from outside
# JobState values of `scontrol show job` that are final and bad (COMPLETED needs the log as well).
readonly SCONTROL_BAD_STATES='FAILED|TIMEOUT|CANCELLED.*|NODE_FAIL|OUT_OF_MEMORY|BOOT_FAIL|DEADLINE|PREEMPTED'
PYTHON=${PYTHON:-}
if [[ -z $PYTHON ]]; then
    if [[ -x $CHECKOUT/.venv/bin/python ]]; then PYTHON=$CHECKOUT/.venv/bin/python; else PYTHON=python3; fi
fi

# ---- smoke -----------------------------------------------------------------------------------

JOB=
DATA_DIR=
JOB_STATE=
TIMED_OUT=0
OUTCOME=       # OK, WARN, FAIL or UNDECIDED: what the job ended as (set by smoke_wait / job_outcome)
OUTCOME_WHY=   # the short reason, as it reads on the job line (the screen line is 100 columns)
OUTCOME_NOTE=  # the detail that goes on a line under the job line (last log line, error tail), or empty
OUTCOME_SRC=   # where the verdict comes from: log, scontrol or status
GONE_AT=       # SECONDS when the job was first seen outside the queue (reset when it is queued again)
LOG_SIG=       # size of slurm_output.log at the last poll that looked at it
LOG_SIG_AT=    # SECONDS when that size last changed
SC_STATE=
SC_CODE=
BRIEF=
POINTED=0
CNT_FILE=
CNT_OK=0
CNT_WARN=
CNT_LINES=()
WALL=?
ERRLINE=()
TABLE=()
ESTIMATE=()

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

# ---- how a job ends --------------------------------------------------------------------------
# A cluster without accounting (no sacct) answers `crab status` with UNKNOWN for a job that left the
# queue, whatever became of it. The job's own files then say what happened.

# cut_text TEXT WIDTH: TEXT, or its first WIDTH-1 characters and a "~" when longer.
cut_text() {
    local text=$1 width=$2
    if ((${#text} > width)); then text="${text:0:width-1}~"; fi
    printf '%s' "$text"
}

# brief_line LINE: sets BRIEF to the message of a worker log line: no time, level or source name,
# blanks squeezed, cut to 70 columns.
brief_line() {
    local line=$1
    if [[ $line =~ ^\[[0-9:]+\][[:space:]]+[A-Z]+[[:space:]]+(.*)$ ]]; then line=${BASH_REMATCH[1]}; fi
    line=${line#CRAB}
    line=$(tr -s ' ' <<<"$line")
    BRIEF=$(cut_text "${line# }" 70)
}

# job_log_last_line FILE: the last non-empty line, ANSI colour codes stripped.
job_log_last_line() {
    last_line "$(tail -n 20 "$1" 2>/dev/null | sed $'s/\x1b\\[[0-9;]*m//g')"
}

# error_tail DATA_DIR: the last non-empty line of slurm_error.log (the end of a traceback), cut;
# nothing when there is none.
error_tail() {
    local file=$1/slurm_error.log
    [[ -s $file ]] || return 0
    cut_text "$(last_line "$(tail -n 5 "$file")")" 50
}

# scontrol_job JOBID: sets SC_STATE and SC_CODE (ExitCode, "x:y") from `scontrol show job`, empty
# when scontrol is missing or does not know the job (it forgets a job about 5 minutes after its end).
scontrol_job() {
    local out
    SC_STATE=
    SC_CODE=
    command -v scontrol >/dev/null || return 0
    out=$(scontrol show job "$1" 2>/dev/null) || return 0
    SC_STATE=$(grep -o 'JobState=[A-Z_]*' <<<"$out" | head -n 1)
    SC_STATE=${SC_STATE#JobState=}
    SC_CODE=$(grep -o 'ExitCode=[0-9]*:[0-9]*' <<<"$out" | head -n 1)
    SC_CODE=${SC_CODE#ExitCode=}
}

# metadata_failures DATA_DIR: prints "missing", "unreadable", "clean" or "failed a,b": the experiments
# of this job that metadata.csv (in the data root, the parent of DATA_DIR) lists as FAILED or with
# failed runs.
metadata_failures() {
    local data=${1%/} out
    out=$("$PYTHON" -I -c 'import csv, sys
path, job = sys.argv[1], sys.argv[2]
try:
    handle = open(path, encoding="utf-8", errors="replace", newline="")
except OSError:
    print("missing")
    sys.exit(0)
failed = []
with handle:
    for row in csv.DictReader(handle):
        rel = (row.get("relative_path") or "").strip()
        if not (rel.startswith("./%s/" % job) or rel.startswith("%s/" % job)):
            continue
        try:
            runs = int(float(row.get("failed_runs") or 0))
        except ValueError:
            runs = 0
        name = row.get("experiment_name") or rel.rsplit("/", 1)[-1]
        if (row.get("status") or "").strip().upper() == "FAILED" or runs > 0:
            if name not in failed:
                failed.append(name)
print("failed " + ",".join(failed) if failed else "clean")' "${data%/*}/metadata.csv" "${data##*/}" 2>/dev/null) || out=unreadable
    printf '%s' "${out:-unreadable}"
}

# outcome_fail WHY SRC [NOTE]: the job failed.
outcome_fail() {
    OUTCOME=FAIL
    OUTCOME_WHY=$1
    OUTCOME_SRC=$2
    OUTCOME_NOTE=${3:-}
}

# outcome_ok WHY SRC DATA_DIR: the job ended well, then the metadata.csv cross-check: an experiment
# of this job listed as FAILED (or with failed runs) turns the OK into a WARN and is named. A missing
# file is a note in the log only.
outcome_ok() {
    local found names
    OUTCOME=OK
    OUTCOME_WHY=$1
    OUTCOME_SRC=$2
    OUTCOME_NOTE=
    found=$(metadata_failures "$3")
    case $found in
        failed*)
            names=${found#failed }
            OUTCOME=WARN
            OUTCOME_NOTE="metadata.csv lists failed runs: $(cut_text "$names" 60)"
            ;;
        missing) log "metadata.csv: not found next to the data dir, cross-check skipped" ;;
        unreadable) log "metadata.csv: could not be read, cross-check skipped" ;;
    esac
}

# job_outcome DATA_DIR JOBID: called when the job is no longer in the queue and crab status gave no
# final state (UNKNOWN, empty, or the call failed). Sets OUTCOME (OK, WARN, FAIL, or UNDECIDED: poll
# again), OUTCOME_WHY and OUTCOME_SRC (log, scontrol). Order:
#  1. scontrol show job: a final bad state decides FAIL at once; COMPLETED 0:0 only lets the log
#     decide; a state that is not final means the job is alive (the silence clocks restart).
#  2. the last non-empty line of slurm_output.log against the markers at the top of this file.
#  3. silence: no change in the size of the log for SMOKE_STALL seconds with no final line, or no log
#     at all that long since the job was first seen outside the queue.
# State kept across polls: GONE_AT, LOG_SIG, LOG_SIG_AT (smoke_wait resets them).
job_outcome() {
    local data=$1 id=$2 log=$1/slurm_output.log line sig errtail why now=$SECONDS
    OUTCOME=UNDECIDED
    OUTCOME_WHY=
    OUTCOME_NOTE=
    OUTCOME_SRC=
    [[ -n $GONE_AT ]] || GONE_AT=$now
    scontrol_job "$id"
    if [[ -z $SC_STATE ]]; then
        :
    elif [[ $SC_STATE == COMPLETED ]]; then
        if [[ -n $SC_CODE && $SC_CODE != 0:0 ]]; then
            outcome_fail "COMPLETED (scontrol, exit $SC_CODE)" scontrol
            return 0
        fi
    elif [[ $SC_STATE =~ ^($SCONTROL_BAD_STATES)$ ]]; then
        outcome_fail "$SC_STATE (scontrol${SC_CODE:+, exit $SC_CODE})" scontrol
        return 0
    else
        GONE_AT=$now
        LOG_SIG_AT=$now
    fi
    errtail=$(error_tail "$data")
    if [[ -f $log ]]; then
        sig=$(wc -c <"$log")
        sig=${sig//[[:space:]]/}
        if [[ $sig != "$LOG_SIG" ]]; then
            LOG_SIG=$sig
            LOG_SIG_AT=$now
        fi
        line=$(job_log_last_line "$log")
        if [[ $line =~ $SMOKE_FAIL_MARKERS ]]; then
            brief_line "$line"
            why="FAILED (job log)"
            [[ $line =~ $SMOKE_STOP_MARKERS ]] && why="terminated (job log)"
            outcome_fail "$why" log "last log line: $BRIEF"
            return 0
        fi
        if [[ $line =~ $SMOKE_OK_MARKERS ]]; then
            outcome_ok "ended OK (job log: ${BASH_REMATCH[0]})" log "$data"
            return 0
        fi
        if ((now - LOG_SIG_AT >= SMOKE_STALL)); then
            outcome_fail "ended without a final line (stopped early?)" log \
                "log silent for ${SMOKE_STALL}s${errtail:+; slurm_error.log: $errtail}"
        fi
    elif ((now - GONE_AT >= SMOKE_STALL)); then
        why="job left the queue without writing a log"
        [[ -z $JOB_STATE ]] && why="no state from crab status and no job log"
        outcome_fail "$why" log "no log for ${SMOKE_STALL}s${errtail:+; slurm_error.log: $errtail}"
    fi
    return 0
}

# smoke_wait: polls `crab status --json JOB` until the job ends. Sets JOB_STATE (the last state
# seen), OUTCOME/OUTCOME_WHY/OUTCOME_SRC and TIMED_OUT. A state of the queue means it is not over; a
# final state of sacct is taken as it is; UNKNOWN (or no answer) hands the decision to job_outcome,
# which says UNDECIDED while the job log is still growing.
smoke_wait() {
    local out parsed tty=0 waited=0 prev=x t0=$SECONDS started=
    [[ -t 1 ]] && tty=1
    JOB_STATE=
    OUTCOME=UNDECIDED
    OUTCOME_WHY=
    OUTCOME_NOTE=
    OUTCOME_SRC=
    GONE_AT=
    LOG_SIG=
    LOG_SIG_AT=
    while :; do
        out=$("$CRAB" status --json "$JOB" 2>>"$RUN_DIR/smoke.status.err")
        parsed=$(printf '%s' "$out" | "$PYTHON" -I -c 'import json, sys
print(json.load(sys.stdin)["jobs"][0]["state"])' 2>/dev/null)
        JOB_STATE=${parsed%%$'\n'*}
        if [[ $JOB_STATE != "$prev" ]]; then
            log "$(date '+%F %T') job $JOB state ${JOB_STATE:-none}"
            prev=$JOB_STATE
        fi
        case ${JOB_STATE%% *} in
            "" | UNKNOWN)
                job_outcome "$DATA_DIR" "$JOB"
                if [[ $OUTCOME != UNDECIDED ]]; then
                    log "$(date '+%F %T') job $JOB ended: $OUTCOME ($OUTCOME_SRC) $OUTCOME_WHY"
                    break
                fi
                ;;
            COMPLETED)
                outcome_ok COMPLETED status "$DATA_DIR"
                break
                ;;
            PENDING | CONFIGURING | REQUEUED | REQUEUE_* | SUSPENDED | STOPPED) GONE_AT= ;;
            RUNNING | COMPLETING | RESIZING | SIGNALING | STAGE_OUT)
                GONE_AT=
                [[ -n $started ]] || {
                    started=1
                    log "$(date '+%F %T') started (job $JOB first seen as $JOB_STATE)"
                }
                ;;
            *)
                outcome_fail "$JOB_STATE" status
                break
                ;;
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
    if ! [[ $SMOKE_POLL =~ ^[0-9]+$ && $SMOKE_TIMEOUT =~ ^[0-9]+$ && $SMOKE_STALL =~ ^[0-9]+$ ]]; then
        echo "gpu_lab: SMOKE_POLL, SMOKE_TIMEOUT and SMOKE_STALL are whole seconds (got '$SMOKE_POLL', '$SMOKE_TIMEOUT' and '$SMOKE_STALL')" >&2
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
    [[ $OUTCOME == OK || $OUTCOME == WARN ]] && job_ok=1
    smoke_diff
    smoke_analyze "$job_ok"
    if ((job_ok)); then
        detail="$JOB $OUTCOME_WHY  run $WALL  total $(fmt_secs $((SECONDS - t_submit)))  data ${DATA_DIR##*/}"
        result job "$OUTCOME" "$detail"
    else
        result job FAIL "$JOB $OUTCOME_WHY  after $(fmt_secs $((SECONDS - t_submit)))  run $WALL"
    fi
    [[ -n $OUTCOME_NOTE ]] && note "  $OUTCOME_NOTE"
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
