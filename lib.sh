# Shared helpers of the lab scripts (sourced, bash). Needs NAME set by the caller.
# Sets A, B (the allocation's two nodes), LOG, TMP; defines say/log/on/run_mpi/report helpers.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BLINK=${BLINK:-$HOME/crab/CRAB/benchmarks/blink/build/bin}
OUT=${OUT:-$HERE/out}
STEP_TIMEOUT=${STEP_TIMEOUT:-60}
SIZE=${SIZE:-134217728}
UCX_FLAGS=(--allow-run-as-root --mca pml ucx --mca btl ^openib)

die() { echo "$NAME: $*" >&2; exit 2; }
[[ -n ${SLURM_JOB_NODELIST:-} ]] || die "run it inside an allocation of two nodes (salloc -N 2 ...)"
mapfile -t NODES < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
((${#NODES[@]} == 2)) || die "needs exactly 2 nodes, the allocation has ${#NODES[@]}"
A=${NODES[0]} B=${NODES[1]}
[[ -x $BLINK/pairwise_nb ]] || die "no $BLINK/pairwise_nb (set BLINK to Blink's build/bin)"
mkdir -p "$OUT" || die "cannot create $OUT"
LOG=$OUT/${NAME}_$(date +%Y%m%d_%H%M%S).log
TMP=$(mktemp -d) || die "mktemp failed"
trap 'rm -rf "$TMP"' EXIT

say() { echo "$*" | tee -a "$LOG"; }
log() { cat >>"$LOG"; }
on() { local node=$1; shift; srun -N1 -n1 -w "$node" "$@"; }
letter() { [[ $1 == "$A" ]] && echo A || { [[ $1 == "$B" ]] && echo B || echo "$1"; }; }
median() { sort -g | awk '{v[NR]=$1} END {if (!NR) {print "-"; exit}
    print (NR % 2) ? v[(NR+1)/2] : (v[NR/2] + v[NR/2+1]) / 2}'; }
# "-H A,A,B,B" style host list with $1 slots per node
hosts() { local n=$1 h="" i; for ((i = 0; i < n; i++)); do h+="$A,"; done
    for ((i = 0; i < n; i++)); do h+="$B,"; done; echo "${h%,}"; }

# one launcher command (mpirun or srun) under a timeout: output in $TMP/run, verdict in
# $VERDICT (OK, unreach, time, err<rc>), first error line in $ERRLINE
run_cmd() {
    local rc
    timeout -k 5 "$STEP_TIMEOUT" "$@" >"$TMP/run" 2>&1
    rc=$?
    { echo "\$ $*"; cat "$TMP/run"; echo "exit $rc"; echo; } | log
    if ((rc == 124 || rc == 137)); then VERDICT=time
    elif grep -q -i -E 'unreachable|not routable' "$TMP/run"; then VERDICT=unreach
    elif ((rc != 0)); then VERDICT=err$rc
    else VERDICT=OK
    fi
    ERRLINE=""
    [[ $VERDICT == OK ]] ||
        ERRLINE=$(grep -m1 -i -E 'error|unreachable|not routable|abort' "$TMP/run" | cut -c1-110)
}

# timing of the last run: T_MAX (median over iterations of the slowest rank), GBPS per rank
# from it (pairwise: each rank sends $SIZE per iteration)
timing() {
    awk -F, 'NF >= 4 && $1 ~ /^[0-9.eE+-]+$/ && $3 ~ /^[0-9.eE+-]+$/ {print $3}' "$TMP/run" >"$TMP/max"
    T_MAX=$(median <"$TMP/max")
    GBPS=$(awk -v s="$SIZE" -v t="$T_MAX" 'BEGIN { if (t + 0 > 0) printf "%.0f", s * 8 / t / 1e9; else print "-" }')
}

# ports UCX used for inter-node traffic, per rank of node A (needs UCX_LOG_LEVEL=info and nic.sh):
# "0:0 1:4+5 ..." (rank:mlx5 port numbers)
ucx_ports() {
    awk -v a="$A" '
        /^lab: rank=/ { split($2, r, "="); split($3, h, "="); split($7, q, "=")
                        if (h[2] == a) rank[q[2]] = r[2] }
        /inter-node cfg/ && match($0, /\[[^]:]+:[0-9]+:/) {
            split(substr($0, RSTART + 1, RLENGTH - 2), hp, ":"); pid = hp[2]; s = $0
            while (match(s, /mlx5_[0-9]+/)) { dev = substr(s, RSTART, RLENGTH); s = substr(s, RSTART + RLENGTH)
                if (!seen[pid, dev]++) used[pid] = used[pid] (used[pid] ? "+" : "") dev }
        }
        END { for (k in rank) print rank[k], (k in used) ? used[k] : "?" }' "$TMP/run" |
        sort -n | awk '{gsub(/mlx5_/, "", $2); printf "%s:%s ", $1, $2}'
}
