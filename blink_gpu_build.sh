#!/usr/bin/env bash
# Build the GPU-capable Blink (HLC-Lab/blink, -gpu mode) into its own folder, ~/crab/lab/blink-gpu, so the
# CRAB-managed Blink (~/crab/CRAB/benchmarks/blink) is not touched. Run it on node A inside the allocation
# (or give the two node names):  bash ~/crab/lab/blink_gpu_build.sh [<a> <b>]
# Clones (or fast-forwards) the repo through the proxy of this shell, finds a cmake >= 3.17 (a node's, else a
# venv with `pip install cmake` in ~/crab/lab/cmake-venv), configures with BLINK_GPU=CUDA and
# BLINK_MPI_WRAPPER=ON, builds, then checks with ldd on both nodes that every library is found.
# Env: BLINK_REPO, CUDA_ROOT (/usr/local/cuda-12.3), JOBS (16), MPI_WRAPPER (ON: Open MPI mpicc wrapper; OFF: FindMPI), OUT, SSH.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OUT=${OUT:-$HERE/out}
SSH=${SSH:-ssh}
REPO=${BLINK_REPO:-https://github.com/HLC-Lab/blink}
CUDA_ROOT=${CUDA_ROOT:-/usr/local/cuda-12.3}
JOBS=${JOBS:-16}
MPI_WRAPPER=${MPI_WRAPPER:-ON}
SRC=$HERE/blink-gpu
die() { echo "blink_gpu_build: $*" >&2; exit 2; }
if (($# == 2)); then
    A=$1 B=$2
else
    [[ -n ${SLURM_JOB_NODELIST:-} ]] || die "run it inside an allocation of two nodes, or: blink_gpu_build.sh <a> <b>"
    mapfile -t NODES < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
    ((${#NODES[@]} == 2)) || die "needs exactly 2 nodes, the allocation has ${#NODES[@]}"
    A=${NODES[0]} B=${NODES[1]}
fi
mkdir -p "$OUT" || die "cannot create $OUT"
LOG=$OUT/blink_gpu_build_$(date +%Y%m%d_%H%M%S).log
say() { echo "$*" | tee -a "$LOG"; }
MPI_HOME=$(dirname "$(dirname "$(command -v mpirun)")")
say "blink_gpu_build on $A | MPI_HOME=$MPI_HOME CUDA_ROOT=$CUDA_ROOT | log: $LOG"

# --- source ----------------------------------------------------------------------------------------------
if [[ -d $SRC/.git ]]; then
    git -C "$SRC" pull --ff-only >>"$LOG" 2>&1 || say "git pull failed (see log), building what is there"
else
    git clone --depth 1 "$REPO" "$SRC" >>"$LOG" 2>&1 || { say "git clone failed (proxy? see log)"; exit 1; }
fi
say "source $(git -C "$SRC" log -1 --format='%h %ad %s' --date=short | cut -c1-80)"

# --- cmake >= 3.17 -------------------------------------------------------------------------------------------
cm_ok() { [[ -n $1 ]] && [[ $(printf '%s\n3.17\n' "$("$1" --version 2>/dev/null | awk 'NR == 1 {print $3}')" | sort -V | head -1) == 3.17 ]]; }
CMAKE=$(command -v cmake)
if ! cm_ok "$CMAKE"; then
    say "cmake here: ${CMAKE:-none} ($("${CMAKE:-true}" --version 2>/dev/null | head -1)): too old or missing, using a venv"
    CMAKE=$HERE/cmake-venv/bin/cmake
    if ! cm_ok "$CMAKE"; then
        python3 -m venv "$HERE/cmake-venv" >>"$LOG" 2>&1 &&
            "$HERE/cmake-venv/bin/pip" install cmake >>"$LOG" 2>&1 || { say "pip install cmake failed (proxy? see log)"; exit 1; }
    fi
fi
say "cmake $("$CMAKE" --version | head -1 | awk '{print $3}') at $CMAKE"

# --- configure and build ---------------------------------------------------------------------------------------
export PATH=$MPI_HOME/bin:$PATH
"$CMAKE" -S "$SRC" -B "$SRC/build-cuda" -DBLINK_GPU=CUDA -DBLINK_MPI_WRAPPER=$MPI_WRAPPER -DBLINK_TESTS=OFF \
    -DCUDAToolkit_ROOT="$CUDA_ROOT" -DCMAKE_BUILD_TYPE=Release >>"$LOG" 2>&1 \
    || { say "configure FAILED:"; grep -i -E 'error|not found' "$LOG" | tail -4 | cut -c1-140 | while IFS= read -r l; do say "  $l"; done; exit 1; }
say "$(grep -m1 'BLINK_GPU: requested' "$LOG" | cut -c1-100)"
"$CMAKE" --build "$SRC/build-cuda" -j"$JOBS" >>"$LOG" 2>&1 \
    || { say "build FAILED:"; grep -i -E 'error' "$LOG" | tail -4 | cut -c1-140 | while IFS= read -r l; do say "  $l"; done; exit 1; }
BIN=$SRC/build-cuda/bin
n=$(find "$BIN" -maxdepth 1 -type f -perm -u+x 2>/dev/null | wc -l)
say "build OK: $n programs in $BIN ($(for p in pairwise_b pairwise_nb pingpong_pairwise_b; do [[ -x $BIN/$p ]] && printf '%s ' "$p"; done))"

# --- libraries on both nodes --------------------------------------------------------------------------------------
for node in "$A" "$B"; do
    res=$($SSH "$node" "LD_LIBRARY_PATH='$MPI_HOME/lib' ldd '$BIN/pairwise_b' 2>&1 |
        awk '/not found/ {miss = miss \" \" \$1} /libcudart/ && !/not found/ {rt = \$3}
            END {print \"libcudart \" (rt == \"\" ? \"-\" : rt) \" | missing:\" (miss == \"\" ? \" none\" : miss)}'" 2>&1)
    say "$node: $res"
done
say "done: $LOG"
