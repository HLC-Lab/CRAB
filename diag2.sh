#!/usr/bin/env bash
# Lab diagnostic 2, two nodes, no CRAB. Why did one port per rank work on 10-19 (CRAB pw4 on these
# two nodes) and fail today? Run it inside the allocation:
#   salloc -N 2 --ntasks-per-node=8 --nodelist=<node>,<node>
#   bash ~/crab/lab/diag2.sh
# R  routes on node A's ports (what UCX checks before it connects two ports)
# S  srun --mpi=pmi2 instead of mpirun: does it start Blink at all
# E  the 10-19 launch without CRAB (pairwise_nb rot, 4 ranks/node, ports mlx5_0/4/10/14, cores
#    0,2,40,42, RNDV_RAILS=1, NUM_PATHS=1), then one change at a time
# F  no port per rank (UCX picks), 1/4/8 ranks per node, and which ports UCX used
# Keeps going past failures. Summary fits one screen, all output in $OUT/diag2_<time>.log.
# Env: BLINK, OUT, STEP_TIMEOUT (s per launch), SIZE (bytes).
set -u
NAME=diag2
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

P4=mlx5_0:1,mlx5_4:1,mlx5_10:1,mlx5_14:1
C4=0,2,40,42
MD=(-x UCX_MAX_RNDV_RAILS=1 -x UCX_IB_NUM_PATHS=1)

say "diag2 A=$A B=$B size $SIZE | log: $LOG"
pml_lib=$(ldd "$(dirname "$(dirname "$(command -v mpirun)")")"/lib/openmpi/mca_pml_ucx.so 2>/dev/null |
    awk '/libucp/ {print $3}')
say "mpirun $(command -v mpirun) | pml_ucx uses ${pml_lib:-?}" \
    "| coll: $(ompi_info 2>/dev/null | awk '$1 == "MCA" && $2 == "coll:" {printf "%s ", $3}')"

# --- R: routes ------------------------------------------------------------------------------------
on "$A" bash "$HERE/facts.sh" table "mlx5_0 mlx5_2 mlx5_4 mlx5_10 mlx5_14" >"$TMP/table" 2>&1
log <"$TMP/table"
on "$A" bash "$HERE/facts.sh" detail 2>&1 | log
say "R routes on A, per port: interface: destinations (main table)"
awk '$1 == "table" { printf "  %-8s %-14s:", $3, $4; for (i = 5; i <= NF; i++) printf " %s", $i; print "" }' \
    "$TMP/table" | cut -c1-140 | while IFS= read -r l; do say "$l"; done
say "  ip rule: $(on "$A" ip rule 2>&1 | awk '{$1 = ""; print}' | tr '\n' ';' | cut -c1-120)"

# --- S: srun ----------------------------------------------------------------------------------------
run_cmd srun --mpi=pmi2 -N2 -n2 --ntasks-per-node=1 "$HERE/nic.sh" "$BLINK/pairwise_nb" -msgsize 8 -iter 10
say "S srun --mpi=pmi2, 1 rank/node, 8 B: $VERDICT ${ERRLINE:+| $ERRLINE}"

# --- E: the 10-19 launch, one change at a time ------------------------------------------------------
e_run() {
    local label=$1 n=$2 cpus=$3; shift 3
    run_cmd mpirun -n $((2 * n)) -H "$(hosts "$n")" --cpu-list "$cpus" --bind-to cpu-list:ordered "${UCX_FLAGS[@]}" "$@"
    if [[ $VERDICT == OK ]]; then
        timing
        say "  $label: OK | max ${T_MAX}s = $GBPS Gb/s/rank"
    else
        GBPS=-
        say "  $label: $VERDICT | $(echo "$ERRLINE" | sed 's/^\[[^]]*\] *//' | cut -c1-80)"
    fi
}
say "E one port per rank, pairs cross nodes on the same port (rank r <-> r+ppn)"
e_run "E1 10-19 launch (pairwise_nb, mlx5_0/4/10/14)" 4 "$C4" "${MD[@]}" -x LAB_NICS=$P4 \
    "$HERE/nic.sh" "$BLINK/pairwise_nb" -mode rot -offset 4 -msgsize "$SIZE" -iter 20
e_run "E2 E1 with pairwise_b offpair     " 4 "$C4" "${MD[@]}" -x LAB_NICS=$P4 \
    "$HERE/nic.sh" "$BLINK/pairwise_b" -offset 4 -msgsize "$SIZE" -iter 20
e_run "E3 E1 on ports mlx5_0/2/4/8       " 4 "$C4" "${MD[@]}" -x LAB_NICS=mlx5_0:1,mlx5_2:1,mlx5_4:1,mlx5_8:1 \
    "$HERE/nic.sh" "$BLINK/pairwise_nb" -mode rot -offset 4 -msgsize "$SIZE" -iter 20
e_run "E4 E1 without the two UCX vars    " 4 "$C4" -x LAB_NICS=$P4 \
    "$HERE/nic.sh" "$BLINK/pairwise_nb" -mode rot -offset 4 -msgsize "$SIZE" -iter 20
e_run "E5 E1 at 2/node, mlx5_0 + mlx5_10 " 2 0,40 "${MD[@]}" -x LAB_NICS=mlx5_0:1,mlx5_10:1 \
    "$HERE/nic.sh" "$BLINK/pairwise_nb" -mode rot -offset 2 -msgsize "$SIZE" -iter 20

# --- F: UCX picks the ports ---------------------------------------------------------------------------
say "F no port per rank (UCX picks), pairwise_nb rot offset=ppn; mlx5 ports UCX used by the ranks of A"
for spec in "1 0" "4 $C4" "8 0,1,2,3,40,41,42,43"; do
    read -r n cpus <<<"$spec"
    e_run "F ppn $n" "$n" "$cpus" -x UCX_LOG_LEVEL=info \
        "$HERE/nic.sh" "$BLINK/pairwise_nb" -mode rot -offset "$n" -msgsize "$SIZE" -iter 20
    say "     node total $(awk -v g="$GBPS" -v n="$n" 'BEGIN { if (g + 0 > 0) print g * n; else print "-" }') Gb/s" \
        "| $(ucx_ports | cut -c1-120)"
done
say "done: $LOG"
