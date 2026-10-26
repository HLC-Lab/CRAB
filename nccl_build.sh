#!/usr/bin/env bash
# Build nccl-tests (MPI=1) into ~/crab/lab/nccl-tests for the GPU-to-GPU tests, checking both nodes
# first. Run it on node A, inside the allocation (or give the two node names):
#   bash ~/crab/lab/nccl_build.sh [<a> <b>]
# Per node: CUDA toolkit (nvcc) and the CUDA version the driver supports, the NCCL library, and the
# processes on the GPUs. It builds on a node whose toolkit is not newer than what both drivers
# support, so one binary runs on both. The clone runs on A (git proxy settings live there).
# Env: OUT, SSH, REPO (https://github.com/NVIDIA/nccl-tests.git), JOBS (16), MPI_HOME (from mpirun).
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OUT=${OUT:-$HERE/out}
SSH=${SSH:-ssh}
REPO=${REPO:-https://github.com/NVIDIA/nccl-tests.git}
JOBS=${JOBS:-16}
die() { echo "nccl_build: $*" >&2; exit 2; }
if (($# == 2)); then
    A=$1 B=$2
else
    [[ -n ${SLURM_JOB_NODELIST:-} ]] || die "run it inside an allocation of two nodes, or: nccl_build.sh <a> <b>"
    mapfile -t NODES < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
    ((${#NODES[@]} == 2)) || die "needs exactly 2 nodes, the allocation has ${#NODES[@]}"
    A=${NODES[0]} B=${NODES[1]}
fi
MPI_HOME=${MPI_HOME:-$(dirname "$(dirname "$(command -v mpirun)")")}
mkdir -p "$OUT" || die "cannot create $OUT"
LOG=$OUT/nccl_build_$(date +%Y%m%d_%H%M%S).log
say() { echo "$*" | tee -a "$LOG"; }

facts='
nvcc=$(/usr/local/cuda/bin/nvcc --version 2>/dev/null | grep -o "release [0-9.]*" | cut -d" " -f2)
drv=$(nvidia-smi 2>/dev/null | grep -o "CUDA Version: [0-9.]*" | cut -d" " -f3)
lib=$(ldconfig -p 2>/dev/null | awk "/libnccl.so.2 / {print \$NF; exit}")
echo "nvcc=${nvcc:-none} driver_cuda=${drv:-?} cuda_dir=$(readlink -f /usr/local/cuda) nccl=$(readlink -f "${lib:-/none}")"
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null |
    awk -F", " "{n++; name[\$2]++} END {printf \"gpu processes: %d\", n; for (p in name) printf \" | %s x%d\", p, name[p]; print \"\"}"
'
declare -A NVCC DRV
for n in "$A" "$B"; do
    out=$($SSH "$n" "$facts" 2>&1)
    say "== $n"
    echo "$out" | while IFS= read -r l; do say "  $l"; done
    NVCC[$n]=$(echo "$out" | grep -o 'nvcc=[^ ]*' | cut -d= -f2)
    DRV[$n]=$(echo "$out" | grep -o 'driver_cuda=[^ ]*' | cut -d= -f2)
done
low=$(printf '%s\n' "${DRV[$A]}" "${DRV[$B]}" | sort -V | head -1)
BUILD=""
for n in "$B" "$A"; do
    [[ ${NVCC[$n]} == none || -z ${NVCC[$n]} ]] && continue
    [[ $(printf '%s\n' "${NVCC[$n]}" "$low" | sort -V | tail -1) == "$low" ]] && { BUILD=$n; break; }
done
[[ -n $BUILD ]] || { say "no node has a CUDA toolkit <= $low (the older driver): cannot build one binary for both"; exit 1; }
say "building on $BUILD (nvcc ${NVCC[$BUILD]} <= driver CUDA $low), MPI_HOME=$MPI_HOME"

if [[ -d $HERE/nccl-tests/.git ]]; then
    git -C "$HERE/nccl-tests" pull --ff-only >>"$LOG" 2>&1 || say "git pull failed (see log), building what is there"
else
    git clone --depth 1 "$REPO" "$HERE/nccl-tests" >>"$LOG" 2>&1 || { say "git clone failed (see log)"; exit 1; }
fi
say "nccl-tests at $(git -C "$HERE/nccl-tests" log -1 --format='%h %ad' --date=short)"
$SSH "$BUILD" "cd '$HERE/nccl-tests' && make -j$JOBS MPI=1 MPI_HOME='$MPI_HOME' CUDA_HOME=/usr/local/cuda \
    NVCC_GENCODE='-gencode=arch=compute_90,code=sm_90'" >>"$LOG" 2>&1 \
    && say "build OK" || { say "build FAILED: $(grep -m1 -i error "$LOG" | cut -c1-110)"; exit 1; }
for n in "$A" "$B"; do
    miss=$($SSH "$n" "ldd '$HERE/nccl-tests/build/sendrecv_perf' 2>&1 | grep -c 'not found'")
    libs=$($SSH "$n" "ldd '$HERE/nccl-tests/build/sendrecv_perf' 2>&1 | awk '/libnccl|libcudart|libmpi\\./ {print \$3}' | tr '\n' ' '")
    say "$n: missing libraries $miss | $libs"
done
say "done: $LOG"
