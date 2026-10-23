#!/usr/bin/env bash
# Check the layered presets end to end on Leonardo: the shared `leonardo` preset from crab-wrappers,
# your own override with the account, and a tiny 2-node job whose srun binding log has binding lines.
#
# Usage (from the CRAB checkout, on the `develop` branch, on a Leonardo login node):
#
#     bash scripts/check_presets_leonardo.sh <account>
#
# <account> is your Slurm account. It is only written into your own override file (see check 3),
# never into the repo.
#
# It checks, and prints `PASS <n> <what>` or `FAIL <n> <what>: <why>` for each:
#   1  prerequisites: a CRAB checkout, `crab` and `python3` on PATH, an account argument.
#   2  `crab update --head` succeeds (CRAB and the crab-wrappers clone go to their branch HEADs,
#      which has presets/), and `crab info --json` lists the `leonardo` preset.
#   3  the override: when a *.json file in your overrides folder already defines `leonardo`, it is
#      used as it is. Otherwise <overrides>/leonardo.json is written with
#      {"leonardo": {"sbatch": ["--account=<account>"]}} and kept.
#   4  `crab presets show leonardo --json` has nothing in `needs` and its srun flags contain
#      `--cpu-bind=verbose,socket`.
#   5  a tiny config (2 nodes, ppn 1, one Blink alltoall app, one run) passes
#      `crab validate -p leonardo`.
#   6  `crab run -p leonardo --json` submits it: the job id and the data directory.
#   7  the job reaches COMPLETED (polled every 20 s, for at most 30 minutes).
#   8  a `bindings_app_*.log` under the data directory has `cpu-bind` lines (srun prints them for
#      `--cpu-bind=verbose`); the first two are shown.
#
# A good result is PASS for every check and exit status 0. Any FAIL ends with exit status 1. A check
# that later checks depend on stops the script at once, with its reason.
#
# Where things land:
#   - the override: <overrides>/leonardo.json (the folder `crab info --json` reports as
#     `presets_overrides`); it stays, so the next run reuses it.
#   - the job's results, including the bindings logs: the data directory printed by check 6.
#   - the config and the log of every crab command: a temp dir, removed on exit (KEEP=1 keeps it
#     and prints its path).
#
# The script never cancels or kills anything: a job that does not finish in time stays queued, and
# check 7 prints its id.
#
# Environment:
#   KEEP=1   keep the temp dir instead of deleting it on exit.
set -euo pipefail

usage() {
    cat >&2 << 'USAGE'
usage: bash scripts/check_presets_leonardo.sh <account>

Checks the layered presets on Leonardo: `crab update --head`, a leonardo override with your
account, `crab presets show leonardo`, `crab validate` and `crab run` of a tiny 2-node job, and the
srun binding lines in its bindings_app_*.log. Prints PASS or FAIL per check; exits 1 if any failed.

Run it from the CRAB checkout (branch develop) on a Leonardo login node. <account> is your Slurm
account; it is written only to your own presets override file, never to the repo.

environment: KEEP=1 keeps the temp dir with the config and the crab command logs.
USAGE
}

if [ "$#" -ne 1 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ] || [ -z "$1" ]; then
    usage
    exit 2
fi
account="$1"

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

failed=0
tmp=""
cleanup() {
    [ -n "$tmp" ] || return 0
    if [ "${KEEP:-0}" = "1" ]; then
        echo "kept $tmp"
    else
        rm -rf "$tmp"
    fi
}
trap cleanup EXIT

pass() { echo "PASS $1 $2"; }
fail() {
    echo "FAIL $1 $2: $3"
    failed=1
}
# A failed check the later ones depend on: report it and stop.
stop() {
    fail "$1" "$2" "$3"
    echo "stopping: the next checks need check $1"
    exit 1
}
# Show the end of a log file, indented, so a FAIL carries its evidence.
show_tail() {
    if [ -s "$1" ]; then
        tail -n 15 "$1" | sed 's/^/    /'
    fi
}

# --- 1. prerequisites ------------------------------------------------------------------------

if [ ! -d "$repo/src/crab" ] || [ ! -f "$repo/pyproject.toml" ]; then
    stop 1 "prerequisites" "$repo is not a CRAB checkout"
