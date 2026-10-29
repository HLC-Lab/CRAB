#!/usr/bin/env bash
# Read-only check of the GPU requirements of the NPLB lab nodes over ssh, and a before/after
# snapshot of their NIC counters. Run it on the node that holds the CRAB checkout:
#   examples/nplb/gpu_preflight.sh [HOST...]                          the checks
#   examples/nplb/gpu_preflight.sh counters snapshot FILE [HOST...]   save the NIC counters in FILE
#   examples/nplb/gpu_preflight.sh counters diff FILE [HOST...]       read them again, print what moved
# With no HOST, the nodes of $PARTITION (sinfo); `counters diff` falls back to the hosts in FILE.
# The checks print one "<host> <check> OK|WARN|FAIL <detail>" line each and exit 1 on any FAIL:
# state ports gpus peermem mpirun ucx blink nfs (workers only) sr (sr_state.sh, once for all hosts).
# `counters diff` prints the nonzero deltas, the ports that carried traffic and whether an ECN/CNP
# counter moved: a proxy for congestion-control activity, not the NPLB setting of the switch.
# Nothing is changed on a node; the only file written is the snapshot FILE, on this machine.
# Exit: checks 0, or 1 on a FAIL; counters 0, 1 when a host is unreachable, 2 when FILE is
# missing or malformed; 2 on bad usage.
# Env: LAB (lab stack, default /root/crab/lab), CRAB_DIR (default: this checkout), CUDA_LIB
#      (default /usr/local/cuda-12.3/targets/x86_64-linux/lib), PREFLIGHT_SSH (default ssh), HEAD
#      (default uname -n), PARTITION, GPU_PREFLIGHT_SYSFS (default /sys), GPU_PREFLIGHT_PROC
#      (default /proc; both are read on the node).
set -uo pipefail

usage() { sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//' >&2; }

# The 16 usable CX8 ports of an H100 node; mlx5_6 and mlx5_7 are a disabled CX5 and are skipped.
PORTS=(mlx5_0 mlx5_1 mlx5_2 mlx5_3 mlx5_4 mlx5_5 mlx5_8 mlx5_9 mlx5_10 mlx5_11 mlx5_12 mlx5_13
    mlx5_14 mlx5_15 mlx5_16 mlx5_17)
GPUS=8
# Read under <sysfs>/class/infiniband/<dev>/ports/1/
COUNTERS=(hw_counters/np_ecn_marked_roce_packets hw_counters/np_cnp_sent hw_counters/rp_cnp_handled
    counters/port_xmit_data counters/port_rcv_data)
ECN_COUNTERS="np_ecn_marked_roce_packets np_cnp_sent rp_cnp_handled"
TRAFFIC_COUNTERS="port_xmit_data port_rcv_data"
PROXY_NOTE="proxy: counters show whether the NICs saw ECN marks/CNPs, not the NPLB setting of the switch"

case ${1:-} in
    -h | --help)
        usage
        exit 0
        ;;
esac

MODE=check
FILE=
if [[ ${1:-} == counters ]]; then
    case ${2:-} in
        snapshot | diff) MODE=$2 ;;
        *)
            usage
            exit 2
            ;;
    esac
    FILE=${3:-}
    if [[ -z $FILE ]]; then
        usage
        exit 2
    fi
    shift 3
fi

CRAB_DIR=${CRAB_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}
LAB=${LAB:-/root/crab/lab}
CUDA_LIB=${CUDA_LIB:-/usr/local/cuda-12.3/targets/x86_64-linux/lib}
HEAD=${HEAD:-$(uname -n)}
SYSFS=${GPU_PREFLIGHT_SYSFS:-/sys}
PROC=${GPU_PREFLIGHT_PROC:-/proc}
SSH_CMD=${PREFLIGHT_SSH:-ssh}
read -ra SSH <<<"$SSH_CMD"
SR_SCRIPT=$(dirname "$0")/sr_state.sh

