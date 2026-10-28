#!/usr/bin/env bash
# Blink with and without -gpu, same launch, same UCX: does the buffer memory change the bandwidth?
#   salloc -N 2 --ntasks-per-node=8 --nodelist=<a>,<b>
#   bash ~/crab/lab/gpu_pw.sh smoke     does -gpu run, which GPU each rank uses, how UCX moves device memory
#   bash ~/crab/lab/gpu_pw.sh main      8/node pairwise_b host vs -gpu first, then the whole matrix
#   bash ~/crab/lab/gpu_pw.sh best      short GPU run at the best setting: pingpong and pairwise_b 2M-512M, pairwise_nb, PCI relaxed ordering
# Placement as in the CPU runs R1-R5: one port per rank (LAB_NICS), each rank on a core of its card's socket,
# rank r paired with the rank of the same local index on the other node (-offset = ranks per node), every rank
# sees exactly one GPU (LAB_GPUS -> CUDA_VISIBLE_DEVICES, the GPU behind the same PCIe switch as its port).
# Every run carries UCX_MAX_RNDV_RAILS=1 and UCX_IB_ROCE_REACHABILITY_MODE=all: Blink's -gpu startup probe is a
# ring (rank r -> r+1), which connects different ports.
# main: rows = program x {host, gpu, gpu +zc (put_zcopy), gpu GDR off (UCX_IB_GPU_DIRECT_RDMA=no)}, columns =
# 8/node, 4/node 2 + 2 sockets, 4/node on socket 0, 1/node; Gibit/s per rank, slowest/fastest (medians over the
# iterations). The command line of every run and Blink's output are in $OUT/gpu_pw_<time>.log, one CSV line per
# cell in the .csv next to it.
# Env: BLINK_GPU_BIN (~/crab/lab/blink-gpu/build-cuda/bin; not BLINK, which env.sh points at the old CRAB build), SIZES ("134217728 2097152"), PROGS ("pairwise_b
#      pingpong_pairwise_b"), STEP_TIMEOUT (180; best: BEST_TIMEOUT, 200), OUT.
set -u
NAME=gpu_pw
HERE_LAB=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BLINK=${BLINK_GPU_BIN:-$HOME/crab/lab/blink-gpu/build-cuda/bin}
[[ -x $HERE_LAB/ompi-cuda/bin/mpirun ]] && export PATH=$HERE_LAB/ompi-cuda/bin:$PATH
STEP_TIMEOUT=${STEP_TIMEOUT:-180}
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
MODE=${1:-main}
SIZES=${SIZES:-134217728 2097152}
PROGS=${PROGS:-pairwise_b pingpong_pairwise_b}
CSV=${LOG%.log}.csv
echo "layout,prog,mem,variant,size,verdict,t_max_s,t_min_s,gibit_slowest,gibit_fastest,gbit_slowest,gbit_fastest" >"$CSV"
[[ -d $HERE_LAB/ucx-cuda/lib ]] && UCX_CUDA_LIB=$HERE_LAB/ucx-cuda/lib: || UCX_CUDA_LIB=
export LD_LIBRARY_PATH="$UCX_CUDA_LIB/usr/local/cuda-12.3/targets/x86_64-linux/lib:$(dirname "$(dirname "$(command -v mpirun)")")/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# layouts: ranks per node, cores, ports, GPUs (local rank i uses entry i)
LAYOUTS=(L8 L4x L4s L1)
declare -A LN LC LP LG LT
LN=([L8]=8 [L4x]=4 [L4s]=4 [L1]=1)
LC=([L8]=0,1,2,3,40,41,42,43 [L4x]=0,2,40,42 [L4s]=0,1,2,3 [L1]=0)
LP=([L8]=mlx5_0:1,mlx5_2:1,mlx5_4:1,mlx5_8:1,mlx5_10:1,mlx5_12:1,mlx5_14:1,mlx5_16:1
    [L4x]=mlx5_0:1,mlx5_4:1,mlx5_10:1,mlx5_14:1 [L4s]=mlx5_0:1,mlx5_2:1,mlx5_4:1,mlx5_8:1 [L1]=mlx5_0:1)
LG=([L8]=0,1,2,3,4,5,6,7 [L4x]=0,2,4,6 [L4s]=0,1,2,3 [L1]=0)
LT=([L8]="8/node" [L4x]="4/node 2+2" [L4s]="4/node 1 sock" [L1]="1/node")

