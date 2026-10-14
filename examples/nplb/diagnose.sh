#!/usr/bin/env bash
# Binding and bandwidth diagnostics for the H100 cluster, one command, no arguments:
#   examples/nplb/diagnose.sh
#
# A: which mpirun flags pin rank i to the i-th core of the profile's cpu list (8 ranks, one
#    node, --report-bindings). B: where the per-node bandwidth cap comes from (nic_check.sh on
#    NIC subsets and on two node pairs). Every step runs in its own salloc, is echoed first, and
#    a failing step is recorded as FAILED while the script goes on. The whole output goes to
#    $WORK/diagnose_<time>.log and ends with a SUMMARY block of at most 20 lines (also written to
#    $WORK/diagnose_<time>_summary.txt). Exit status: 0 when every step ran, 1 when a step FAILED,
#    2 when the setup is missing (profiles, BLINK, fewer than 3 nodes).
# Env: PARTITION (h100_4nodes), WORK (<checkout>/local/nplb-work), PROFILE_DIR (<checkout>:
#      profile8.json and profile16.json), NIC_CHECK (default: nic_check.sh next to this script).
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CHECKOUT=$(cd "$HERE/../.." && pwd)
# shellcheck disable=SC1091
. "$HERE/env.sh"

PARTITION=${PARTITION:-h100_4nodes}
WORK=${WORK:-$CHECKOUT/local/nplb-work}
PROFILE_DIR=${PROFILE_DIR:-$CHECKOUT}
NIC_CHECK=${NIC_CHECK:-$HERE/nic_check.sh}
PYTHON=${PYTHON:-$CHECKOUT/.venv/bin/python}
[[ -x $PYTHON ]] || PYTHON=python3
MPIRUN_FLAGS="--allow-run-as-root --mca pml ucx --mca btl ^openib"

fail_setup() {
    echo "diagnose: $*" >&2
    exit 2
}

