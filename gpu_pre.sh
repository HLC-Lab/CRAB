#!/usr/bin/env bash
# Read-only preflight for GPU-memory tests (Blink -gpu, ib_write_bw --use_cuda). Run it on node A,
# inside the allocation (or give the two node names):  bash ~/crab/lab/gpu_pre.sh [<a> <b>]
# Is this Open MPI built with CUDA? Does UCX have the CUDA / GPUDirect transports? Are cmake, the CUDA
# runtime library, nvidia_peermem and a GitHub connection there? Is ib_write_bw built with --use_cuda?
# Summary fits one screen, everything in $OUT/gpu_pre_<time>.log. Env: OUT, SSH.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OUT=${OUT:-$HERE/out}
SSH=${SSH:-ssh}
die() { echo "gpu_pre: $*" >&2; exit 2; }
if (($# == 2)); then
    A=$1 B=$2
else
    [[ -n ${SLURM_JOB_NODELIST:-} ]] || die "run it inside an allocation of two nodes, or: gpu_pre.sh <a> <b>"
    mapfile -t NODES < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
    ((${#NODES[@]} == 2)) || die "needs exactly 2 nodes, the allocation has ${#NODES[@]}"
    A=${NODES[0]} B=${NODES[1]}
fi
mkdir -p "$OUT" || die "cannot create $OUT"
LOG=$OUT/gpu_pre_$(date +%Y%m%d_%H%M%S).log
say() { echo "$*" | tee -a "$LOG"; }
log() { cat >>"$LOG"; }
say "gpu_pre A=$A B=$B | log: $LOG"

# --- MPI and UCX (as seen on A) ---------------------------------------------------------------------
mpirun_bin=$(command -v mpirun)
cuda_flag=$(ompi_info --parsable --all 2>/dev/null | awk -F: '/mpi_built_with_cuda_support:value/ {print $NF; exit}')
ompi_ver=$(ompi_info --version 2>/dev/null | awk '/Open MPI:/ {print $3; exit}')
ucx_ver=$(ucx_info -v 2>/dev/null | awk '/^# Library version/ {print $NF; exit}')
ucx_conf=$(ucx_info -v 2>/dev/null | grep -i 'configured with' | grep -o -E -e '--with-(cuda|gdrcopy)[^ ]*' | tr '\n' ' ')
ucx_tl=$(ucx_info -d 2>/dev/null | grep -o -E 'cuda_copy|cuda_ipc|gdr_copy|cuda_gdr' | sort -u | tr '\n' ' ')
ucp_lib=$(ldconfig -p 2>/dev/null | awk '/libucp.so.0 / {print $NF; exit}')
ucx_mods=$(ls "$(dirname "${ucp_lib:-/none}")/ucx" 2>/dev/null | grep -o -E 'libuct_(cuda|gdr)[a-z_]*' | sort -u | tr '\n' ' ')
{ ompi_info --parsable 2>&1 | grep -i -E 'cuda|ucx'; ucx_info -v 2>&1; ucx_info -d 2>&1 | head -80; } | log
say "mpirun $mpirun_bin (Open MPI ${ompi_ver:-?}) | built with CUDA: ${cuda_flag:-unknown}"
say "UCX ${ucx_ver:-?} | ${ucx_conf:-no --with-cuda/gdrcopy shown}| transports: ${ucx_tl:-none}| modules: ${ucx_mods:-none}"
say "other MPIs: $(find /opt /usr/mpi -maxdepth 3 \( -iname '*hpcx*' -o -iname 'openmpi*' \) -type d 2>/dev/null |
    head -6 | tr '\n' ' ')"

# --- per node ---------------------------------------------------------------------------------------
cat >"$OUT/.pre_node.sh" <<'EOF'
cm=$(cmake --version 2>/dev/null | awk 'NR == 1 {print $3}')
rt=$(ls /usr/local/cuda-12.3/targets/x86_64-linux/lib/libcudart.so.12* 2>/dev/null | head -1)
peer=$(lsmod 2>/dev/null | awk '/^(nvidia_peermem|nv_peer_mem) / {print $1}' | tr '\n' ' ')
load=$(nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits 2>/dev/null |
    awk -F", " '{u += $1; m += $2} END {if (NR) printf "gpu util avg %.0f%% mem used %.0f GiB", u / NR, m / 1024}')
echo "$(hostname -s): cmake ${cm:-none} | libcudart ${rt:-none} | ${peer:-no peermem} | ${load:-no nvidia-smi}"
EOF
for n in "$A" "$B"; do
    $SSH "$n" "bash '$OUT/.pre_node.sh'" 2>&1 | while IFS= read -r l; do say "$l"; done
done
rm -f "$OUT/.pre_node.sh"

# --- perftest and GitHub ------------------------------------------------------------------------------
wb_cuda=no
ib_write_bw --help 2>&1 | grep -q use_cuda && wb_cuda=yes
say "ib_write_bw $(ib_write_bw --version 2>&1 | head -1) | --use_cuda: $wb_cuda"
gh=$(timeout 25 git ls-remote https://github.com/HLC-Lab/blink HEAD 2>&1 | head -1 | cut -c1-48)
say "github HLC-Lab/blink HEAD: ${gh:-no answer (proxy?)}"

# --- verdict --------------------------------------------------------------------------------------------
if [[ $cuda_flag == true && -n $ucx_tl ]]; then
    say "VERDICT: Open MPI and UCX look CUDA-aware: Blink -gpu should run"
elif [[ $cuda_flag == true ]]; then
    say "VERDICT: Open MPI says CUDA, but UCX shows no cuda transports: -gpu will probably abort"
else
    say "VERDICT: Open MPI NOT built with CUDA (${cuda_flag:-unknown}): Blink -gpu probably cannot run; $wb_cuda ib_write_bw --use_cuda"
fi
say "done: $LOG"