gib() { awk -v s="$1" -v t="$2" 'BEGIN { if (t + 0 <= 0) print "-"; else printf "%.0f", s * 8 / 2^30 / t }'; }
# decimal Gb/s (what ib_write_bw and the 400 Gb/s port speed use) of $1 bytes in $2 seconds
gb() { awk -v s="$1" -v t="$2" 'BEGIN { if (t + 0 <= 0) print "-"; else printf "%.0f", s * 8 / 1e9 / t }'; }
human() { awk -v s="$1" 'BEGIN { split("B K M G", u); i = 1
    while (s >= 1024 && i < 4) { s /= 1024; i++ }; printf "%d%s", s, u[i] }'; }

# run_blink LAYOUT PROGRAM MEM(host|gpu) VARIANT(base|zc|nogdr) BLINK-ARGS... (-gpu is added for MEM=gpu)
run_blink() {
    local lay=$1 prog=$2 mem=$3 var=$4; shift 4
    local n=${LN[$lay]} env=(-x UCX_MAX_RNDV_RAILS=1 -x UCX_IB_ROCE_REACHABILITY_MODE=all)
    [[ $var == zc ]] && env+=(-x UCX_RNDV_SCHEME=put_zcopy)
    [[ $var == nogdr ]] && env+=(-x UCX_IB_GPU_DIRECT_RDMA=no)
    [[ $var == ro ]] && env+=(-x UCX_IB_PCI_RELAXED_ORDERING=y)
    [[ $mem == gpu ]] && set -- "$@" -gpu
    run_cmd mpirun -n $((2 * n)) -H "$(hosts "$n")" --cpu-list "${LC[$lay]}" --bind-to cpu-list:ordered \
        "${UCX_FLAGS[@]}" "${env[@]}" ${EXTRA_X[@]+"${EXTRA_X[@]}"} -x LAB_NICS="${LP[$lay]}" -x LAB_GPUS="${LG[$lay]}" \
        -x LD_LIBRARY_PATH "$HERE/nic.sh" "$BLINK/$prog" "$@"
}
EXTRA_X=()

# one measured cell: sets CELL ("slowest/fastest" Gibit/s or the verdict) and appends the CSV line
cell() {
    local lay=$1 prog=$2 mem=$3 var=$4 size=$5 iter=$6 warm=$7
    local args=(-msgsize "$size" -iter "$iter" -warmup "$warm")
    [[ $prog == pairwise* ]] && args=(-offset "${LN[$lay]}" "${args[@]}")
    run_blink "$lay" "$prog" "$mem" "$var" "${args[@]}"
    [[ $VERDICT == OK ]] && { timing "$size"; [[ $T_MAX == - ]] && VERDICT=nodata; }
    if [[ $VERDICT == OK ]]; then
        local gs gf; gs=$(gib "$size" "$T_MAX"); gf=$(gib "$size" "$T_MIN")
        CELL="$gs/$gf"; CELL_GB="$(gb "$size" "$T_MAX")/$(gb "$size" "$T_MIN")"
        echo "$lay,$prog,$mem,$var,$size,OK,$T_MAX,$T_MIN,$gs,$gf,${CELL_GB%/*},${CELL_GB#*/}" >>"$CSV"
    else
        CELL=$VERDICT CELL_GB=$VERDICT
        echo "$lay,$prog,$mem,$var,$size,$VERDICT,,,,,," >>"$CSV"
    fi
}
iters() { if (($1 >= 33554432)); then echo "100 50"; else echo "1000 100"; fi; }

# --------------------------------------------------------------------------------------------------------
smoke() {
    say "gpu_pw smoke A=$A B=$B | log: $LOG"
    local lay out ok total devs gpuline
    for lay in L1 L4x; do
        run_blink "$lay" pairwise_b gpu base -offset "${LN[$lay]}" -msgsize 1048576 -iter 3 -warmup 3 -debug
        ok=$(grep -c 'check=OK' "$TMP/run"); total=$(grep -c 'DEBUG rank=' "$TMP/run")
        devs=$(grep -o 'device=[0-9]*' "$TMP/run" | cut -d= -f2 | sort | uniq -c | awk '{printf "%s x%s ", $2, $1}')
        gpuline=$(grep -m1 -i -E 'gpu.aware|gpu support|not gpu' "$TMP/run" | cut -c1-70)
        say "S ${LT[$lay]} pairwise_b -gpu -debug: $VERDICT | check=OK $ok/$total | device $devs${ERRLINE:+| $(echo "$ERRLINE" | cut -c1-50)}${gpuline:+| ${gpuline:0:45}}"
    done
    # how UCX moves device memory at 128 MiB, 1 rank per node
    EXTRA_X=(-x UCX_LOG_LEVEL=info -x UCX_PROTO_INFO=y)
    run_blink L1 pairwise_b gpu base -offset 1 -msgsize 134217728 -iter 3 -warmup 3
    EXTRA_X=()
    local tl
    tl=$(grep -o -E 'rc_mlx5|rc_verbs|cuda_copy|gdr_copy|cuda_ipc|sysv|posix|cma' "$TMP/run" | sort | uniq -c | awk '{printf "%s x%s ", $2, $1}')
    say "S UCX 128 MiB 1/node, transports named in its log: ${tl:-none} ($VERDICT)"
    grep -i -E 'rndv|rendezvous|zero-copy|gdr|zcopy' "$TMP/run" | sort -u | head -4 | cut -c1-140 | while IFS= read -r l; do say "    $l"; done
    # default offset (w_size/2) against the explicit one, 8 per node, 128 MiB, host memory
    for prog in pairwise_b; do
        run_blink L8 "$prog" host base -msgsize 134217728 -iter 100 -warmup 50
        timing 134217728; local d="$(gib 134217728 "$T_MAX")/$(gib 134217728 "$T_MIN")"
        run_blink L8 "$prog" host base -offset 8 -msgsize 134217728 -iter 100 -warmup 50
        timing 134217728
        say "S 8/node $prog host, default offset $d vs -offset 8 $(gib 134217728 "$T_MAX")/$(gib 134217728 "$T_MIN") Gibit/s"
    done
    say "done: $LOG"
}

