# shellcheck shell=bash
# Sourced, never run: nplb_cuda_root prints the CUDA folder the lab scripts use when CUDA_ROOT (or CUDA_LIB)
# is not set. The lab stack (ucx-cuda, ompi-cuda, Blink) was built with CUDA 12.3, but a node can lose that
# folder to a driver or toolkit update (node110 on 2026-11-12 had cuda, cuda-12.8 and cuda-13.3 only).
# The soname stays libcudart.so.12 inside the 12.x family, so the newest 12.x is the fallback; 13.x is never
# picked on its own because it would not match the libraries the stack was built against.
# NPLB_CUDA_PARENT (default /usr/local) is the folder that holds the cuda-* folders; tests set it.

# nplb_cuda_root: prints /usr/local/cuda-12.3 when it exists, else the newest cuda-12.* folder, else
# /usr/local/cuda-12.3 again (so the check that follows says what is missing).
nplb_cuda_root() {
    local parent=${NPLB_CUDA_PARENT:-/usr/local} dir newest=""
    if [[ -d $parent/cuda-12.3 ]]; then
        echo "$parent/cuda-12.3"
        return
    fi
    while read -r dir; do
        [[ -d $dir ]] && newest=$dir
    done < <(printf '%s\n' "$parent"/cuda-12.* | sort -V)
    echo "${newest:-$parent/cuda-12.3}"
}
