#!/usr/bin/env bash
# Lab diagnostic 4, two nodes. Run it inside the allocation:
#   salloc -N 2 --ntasks-per-node=8 --nodelist=<node>,<node>
#   bash ~/crab/lab/diag4.sh
# R  UCX checks reachability with UCX_*_ROCE_REACHABILITY_MODE=route (diag3) and only lets same-port
#    pairs connect. The nodes have policy routes (from each port to 10.0.0.0/8 via a gateway).
#    With the check off (=all), do different ports connect, and does all2all with one port per
#    rank run?
# M  pw.sh: at 1 rank/node one port without the campaign variables is slow at >= 16 MiB. Which of
#    the two (UCX_MAX_RNDV_RAILS=1, UCX_IB_NUM_PATHS=1) matters? 1 and 8 ranks/node, 16 and 128 MiB.
# C  the CRAB checkout here (for the CRAB + Blink step)
# Keeps going past failures. Summary fits one screen, all output in $OUT/diag4_<time>.log.
# Env: BLINK, OUT, STEP_TIMEOUT, CRAB_DIR (default ~/crab/CRAB), ITER (100).
set -u
NAME=diag4
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
CRAB_DIR=${CRAB_DIR:-$HOME/crab/CRAB}
ITER=${ITER:-100}
REACH=(-x UCX_IB_ROCE_REACHABILITY_MODE=all)
short() { echo "$ERRLINE" | sed 's/^\[[^]]*\] *//' | cut -c1-70; }

say "diag4 A=$A B=$B iter $ITER | log: $LOG"

# --- R: reachability check off --------------------------------------------------------------------
say "R UCX_IB_ROCE_REACHABILITY_MODE=all, one port per rank"
CORES2=0,40 PORTS2=mlx5_0:1,mlx5_10:1
mpi_ppn 2 "${REACH[@]}" -x LAB_NICS=$PORTS2 \
    "$HERE/nic.sh" "$BLINK/pairwise_nb" -mode rot -offset 2 -msgsize 1048576 -iter 20
say "  R1 pairwise 2/node, default barrier (failed in K1): $VERDICT${ERRLINE:+ | $(short)}"
grep -i -m1 -E 'invalid|unknown|not.*(used|valid)' "$TMP/run" | cut -c1-110 | while IFS= read -r l; do say "     $l"; done

# all2all: each rank sends -msgsize to each other rank; Gb/s per rank = size*(P-1)*8/t
a2a() {
    local label=$1 n=$2; shift 2
    local s=1048576 p=$((2 * n))
    mpi_ppn "$n" "$@" "$HERE/nic.sh" "$BLINK/alltoall_nb" -msgsize $s -iter "$ITER"
    if [[ $VERDICT == OK ]]; then
        timing $((s * (p - 1)))
        say "  $label: OK | max ${T_MAX}s = $GBPS Gb/s/rank (size*(P-1)) = $(awk -v g="$GBPS" -v n="$n" 'BEGIN{print g * n}') Gb/s/node"
    else
        say "  $label: $VERDICT | $(short)"
    fi
}
for n in 4 8; do
    a2a "R a2a 1MiB $n/node, port per rank, check off" "$n" "${REACH[@]}" -x LAB_NICS="$(ports_for "$n")"
    a2a "R a2a 1MiB $n/node, UCX picks             " "$n"
done

# --- M: which campaign variable ---------------------------------------------------------------------
say "M pairwise_nb one port per rank, Gb/s per rank:  16M / 128M"
for n in 1 8; do
    for v in "port" "port+rails1" "port+paths1" "port+md"; do
        env=()
        [[ $v == *rails1* || $v == *md* ]] && env+=(-x UCX_MAX_RNDV_RAILS=1)
        [[ $v == *paths1* || $v == *md* ]] && env+=(-x UCX_IB_NUM_PATHS=1)
        row=$(printf '  %s/node %-12s' "$n" "$v")
        for s in 16777216 134217728; do
            mpi_ppn "$n" "${env[@]}" -x LAB_NICS="$(ports_for "$n")" \
                "$HERE/nic.sh" "$BLINK/pairwise_nb" -mode rot -offset "$n" -msgsize $s -iter "$ITER"
            if [[ $VERDICT == OK ]]; then timing $s; row+=$(printf ' %8s' "$GBPS"); else row+=$(printf ' %8s' "$VERDICT"); fi
        done
        say "$row"
    done
done

# --- C: CRAB checkout ---------------------------------------------------------------------------------
say "C CRAB at $CRAB_DIR: $(git -C "$CRAB_DIR" log -1 --format='%h %ad %s' --date=short 2>&1 | cut -c1-70)"
say "  branch $(git -C "$CRAB_DIR" rev-parse --abbrev-ref HEAD 2>&1) | changed files $(git -C "$CRAB_DIR" status --porcelain 2>/dev/null | wc -l)" \
    "| crab $(command -v crab || echo 'not on PATH') $(crab --version 2>&1 | head -1 | cut -c1-30)"
say "  presets: $(cd "$CRAB_DIR" 2>/dev/null && ls -d local/presets* config/presets* src/crab/presets.json 2>/dev/null | tr '\n' ' ')" \
    "| nplb_h100 in: $(cd "$CRAB_DIR" 2>/dev/null && grep -l -s nplb_h100 local/presets*.json local/presets/*.json src/crab/presets.json 2>/dev/null | tr '\n' ' ')"
say "done: $LOG"
