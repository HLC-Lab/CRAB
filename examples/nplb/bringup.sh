#!/usr/bin/env bash
# One command per bring-up stage of the NPLB campaign, run from anywhere on the cluster console:
#   examples/nplb/bringup.sh SUBCOMMAND [args]
#
#   nic PPN              NIC check in a 2-node allocation (PPN 8 or 16), output kept in $WORK
#   smoke PPN            smoke configs, then submit md p0, p1 and p2_a2a
#   real PPN             full configs (RUNS runs), then submit md p2_a2a
#   topo [HOST...]       topology probe (topology.sh): ports, LLDP leaf:port, pods, victim pair;
#                        no HOST lists the nodes of $PARTITION. Exits with topology.sh's code
#   pingpong             ping-pong ceilings: generate the set (3 pairs from node 1 and the node
#                        total) into $WORK/pingpong, then submit its 4 jobs one after another
#   pw N...              node total with N ranks per node (N = 4, 8, ...): per N one job, one long run
#                        (ITER iterations, WARMUP warmup), with the UCX variables and without (`d`)
#   status               squeue of the partition and the last submits
#   check [DATA_DIR...]  per experiment: unbound ranks and NICs per host against ranks per host
#   analyze smoke|real PPN   analyze.py on the data dirs of that kind and PPN, summary.md head
#   analyze pingpong     analyze.py on the ping-pong data dirs ($DATA_ROOT/*/pp_*), summary.md head
#   analyze pw           analyze.py on the data dirs of the pw jobs in run_log.tsv, summary.md head
#
# It sources env.sh first (Open MPI in PATH, BLINK, local/nplb_env.sh). Every command is echoed
# before it runs and the first failure stops the script. Every submit adds a line to
# $WORK/run_log.tsv: time, net, paths, ppn, config file, job id, data dir.
# pingpong needs profile8.json (per card) and profile16.json (every port) in PROFILE_DIR; it runs
# one NET and one PATHS, RUNS runs, 1000 iterations up to 128 MiB and 200 at 512 MiB.
# pw needs the same two profiles; per N it generates the ping-pong set with --ranks N into
# $WORK/pw/ppn<N> and submits only its node-total file, with N ranks per node.
# Env: PRESET (nplb_h100), PARTITION (h100_4nodes), NET (C2), PATHS (1), RUNS (5),
#      ITER (10000), WARMUP (200), PW_RUNS (1) for pw,
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
        while IFS= read -r f; do PICKED+=("$f"); done < <(compgen -G "$dir/$pat" | LC_ALL=C sort)
        ((${#PICKED[@]} > before)) || die "no $pat in $dir"
    done
}

cmd_topo() {
    show "$HERE/topology.sh" "$@"
    PARTITION=$PARTITION WORK=$WORK "$HERE/topology.sh" "$@"
    exit $?
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

cmd_pingpong() {
    local profile ports out ppn8 f
    profile=$(profile_for 8) || exit 1
    ports=$(profile_for 16) || exit 1
    out=$WORK/pingpong
    mkdir -p "$out"
    step rm -f "$out"/*.json
    # NET and PATHS may hold several values (make_configs then refuses), so they stay unquoted
    # shellcheck disable=SC2086
    step "$PYTHON" "$HERE/make_configs.py" "$profile" --all-ports "$ports" --set pingpong \
        --out "$out" --net $NET --paths $PATHS --minruns "$RUNS" --maxruns "$RUNS" \
        --iter-small 1000 --iter-128m 1000 --iter-512m 200
    pick "$out" 'pp_*.json'
    ppn8=$("$PYTHON" -c 'import json, sys; print(json.load(open(sys.argv[1]))["ppn"])' "$profile") ||
        die "cannot read the ppn of $profile"
    for f in "${PICKED[@]}"; do
        # the node total (pw8) runs every rank of the node, the pairs one rank per node
        if [[ $f == *_pw8_* ]]; then submit "$ppn8" "$f"; else submit 1 "$f"; fi
    done
    echo "Submitted ${#PICKED[@]} ping-pong jobs ($NET, paths $PATHS, $RUNS runs). Run: $0 status"
}

cmd_pw() {
    local profile ports n out f count=0
    (($# > 0)) || { echo "bringup: pw needs at least one N (ranks per node), e.g. pw 4 8" >&2; usage; }
    for n in "$@"; do
        [[ $n =~ ^[1-9][0-9]*$ ]] || { echo "bringup: pw: N must be a positive integer, got '$n'" >&2; usage; }
    done
    local iter=${ITER:-10000} warmup=${WARMUP:-200} runs=${PW_RUNS:-1}
    profile=$(profile_for 8) || exit 1
    ports=$(profile_for 16) || exit 1
    for n in "$@"; do
        out=$WORK/pw/ppn$n
        mkdir -p "$out"
        step rm -f "$out"/*.json
        # NET and PATHS may hold several values (make_configs then refuses), so they stay unquoted
        # shellcheck disable=SC2086
        step "$PYTHON" "$HERE/make_configs.py" "$profile" --all-ports "$ports" --set pingpong \
            --ranks "$n" --warmup "$warmup" --iter-small "$iter" --iter-128m "$iter" \
            --iter-512m "$iter" --minruns "$runs" --maxruns "$runs" \
            --out "$out" --net $NET --paths $PATHS
        pick "$out" "pp_*_pw${n}_*.json"
        for f in "${PICKED[@]}"; do
            submit "$n" "$f"
            count=$((count + 1))
        done
    done
    echo "Submitted $count pw jobs ($NET, paths $PATHS, $iter iterations, $runs run(s)). Run: $0 status"
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
    local ppn pat out dirs=() d
    case $kind in
        smoke | real)
            need_ppn "${2:-}"
            ppn=$2
            [[ $kind == smoke ]] && pat="smoke_*_ppn${ppn}_*" || pat="md_*_ppn${ppn}_*"
            out=$WORK/results_${kind}_ppn$ppn
            ;;
        pingpong)
            pat="pp_*"
            out=$WORK/results_pingpong
            ;;
        pw)
            out=$WORK/results_pw
            ;;
        *) echo "bringup: analyze needs smoke, real, pingpong or pw" >&2; usage ;;
    esac
    if [[ $kind == pw ]]; then
        # the last row of each pw config file; a job submitted twice counts once
        [[ -r $RUN_LOG ]] || die "no $RUN_LOG yet: submit pw jobs first (bringup.sh pw N...)"
        while IFS= read -r d; do dirs+=("$d"); done < <(
            awk -F'\t' -v pre="$WORK/pw/" 'index($5, pre) == 1 { dir[$5] = $7 }
                END { for (f in dir) if (dir[f] != "-") print dir[f] }' "$RUN_LOG" | sort)
        ((${#dirs[@]})) || die "no pw rows in $RUN_LOG (submit with bringup.sh pw N...)"
    else
        while IFS= read -r d; do dirs+=("$d"); done < <(compgen -G "$DATA_ROOT/*/$pat" | sort)
        ((${#dirs[@]})) || die "no data dirs matching $DATA_ROOT/*/$pat"
    fi
    step "$PYTHON" "$HERE/analyze.py" "${dirs[@]}" --out "$out"
    echo "+ head -n 60 $out/summary.md"
    head -n 60 "$out/summary.md"
}

sub=${1:-}
[[ $# -gt 0 ]] && shift
case $sub in
    topo) cmd_topo "$@" ;;
    nic) cmd_nic "$@" ;;
    smoke) cmd_smoke "$@" ;;
    real) cmd_real "$@" ;;
    pingpong) cmd_pingpong ;;
    pw) cmd_pw "$@" ;;
    status) cmd_status ;;
    check) cmd_check "$@" ;;
    analyze) cmd_analyze "$@" ;;
    *) usage ;;
esac
