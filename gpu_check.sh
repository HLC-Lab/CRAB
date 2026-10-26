#!/usr/bin/env bash
# Read-only check before GPU-to-GPU tests with nccl-tests. Run it on node A, inside the allocation
# (or give the two node names):  bash ~/crab/lab/gpu_check.sh [<a> <b>]
# Per node: GPUs, driver/CUDA, nvcc, NCCL (system library or header), GPUDirect RDMA module,
# GPUs already busy. On A: each GPU's NUMA node and the network ports next to it (nvidia-smi topo -m:
# PIX = same PCIe switch, PXB = across PCIe bridges, the paths NCCL prefers).
# Summary fits one screen, everything in $OUT/gpu_check_<time>.log. Env: OUT, SSH.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
OUT=${OUT:-$HERE/out}
SSH=${SSH:-ssh}
die() { echo "gpu_check: $*" >&2; exit 2; }
if (($# == 2)); then
    A=$1 B=$2
else
    [[ -n ${SLURM_JOB_NODELIST:-} ]] || die "run it inside an allocation of two nodes, or: gpu_check.sh <a> <b>"
    mapfile -t NODES < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
    ((${#NODES[@]} == 2)) || die "needs exactly 2 nodes, the allocation has ${#NODES[@]}"
    A=${NODES[0]} B=${NODES[1]}
fi
mkdir -p "$OUT" || die "cannot create $OUT"
LOG=$OUT/gpu_check_$(date +%Y%m%d_%H%M%S).log
say() { echo "$*" | tee -a "$LOG"; }

# one line of facts per node
facts='
gpus=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | sort | uniq -c | sed "s/^ *//" | tr "\n" " ")
drv=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)
cuda=$(nvidia-smi 2>/dev/null | grep -o "CUDA Version: [0-9.]*" | cut -d" " -f3)
nvcc=$(command -v nvcc || ls /usr/local/cuda/bin/nvcc 2>/dev/null || echo none)
lib=$(ldconfig -p 2>/dev/null | awk "/libnccl.so/ {print \$NF; exit}")
hdr=$(ls /usr/include/nccl.h /usr/local/cuda/include/nccl.h 2>/dev/null | head -1)
ver=""; [[ -n $hdr ]] && ver=$(awk "/define NCCL_(MAJOR|MINOR|PATCH) / {printf \"%s.\", \$3}" "$hdr" | sed "s/\.$//")
peer=$(lsmod 2>/dev/null | awk "/^(nvidia_peermem|nv_peer_mem) / {print \$1}" | tr "\n" " ")
busy=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | wc -l)
echo "gpus: ${gpus:-none}| driver ${drv:-?} cuda ${cuda:-?}"
echo "nvcc: $nvcc | nccl lib: ${lib:-none} header: ${hdr:-none} ${ver} | gdr module: ${peer:-none} | busy gpu procs: $busy"
'
for n in "$A" "$B"; do
    say "== $n"
    $SSH "$n" "$facts" 2>&1 | while IFS= read -r l; do say "  $l"; done
done

# GPU -> NUMA node and nearby ports, on A
$SSH "$A" 'nvidia-smi topo -m 2>&1' >"$OUT/.topo" 2>&1
{ echo "== nvidia-smi topo -m on $A"; cat "$OUT/.topo"; } >>"$LOG"
$SSH "$A" 'nvidia-smi --query-gpu=index,pci.bus_id --format=csv,noheader 2>/dev/null | while IFS=", " read -r i bus; do
    b=$(echo "$bus" | tr "A-F" "a-f" | sed "s/^0000//"); echo "$i $(cat /sys/bus/pci/devices/0000${b#0000}/numa_node 2>/dev/null)"; done' \
    >"$OUT/.numa" 2>&1
say "GPUs on $A: index numa | ports at PIX/PXB distance (nvidia-smi topo -m)"
sed 's/\x1b\[[0-9;]*m//g' "$OUT/.topo" | awk -v numafile="$OUT/.numa" '
    BEGIN { while ((getline l < numafile) > 0) { split(l, f, " "); numa[f[1]] = f[2] } }
    /^[[:space:]]*NIC[0-9]+:/ { gsub(/[[:space:]:]/, " "); split($0, f, " "); name[f[1]] = f[2]; next }
    !hdr && $1 ~ /^GPU0$/ { hdr = 1; for (i = 1; i <= NF; i++) col[i] = $i; next }
    hdr && $1 ~ /^GPU[0-9]+$/ { rows[++n] = $0 }
    END {
        for (r = 1; r <= n; r++) {
            split(rows[r], f, " "); g = f[1]; idx = substr(g, 4); out = ""
            for (i = 2; i <= length(col) + 1; i++) {
                c = col[i - 1]
                if (c ~ /^NIC[0-9]+$/ && (f[i] == "PIX" || f[i] == "PXB")) out = out " " (c in name ? name[c] : c) "(" f[i] ")"
            }
            printf "  %-5s numa%-2s%s\n", g, numa[idx], out
        }
    }' | while IFS= read -r l; do say "$l"; done
rm -f "$OUT/.topo" "$OUT/.numa"
say "nccl-tests built here: $(ls "$HERE/nccl-tests/build/sendrecv_perf" 2>/dev/null || echo no)"
say "done: $LOG"
