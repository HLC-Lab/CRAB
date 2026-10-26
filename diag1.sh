#!/usr/bin/env bash
# Lab diagnostic 1, two nodes, no CRAB. Run it inside the allocation:
#   salloc -N 2 -n 4 --nodelist=<node>,<node>
#   bash ~/crab/lab/diag1.sh
# A  each tested port on both nodes: state, link layer, interface, IPv4 address, NUMA node.
# B  `ip route get <peer port address> oif <local port interface>` for every port pair (the check
#    UCX makes before it opens a connection).
# C  Blink pairwise_b, 8 B x 10, one rank per node: rank 0 on port i of node A, rank 1 on port j
#    of node B, for every i, j.
# D  the first test again (mpirun -np 4, default placement and pairing) and three variants, with
#    where each rank ran, whom it paired with, and the time.
# Keeps going past failures. Summary fits one screen (~32 lines), all output in $OUT/diag1_<time>.log.
# Env: BLINK (dir with pairwise_b), DEVS (ports to test), OUT, STEP_TIMEOUT (s per mpirun),
#      MPIRUN_FLAGS (C and D3-D4), SIZE (bytes, D).
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BLINK=${BLINK:-$HOME/crab/CRAB/benchmarks/blink/build/bin}
DEVS=${DEVS:-mlx5_0 mlx5_1 mlx5_2 mlx5_4}
OUT=${OUT:-$HERE/out}
STEP_TIMEOUT=${STEP_TIMEOUT:-40}
MPIRUN_FLAGS=${MPIRUN_FLAGS:---allow-run-as-root --mca pml ucx --mca btl ^openib}
SIZE=${SIZE:-134217728}

