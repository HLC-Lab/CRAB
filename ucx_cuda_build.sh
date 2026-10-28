#!/usr/bin/env bash
# Build a UCX with CUDA support into ~/crab/lab/ucx-cuda. The system UCX 1.20.0 (/usr) has no libuct_cuda module,
# so Blink -gpu crashes moving device memory even with a CUDA-aware Open MPI. Same version as the system UCX, so
# the Open MPI built by ompi_cuda_build.sh keeps working: gpu_pw.sh just puts ucx-cuda/lib first in LD_LIBRARY_PATH.
#   bash ~/crab/lab/ucx_cuda_build.sh
# The tarball is in this branch (dist/), nothing is downloaded. Takes a few minutes. Nothing outside ~/crab/lab is touched.
# Env: UCX_VER (1.20.0), CUDA_ROOT (/usr/local/cuda-12.3), JOBS (32), OUT.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OUT=${OUT:-$HERE/out}
UCX_VER=${UCX_VER:-1.20.0}
CUDA_ROOT=${CUDA_ROOT:-/usr/local/cuda-12.3}
JOBS=${JOBS:-32}
PREFIX=$HERE/ucx-cuda
TARBALL=$HERE/dist/ucx-$UCX_VER.tar.gz
SRC=$HERE/ucx-$UCX_VER
mkdir -p "$OUT" || { echo "cannot create $OUT" >&2; exit 2; }
LOG=$OUT/ucx_cuda_build_$(date +%Y%m%d_%H%M%S).log
say() { echo "$*" | tee -a "$LOG"; }
fail() { say "$1"; grep -i -E 'error|cannot|not found|fatal' "$LOG" | tail -6 | cut -c1-150 | while IFS= read -r l; do say "  $l"; done; exit 1; }
[[ -f $TARBALL ]] || fail "no $TARBALL (git pull the nplb-lab branch)"
[[ -f $CUDA_ROOT/include/cuda.h ]] || fail "no $CUDA_ROOT/include/cuda.h (set CUDA_ROOT)"
say "ucx_cuda_build $UCX_VER | CUDA_ROOT=$CUDA_ROOT | system UCX: $(ucx_info -v | awk '/Library version/ {print $NF}') | log: $LOG"
[[ -d $SRC ]] || tar -xzf "$TARBALL" -C "$HERE" >>"$LOG" 2>&1 || fail "unpack failed"
cd "$SRC" || exit 2
say "configure"
./configure --prefix="$PREFIX" --with-cuda="$CUDA_ROOT" --with-verbs --with-rdmacm --with-mlx5-dv \
    --disable-doxygen-doc >>"$LOG" 2>&1 || fail "configure FAILED:"
say "make -j$JOBS"
make -j"$JOBS" >>"$LOG" 2>&1 || fail "make FAILED:"
make install >>"$LOG" 2>&1 || fail "make install FAILED:"
export LD_LIBRARY_PATH=$PREFIX/lib:$CUDA_ROOT/targets/x86_64-linux/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
tl=$("$PREFIX/bin/ucx_info" -d 2>&1 | grep -o -E 'Transport: *(cuda_copy|cuda_ipc|gdr_copy|rc_mlx5|dc_mlx5)' | sort -u | tr -s ' ' | tr '\n' ';')
say "ucx-cuda: $("$PREFIX/bin/ucx_info" -v | awk '/Library version/ {print $NF}') | $tl"
case $tl in *cuda_copy*rc_mlx5* | *rc_mlx5*cuda_copy*) say "OK: gpu_pw.sh uses it by itself; next: bash $HERE/gpu_pw.sh smoke" ;;
    *) say "WARNING: cuda_copy and rc_mlx5 must both be listed, see $LOG" ;; esac
say "done: $LOG"
