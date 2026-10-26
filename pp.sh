#!/usr/bin/env bash
# Blink, two nodes, 1/4/8 ranks per node, launched as in R1-R3 (one port per rank, ranks pinned to
# their card's socket, rank r paired with rank r+ppn, UCX_MAX_RNDV_RAILS=1, -warmup 50 -iter 10).
# Run it inside  salloc -N 2 --ntasks-per-node=8 --nodelist=<node>,<node>:  bash ~/crab/lab/pp.sh
# Programs (rows), each without and with UCX_RNDV_SCHEME=put_zcopy (+zc):
#   pairwise_b           MPI_Sendrecv: each rank sends and receives at the same time
#   pingpong_pairwise_b  classic ping-pong per pair (rank r <-> r + nranks/2): one direction at a
#                        time, time = round trip / 2
#   pairwise_nb          Isend/Irecv/Waitall (as pw2), to compare with pairwise_b at 2 MiB (R6)
# Screen: Gibit/s per rank, "slowest/fastest" (medians over the 10 iterations of the Maximum and
# Minimum column), per message size and ranks per node. Raw Blink output in $OUT/pp_<time>.log.
# Env: PROGS, SIZES ("134217728 2097152"), PPNS ("1 4 8"), ITER (10), WARMUP (50).
set -u
NAME=pp
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PROGS=${PROGS:-pairwise_b pingpong_pairwise_b pairwise_nb}
SIZES=${SIZES:-134217728 2097152}
PPNS=${PPNS:-1 4 8}
ITER=${ITER:-10}
WARMUP=${WARMUP:-50}
for p in $PROGS; do [[ -x $BLINK/$p ]] || die "no $BLINK/$p"; done
human() { awk -v s="$1" 'BEGIN { split("B K M G", u); i = 1
    while (s >= 1024 && i < 4) { s /= 1024; i++ }; printf "%d%s", s, u[i] }'; }

# Gibit/s per rank from a time in seconds, one message of $1 bytes
gib() { awk -v s="$1" -v t="$2" 'BEGIN { if (t + 0 <= 0) print "-"; else printf "%.0f", s * 8 / 2^30 / t }'; }

say "pp A=$A B=$B iter $ITER warmup $WARMUP | Gibit/s per rank, slowest/fastest | log: $LOG"
for s in $SIZES; do
    head=$(printf '%-26s' "$(human "$s")")
    for n in $PPNS; do head+=$(printf ' %13s' "$n/node"); done
    say "$head"
    for p in $PROGS; do
        for zc in 0 1; do
            label=$p
            env=(-x UCX_MAX_RNDV_RAILS=1)
            ((zc)) && { label+=" +zc"; env+=(-x UCX_RNDV_SCHEME=put_zcopy); }
            row=$(printf '  %-24s' "$label")
            for n in $PPNS; do
                args=(-msgsize "$s" -iter "$ITER" -warmup "$WARMUP")
                [[ $p == pairwise* ]] && args=(-offset "$n" "${args[@]}")
                mpi_ppn "$n" "${env[@]}" -x LAB_NICS="$(ports_for "$n")" "$HERE/nic.sh" "$BLINK/$p" "${args[@]}"
                if [[ $VERDICT == OK ]]; then
                    timing "$s"
                    cell="$(gib "$s" "$T_MAX")/$(gib "$s" "$T_MIN")"
                else
                    cell=$VERDICT
                fi
                row+=$(printf ' %13s' "$cell")
            done
            say "$row"
        done
    done
done
say "done: $LOG"