# --------------------------------------------------------------------------------------------------------
main() {
    say "gpu_pw main A=$A B=$B | Gibit/s per rank slowest/fastest | log: $LOG"
    local it warm
    cell L8 pairwise_b host base 134217728 100 50; local h=$CELL
    cell L8 pairwise_b gpu base 134217728 100 50
    say "D 8/node pairwise_b 128M: host $h | gpu $CELL"
    if [[ $CELL != */* ]]; then
        say "the 8/node -gpu cell failed ($CELL): every GPU row would fail the same way, stopping"
        grep -m2 -i -E "error|abort|not gpu|no cuda|unreach" "$TMP/run" | cut -c1-140 | while IFS= read -r l; do say "  $l"; done
        exit 1
    fi
    local size prog row spec mem var head lay
    for size in $SIZES; do
        read -r it warm <<<"$(iters "$size")"
        head=$(printf '%-24s' "$(human "$size")")
        for lay in "${LAYOUTS[@]}"; do head+=$(printf ' %13s' "${LT[$lay]}"); done
        say "$head"
        for prog in $PROGS; do
            for spec in host:base gpu:base gpu:zc gpu:nogdr; do
                mem=${spec%%:*} var=${spec##*:}
                row=$(printf '%-24s' "${prog/pingpong_pairwise_b/pingpong} ${mem}$([[ $var == zc ]] && echo ' +zc')$([[ $var == nogdr ]] && echo ' noGDR')")
                for lay in "${LAYOUTS[@]}"; do
                    cell "$lay" "$prog" "$mem" "$var" "$size" "$it" "$warm"
                    row+=$(printf ' %13s' "$CELL")
                done
                say "$row"
            done
        done
    done
    say "done: $LOG | $CSV"
}

# --------------------------------------------------------------------------------------------------------
# best: short run at the best setting (8/node, GPU memory, one port + one GPU per rank), one run per cell, every
# cell printed as soon as it finishes: Gibit/s slowest/fastest rank (2^30, as the other tables) | Gb/s slowest
# (decimal, comparable with ib_write_bw and the 400 Gb/s port). 5000 iterations / 500 warmup, 512M: 1000 / 200.
best() {
    STEP_TIMEOUT=${BEST_TIMEOUT:-200}
    local prog size mem it warm
    say "gpu_pw best A=$A B=$B | 8/node, GPU memory | Gibit/s slowest/fastest | Gb/s slowest | log: $LOG | $CSV"
    for prog in pingpong_pairwise_b pairwise_b; do
        for size in 2097152 33554432 134217728 536870912; do
            if ((size >= 536870912)); then it=1000 warm=200; else it=5000 warm=500; fi
            cell L8 "$prog" gpu base "$size" "$it" "$warm"
            say "B1 $(printf '%-9s' "${prog/pingpong_pairwise_b/pingpong}") $(printf '%-5s' "$(human "$size")") ${it}it: $CELL | ${CELL_GB%/*} Gb/s"
        done
    done
    for mem in gpu host; do
        cell L8 pairwise_nb "$mem" base 134217728 5000 500
        say "B2 pairwise_nb $mem 128M 5000it: $CELL | ${CELL_GB%/*} Gb/s"
    done
    cell L8 pairwise_b gpu ro 134217728 5000 500
    say "B3 pairwise_b gpu 128M 5000it, UCX_IB_PCI_RELAXED_ORDERING=y: $CELL | ${CELL_GB%/*} Gb/s"
    say "done: $LOG | $CSV"
}

case $MODE in
smoke) smoke ;;
main) main ;;
best) best ;;
*) die "usage: gpu_pw.sh smoke|main|best" ;;
esac
