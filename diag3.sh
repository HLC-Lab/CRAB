#!/usr/bin/env bash
# Lab diagnostic 3, two nodes, no CRAB. Run it inside the allocation:
#   salloc -N 2 --ntasks-per-node=8 --nodelist=<node>,<node>
#   bash ~/crab/lab/diag3.sh
# U  UCX settings about RoCE reachability (ucx_info -cf)
# P  policy routing on node A: ip rules and what their tables hold
# K  one port per rank failed at 2 ranks/node (D4, E5) but worked at 4 (E1). Is it the barrier
#    algorithm (which ranks of different ports must connect)? Force it and see.
# L  all2all with one port per rank (every rank talks to every rank, so ports must cross)
# Keeps going past failures. Summary fits one screen, all output in $OUT/diag3_<time>.log.
set -u
NAME=diag3
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

say "diag3 A=$A B=$B | log: $LOG"

# --- U: UCX settings --------------------------------------------------------------------------------
ucx_info -cf >"$TMP/ucx" 2>&1
log <"$TMP/ucx"
say "U ucx_info -cf, RoCE/reachability settings:"
grep -i -E '^UCX_[A-Z_]*(ROCE|REACH|SUBNET|ROUTE)[A-Z_]*=' "$TMP/ucx" | head -8 | cut -c1-140 |
    while IFS= read -r l; do say "  $l"; done

# --- P: policy routing ----------------------------------------------------------------------------
on "$A" bash -c 'ip rule; for t in $(ip rule | grep -o "lookup [0-9]*" | awk "{print \$2}" | sort -un); do
    echo "table $t: $(ip -4 route show table "$t" | tr "\n" ";")"; done' >"$TMP/rules" 2>&1
log <"$TMP/rules"
say "P ip rules on A (from -> table): $(grep -o -E 'from [0-9.]+ lookup [0-9]+' "$TMP/rules" |
    awk '{printf "%s>%s ", $2, $4}' | cut -c1-110)"
grep '^table' "$TMP/rules" | head -4 | cut -c1-140 | while IFS= read -r l; do say "  $l"; done

# --- K: barrier algorithm ---------------------------------------------------------------------------
say "K barrier algorithms: $(ompi_info --param coll tuned --level 9 2>/dev/null |
    grep -A2 'coll_tuned_barrier_algorithm"' | grep -o '[0-9]:"[a-z_]*"' | tr '\n' ' ')"
k_run() {
    local label=$1 n=$2 ports=$3 alg=$4
    local dyn=()
    [[ $alg != - ]] && dyn=(--mca coll_tuned_use_dynamic_rules 1 --mca coll_tuned_barrier_algorithm "$alg")
    mpi_ppn "$n" "${dyn[@]}" -x LAB_NICS="$ports" \
        "$HERE/nic.sh" "$BLINK/pairwise_nb" -mode rot -offset "$n" -msgsize 1048576 -iter 20
    say "  $label: $VERDICT${ERRLINE:+ | $(echo "$ERRLINE" | sed 's/^\[[^]]*\] *//' | cut -c1-70)}"
}
CORES2=0,40
PORTS2=mlx5_0:1,mlx5_10:1
k_run "K1 2/node, default barrier          " 2 "$PORTS2" -
k_run "K2 2/node, recursive doubling (3)   " 2 "$PORTS2" 3
k_run "K3 4/node, default barrier          " 4 "$PORTS4" -
k_run "K4 4/node, bruck (4)                " 4 "$PORTS4" 4
k_run "K5 8/node, default barrier          " 8 "$PORTS8" -

# --- L: all2all -------------------------------------------------------------------------------------
say "L alltoall_nb 1 MiB, one port per rank"
for n in 1 4 8; do
    mpi_ppn "$n" -x LAB_NICS="$(ports_for "$n")" \
        "$HERE/nic.sh" "$BLINK/alltoall_nb" -msgsize 1048576 -iter 20
    say "  $n/node: $VERDICT${ERRLINE:+ | $(echo "$ERRLINE" | sed 's/^\[[^]]*\] *//' | cut -c1-80)}"
done
mpi_ppn 4 "$HERE/nic.sh" "$BLINK/alltoall_nb" -msgsize 1048576 -iter 20
say "  4/node, UCX picks the ports: $VERDICT"
say "done: $LOG"
