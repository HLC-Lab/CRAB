#!/usr/bin/env bash
# Check that each rank sends on its own NIC. Run it inside a 2-node Slurm allocation:
#   examples/nplb/nic_check.sh PROFILE BLINK_BIN_DIR
# PROFILE is the probe's profile.json. Ranks start with srun, or with Open MPI 4 mpirun when the
# cluster's Slurm cannot launch MPI jobs (no pmix); the NIC counters are always read with srun.
# Env: LAUNCHER (srun, the default, or mpirun), SRUN_FLAGS (extra srun flags, e.g. --mpi=pmix),
#      MPIRUN (the mpirun command, default mpirun), MPIRUN_FLAGS (extra mpirun flags, e.g.
#      "--allow-run-as-root --mca pml ucx"), NIC_SYSFS (default /sys/class/infiniband),
#      MSG (bytes, default 2097152), ITER (default 200), PYTHON (default python3).
set -uo pipefail

if [[ $# -ne 2 ]]; then
    sed -n '2,/^[^#]/{/^#/p}' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 2
fi
PROFILE=$1
BIN=$2/pairwise_nb
[[ -r $PROFILE ]] || { echo "nic_check: cannot read profile $PROFILE" >&2; exit 2; }
[[ -x $BIN ]] || { echo "nic_check: no executable pairwise_nb in $2" >&2; exit 2; }

export NIC_SYSFS=${NIC_SYSFS:-/sys/class/infiniband}
MSG=${MSG:-2097152}
ITER=${ITER:-200}
read -ra EXTRA <<<"${SRUN_FLAGS:-}"
read -ra MPIRUN_EXTRA <<<"${MPIRUN_FLAGS:-}"
LAUNCHER=${LAUNCHER:-srun}
MPIRUN=${MPIRUN:-mpirun}
case $LAUNCHER in
    srun | mpirun) ;;
    *) echo "nic_check: LAUNCHER must be srun|mpirun, not $LAUNCHER" >&2; exit 2 ;;
esac

