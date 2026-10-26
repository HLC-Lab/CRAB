#!/usr/bin/env bash
# GPU to GPU across the two nodes with nccl-tests. Pair g = GPU g on A with GPU g on B, through the
# port next to GPU g (one port per GPU, NCCL_IB_HCA). Each pair is its own 2-rank job; the pairs of
# one test run at the same time. Run it on node A inside the allocation, after nccl_build.sh:
#   bash ~/crab/lab/nccl_run.sh
# Programs:  sendrecv   both directions at once (like pairwise)
#            broadcast  GPU on A sends to GPU on B, one direction
# Tests:     G1 GPU0 | G4 GPU0-3 (socket 0) | G22 GPU0,1,4,5 (2 + 2 sockets) | G8 GPU0-7
# Screen: algbw of each pair (out-of-place, nccl-tests average over -n iterations) in Gibit/s, per
# message size, and how many pairs NCCL ran with GPUDirect RDMA (GDRDMA: the card reads and writes
# GPU memory directly). nccl-tests output in $OUT/nccl_<time>.log, NCCL's own log per process in
# $OUT/nccl_<time>_debug/.
# Refuses to run while other processes use the GPUs, unless BUSY_OK=1.
# Env: TESTS ("G1 G4 G22 G8"), PROGS ("sendrecv broadcast"), ITER (100), WARMUP (20), GID (auto),
#      MGMT (interface of the default route), NCCL_EXTRA ("NAME=value ..." passed to every rank),
#      BUSY_OK, OUT, SSH.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OUT=${OUT:-$HERE/out}
SSH=${SSH:-ssh}
NT=$HERE/nccl-tests/build
TESTS=${TESTS:-G1 G4 G22 G8}
PROGS=${PROGS:-sendrecv broadcast}
ITER=${ITER:-100}
WARMUP=${WARMUP:-20}
die() { echo "nccl_run: $*" >&2; exit 2; }
[[ -n ${SLURM_JOB_NODELIST:-} ]] || die "run it inside an allocation of two nodes"
mapfile -t NODES < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
((${#NODES[@]} == 2)) || die "needs exactly 2 nodes, the allocation has ${#NODES[@]}"
A=${NODES[0]} B=${NODES[1]}
[[ -x $NT/sendrecv_perf ]] || die "no $NT/sendrecv_perf: run nccl_build.sh first"
mkdir -p "$OUT" || die "cannot create $OUT"
STAMP=$(date +%Y%m%d_%H%M%S)
LOG=$OUT/nccl_$STAMP.log
DBG=$OUT/nccl_${STAMP}_debug
mkdir -p "$DBG" || die "cannot create $DBG"
EXTRA=()
for kv in ${NCCL_EXTRA:-}; do EXTRA+=(-x "$kv"); done
TMP=$(mktemp -d) || die "mktemp failed"
trap 'rm -rf "$TMP"' EXIT
say() { echo "$*" | tee -a "$LOG"; }

# the port next to each GPU (nvidia-smi topo -m on these nodes: PIX), first port of the card
PORT=(mlx5_0 mlx5_2 mlx5_4 mlx5_8 mlx5_10 mlx5_12 mlx5_14 mlx5_16)

for n in "$A" "$B"; do
    busy=$($SSH "$n" "nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader 2>/dev/null" | grep -v -i "no running")
    if [[ -n $busy && ${BUSY_OK:-0} != 1 ]]; then
        say "$n: $(echo "$busy" | wc -l) processes on the GPUs: $(echo "$busy" | awk -F', ' '{print $2}' | sort | uniq -c | tr -s ' \n' ' ')"
        die "GPUs in use on $n: check who runs them; BUSY_OK=1 runs anyway"
    fi
done

# RoCE v2 GID index with an IPv4 address on the first port (NCCL 2.18 does not pick it by itself)
GID=${GID:-$(for t in /sys/class/infiniband/mlx5_0/ports/1/gid_attrs/types/*; do
    i=${t##*/}
    [[ $(cat "$t" 2>/dev/null) == "RoCE v2" ]] &&
        grep -q '^0000:0000:0000:0000:0000:ffff:' /sys/class/infiniband/mlx5_0/ports/1/gids/"$i" 2>/dev/null &&
        { echo "$i"; break; }
done)}
MGMT=${MGMT:-$(ip -4 route show default | awk '{for (i = 1; i < NF; i++) if ($i == "dev") {print $(i + 1); exit}}')}
say "nccl A=$A B=$B gid ${GID:-?} bootstrap ${MGMT:-?} iter $ITER warmup $WARMUP${NCCL_EXTRA:+ | $NCCL_EXTRA} | Gibit/s per pair | log: $LOG"
[[ -n $GID ]] || die "no RoCE v2 IPv4 GID on mlx5_0 (set GID=)"

# nccl-tests links libmpi without an rpath: give the ranks Open MPI's library directory
MPI_HOME=$(dirname "$(dirname "$(command -v mpirun)")")
export LD_LIBRARY_PATH="$MPI_HOME/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

gib() { awk -v g="$1" 'BEGIN { if (g == "") print "?"; else printf "%.0f", g * 8e9 / 2^30 }'; }

# run_test LABEL PROG GPU...
run_test() {
    local label=$1 prog=$2; shift 2
    local g pids=() s row gdr=0
    for g in "$@"; do
        # NCCL's INFO lines go to files (NCCL_DEBUG_FILE): on stdout they land inside the result rows
        timeout -k 10 300 mpirun --prefix "$MPI_HOME" --mca plm rsh -n 2 -H "$A,$B" --allow-run-as-root --bind-to none \
            -x CUDA_VISIBLE_DEVICES="$g" -x NCCL_IB_HCA="=${PORT[g]}:1" -x NCCL_IB_GID_INDEX="$GID" \
            ${MGMT:+-x NCCL_SOCKET_IFNAME="=$MGMT"} -x NCCL_DEBUG=INFO -x NCCL_DEBUG_SUBSYS=INIT,NET \
            -x NCCL_DEBUG_FILE="$DBG/${label}_${prog}_gpu$g.%h.%p" "${EXTRA[@]}" \
            -x LD_LIBRARY_PATH -x PATH \
            "$NT/${prog}_perf" -b 2M -e 128M -f 64 -g 1 -w "$WARMUP" -n "$ITER" -c 0 \
            >"$TMP/$g.out" 2>&1 &
        pids+=($!)
    done
    for g in "${pids[@]}"; do wait "$g"; done
    for g in "$@"; do
        { echo "#### $label $prog GPU$g port ${PORT[g]}"; cat "$TMP/$g.out"; } >>"$LOG"
        cat "$DBG/${label}_${prog}_gpu$g".* 2>/dev/null | grep -q 'GDRDMA' && gdr=$((gdr + 1))
    done
    for s in 2097152 134217728; do
        row=$(printf '  %-4s %-9s %4s ' "$label" "$prog" "$((s / 1048576))M")
        for g in "$@"; do
            v=$(awk -v s="$s" '$1 == s && NF >= 8 {print $7; exit}' "$TMP/$g.out")
            [[ -z $v ]] && v=FAIL || v=$(gib "$v")
            row+=$(printf ' %s:%-5s' "$g" "$v")
        done
        say "$row| gdr $gdr/$#"
    done
}

for t in $TESTS; do
    for p in $PROGS; do
        case $t in
        G1) run_test G1 "$p" 0 ;;
        G4) run_test G4 "$p" 0 1 2 3 ;;
        G22) run_test G22 "$p" 0 1 4 5 ;;
        G8) run_test G8 "$p" 0 1 2 3 4 5 6 7 ;;
        *) say "unknown test $t" ;;
        esac
    done
done
say "done: $LOG"