fi
command -v crab > /dev/null 2>&1 || stop 1 "prerequisites" "no \`crab\` on PATH (activate the environment CRAB is installed in)"
command -v python3 > /dev/null 2>&1 || stop 1 "prerequisites" "no python3 on PATH"
cd "$repo"
pass 1 "prerequisites: checkout $repo, crab and python3 on PATH, account given"

tmp="$(mktemp -d)"

# --- 2. update and the preset is listed --------------------------------------------------------

if ! crab update --head > "$tmp/update.log" 2>&1; then
    show_tail "$tmp/update.log"
    stop 2 "crab update --head" "it exited non-zero (log tail above)"
fi
if ! crab info --json > "$tmp/info.json" 2> "$tmp/info.err"; then
    show_tail "$tmp/info.err"
    stop 2 "crab info --json" "it exited non-zero (stderr tail above)"
fi
if ! python3 -c '
import json
import sys

info = json.load(open(sys.argv[1]))
names = [preset["name"] for preset in info.get("presets", [])]
sys.exit(0 if "leonardo" in names else 1)
' "$tmp/info.json"; then
    stop 2 "crab update --head, leonardo preset listed" "crab info --json does not list a preset named leonardo (see presets_sources and presets_error in $tmp/info.json; KEEP=1 keeps it)"
fi
pass 2 "crab update --head, and crab info --json lists leonardo"

# --- 3. the override ---------------------------------------------------------------------------

overrides="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["presets_overrides"])' "$tmp/info.json")" \
    || stop 3 "override" "crab info --json has no presets_overrides"
existing="$(python3 -c '
import json
import sys
from pathlib import Path

folder = Path(sys.argv[1])
for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
    try:
        body = json.loads(path.read_text())
    except (OSError, ValueError):
        continue
    if isinstance(body, dict) and "leonardo" in body:
        print(path)
        break
' "$overrides")" || stop 3 "override" "could not read $overrides"
if [ -n "$existing" ]; then
    pass 3 "override: $existing already defines leonardo, used as it is"
else
    mkdir -p "$overrides" || stop 3 "override" "cannot create $overrides"
    if ! python3 -c '
import json
import sys

with open(sys.argv[1], "w") as out:
    json.dump({"leonardo": {"sbatch": ["--account=" + sys.argv[2]]}}, out, indent=2)
    out.write("\n")
' "$overrides/leonardo.json" "$account"; then
        stop 3 "override" "could not write $overrides/leonardo.json"
    fi
    pass 3 "override: wrote $overrides/leonardo.json with your account (kept)"
fi

# --- 4. the resolved preset --------------------------------------------------------------------

if ! crab presets show leonardo --json > "$tmp/show.json" 2> "$tmp/show.err"; then
    show_tail "$tmp/show.err"
    stop 4 "crab presets show leonardo" "it exited non-zero (stderr tail above)"
fi
if ! detail="$(python3 -c '
import json
import sys

data = json.load(open(sys.argv[1]))
needs = data.get("needs", [])
launchers = data.get("preset", {}).get("launchers", {})
flags = launchers.get("srun", {}).get("flags", []) if isinstance(launchers, dict) else []
problems = []
if needs:
    problems.append("still unset: " + ", ".join(needs))
if "--cpu-bind=verbose,socket" not in flags:
    problems.append("srun flags " + json.dumps(flags) + " lack --cpu-bind=verbose,socket")
print("; ".join(problems))
sys.exit(1 if problems else 0)
' "$tmp/show.json")"; then
    stop 4 "crab presets show leonardo" "$detail"
fi
pass 4 "crab presets show leonardo: nothing unset, srun flags have --cpu-bind=verbose,socket"

# --- 5. a tiny config and validate -------------------------------------------------------------

config="$tmp/presets_check.json"
cat > "$config" << 'JSON'
{
    "schema_version": 2,
    "global_options": {
        "name": "presets_check",
        "numnodes": "2",
        "ppn": "1",
        "allocation": { "mode": "linear" },
        "minruns": "1",
        "maxruns": "1",
        "timeout": "120.0",
        "outformat": "csv",
        "sbatch_directives": { "time": "00:10:00", "job-name": "crab_presets_check" }
    },
    "experiments": {
        "a2a_64B": {
            "apps": {
                "0": {
                    "path": "blink/alltoall/alltoall_comm_only.py",
                    "args": "-msgsize 64 -iter 100",
                    "collect": true,
                    "start": "0",
                    "end": ""
                }
            }
        }
    }
}
JSON
if ! crab validate "$config" -p leonardo > "$tmp/validate.log" 2>&1; then
    show_tail "$tmp/validate.log"
    stop 5 "crab validate -p leonardo" "it exited non-zero (output tail above)"
