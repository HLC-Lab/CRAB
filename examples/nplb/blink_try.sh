#!/usr/bin/env bash
# One real Blink run by hand, to see the message a failed smoke app hides.
#   nplb build try [HOST HOST]
# Runs alltoall_comm_only through the lab's CUDA-aware mpirun on two nodes (the first two of $PARTITION
# when none are given), twice: first without -gpu (does MPI over UCX work at all?), then with -gpu (does
# the GPU path?). The library path and the UCX variables are those of the nplb_h100_gpu preset. Each
# run has a time limit (TRY_TIMEOUT seconds, default 90); its exit code and the first and last lines
# of its output go into the report. Stdout is the report, paged and saved as build-try; the first
# content line is "VERDICT: OK|FAIL ...". Exit 0 when both runs end with exit 0, 1 otherwise, 2 for bad usage.
# Env: NPLB_LAB (default $HOME/crab/lab), CUDA_ROOT (as blink_build.sh), PARTITION (h100_4nodes),
#      TRY_TIMEOUT, TRY_MSG (message bytes, default 65536), NPLB_REPORT_DIR, TRY_NICS (the NICs of the
#      per-NIC runs, default the eight the smoke test binds: both ranks use that NIC, port 1).
set -u

unset PYTHONPATH
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
LAB=${NPLB_LAB:-${HOME:-}/crab/lab}
# shellcheck source=lib/cuda.sh
. "$HERE/lib/cuda.sh"
# shellcheck source=lib/pages.sh
. "$HERE/lib/pages.sh"
CUDA_ROOT=${CUDA_ROOT:-$(nplb_cuda_root)}
PARTITION=${PARTITION:-h100_4nodes}
TRY_TIMEOUT=${TRY_TIMEOUT:-90}
TRY_MSG=${TRY_MSG:-65536}
TRY_NICS=${TRY_NICS:-mlx5_0 mlx5_2 mlx5_4 mlx5_8 mlx5_10 mlx5_12 mlx5_14 mlx5_16}
BIN=$LAB/blink-gpu/build-cuda/bin
MPIRUN=$LAB/ompi-cuda/bin/mpirun
LD=$LAB/ucx-cuda/lib:$CUDA_ROOT/targets/x86_64-linux/lib:$LAB/ompi-cuda/lib

note() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

if [[ $# -ne 0 && $# -ne 2 ]]; then
    echo "usage: nplb build try [HOST HOST]" >&2
    exit 2
fi
if [[ $# -eq 2 ]]; then
    NODE_A=$1
    NODE_B=$2
else
    mapfile -t NODES < <(sinfo -h -p "$PARTITION" -N -o %n 2>/dev/null | sort -u | head -n 2)
    NODE_A=${NODES[0]:-}
    NODE_B=${NODES[1]:-}
fi
if [[ -z $NODE_A || -z $NODE_B ]]; then
    echo "blink_try: no two nodes found in partition $PARTITION: give them, nplb build try HOST HOST" >&2
    exit 2
fi
if [[ ! -x $MPIRUN || ! -x $BIN/alltoall_comm_only ]]; then
    echo "blink_try: missing $MPIRUN or $BIN/alltoall_comm_only: run nplb build blink" >&2
    exit 2
fi

REPORT=()
FAILS=()

# try_run NAME DEVICE [EXTRA_BLINK_ARG]: one mpirun on the two nodes, exit code and output lines into
# REPORT. DEVICE ("" for the default) is the UCX NIC both ranks use, as UCX_NET_DEVICES=DEVICE:1; a run
# with a DEVICE shows only its Fatal or error lines, the others the head and tail of the output.
try_run() {
    local name=$1 dev=$2 extra=${3:-} out rc count line ppn=1
    local -a shown=() devarg=() wrap=()
    [[ -n $dev ]] && devarg=(-x "UCX_NET_DEVICES=$dev:1")
    if [[ $dev == bound ]]; then
        # the smoke layout: 8 ranks per node, local rank i on the i-th NIC of TRY_NICS
        ppn=8
        devarg=()
        wrap=(bash -c 'IFS=, read -ra n <<<"$0"; export UCX_NET_DEVICES=${n[$OMPI_COMM_WORLD_LOCAL_RANK]}:1; exec "$@"' "${TRY_NICS// /,}")
    fi
    out=$(mktemp)
    note "run $name on $NODE_A $NODE_B (limit ${TRY_TIMEOUT}s)"
    LD_LIBRARY_PATH=$LD:${LD_LIBRARY_PATH:-} PATH=$LAB/ompi-cuda/bin:$PATH timeout -k 5 "$TRY_TIMEOUT" \
        "$MPIRUN" -n $((2 * ppn)) -H "$NODE_A:$ppn,$NODE_B:$ppn" --allow-run-as-root --mca pml ucx \
        --mca btl '^openib' -x UCX_MAX_RNDV_RAILS=1 -x UCX_IB_ROCE_REACHABILITY_MODE=all \
        -x CUDA_DEVICE_ORDER=PCI_BUS_ID "${devarg[@]}" -x LD_LIBRARY_PATH "${wrap[@]}" \
        "$BIN/alltoall_comm_only" -msgsize "$TRY_MSG" -iter 5 -warmup 1 -grty 1 \
        ${extra:+"$extra"} >"$out" 2>&1 </dev/null
    rc=$?
    if ((rc == 0)); then
        REPORT+=("OK $name: exit 0")
    else
        REPORT+=("FAIL $name: exit $rc")
        FAILS+=("$name")
    fi
    if [[ -n $dev && $dev != bound ]]; then
        ((rc == 0)) || mapfile -t shown < <(grep -i -m 2 'fatal\|error' "$out")
    else
        grep . "$out" >"$out.lines"
        count=$(wc -l <"$out.lines")
        if ((count > 16)); then
            mapfile -t shown < <(head -n 9 "$out.lines"; echo "..."; tail -n 6 "$out.lines")
        else
            mapfile -t shown <"$out.lines"
        fi
    fi
    for line in "${shown[@]}"; do
        REPORT+=("  ${line:0:96}")
    done
    rm -f "$out" "$out.lines"
}

try_run cpu ""
try_run gpu "" -gpu
try_run bound16 bound -gpu
for nic in $TRY_NICS; do
    try_run "nic-$nic" "$nic"
done
{
    if ((${#FAILS[@]} == 0)); then
        echo "VERDICT: OK Blink alltoall_comm_only ran on $NODE_A and $NODE_B: default, -gpu, each NIC"
    else
        echo "VERDICT: FAIL ${FAILS[*]} (alltoall_comm_only on $NODE_A and $NODE_B)"
    fi
    echo "libs   $LD"
    printf '%s\n' "${REPORT[@]}"
} | nplb_paged build-try
((${#FAILS[@]} == 0))
