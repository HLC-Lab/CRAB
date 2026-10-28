#!/usr/bin/env bash
# Read-only: can this UCX move GPU memory? Looks for the UCX CUDA modules (in /usr, /opt, the UCX prefix only),
# checks their libraries with ldd, and lists the CUDA transports ucx_info sees with and without the CUDA runtime
# on the library path. Run on node A, no allocation needed:  bash ~/crab/lab/ucx_check.sh
# Summary on screen, everything in $OUT/ucx_check_<time>.log.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OUT=${OUT:-$HERE/out}
CUDA_LIB=${CUDA_LIB:-/usr/local/cuda-12.3/targets/x86_64-linux/lib}
mkdir -p "$OUT"
LOG=$OUT/ucx_check_$(date +%Y%m%d_%H%M%S).log
say() { echo "$*" | tee -a "$LOG"; }
UCX_BIN=$(command -v ucx_info)
UCX_ROOT=$(dirname "$(dirname "${UCX_BIN:-/usr/bin/x}")")
say "ucx_check | ucx_info $UCX_BIN ($(ucx_info -v | awk '/Library version/ {print $NF}')) | log: $LOG"
say "configured: $(ucx_info -v | grep -i 'configured with' | grep -o -E -e '--with-(cuda|gdrcopy|verbs|rdmacm)[^ ]*' | tr '\n' ' ')"
mods=$(find /usr /opt "$UCX_ROOT" -xdev \( -name 'libuct_cuda*' -o -name 'libucx_perftest_cuda*' -o -name 'libuct_gdrcopy*' \) 2>/dev/null | sort -u)
say "CUDA modules found: $(echo "$mods" | grep -c .)"
echo "$mods" | head -12 | while IFS= read -r f; do [[ -n $f ]] && say "  $f"; done
for f in $(echo "$mods" | grep -E 'libuct_cuda\.so[.0-9]*$|libuct_cuda_ipc' | head -3); do
    say "ldd $f: $(ldd "$f" 2>&1 | grep -i -E 'not found' | tr '\n' ' ' | tr -s ' ')$(ldd "$f" 2>&1 | grep -q 'not found' || echo 'all libraries found')"
    say "  with CUDA lib path: $(LD_LIBRARY_PATH=$CUDA_LIB ldd "$f" 2>&1 | grep -i -E 'not found' | tr '\n' ' ' | tr -s ' ')$(LD_LIBRARY_PATH=$CUDA_LIB ldd "$f" 2>&1 | grep -q 'not found' || echo 'all libraries found')"
done
pat='Transport: *(cuda|gdr)|Component: *(cuda|gdr)|cuda_copy|cuda_ipc|gdr_copy'
say "ucx_info CUDA transports, plain shell:      $(ucx_info -d 2>&1 | grep -i -E "$pat" | sort -u | tr -s ' ' | tr '\n' ';')"
say "ucx_info CUDA transports, CUDA on lib path: $(LD_LIBRARY_PATH=$CUDA_LIB ucx_info -d 2>&1 | grep -i -E "$pat" | sort -u | tr -s ' ' | tr '\n' ';')"
{ ucx_info -v; echo ---; ucx_info -d; echo ---; LD_LIBRARY_PATH=$CUDA_LIB UCX_LOG_LEVEL=debug ucx_info -d 2>&1 | grep -i -E 'cuda|dlopen|module' | head -40; } >>"$LOG" 2>&1
say "libcuda: $(ldconfig -p | awk '/libcuda.so.1 / {print $NF; exit}') | libcudart: $(ls $CUDA_LIB/libcudart.so.12* 2>/dev/null | head -1)"
say "done: $LOG"
