#!/usr/bin/env bash
# Build a CUDA-aware Open MPI (with the system UCX) into ~/crab/lab/ompi-cuda, because the installed
# /usr/mpi/gcc/openmpi-* are built without CUDA and Blink -gpu stops with "this MPI is not GPU-aware".
# Run it on one node (it builds there; ~/crab is shared, so both nodes see the result):
#   bash ~/crab/lab/ompi_cuda_build.sh
# The tarball (4.1.6, sha256 checked against open-mpi.org) is in this branch, dist/: no download needed here. Takes about
# 10-15 minutes. Nothing outside ~/crab/lab is touched.
# Afterwards gpu_pw.sh and blink_gpu_build.sh put ompi-cuda/bin first in PATH by themselves; rebuild Blink once:
#   bash ~/crab/lab/blink_gpu_build.sh
# Env: OMPI_VER (4.1.6), CUDA_ROOT (/usr/local/cuda-12.3), UCX_ROOT (found from ucx_info), JOBS (16), OUT.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OUT=${OUT:-$HERE/out}
OMPI_VER=${OMPI_VER:-4.1.6}
CUDA_ROOT=${CUDA_ROOT:-/usr/local/cuda-12.3}
JOBS=${JOBS:-16}
PREFIX=$HERE/ompi-cuda
TARBALL=$HERE/dist/openmpi-$OMPI_VER.tar.bz2
[[ -f $TARBALL ]] || TARBALL=$HERE/openmpi-$OMPI_VER.tar.bz2
SRC=$HERE/openmpi-$OMPI_VER
mkdir -p "$OUT" || { echo "cannot create $OUT" >&2; exit 2; }
LOG=$OUT/ompi_cuda_build_$(date +%Y%m%d_%H%M%S).log
say() { echo "$*" | tee -a "$LOG"; }
fail() { say "$1"; grep -i -E 'error|cannot|not found|fatal' "$LOG" | tail -6 | cut -c1-150 | while IFS= read -r l; do say "  $l"; done; exit 1; }

UCX_BIN=$(command -v ucx_info)
UCX_ROOT=${UCX_ROOT:-$(dirname "$(dirname "${UCX_BIN:-/usr/bin/x}")")}
[[ -f $UCX_ROOT/include/ucp/api/ucp.h ]] || fail "no UCX headers in $UCX_ROOT/include (set UCX_ROOT to a UCX with include/ucp)"
[[ -f $CUDA_ROOT/include/cuda.h ]] || fail "no $CUDA_ROOT/include/cuda.h (set CUDA_ROOT)"
STUBS=$CUDA_ROOT/targets/x86_64-linux/lib/stubs
say "ompi_cuda_build $OMPI_VER | UCX_ROOT=$UCX_ROOT ($(ucx_info -v | awk '/Library version/ {print $NF}')) CUDA_ROOT=$CUDA_ROOT | log: $LOG"

if [[ ! -f $TARBALL ]]; then
    url=https://download.open-mpi.org/release/open-mpi/v${OMPI_VER%.*}/openmpi-$OMPI_VER.tar.bz2
    say "download $url"
    curl -fsSL -o "$TARBALL" "$url" >>"$LOG" 2>&1 || { rm -f "$TARBALL"; fail "download failed (proxy set? else copy the tarball to $TARBALL)"; }
fi
[[ -d $SRC ]] || tar -xjf "$TARBALL" -C "$HERE" >>"$LOG" 2>&1 || fail "unpack failed"

cd "$SRC" || exit 2
say "configure (a few minutes)"
./configure --prefix="$PREFIX" --with-cuda="$CUDA_ROOT" ${STUBS:+--with-cuda-libdir="$STUBS"} \
    --with-ucx="$UCX_ROOT" --without-verbs --enable-mpirun-prefix-by-default --disable-man-pages \
    >>"$LOG" 2>&1 || fail "configure FAILED:"
say "make -j$JOBS (about 10 minutes)"
make -j"$JOBS" >>"$LOG" 2>&1 || fail "make FAILED:"
make install >>"$LOG" 2>&1 || fail "make install FAILED:"

cuda=$("$PREFIX/bin/ompi_info" --parsable --all 2>/dev/null | awk -F: '/mpi_built_with_cuda_support:value/ {print $NF; exit}')
say "ompi-cuda: $("$PREFIX/bin/ompi_info" --version | awk '/Open MPI:/ {print $3; exit}') | built with CUDA: ${cuda:-unknown}"
node=$(hostname)
for n in $(scontrol show hostnames "${SLURM_JOB_NODELIST:-}" 2>/dev/null); do
    [[ $n == "$node" ]] && continue
    say "$n: $(ssh "$n" "$PREFIX/bin/ompi_info --parsable --all | awk -F: '/mpi_built_with_cuda_support:value/ {print \$NF; exit}'" 2>&1 | head -1) (built with CUDA, seen from there)"
done
[[ $cuda == true ]] && say "OK: next 'bash $HERE/blink_gpu_build.sh', then gpu_pw.sh" || say "WARNING: not CUDA-aware, see $LOG"
say "done: $LOG"
