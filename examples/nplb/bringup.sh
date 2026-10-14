#!/usr/bin/env bash
# One command per bring-up stage of the NPLB campaign, run from anywhere on the cluster console:
#   examples/nplb/bringup.sh SUBCOMMAND [args]
#
#   nic PPN              NIC check in a 2-node allocation (PPN 8 or 16), output kept in $WORK
#   smoke PPN            smoke configs, then submit md p0, p1 and p2_a2a
#   real PPN             full configs (RUNS runs), then submit md p2_a2a
#   status               squeue of the partition and the last submits
#   check [DATA_DIR...]  per experiment: unbound ranks and NICs per host against ranks per host
#   analyze smoke|real PPN   analyze.py on the data dirs of that kind and PPN, summary.md head
#
# It sources env.sh first (Open MPI in PATH, BLINK, local/nplb_env.sh). Every command is echoed
# before it runs and the first failure stops the script. Every submit adds a line to
# $WORK/run_log.tsv: time, net, paths, ppn, config file, job id, data dir.
# Env: PRESET (nplb_h100), PARTITION (h100_4nodes), NET (C2), PATHS (1), RUNS (5),
#      WORK (<checkout>/local/nplb-work), PROFILE_DIR (<checkout>: profile8.json, profile16.json),
#      DATA_ROOT (<checkout>/data), PYTHON and CRAB (default: the checkout's .venv).
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CHECKOUT=$(cd "$HERE/../.." && pwd)
# shellcheck disable=SC1091
. "$HERE/env.sh"

PRESET=${PRESET:-nplb_h100}
PARTITION=${PARTITION:-h100_4nodes}
NET=${NET:-C2}
PATHS=${PATHS:-1}
RUNS=${RUNS:-5}
WORK=${WORK:-$CHECKOUT/local/nplb-work}
PROFILE_DIR=${PROFILE_DIR:-$CHECKOUT}
DATA_ROOT=${DATA_ROOT:-$CHECKOUT/data}
PYTHON=${PYTHON:-$CHECKOUT/.venv/bin/python}
[[ -x $PYTHON ]] || PYTHON=python3
CRAB=${CRAB:-$CHECKOUT/.venv/bin/crab}
[[ -x $CRAB ]] || CRAB=crab
RUN_LOG=$WORK/run_log.tsv

usage() {
    sed -n '2,/^[^#]/{/^#/p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
    exit 2
}

die() {
    echo "bringup: $*" >&2
    exit 1
}

show() { echo "+ $(printf '%q ' "$@")"; }

# step CMD...: echo it, run it, stop the script when it fails.
step() {
    show "$@"
    "$@"
    local rc=$?
    ((rc == 0)) || die "step failed (exit $rc): $1"
}

need_ppn() {
    case ${1:-} in
        8 | 16) ;;
        *) echo "bringup: PPN must be 8 or 16, got '${1:-}'" >&2; usage ;;
    esac
}

profile_for() {
    local profile=$PROFILE_DIR/profile$1.json
    [[ -r $profile ]] || die "cannot read $profile (set PROFILE_DIR)"
    echo "$profile"
}

