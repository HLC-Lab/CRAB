#!/usr/bin/env bash
# Rank wrapper for mpirun: picks this rank's RDMA port, says where the rank runs, runs the program.
#   LAB_NIC_BY_RANK=a,b,...  one port per world rank (e.g. mlx5_0:1,mlx5_2:1)
#   LAB_NICS=a,b,...         one port per node-local rank, cycled
#   neither set              UCX_NET_DEVICES is left as it is (UCX picks)
#   LAB_GPUS=g,h,...         one GPU per node-local rank, cycled: sets CUDA_VISIBLE_DEVICES, so the rank sees
#                            exactly that GPU (Blink -gpu then always uses device 0); unset: left as it is
# Pass them with mpirun -x NAME=value. The placement line goes to stderr.
rank=${OMPI_COMM_WORLD_RANK:-${SLURM_PROCID:-0}}
lrank=${OMPI_COMM_WORLD_LOCAL_RANK:-${SLURM_LOCALID:-0}}
if [[ -n ${LAB_NIC_BY_RANK:-} ]]; then
    IFS=, read -ra list <<<"$LAB_NIC_BY_RANK"
    export UCX_NET_DEVICES=${list[rank]}
elif [[ -n ${LAB_NICS:-} ]]; then
    IFS=, read -ra list <<<"$LAB_NICS"
    export UCX_NET_DEVICES=${list[lrank % ${#list[@]}]}
fi
if [[ -n ${LAB_GPUS:-} ]]; then
    IFS=, read -ra glist <<<"$LAB_GPUS"
    export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=${glist[lrank % ${#glist[@]}]}
fi
cpus=$(awk '/^Cpus_allowed_list/ {print $2}' /proc/self/status 2>/dev/null)
echo "lab: rank=$rank host=$(hostname -s) local=$lrank dev=${UCX_NET_DEVICES:--} cpus=${cpus:--} pid=$$ gpu=${CUDA_VISIBLE_DEVICES:--}" >&2
exec "$@"