HOSTS=("$@")
if ((${#HOSTS[@]} == 0)) && [[ -n ${PARTITION:-} ]]; then
    mapfile -t HOSTS < <(sinfo -h -N -p "$PARTITION" -o %N | sort -u)
    if ((${#HOSTS[@]} == 0)); then
        echo "gpu_preflight: sinfo lists no nodes in partition $PARTITION" >&2
        exit 2
    fi
fi
if ((${#HOSTS[@]} == 0)) && [[ $MODE != diff ]]; then
    usage
    exit 2
fi

ERR=$(mktemp)
trap 'rm -f "$ERR"' EXIT

# ---- the script each host runs for the checks ------------------------------------------------
# Positional args: is_head checkout lab cuda_lib sysfs proc host ports gpus.
# It prints "<check> <status> <detail>" lines.
REMOTE_CHECKS=$(
    cat <<'REMOTE_SCRIPT'
set -u
is_head=$1 checkout=$2 lab=$3 cuda_lib=$4 sysfs=$5 proc=$6 host=$7 ports=$8 want_gpus=$9

emit() { local d=${3//$'\n'/ }; printf '%s %s %s\n' "$1" "$2" "$d"; }

# state: the Slurm state of this node
if ! command -v sinfo >/dev/null; then
    emit state WARN "sinfo not found, cannot read the Slurm state of $host"
elif ! st=$(sinfo -h -N -n "$host" -o %t 2>&1); then
    emit state WARN "sinfo failed: ${st%%$'\n'*}"
elif [[ -z $st ]]; then
    emit state WARN "sinfo does not list a node $host"
else
    bad=
    for s in $st; do
        s=${s,,}
        t=${s%%[^a-z]*}
        case $t in
            down | drain | drained | drng | draining | fail | failing | maint | unk | unknown) bad+="${bad:+ }$s" ;;
        esac
    done
    if [[ -n $bad ]]; then
        emit state FAIL "Slurm state of $host: $bad"
    else
        emit state OK "Slurm state of $host: ${st//$'\n'/ }"
    fi
fi

# ports: the 16 CX8 devices exist and port 1 is ACTIVE
missing=()
inactive=()
for d in $ports; do
    dir=$sysfs/class/infiniband/$d
    if [[ ! -d $dir ]]; then
        missing+=("$d")
        continue
    fi
    f=$dir/ports/1/state
    if [[ -r $f ]]; then s=$(<"$f"); else s="state unreadable"; fi
    [[ $s == "4: ACTIVE" ]] || inactive+=("$d ($s)")
done
extra=()
for dir in "$sysfs"/class/infiniband/*; do
    [[ -e $dir ]] || continue
    n=${dir##*/}
    [[ " $ports mlx5_6 mlx5_7 " == *" $n "* ]] || extra+=("$n")
done
if ((${#missing[@]} || ${#inactive[@]})); then
    msg=
    ((${#missing[@]})) && msg+="missing: ${missing[*]}"
    ((${#inactive[@]})) && msg+="${msg:+; }not ACTIVE: ${inactive[*]}"
    emit ports FAIL "$msg"
elif ((${#extra[@]})); then
    emit ports WARN "all expected ports ACTIVE, unexpected devices: ${extra[*]}"
else
    set -- $ports
    emit ports OK "$# ports ACTIVE"
fi

# gpus
if ! command -v nvidia-smi >/dev/null; then
    emit gpus FAIL "nvidia-smi not found"
elif ! out=$(nvidia-smi -L 2>&1); then
    emit gpus FAIL "nvidia-smi -L failed: ${out%%$'\n'*}"
else
    n=0
    while IFS= read -r line; do
        [[ $line == "GPU "* ]] && n=$((n + 1))
    done <<<"$out"
    if ((n == want_gpus)); then
        emit gpus OK "$n GPUs"
    else
        emit gpus FAIL "$n GPUs listed by nvidia-smi -L, expected $want_gpus"
    fi
fi

# peermem: GPUDirect RDMA needs the nvidia_peermem module
found=0
if [[ -r $proc/modules ]]; then
    while read -r m _; do
        [[ $m == nvidia_peermem ]] && found=1
    done <"$proc/modules"
    if ((found)); then
        emit peermem OK "nvidia_peermem loaded"
    else
        emit peermem FAIL "nvidia_peermem is not in $proc/modules (GPUDirect RDMA needs it)"
    fi
else
    emit peermem FAIL "cannot read $proc/modules"
fi

# mpirun: the CUDA-aware Open MPI
mpirun=$lab/ompi-cuda/bin/mpirun
info=$lab/ompi-cuda/bin/ompi_info
if [[ ! -x $mpirun ]]; then
    emit mpirun FAIL "$mpirun is not an executable file"
elif [[ ! -x $info ]]; then
    emit mpirun FAIL "$info is not an executable file"
else
    out=$(LD_LIBRARY_PATH=$lab/ompi-cuda/lib:$lab/ucx-cuda/lib "$info" --parsable --all 2>&1)
    cuda_line=
    while IFS= read -r line; do
        [[ $line == *mpi_built_with_cuda_support:value:* ]] && cuda_line=$line
    done <<<"$out"
    if [[ $cuda_line == *mpi_built_with_cuda_support:value:true* ]]; then
        emit mpirun OK "$mpirun, mpi_built_with_cuda_support true"
    else
        emit mpirun FAIL "$info does not report CUDA support: ${cuda_line:-no mpi_built_with_cuda_support line}"
    fi
fi

# ucx: the CUDA-aware UCX the ranks load
shopt -s nullglob
libs=("$lab/ucx-cuda/lib"/libucp.so*)
if ((${#libs[@]})); then
    emit ucx OK "${libs[0]##*/} in $lab/ucx-cuda/lib"
else
    emit ucx FAIL "no libucp.so* in $lab/ucx-cuda/lib"
fi

# blink: both comm-only programs exist and resolve every library
bin=$lab/blink-gpu/build-cuda/bin
progs="alltoall_comm_only allgather_comm_only"
absent=()
for p in $progs; do
    [[ -f $bin/$p && -x $bin/$p ]] || absent+=("$p")
done
if ((${#absent[@]})); then
    emit blink FAIL "missing or not executable in $bin: ${absent[*]}"
elif ! command -v ldd >/dev/null; then
    emit blink WARN "both programs exist in $bin, ldd not found to check their libraries"
else
    problems=
    for p in $progs; do
        out=$(LD_LIBRARY_PATH=$lab/ucx-cuda/lib:$cuda_lib:$lab/ompi-cuda/lib ldd "$bin/$p" 2>&1)
        nf=
        while IFS= read -r line; do
            if [[ $line == *"not found"* ]]; then
                read -r lib _ <<<"$line"
                nf+="${nf:+ }$lib"
            fi
        done <<<"$out"
        [[ -z $nf ]] || problems+="${problems:+; }$p: not found: $nf"
    done
    if [[ -n $problems ]]; then
        emit blink FAIL "$problems"
    else
        emit blink OK "both programs in $bin, every library resolves"
    fi
fi

# nfs: workers only, the checkout is a mount from the head
if [[ $is_head != 1 ]]; then
    if ! command -v findmnt >/dev/null; then
        emit nfs WARN "findmnt not found, cannot tell whether $checkout is on NFS"
    else
        fstype=$(findmnt -n -o FSTYPE --target "$checkout" 2>/dev/null)
        fstype=${fstype%%$'\n'*}
        src=$(findmnt -n -o SOURCE --target "$checkout" 2>/dev/null)
        src=${src%%$'\n'*}
        if [[ $fstype == nfs* ]]; then
            emit nfs OK "$checkout is $fstype from ${src:-?}"
        else
            emit nfs FAIL "$checkout is on ${fstype:-no mount found}, not NFS: the ranks need the head's checkout here"
        fi
    fi
fi
exit 0
REMOTE_SCRIPT
)

# ---- the script each host runs for the counters ----------------------------------------------
# Positional args: sysfs ports counters. It prints "<dev> <name> <value>" lines, NA when unreadable.
REMOTE_COUNTERS=$(
    cat <<'REMOTE_SCRIPT'
set -u
sysfs=$1 ports=$2 counters=$3
for d in $ports; do
    for c in $counters; do
        f=$sysfs/class/infiniband/$d/ports/1/$c
        v=NA
        if [[ -r $f ]]; then
            raw=$(cat "$f" 2>/dev/null)
            [[ $raw =~ ^[0-9]+$ ]] && v=$raw
        fi
        printf '%s %s %s\n' "$d" "${c##*/}" "$v"
    done
done
exit 0
REMOTE_SCRIPT
)

# ssh_run HOST SCRIPT ARG...: runs SCRIPT with bash -s on HOST; sets OUT (stdout) and RC.
# ssh joins its arguments into one remote command line: quote each value for the remote shell.
ssh_run() {
    local host=$1 script=$2 remote_cmd
    shift 2
    remote_cmd=$(printf '%q ' bash -s -- "$@")
    OUT=$("${SSH[@]}" -o BatchMode=yes -o ConnectTimeout=5 "$host" "$remote_cmd" <<<"$script" 2>"$ERR")
    RC=$?
}

ssh_fail_detail() { # RC: the last stderr line of the last ssh_run, or the exit code
    local detail
    detail=$(tail -n 1 "$ERR")
    echo "${detail:-ssh exited $RC}"
}

# ---- counters --------------------------------------------------------------------------------
# read_counters HOST: sets READ to "<host> <dev> <name> <value>" lines, and RC.
read_counters() {
    local host=$1 line
    READ=
    ssh_run "$host" "$REMOTE_COUNTERS" "$SYSFS" "${PORTS[*]}" "${COUNTERS[*]}"
    if ((RC != 0)); then
        echo "$host ssh FAIL $(ssh_fail_detail)" >&2
        return 1
    fi
    if [[ -z $OUT ]]; then
        echo "$host ssh FAIL no output from the remote counter read" >&2
        RC=1
        return 1
    fi
    while IFS= read -r line; do
        READ+="$host $line"$'\n'
    done <<<"$OUT"
}

counters_snapshot() {
    local host buf= bad=()
    for host in "${HOSTS[@]}"; do
        if read_counters "$host"; then
            buf+=$READ
        else
            bad+=("$host")
        fi
    done
    if ! printf '%s' "$buf" >"$FILE"; then
        echo "gpu_preflight: cannot write $FILE" >&2
        return 2
    fi
    local lines=0
    [[ -z $buf ]] || lines=$(printf '%s' "$buf" | wc -l)
    echo "gpu_preflight: wrote $lines counter lines for $((${#HOSTS[@]} - ${#bad[@]})) of ${#HOSTS[@]} hosts to $FILE"
    ((${#bad[@]} == 0)) || return 1
}

counters_diff() {
    local -A before=() known=()
    local file_hosts=() n=0 h d name v extra
    if [[ ! -r $FILE ]]; then
        echo "gpu_preflight: cannot read $FILE" >&2
        return 2
    fi
    while read -r h d name v extra; do
        n=$((n + 1))
        if [[ -z $h || -z $v || -n $extra ]] || ! [[ $v == NA || $v =~ ^[0-9]+$ ]]; then
            echo "gpu_preflight: $FILE line $n is not '<host> <dev> <name> <value>'" >&2
            return 2
        fi
        before["$h $d $name"]=$v
        if [[ -z ${known[$h]:-} ]]; then
            known[$h]=1
            file_hosts+=("$h")
        fi
    done <"$FILE"
    if ((${#file_hosts[@]} == 0)); then
        echo "gpu_preflight: $FILE holds no counters" >&2
        return 2
    fi
    ((${#HOSTS[@]})) || HOSTS=("${file_hosts[@]}")
    for h in "${HOSTS[@]}"; do
        if [[ -z ${known[$h]:-} ]]; then
            echo "gpu_preflight: $FILE has no baseline for $h" >&2
            return 2
        fi
    done

    local host line b delta sign ecn_moved=0 ecn_seen=0 moved=0 rc=0
    local -A seen=()
    local traffic=() unreachable=()
    local width=4
    for h in "${HOSTS[@]}"; do ((${#h} > width)) && width=${#h}; done
    for host in "${HOSTS[@]}"; do
        if ! read_counters "$host"; then
            unreachable+=("$host")
            rc=1
            continue
        fi
        while read -r h d name v; do
            b=${before["$h $d $name"]:-}
            [[ $b =~ ^[0-9]+$ && $v =~ ^[0-9]+$ ]] || continue
            delta=$((v - b))
            [[ " $ECN_COUNTERS " == *" $name "* ]] && ecn_seen=1
            ((delta != 0)) || continue
            moved=1
            sign=
            ((delta > 0)) && sign=+
            printf '%-*s %-8s %-24s %s%s\n' "$width" "$h" "$d" "$name" "$sign" "$delta"
            if [[ " $ECN_COUNTERS " == *" $name "* ]]; then
                ecn_moved=1
            elif [[ " $TRAFFIC_COUNTERS " == *" $name "* && -z ${seen["$h/$d"]:-} ]]; then
                seen["$h/$d"]=1
                traffic+=("$h/$d")
            fi
        done <<<"$READ"
    done
    ((moved)) || echo "no counter moved"
    echo "ports with traffic: ${traffic[*]:-none}"
    if ((ecn_moved)); then
        echo "ECN/CNP activity: yes"
    elif ((ecn_seen)); then
        echo "ECN/CNP activity: no"
    else
        echo "ECN/CNP activity: unknown"
    fi
    echo "$PROXY_NOTE"
    ((${#unreachable[@]} == 0)) || echo "unreachable, not in this diff: ${unreachable[*]}"
    return "$rc"
}

if [[ $MODE == snapshot ]]; then
    counters_snapshot
    exit $?
elif [[ $MODE == diff ]]; then
    counters_diff
    exit $?
fi

# ---- checks: one ssh per host -----------------------------------------------------------------
width=4
for h in "${HOSTS[@]}"; do ((${#h} > width)) && width=${#h}; done
fails=0
warns=0

report() { # host check status detail
    printf '%-*s %-8s %-4s %s\n' "$width" "$1" "$2" "$3" "$4"
    case $3 in
        FAIL) fails=$((fails + 1)) ;;
        WARN) warns=$((warns + 1)) ;;
    esac
}

reachable=()
for host in "${HOSTS[@]}"; do
    is_head=0
    [[ $host == "$HEAD" || ${host%%.*} == "${HEAD%%.*}" ]] && is_head=1
    ssh_run "$host" "$REMOTE_CHECKS" "$is_head" "$CRAB_DIR" "$LAB" "$CUDA_LIB" "$SYSFS" "$PROC" \
        "$host" "${PORTS[*]}" "$GPUS"
    if ((RC != 0)); then
        report "$host" ssh FAIL "$(ssh_fail_detail)"
        continue
    fi
    if [[ -z $OUT ]]; then
        report "$host" ssh FAIL "no output from the remote checks"
        continue
    fi
    reachable+=("$host")
    while read -r check status detail; do
        report "$host" "$check" "$status" "$detail"
    done <<<"$OUT"
done

# ---- sr: Selective Repeat and adaptive routing, read by sr_state.sh for all reachable hosts ---
if ((${#reachable[@]})); then
    sr_out=$(SR_SSH=$SSH_CMD bash "$SR_SCRIPT" "${reachable[@]}" 2>&1)
    sr_rc=$?
    declare -A sr_count=() sr_values=() sr_ar=() sr_err=()
    sr_summary=
    while read -r h a rest; do
        [[ -n $h ]] || continue
        if [[ $h == SR ]]; then
            sr_summary="$h $a $rest"
        elif [[ $a == FAIL ]]; then
            sr_err[$h]=$rest
        elif [[ $rest == "-"[[:space:]]*skipped* ]]; then
            continue
        elif [[ $rest == *"SR=?"* ]]; then
            sr_err[$h]="$a could not be read: ${rest#*AR=? }"
        else
            sr=${rest#* SR=}
            sr=${sr%% *}
            ar=${rest##*AR=}
            sr_count[$h]=$((${sr_count[$h]:-0} + 1))
            [[ " ${sr_values[$h]:-} " == *" $sr "* ]] || sr_values[$h]+="${sr_values[$h]:+ }$sr"
            [[ " ${sr_ar[$h]:-} " == *" $ar "* ]] || sr_ar[$h]+="${sr_ar[$h]:+ }$ar"
        fi
    done <<<"$sr_out"
    for host in "${reachable[@]}"; do
        if [[ -n ${sr_err[$host]:-} ]]; then
            report "$host" sr FAIL "${sr_err[$host]}"
        elif [[ -z ${sr_count[$host]:-} ]]; then
            report "$host" sr FAIL "sr_state.sh read no device on this host (exit $sr_rc)"
        elif ((sr_rc != 0)) && [[ $sr_summary == *"SR mixed"* ]]; then
            report "$host" sr WARN "SR=${sr_values[$host]} AR=${sr_ar[$host]} on this host, but $sr_summary"
        else
            report "$host" sr OK "SR=${sr_values[$host]} AR=${sr_ar[$host]} on ${sr_count[$host]} devices"
        fi
    done
fi

echo "gpu_preflight: $fails FAIL, $warns WARN on ${#HOSTS[@]} hosts"
((fails == 0))