# gen_configs KIND PPN: the configs of this PPN into $WORK/<kind>_ppn<PPN>, old json files removed.
gen_configs() {
    local kind=$1 ppn=$2 profile out
    profile=$(profile_for "$ppn") || exit 1
    out=$WORK/${kind}_ppn$ppn
    mkdir -p "$out"
    step rm -f "$out"/*.json
    local extra=(--smoke)
    [[ $kind == real ]] && extra=(--minruns "$RUNS" --maxruns "$RUNS")
    # NET and PATHS may hold several values, so they stay unquoted
    # shellcheck disable=SC2086
    step "$PYTHON" "$HERE/make_configs.py" "$profile" --out "$out" "${extra[@]}" --set md \
        --net $NET --paths $PATHS --aggressors a2a --tag "ppn$ppn"
}

# submit PPN FILE: crab run, one line in run_log.tsv, echo the job id.
submit() {
    local ppn=$1 file=$2 out rc fields job dir
    show "$CRAB" run -p "$PRESET" --json "$file"
    out=$("$CRAB" run -p "$PRESET" --json "$file")
    rc=$?
    ((rc == 0)) || die "step failed (exit $rc): crab run $file"
    fields=$(printf '%s' "$out" | "$PYTHON" -c \
        'import json, sys
d = json.load(sys.stdin)
print(d.get("job_id", "") or "-")
print(d.get("data_dir", "") or "-")') || die "crab run printed no JSON for $file: $out"
    { read -r job; read -r dir; } <<<"$fields"
    mkdir -p "$WORK"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date +%Y-%m-%dT%H:%M:%S)" "$NET" "$PATHS" "$ppn" \
        "$file" "$job" "$dir" >>"$RUN_LOG"
    echo "submitted job $job ($(basename "$file"))"
}

# pick DIR PATTERN...: sets PICKED to the files of DIR matching each pattern, in that order.
# (A function that prints would run in a subshell, where die cannot stop the script.)
pick() {
    local dir=$1 pat f
    shift
    PICKED=()
    for pat in "$@"; do
        local before=${#PICKED[@]}
        while IFS= read -r f; do PICKED+=("$f"); done < <(compgen -G "$dir/$pat" | sort)
        ((${#PICKED[@]} > before)) || die "no $pat in $dir"
    done
}

cmd_nic() {
    need_ppn "${1:-}"
    local ppn=$1 profile out
    profile=$(profile_for "$ppn") || exit 1
    [[ -n ${BLINK:-} ]] || die "BLINK is not set (no Blink receipt; see env.sh)"
    mkdir -p "$WORK"
    out=$WORK/nic_check_ppn${ppn}_$(date +%Y%m%d-%H%M%S).txt
    local cmd=(salloc -N2 --exclusive -p "$PARTITION" -t 00:20:00 env LAUNCHER=mpirun
        "MPIRUN_FLAGS=--allow-run-as-root --mca pml ucx --mca btl ^openib"
        "$HERE/nic_check.sh" "$profile" "$BLINK")
    echo "+ $(printf '%q ' "${cmd[@]}")2>&1 | tee $out"
    "${cmd[@]}" 2>&1 | tee "$out"
    local rc=${PIPESTATUS[0]}
    ((rc == 0)) || die "step failed (exit $rc): salloc (output in $out)"
    echo "NIC check output: $out"
}

cmd_smoke() {
    need_ppn "${1:-}"
    local ppn=$1 f
    gen_configs smoke "$ppn"
    pick "$WORK/smoke_ppn$ppn" 'smoke_md_p0_*.json' 'smoke_md_p1_*.json' 'smoke_md_p2_a2a_*.json'
    for f in "${PICKED[@]}"; do submit "$ppn" "$f"; done
    echo "Submitted ${#PICKED[@]} smoke jobs (ppn $ppn). Run: $0 status"
}

cmd_real() {
    need_ppn "${1:-}"
    local ppn=$1 f
    gen_configs real "$ppn"
    pick "$WORK/real_ppn$ppn" 'md_p2_a2a_*.json'
    for f in "${PICKED[@]}"; do submit "$ppn" "$f"; done
    echo "Submitted ${#PICKED[@]} real job(s) (ppn $ppn, $RUNS runs). Run: $0 status"
}

cmd_status() {
    step squeue -p "$PARTITION"
    echo "+ tail -n 10 $RUN_LOG"
    tail -n 10 "$RUN_LOG" 2>/dev/null || echo "(no $RUN_LOG yet)"
}

# job_of_dir DIR: the job id recorded for DIR in run_log.tsv, or nothing.
job_of_dir() {
    [[ -r $RUN_LOG ]] || return 0
    awk -F'\t' -v d="$1" '$7 == d { j = $6 } END { if (j != "") print j }' "$RUN_LOG"
}

# check_experiment DIR: print one OK/CHECK line for an experiment dir; return 1 for CHECK.
check_experiment() {
    local dir=$1 name logs=() result
    name=$(basename "$dir")
    logs=("$dir"/bindings_app_*.log)
    if [[ ! -e ${logs[0]} ]]; then
        printf '  %-40s CHECK  no bindings_app_*.log yet\n' "$name"
        return 1
    fi
    result=$(cat "${logs[@]}" | awk '
        /is not bound/ { unbound++ }
        {
            if (match($0, /crab: [^ ]+ local rank [0-9]+ -> UCX_NET_DEVICES=[^ ]+/)) {
                split(substr($0, RSTART, RLENGTH), f, " ")
                host = f[2]; rank = f[5]; nic = f[7]
                sub(/UCX_NET_DEVICES=/, "", nic)
                if (!(host in hosts)) hosts[host] = 1
                if (!((host, rank) in seen_r)) { seen_r[host, rank] = 1; nr[host]++ }
                if (!((host, nic) in seen_n)) { seen_n[host, nic] = 1; nn[host]++ }
            }
        }
        END {
            bad = (unbound > 0); nh = 0; detail = ""
            for (h in hosts) {
                nh++
                detail = detail sprintf(" %s: %d ranks, %d NICs;", h, nr[h], nn[h])
                if (nn[h] != nr[h]) bad = 1
            }
            if (nh == 0) { bad = 1; detail = " no \"local rank -> UCX_NET_DEVICES\" lines;" }
            printf "%s\tunbound %d,%s\n", bad ? "CHECK" : "OK", unbound, detail
        }')
    printf '  %-40s %s\n' "$name" "$(printf '%s' "$result" | awk -F'\t' '{ printf "%-6s %s", $1, $2 }')"
    [[ $result == OK$'\t'* ]]
}

cmd_check() {
    local dirs=("$@") d exp job state bad=0
    if ((${#dirs[@]} == 0)); then
        if [[ -r $RUN_LOG ]]; then
            while IFS= read -r d; do
                [[ $d == - ]] && continue
                [[ -d $d ]] && dirs+=("$d") || echo "(skipped, not found: $d)"
            done < <(awk -F'\t' '!seen[$7]++ { print $7 }' "$RUN_LOG")
        fi
        if ((${#dirs[@]} == 0)); then
            while IFS= read -r d; do dirs+=("$d"); done < <(
                compgen -G "$DATA_ROOT/*/*_ppn*" | xargs -r ls -dt | head -n 6)
        fi
    fi
    ((${#dirs[@]})) || die "no data dirs to check (give DATA_DIR..., or submit first)"
    for d in "${dirs[@]}"; do
        job=$(job_of_dir "$d")
        state=""
        if [[ -n $job ]] && command -v sacct >/dev/null 2>&1; then
            state=$(sacct -j "$job" -X -n -P -o State 2>/dev/null | head -n 1)
        fi
        echo "== $d${job:+ (job $job, state ${state:-n/a})}"
        while IFS= read -r exp; do
            check_experiment "$exp" || bad=1
        done < <(find "$d" -mindepth 1 -maxdepth 1 -type d | sort)
    done
    ((bad == 0)) || { echo "check: at least one experiment needs a look (CHECK)"; exit 1; }
    echo "check: all OK"
}

cmd_analyze() {
    local kind=${1:-}
    case $kind in
        smoke | real) ;;
        *) echo "bringup: analyze needs smoke or real" >&2; usage ;;
    esac
    need_ppn "${2:-}"
    local ppn=$2 pat out dirs=() d
    [[ $kind == smoke ]] && pat="smoke_*_ppn${ppn}_*" || pat="md_*_ppn${ppn}_*"
    while IFS= read -r d; do dirs+=("$d"); done < <(compgen -G "$DATA_ROOT/*/$pat" | sort)
    ((${#dirs[@]})) || die "no data dirs matching $DATA_ROOT/*/$pat"
    out=$WORK/results_${kind}_ppn$ppn
    step "$PYTHON" "$HERE/analyze.py" "${dirs[@]}" --out "$out"
    echo "+ head -n 60 $out/summary.md"
    head -n 60 "$out/summary.md"
}

sub=${1:-}
[[ $# -gt 0 ]] && shift
case $sub in
    nic) cmd_nic "$@" ;;
    smoke) cmd_smoke "$@" ;;
    real) cmd_real "$@" ;;
    status) cmd_status ;;
    check) cmd_check "$@" ;;
    analyze) cmd_analyze "$@" ;;
    *) usage ;;
esac
