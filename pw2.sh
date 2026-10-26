#!/usr/bin/env bash
# Blink pairwise alone (no CRAB), two nodes, 1/4/8 ranks per node: do all ranks take the same time,
# and is the full bandwidth used? Run it inside
#   salloc -N 2 --ntasks-per-node=8 --nodelist=<node>,<node>
#   bash ~/crab/lab/pw2.sh
# Every rank has its own port (one per card, on the card's socket) and pairs with the same local
# rank on the other node (-mode rot -offset <ppn>). One mpirun per (ppn, variant, size).
# Variants: md = UCX_MAX_RNDV_RAILS=1 UCX_IB_NUM_PATHS=1 (diag4: either alone gives the same);
#           md+zc = md + UCX_RNDV_SCHEME=put_zcopy.
# Screen, per size: "slowest/fastest" rank in Gb/s (medians over iterations of the slowest and of
# the fastest rank of each iteration), and the node total (slowest x ppn). One port carries about
# 390 Gb/s per direction.
# Data: $OUT/pw2_<time>.csv, full output in $OUT/pw2_<time>.log.
# Env: PPNS ("1 4 8"), VARIANTS ("md md+zc"), SIZES, ITER (1000), WARMUP (50), PROG (pairwise_nb),
#      STEP_TIMEOUT (180 s per mpirun).
set -u
NAME=pw2
STEP_TIMEOUT=${STEP_TIMEOUT:-180}   # 512 MiB x 1000 at 8/node takes ~30 s
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PPNS=${PPNS:-1 4 8}
VARIANTS=${VARIANTS:-md md+zc}
SIZES=${SIZES:-65536 2097152 16777216 134217728 536870912}
ITER=${ITER:-1000}
WARMUP=${WARMUP:-50}
PROG=${PROG:-pairwise_nb}
CSV=${LOG%.log}.csv
echo "ppn,variant,size,grty,iter,verdict,t_max_med_s,t_min_med_s,t_avg_med_s,gbps_slowest,gbps_fastest" >"$CSV"

variant_env() {
    echo -x UCX_MAX_RNDV_RAILS=1 -x UCX_IB_NUM_PATHS=1
    [[ $1 == *zc* ]] && echo -x UCX_RNDV_SCHEME=put_zcopy
    return 0
}
human() { awk -v s="$1" 'BEGIN { split("B K M G", u); i = 1
    while (s >= 1024 && i < 4) { s /= 1024; i++ }; printf "%d%s", s, u[i] }'; }

say "pw2 $PROG A=$A B=$B iter $ITER warmup $WARMUP | slowest/fastest rank Gb/s | csv: $CSV"
for n in $PPNS; do
    say "ppn $n  ports $(ports_for "$n" | sed 's/:1//g; s/mlx5_//g')  cores $(cores_for "$n")"
    head=$(printf '  %-12s' variant)
    for s in $SIZES; do head+=$(printf ' %11s' "$(human "$s")"); done
    say "$head"
    for v in $VARIANTS; do
        row=$(printf '  %-12s' "$v") node=$(printf '  %-12s' "  node total")
        for s in $SIZES; do
            grty=$(awk -v s="$s" 'BEGIN { g = int((2097152 + s - 1) / s); print (g > 100) ? 100 : (g < 1 ? 1 : g) }')
            # shellcheck disable=SC2046
            mpi_ppn "$n" $(variant_env "$v") -x LAB_NICS="$(ports_for "$n")" "$HERE/nic.sh" "$BLINK/$PROG" \
                -mode rot -offset "$n" -msgsize "$s" -grty "$grty" -iter "$ITER" -warmup "$WARMUP"
            if [[ $VERDICT == OK ]]; then
                timing $((s * grty))
                cell="$GBPS/$GBPS_FAST"
                tot=$(awk -v g="$GBPS" -v n="$n" 'BEGIN { printf "%.0f", g * n }')
            else
                T_MAX=- T_MIN=- T_AVG=- GBPS=- GBPS_FAST=- cell=$VERDICT tot=-
            fi
            echo "$n,$v,$s,$grty,$ITER,$VERDICT,$T_MAX,$T_MIN,$T_AVG,$GBPS,$GBPS_FAST" >>"$CSV"
            row+=$(printf ' %11s' "$cell")
            node+=$(printf ' %11s' "$tot")
        done
        say "$row"
        say "$node"
    done
done
say "done: $CSV"