die() { echo "diag1: $*" >&2; exit 2; }
[[ -n ${SLURM_JOB_NODELIST:-} ]] || die "run it inside an allocation: salloc -N 2 -n 4 --nodelist=<a>,<b>"
mapfile -t NODES < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
((${#NODES[@]} == 2)) || die "needs exactly 2 nodes, the allocation has ${#NODES[@]}"
A=${NODES[0]} B=${NODES[1]}
[[ -x $BLINK/pairwise_b ]] || die "no $BLINK/pairwise_b (set BLINK to Blink's build/bin)"
mkdir -p "$OUT" || die "cannot create $OUT"
LOG=$OUT/diag1_$(date +%Y%m%d_%H%M%S).log
TMP=$(mktemp -d) || die "mktemp failed"
trap 'rm -rf "$TMP"' EXIT

say() { echo "$*" | tee -a "$LOG"; }
log() { cat >>"$LOG"; }
on() { local node=$1; shift; srun -N1 -n1 -w "$node" "$@"; }
letter() { [[ $1 == "$A" ]] && echo A || { [[ $1 == "$B" ]] && echo B || echo "$1"; }; }
median() { sort -g | awk '{v[NR]=$1} END {if (!NR) {print "-"; exit}
    print (NR % 2) ? v[(NR+1)/2] : (v[NR/2] + v[NR/2+1]) / 2}'; }

# one mpirun under a timeout; output in $TMP/run, verdict in $VERDICT
run_mpi() {
    local rc
    timeout -k 5 "$STEP_TIMEOUT" mpirun "$@" >"$TMP/run" 2>&1
    rc=$?
    { echo "\$ mpirun $*"; cat "$TMP/run"; echo "exit $rc"; echo; } | log
    if ((rc == 124 || rc == 137)); then VERDICT=time
    elif grep -q -i -E 'unreachable|not routable' "$TMP/run"; then VERDICT=unreach
    elif ((rc != 0)); then VERDICT=err$rc
    else VERDICT=OK
    fi
}

say "diag1 A=$A B=$B ports: $DEVS | log: $LOG"
say "mpirun $(mpirun --version 2>/dev/null | head -1 | awk '{print $NF}')" \
    "ucx $(ucx_info -v 2>/dev/null | grep -o -m1 'version=[^ ]*' | cut -d= -f2)" \
    "| srun --mpi: $(srun --mpi=list 2>&1 | grep -v -i 'mpi plugin types' | awk '{print $NF}' | tr '\n' ' ')"
ucx_vars=$(env | grep '^UCX_' | tr '\n' ' ')
say "UCX vars in this shell: ${ucx_vars:-none}"

# --- A: ports ---------------------------------------------------------------------------------
declare -A ADDR
say "A ports: dev state link netdev ipv4 numa"
for n in "$A" "$B"; do
    on "$n" bash "$HERE/facts.sh" ports >"$TMP/ports" 2>&1
    log <"$TMP/ports"
    on "$n" bash "$HERE/facts.sh" detail 2>&1 | log
    for d in $DEVS; do
        line=$(awk -v d="$d" '$1 == "port" && $3 == d' "$TMP/ports")
        if [[ -z $line ]]; then
            say "  $(letter "$n") $d not found"
            ADDR[$n:$d]=-
            continue
        fi
        read -r _ _ _ state link nd ip numa _ <<<"$line"
        ADDR[$n:$d]=$ip
        say "  $(letter "$n") $d $state $link $nd $ip numa$numa"
    done
done

# --- B: routes --------------------------------------------------------------------------------
grid_header() { local h="  $1" d; for d in $DEVS; do h+=$(printf ' %-8s' "$d"); done; say "$h"; }
: >"$TMP/routes"
for pair in "$A $B" "$B $A"; do
    read -r src dst <<<"$pair"
    peers=""
    for d in $DEVS; do peers+="$d=${ADDR[$dst:$d]} "; done
    on "$src" bash "$HERE/facts.sh" routes "$DEVS" "$peers" >>"$TMP/routes" 2>&1
done
log <"$TMP/routes"
say "B route: cell = A port (row) -> B port address (col) / B port (col) -> A port address (row)"
grid_header "        "
for l in $DEVS; do
    row=$(printf '  %-8s' "$l")
    for p in $DEVS; do
        ab=$(awk -v h="$A" -v l="$l" -v p="$p" '$1 == "route" && $2 == h && $3 == l && $4 == p {print $5}' "$TMP/routes")
        ba=$(awk -v h="$B" -v l="$p" -v p="$l" '$1 == "route" && $2 == h && $3 == l && $4 == p {print $5}' "$TMP/routes")
        row+=$(printf ' %-8s' "${ab:-?}/${ba:-?}")
    done
    say "$row"
done

# --- C: MPI reachability, one rank per node -----------------------------------------------------
say "C pairwise_b 8 B, 1 rank/node: rank 0 on A port (rows), rank 1 on B port (cols)"
grid_header "        "
for i in $DEVS; do
    row=$(printf '  %-8s' "$i")
    for j in $DEVS; do
        # shellcheck disable=SC2086
        run_mpi -np 2 --map-by ppr:1:node $MPIRUN_FLAGS -x "LAB_NIC_BY_RANK=$i:1,$j:1" \
            "$HERE/nic.sh" "$BLINK/pairwise_b" -msgsize 8 -iter 10
        row+=$(printf ' %-8s' "$VERDICT")
    done
    say "$row"
done

# --- D: placement and pairing of the first test --------------------------------------------------
# summary of the last run: ranks (rank@node/local:port), partners, median over iterations
# of the slowest rank (max) and of the rank average, and Gb/s per rank from the max
d_report() {
    local label=$1 ranks partners tmax tavg gbps
    ranks=$(awk '/^lab: rank=/ {
            split($2, r, "="); split($3, h, "="); split($4, l, "="); split($5, d, "=")
            print r[2], h[2], l[2], d[2] }' "$TMP/run" | sort -n |
        while read -r r h l d; do printf '%s@%s/%s:%s ' "$r" "$(letter "$h")" "$l" "${d%:1}"; done)
    partners=$(awk '/^DEBUG rank=/ { split($2, r, "="); split($4, p, "="); print r[2] ">" p[2] }' \
        "$TMP/run" | sort -n | tr '\n' ' ')
    awk -F, 'NF >= 4 && $1 ~ /^[0-9.eE+-]+$/ && $3 ~ /^[0-9.eE+-]+$/ {print $3}' "$TMP/run" >"$TMP/max"
    awk -F, 'NF >= 4 && $1 ~ /^[0-9.eE+-]+$/ && $3 ~ /^[0-9.eE+-]+$/ {print $1}' "$TMP/run" >"$TMP/avg"
    tmax=$(median <"$TMP/max") tavg=$(median <"$TMP/avg")
    gbps=$(awk -v s="$SIZE" -v t="$tmax" 'BEGIN { if (t + 0 > 0) printf "%.0f", s * 8 / t / 1e9; else print "-" }')
    say "  $label [$VERDICT] $ranks"
    say "     pairs ${partners:-? (no -debug output)} | max ${tmax}s = $gbps Gb/s/rank | avg ${tavg}s"
}

# Blink without -debug support rejects the flag: run again without it
blink_d() {
    run_mpi "$@" -debug
    grep -q 'Unknown argument' "$TMP/run" && run_mpi "$@"
}

say "D pairwise_b $SIZE B x 20, 4 ranks, placement rank@node/local:port"
blink_d -np 4 --allow-run-as-root \
    "$HERE/nic.sh" "$BLINK/pairwise_b" -msgsize "$SIZE" -iter 20
d_report "D1 first test (plain mpirun, offset 1)"
blink_d -np 4 --allow-run-as-root \
    "$HERE/nic.sh" "$BLINK/pairwise_b" -msgsize "$SIZE" -iter 20 -offset 2
d_report "D2 plain mpirun, offset 2"
# shellcheck disable=SC2086
blink_d -np 4 --map-by ppr:2:node $MPIRUN_FLAGS -x LAB_NICS=mlx5_0:1 \
    "$HERE/nic.sh" "$BLINK/pairwise_b" -msgsize "$SIZE" -iter 20 -offset 2
d_report "D3 ucx, offset 2, all on mlx5_0"
# shellcheck disable=SC2086
blink_d -np 4 --map-by ppr:2:node $MPIRUN_FLAGS -x LAB_NICS=mlx5_0:1,mlx5_2:1 \
    "$HERE/nic.sh" "$BLINK/pairwise_b" -msgsize "$SIZE" -iter 20 -offset 2
d_report "D4 ucx, offset 2, mlx5_0/mlx5_2 by local rank"
say "done: $LOG"