# ppn, cpus and the NIC list, one per line
profile=$(${PYTHON:-python3} -c '
import json, sys
p = json.load(open(sys.argv[1]))
print(p["ppn"]); print(p["cpus"]); print(",".join(p["nics"]))' "$PROFILE") \
    || { echo "nic_check: cannot read ppn, cpus and nics from $PROFILE" >&2; exit 2; }
{ read -r PPN; read -r CPUS; read -r NICS; } <<<"$profile"
IFS=, read -ra NIC_LIST <<<"$NICS"

# mpirun mode: the first two hosts of the allocation (each case places n ranks on each).
HOST_A=""; HOST_B=""
if [[ $LAUNCHER == mpirun ]]; then
    hosts=()
    if [[ -n ${SLURM_JOB_NODELIST:-} ]]; then
        mapfile -t hosts < <(scontrol show hostnames "$SLURM_JOB_NODELIST" 2>/dev/null)
    fi
    if ((${#hosts[@]} < 2)); then
        echo "nic_check: LAUNCHER=mpirun needs a 2-node allocation (SLURM_JOB_NODELIST with two hosts)" >&2
        exit 2
    fi
    HOST_A=${hosts[0]}; HOST_B=${hosts[1]}
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Each rank picks its NIC from $NICS by local rank, like CRAB's nics shim.
WRAP='IFS=, read -ra d <<<"$NICS"; export UCX_NET_DEVICES=${d[SLURM_LOCALID % ${#d[@]}]}; exec "$@"'
WRAP_MPI='IFS=, read -ra d <<<"$NICS"; export UCX_NET_DEVICES=${d[OMPI_COMM_WORLD_LOCAL_RANK % ${#d[@]}]}; exec "$@"'

# "host,host,..." with each of the two hosts repeated n times (Open MPI 4: a repeat adds a slot).
mpirun_hosts() {
    local n=$1 list="" i
    for ((i = 0; i < n; i++)); do list+="$HOST_A,"; done
    for ((i = 0; i < n; i++)); do list+="$HOST_B,"; done
    printf '%s' "${list%,}"
}

# One "host dev:port=counter ..." line per node, sorted by host.
snapshot() {
    srun -N2 --ntasks-per-node=1 ${EXTRA[@]+"${EXTRA[@]}"} bash -c '
        line=$(uname -n)
        for d in "$@"; do
            v=$(cat "$NIC_SYSFS/${d%%:*}/ports/${d##*:}/counters/port_xmit_data" 2>/dev/null)
            line+=" $d=${v:-n/a}"
        done
        echo "$line"' _ "${NIC_LIST[@]}" 2>/dev/null | sort >"$1"
}

# GB sent per NIC and node between two snapshots (the counter is in 4-byte units).
sent_gb() {
    awk 'NR == FNR { for (i = 2; i <= NF; i++) { split($i, p, "="); b[$1, i] = p[2] } next }
         { printf "%s:", $1
           for (i = 2; i <= NF; i++) {
               split($i, p, "="); dev = p[1]; sub(/:.*/, "", dev)
               if (p[2] == "n/a" || b[$1, i] == "" || b[$1, i] == "n/a") printf " %s n/a", dev
               else printf " %s %.2f", dev, (p[2] - b[$1, i]) * 4 / 1e9
           }
           printf " GB\n" }' "$1" "$2"
}

# Gb/s per rank from the median of Blink's Median column.
gbps() {
    awk -F, '/^Average,/ { on = 1; next } /^Ran / { on = 0 } on { print $4 }' "$1" | sort -g |
        awk -v msg="$MSG" '{ v[NR] = $1 }
            END { if (!NR) { print "n/a"; exit }
                  m = (NR % 2) ? v[(NR + 1) / 2] : (v[NR / 2] + v[NR / 2 + 1]) / 2
                  printf "%.1f", msg * 8 / m / 1e9 }'
}

# run_case NAME RANKS_PER_NODE NICS CPU_MAP [PML]  (PML is srun mode only)
run_case() {
    local name=$1 n=$2 nics=$3 cpumap=$4 pml=${5:-} rc unbound
    snapshot "$TMP/before"
    (
        export NICS=$nics UCX_MAX_RNDV_RAILS=1
        if [[ $LAUNCHER == mpirun ]]; then
            exec "$MPIRUN" -n $((2 * n)) -H "$(mpirun_hosts "$n")" --cpu-set "$cpumap" --bind-to core \
                --map-by core --report-bindings -x NICS -x UCX_MAX_RNDV_RAILS ${MPIRUN_EXTRA[@]+"${MPIRUN_EXTRA[@]}"} \
                bash -c "$WRAP_MPI" _ "$BIN" -mode rot -offset "$n" -msgsize "$MSG" -iter "$ITER"
        fi
        [[ -n $pml ]] && export OMPI_MCA_pml=$pml
        exec srun -N2 --ntasks-per-node="$n" ${EXTRA[@]+"${EXTRA[@]}"} --cpu-bind="map_cpu:$cpumap" \
            bash -c "$WRAP" _ "$BIN" -mode rot -offset "$n" -msgsize "$MSG" -iter "$ITER"
    ) >"$TMP/out" 2>"$TMP/err"
    rc=$?
    if ((rc != 0)); then
        printf '%-13s failed (exit %d)\n' "$name" "$rc"
        # mpirun's --report-bindings lines would bury the real error
        { if [[ $LAUNCHER == mpirun ]]; then grep -v 'MCW rank' "$TMP/err"; else cat "$TMP/err"; fi; } |
            awk '!seen[$0]++' | tail -n 3 | sed 's/^/    /'
        return
    fi
    snapshot "$TMP/after"
    mapfile -t sent < <(sent_gb "$TMP/before" "$TMP/after")
    ((${#sent[@]})) || sent=("counters n/a")
    printf '%-13s %6s Gb/s per rank   %s\n' "$name" "$(gbps "$TMP/out")" "${sent[0]}"
    for line in "${sent[@]:1}"; do printf '%*s%s\n' 35 '' "$line"; done
    if [[ $LAUNCHER == mpirun ]]; then
        # --report-bindings (stderr): "MCW rank N is not bound (or bound to all available processors)"
        unbound=$(grep -c 'is not bound' "$TMP/err" || true)
        printf '%*sunbound ranks: %s\n' 35 '' "$unbound"
        if [[ $name == per-rank ]]; then
            # local rank i should sit on the i-th core of the profile's cpus
            printf '%*sbindings:\n' 35 ''
            grep 'MCW rank' "$TMP/err" | awk '!seen[$0]++' |
                awk '{ match($0, /MCW rank [0-9]+/); split(substr($0, RSTART, RLENGTH), a, " "); print a[3] "\t" $0 }' |
                sort -n -k1,1 | cut -f2- | sed 's/^/                                     /'
        fi
    fi
}

echo "pairwise_nb, 2 nodes, $MSG bytes, $ITER iterations, UCX_MAX_RNDV_RAILS=1"
run_case one-rank 1 "${NIC_LIST[0]}" "${CPUS%%,*}"
run_case per-rank "$PPN" "$NICS" "$CPUS"
run_case first-nic "$PPN" "${NIC_LIST[0]}" "$CPUS"
[[ $LAUNCHER == srun ]] && run_case per-rank-ucx "$PPN" "$NICS" "$CPUS" ucx
cat <<'HINT'

per-rank should be close to one-rank and spread its bytes over every NIC.
per-rank close to first-nic (about one-rank / ppn) means the ranks share one card:
stop and find out why before running the campaign.
HINT