fi
pass 5 "crab validate -p leonardo on the 2-node config"

# --- 6. submit ---------------------------------------------------------------------------------

if ! crab run "$config" -p leonardo --json > "$tmp/run.json" 2> "$tmp/run.err"; then
    show_tail "$tmp/run.err"
    stop 6 "crab run -p leonardo" "it exited non-zero (stderr tail above)"
fi
if ! ids="$(python3 -c '
import json
import sys

result = json.load(open(sys.argv[1]))
print(result["job_id"])
print(result["data_dir"])
' "$tmp/run.json")"; then
    show_tail "$tmp/run.json"
    stop 6 "crab run -p leonardo" "no job_id and data_dir in its JSON answer (tail above)"
fi
job_id="$(echo "$ids" | sed -n 1p)"
data_dir="$(echo "$ids" | sed -n 2p)"
pass 6 "crab run -p leonardo: job $job_id, data directory $data_dir"

# --- 7. wait for the job -----------------------------------------------------------------------

poll_s=20
limit_s=1800
unknown_limit=15 # polls in a row with no state (5 minutes): the id is probably not Slurm's
waited=0
unknown=0
state="UNKNOWN"
finished=0
while [ "$waited" -le "$limit_s" ]; do
    if crab status "$job_id" --json > "$tmp/status.json" 2> /dev/null; then
        state="$(python3 -c '
import json
import sys

print(json.load(open(sys.argv[1]))["jobs"][0]["state"])
' "$tmp/status.json" 2> /dev/null)" || state="UNKNOWN"
    else
        state="UNKNOWN"
    fi
    case "$state" in
        UNKNOWN)
            unknown=$((unknown + 1))
            if [ "$unknown" -ge "$unknown_limit" ]; then
                break
            fi
            ;;
        PENDING | RUNNING | CONFIGURING | COMPLETING | SUSPENDED | REQUEUED | REQUEUE_* | RESIZING | SIGNALING | STAGE_OUT | STOPPED)
            unknown=0
            ;;
        *)
            finished=1
            break
            ;;
    esac
    sleep "$poll_s"
    waited=$((waited + poll_s))
done
if [ "$finished" -eq 0 ]; then
    if [ "$unknown" -ge "$unknown_limit" ]; then
        fail 7 "job $job_id reaches COMPLETED" "Slurm reports no state for it (squeue and sacct do not know the id)"
    else
        fail 7 "job $job_id reaches COMPLETED" "still $state after $((limit_s / 60)) minutes; it was left alone, check it with: crab status $job_id"
    fi
elif [ "$state" = "COMPLETED" ]; then
    pass 7 "job $job_id reached COMPLETED"
else
    fail 7 "job $job_id reaches COMPLETED" "it ended $state (look at the slurm_*.log files in $data_dir)"
fi

# --- 8. binding lines --------------------------------------------------------------------------

logs="$tmp/bindings.list"
find "$data_dir" -name 'bindings_app_*.log' 2> /dev/null | sort > "$logs" || true
if [ ! -s "$logs" ]; then
    fail 8 "bindings log" "no bindings_app_*.log under $data_dir"
else
    : > "$tmp/bindings.lines"
    while IFS= read -r log; do
        grep -i -E 'cpu[-_]bind' "$log" >> "$tmp/bindings.lines" 2> /dev/null || true
    done < "$logs"
    if [ -s "$tmp/bindings.lines" ]; then
        pass 8 "bindings log has cpu-bind lines ($(wc -l < "$logs" | tr -d ' ') log file(s)); the first two:"
        sed -n 1,2p "$tmp/bindings.lines" | sed 's/^/    /'
    else
        fail 8 "bindings log" "$(wc -l < "$logs" | tr -d ' ') bindings_app_*.log file(s) under $data_dir, none with a cpu-bind line"
    fi
fi

# --- result ------------------------------------------------------------------------------------

if [ "$failed" -ne 0 ]; then
    echo "presets check: FAILED"
    exit 1
fi
echo "presets check: ok"
