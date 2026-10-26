#!/usr/bin/env bash
# Blink pairwise alone (no CRAB), two nodes, 1/4/8 ranks per node, a few UCX settings. Run it inside
#   salloc -N 2 --ntasks-per-node=8 --nodelist=<node>,<node>
#   bash ~/crab/lab/pw.sh
# Every rank pairs with the same local rank on the other node (-mode rot -offset <ppn>), so a pair
# shares one port number. One mpirun per (ppn, variant, size).
# Variants (rows):
#   auto        no port per rank, no UCX variable (UCX picks the ports)
#   port        one port per rank (one per card, on its socket)
#   port+md     + UCX_MAX_RNDV_RAILS=1 UCX_IB_NUM_PATHS=1 (the campaign settings)
#   port+zc     + UCX_RNDV_SCHEME=put_zcopy
#   port+md+zc  all of them
# Screen: Gb/s per rank (slowest rank, median over iterations) per ppn, variant and size.
# Data: $OUT/pw_<time>.csv (one line per cell), full output in $OUT/pw_<time>.log.
# Env: PPNS ("1 4 8"), VARIANTS, SIZES (bytes), ITER (100), WARMUP (10), PROG (pairwise_nb),
#      PORTS1/4/8 and CORES1/4/8 (lib.sh), BLINK, OUT, STEP_TIMEOUT.
set -u
NAME=pw
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PPNS=${PPNS:-1 4 8}
VARIANTS=${VARIANTS:-auto port port+md port+zc port+md+zc}
SIZES=${SIZES:-8 4096 65536 2097152 16777216 134217728}
ITER=${ITER:-100}
WARMUP=${WARMUP:-10}
PROG=${PROG:-pairwise_nb}
CSV=${LOG%.log}.csv
echo "ppn,variant,size,grty,iter,verdict,t_max_med_s,t_avg_med_s,gbps_per_rank" >"$CSV"

variant_args() {
    local n=$1 v=$2
    [[ $v == port* ]] && echo -x "LAB_NICS=$(ports_for "$n")"
    [[ $v == *md* ]] && echo -x UCX_MAX_RNDV_RAILS=1 -x UCX_IB_NUM_PATHS=1
    [[ $v == *zc* ]] && echo -x UCX_RNDV_SCHEME=put_zcopy
    return 0
}
human() { awk -v s="$1" 'BEGIN { split("B K M G", u); i = 1
    while (s >= 1024 && i < 4) { s /= 1024; i++ }; printf "%d%s", s, u[i] }'; }

say "pw $PROG A=$A B=$B iter $ITER warmup $WARMUP | csv: $CSV"
for n in $PPNS; do
    say "ppn $n  ports $(ports_for "$n" | sed 's/:1//g; s/mlx5_//g')  cores $(cores_for "$n")  (Gb/s per rank)"
    head=$(printf '  %-11s' variant)
    for s in $SIZES; do head+=$(printf ' %8s' "$(human "$s")"); done
    say "$head"
    for v in $VARIANTS; do
        row=$(printf '  %-11s' "$v")
        for s in $SIZES; do
            grty=$(awk -v s="$s" 'BEGIN { g = int((2097152 + s - 1) / s); print (g > 100) ? 100 : (g < 1 ? 1 : g) }')
            # shellcheck disable=SC2046
            mpi_ppn "$n" $(variant_args "$n" "$v") "$HERE/nic.sh" "$BLINK/$PROG" \
                -mode rot -offset "$n" -msgsize "$s" -grty "$grty" -iter "$ITER" -warmup "$WARMUP"
            if [[ $VERDICT == OK ]]; then
                timing $((s * grty))
                cell=$GBPS
            else
                T_MAX=- T_AVG=- GBPS=- cell=$VERDICT
            fi
            echo "$n,$v,$s,$grty,$ITER,$VERDICT,$T_MAX,$T_AVG,$GBPS" >>"$CSV"
            row+=$(printf ' %8s' "$cell")
        done
        say "$row"
    done
done
say "done: $CSV"
