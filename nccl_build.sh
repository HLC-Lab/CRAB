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
nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null |
    awk -F", " "{u += \$1; if (\$1 > m) m = \$1; mu += \$2; mt += \$3}
        END {if (NR) printf \"gpu load now: util avg %.0f%% max %d%% | memory used %.0f of %.0f GiB\n\", u / NR, m, mu / 1024, mt / 1024}"
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

# the system has only libnccl.so.2 (no libnccl.so to link with -lnccl), and its header may sit
# apart from it: link against a small NCCL_HOME of symlinks to the installed library and header
NH=$HERE/nccl-home
lib=$(ldconfig -p | awk '/libnccl.so.2 / {print $NF; exit}')
lib=$(readlink -f "$lib")
hdr=$(ls "$(dirname "$(dirname "$lib")")/include/nccl.h" /usr/local/include/nccl.h /usr/include/nccl.h 2>/dev/null | head -1)
[[ -f $lib && -f $hdr ]] || { say "NCCL library ($lib) or header ($hdr) not found"; exit 1; }
mkdir -p "$NH/include" "$NH/lib"
ln -sfn "$hdr" "$NH/include/nccl.h"
ln -sfn "$lib" "$NH/lib/libnccl.so"
ln -sfn "$lib" "$NH/lib/libnccl.so.2"
say "NCCL_HOME=$NH: lib $(basename "$lib"), header $hdr $(awk '/define NCCL_(MAJOR|MINOR|PATCH) / {printf "%s.", $3}' "$hdr" | sed 's/\.$//')"

$SSH "$BUILD" "cd '$HERE/nccl-tests' && make -j$JOBS MPI=1 MPI_HOME='$MPI_HOME' CUDA_HOME=/usr/local/cuda \
    NCCL_HOME='$NH' NVCC_GENCODE='-gencode=arch=compute_90,code=sm_90'" >>"$LOG" 2>&1 \
    && say "build OK" || {
        say "build FAILED, first errors:"
        grep -i -E 'cannot find|undefined reference|error' "$LOG" | grep -v collect2 | sort -u | head -4 |
            cut -c1-140 | while IFS= read -r l; do say "  $l"; done
        exit 1
    }
# nccl_run.sh puts $MPI_HOME/lib in LD_LIBRARY_PATH (nvcc links libmpi without an rpath)
for n in "$A" "$B"; do
    libs=$($SSH "$n" "LD_LIBRARY_PATH='$MPI_HOME/lib' ldd '$HERE/nccl-tests/build/sendrecv_perf' 2>&1 |
        awk '/not found/ {miss = miss \" \" \$1} /libnccl|libcudart|libmpi\\./ && !/not found/ {print \$3}
            END {print \"missing:\" (miss == \"\" ? \" none\" : miss)}' | tr '\n' ' '")
    say "$n: $libs"
done
say "done: $LOG"