if (($# > 0)); then
    sed -n '2,/^[^#]/{/^#/p}' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
    exit 2
fi
for f in profile8.json profile16.json; do
    [[ -r $PROFILE_DIR/$f ]] || fail_setup "cannot read $PROFILE_DIR/$f (set PROFILE_DIR)"
done
[[ -n ${BLINK:-} ]] || fail_setup "BLINK is not set (no Blink receipt; see env.sh)"
mapfile -t NODES < <(sinfo -h -N -p "$PARTITION" -o %N 2>/dev/null | sort -u)
((${#NODES[@]} >= 3)) || fail_setup "need at least 3 nodes in partition $PARTITION, sinfo gave ${#NODES[@]}"
N1=${NODES[0]}
N2=${NODES[1]}
N3=${NODES[2]}

mkdir -p "$WORK" || fail_setup "cannot create $WORK"
TS=$(date +%Y%m%d-%H%M%S)
LOG=$WORK/diagnose_$TS.log
SUMFILE=$WORK/diagnose_${TS}_summary.txt
STEPDIR=$WORK/diagnose_$TS
mkdir -p "$STEPDIR"
SUMMARY=()
ANY_FAILED=0

# show CMD...: the command as one line, quoted only where the shell needs it (commas stay plain).
show() {
    local a line="+"
    for a in "$@"; do
        if [[ $a =~ ^[A-Za-z0-9_@%+=:,./-]+$ ]]; then line+=" $a"; else line+=" $(printf '%q' "$a")"; fi
    done
    echo "$line"
}

# run_step NAME CMD...: echo it, run it, output to the log and $STEPDIR/NAME.out; sets RC, STEP_OUT.
run_step() {
    local name=$1
    shift
    STEP_OUT=$STEPDIR/$name.out
    echo
    show "$@"
    "$@" 2>&1 | tee "$STEP_OUT"
    RC=${PIPESTATUS[0]}
}

# first_error FILE RC: one line that says why the step failed.
first_error() {
    local line cand
    cand=$(grep -v -E '^\+|^(one-rank|per-rank|first-nic)' "$1" 2>/dev/null | grep -v '^[[:space:]]*$')
    line=$(printf '%s\n' "$cand" | grep -m1 -iE 'error|fail|refus|conflict|cannot|denied|unable|not found')
    [[ -n $line ]] || line=$(printf '%s\n' "$cand" | tail -n 1)
    [[ -n $line ]] || line="exit $2, no output"
    printf '%s' "$line" | sed 's/^[[:space:]]*//' | cut -c1-70
}

add_line() { SUMMARY+=("$(printf '%s' "$1" | cut -c1-100)"); }

# bindings FILE N: "<bound>/<N> bound  rank:core ..." from the MCW rank lines (a rank spanning
# several cores shows them joined with +).
bindings() {
    awk -v total="$2" '
        /MCW rank [0-9]+/ {
            match($0, /MCW rank [0-9]+/)
            split(substr($0, RSTART, RLENGTH), a, " ")
            r = a[3] + 0
            if (r in seen) next
            seen[r] = 1
            if ($0 ~ /not bound/) next
            bound++
            rest = $0
            sub(/.*bound to/, "", rest)
            cores = ""
            while (match(rest, /core [0-9]+\[/)) {
                cores = cores (cores == "" ? "" : "+") substr(rest, RSTART + 5, RLENGTH - 6)
                rest = substr(rest, RSTART + RLENGTH)
            }
            where[r] = (cores == "" ? "?" : cores)
            if (r > max) max = r
        }
        END {
            list = ""
            for (i = 0; i <= max; i++) if (i in where) list = list (list == "" ? "" : " ") i ":" where[i]
            printf "%d/%d bound  %s", bound, total, list
        }' "$1" | sed 's/ *$//'
}

# variant_a ID LABEL FLAGS...: one allocation, 8 ranks on N1, binding report.
variant_a() {
    local id=$1 label=$2 hosts="" i
    shift 2
    for ((i = 0; i < 8; i++)); do hosts+="$N1,"; done
    run_step "$id" salloc -N1 -w "$N1" --exclusive -p "$PARTITION" -t 00:05:00 \
        mpirun --allow-run-as-root -n 8 -H "${hosts%,}" "$@" --report-bindings true
    if ((RC != 0)); then
        ANY_FAILED=1
        add_line "$(printf '%-2s %-18s FAILED: %s' "$id" "$label" "$(first_error "$STEP_OUT" "$RC")")"
    elif ! grep -q 'MCW rank' "$STEP_OUT"; then
        ANY_FAILED=1
        add_line "$(printf '%-2s %-18s FAILED: no binding lines' "$id" "$label")"
    else
        add_line "$(printf '%-2s %-18s %s' "$id" "$label" "$(bindings "$STEP_OUT" 8)")"
    fi
}

# nic_numbers FILE: "one per first unbound" from nic_check's output, - for a missing value.
nic_numbers() {
    awk '
        $1 ~ /^(one-rank|per-rank|first-nic)$/ { cur = $1; if ($2 ~ /^[0-9.]+$/) v[cur] = $2 }
        /unbound ranks:/ && cur == "per-rank" { u = $NF }
        END {
            printf "%s %s %s %s", (v["one-rank"] == "" ? "-" : v["one-rank"]),
                (v["per-rank"] == "" ? "-" : v["per-rank"]),
                (v["first-nic"] == "" ? "-" : v["first-nic"]), (u == "" ? "-" : u)
        }' "$1"
}

# variant_b ID NODES_LABEL NODE_LIST PROFILE_NAME PROFILE_FILE
variant_b() {
    local id=$1 pair=$2 nodes=$3 pname=$4 profile=$5 one per first unbound
    run_step "$id" salloc -N2 -w "$nodes" --exclusive -p "$PARTITION" -t 00:20:00 env LAUNCHER=mpirun \
        "MPIRUN_FLAGS=$MPIRUN_FLAGS" "$NIC_CHECK" "$profile" "$BLINK"
    read -r one per first unbound <<<"$(nic_numbers "$STEP_OUT")"
    if [[ $one == - && $per == - && $first == - ]]; then
        ANY_FAILED=1
        add_line "$(printf '%-2s %s %-3s  FAILED: %s' "$id" "$pair" "$pname" "$(first_error "$STEP_OUT" "$RC")")"
    else
        add_line "$(printf '%-2s %s %-3s  one %s  per %s  first %s  unbound %s' \
            "$id" "$pair" "$pname" "$one" "$per" "$first" "$unbound")"
    fi
}

# make_profiles: k2, k4 and odd profiles into $WORK; prints the sorted core list of profile8.
make_profiles() {
    "$PYTHON" - "$PROFILE_DIR/profile8.json" "$PROFILE_DIR/profile16.json" "$WORK" <<'PY'
import json
import re
import sys


def cores(profile):
    cpus = profile["cpus"]
    if isinstance(cpus, str):
        found = []
        for part in cpus.split(","):
            lo, _, hi = part.strip().partition("-")
            found.extend(range(int(lo), int(hi or lo) + 1))
        return sorted(found)
    return sorted(int(c) for c in cpus)


def pairs(profile):
    return list(zip(profile["nics"], cores(profile)))


def write(base, name, chosen):
    out = dict(base)
    out["ppn"] = len(chosen)
    out["nics"] = [n for n, _ in chosen]
    out["cpus"] = ",".join(str(c) for _, c in chosen)
    with open(f"{sys.argv[3]}/diagnose_profile_{name}.json", "w") as f:
        json.dump(out, f)


p8 = json.load(open(sys.argv[1]))
p16 = json.load(open(sys.argv[2]))
pairs8 = pairs(p8)
write(p8, "k2", pairs8[:2])
write(p8, "k4", pairs8[:4])
write(p8, "odd", [(n, c) for n, c in pairs(p16) if int(re.search(r"mlx5_(\d+)", n).group(1)) % 2])
print(",".join(str(c) for _, c in pairs8))
PY
}

main() {
    local cores
    echo "diagnose: partition $PARTITION, nodes $N1 $N2 $N3, log $LOG"
    if ! cores=$(make_profiles); then
        echo "diagnose: cannot build the test profiles from $PROFILE_DIR" >&2
        return 2
    fi
    echo "cores of profile8: $cores"

    add_line "A binding, 8 ranks on $N1 (rank:core)"
    variant_a A1 cpu-list:ordered --cpu-list "$cores" --bind-to cpu-list:ordered
    variant_a A2 cpu-list --cpu-list "$cores" --bind-to cpu-list
    variant_a A3 cpu-set+core --cpu-set "$cores" --bind-to core

    add_line "B bandwidth Gb/s per rank (profile: k2 k4 p8 odd NICs)"
    variant_b B1 "$N1-$N2" "$N1,$N2" k2 "$WORK/diagnose_profile_k2.json"
    variant_b B2 "$N1-$N2" "$N1,$N2" k4 "$WORK/diagnose_profile_k4.json"
    variant_b B3 "$N1-$N2" "$N1,$N2" p8 "$PROFILE_DIR/profile8.json"
    variant_b B4 "$N1-$N2" "$N1,$N2" odd "$WORK/diagnose_profile_odd.json"
    variant_b B5 "$N1-$N3" "$N1,$N3" p8 "$PROFILE_DIR/profile8.json"

    SUMMARY=("=== SUMMARY" "${SUMMARY[@]}" "log: ${LOG#"$CHECKOUT"/}")
    echo
    printf '%s\n' "${SUMMARY[@]}"
    printf '%s\n' "${SUMMARY[@]}" >"$SUMFILE"
    return "$ANY_FAILED"
}

main 2>&1 | tee "$LOG"
exit "${PIPESTATUS[0]}"
