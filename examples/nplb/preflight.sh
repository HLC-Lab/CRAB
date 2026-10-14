#!/usr/bin/env bash
# Check over ssh that every node can run a rank. Run it on the node that holds the CRAB checkout:
#   examples/nplb/preflight.sh [HOST...]
# Prints one "<host> <check> OK|WARN|FAIL <detail>" line per check and host, exits 1 on any FAIL.
# With no HOST, the nodes of $PARTITION (sinfo). The head is checked too, through ssh, the way
# mpirun reaches its workers (non-interactive, BatchMode).
# Checks: ssh checkout mount data blink mpirun ldd ompi python ucx route (route: workers only).
# ldd runs with the head's LD_LIBRARY_PATH, as the ranks do.
# Env: CRAB_DIR (default: this checkout), PREFLIGHT_SSH (default ssh), HEAD (default uname -n),
#      PARTITION (hosts to check when none are given), PREFLIGHT_SYSFS (default /sys).
set -uo pipefail

usage() { sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' >&2; }

case ${1:-} in
    -h | --help)
        usage
        exit 0
        ;;
esac

CRAB_DIR=${CRAB_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}
HEAD=${HEAD:-$(uname -n)}
SYSFS=${PREFLIGHT_SYSFS:-/sys}
read -ra SSH <<<"${PREFLIGHT_SSH:-ssh}"
PROGRAMS="pingpong_b pairwise_nb alltoall_nb allgather_nb incast_nb alltoall_man alltoall_comm_only allgather_comm_only allreduce_nb"

HOSTS=("$@")
if ((${#HOSTS[@]} == 0)); then
    if [[ -z ${PARTITION:-} ]]; then
        usage
        exit 2
    fi
    mapfile -t HOSTS < <(sinfo -h -N -p "$PARTITION" -o %N | sort -u)
    if ((${#HOSTS[@]} == 0)); then
        echo "preflight: sinfo lists no nodes in partition $PARTITION" >&2
        exit 2
    fi
fi

# ---- head facts ------------------------------------------------------------------------------
BLINK=$("$CRAB_DIR/.venv/bin/python" -c "import crab.setup.memory as m; print(m.get_receipt('blink')['binary_path'])" 2>/dev/null)
if [[ -z $BLINK ]]; then
    echo "preflight: cannot read the Blink receipt with $CRAB_DIR/.venv/bin/python (run crab setup)" >&2
    exit 2
fi
HEAD_MPIRUN=$(command -v mpirun)
if [[ -z $HEAD_MPIRUN ]]; then
    echo "preflight: no mpirun on this node's PATH" >&2
    exit 2
fi
HEAD_OMPI_INFO=${HEAD_MPIRUN%/*}/ompi_info
[[ -x $HEAD_OMPI_INFO ]] || HEAD_OMPI_INFO=$(command -v ompi_info)
HEAD_OMPI=
[[ -n $HEAD_OMPI_INFO ]] && HEAD_OMPI=$("$HEAD_OMPI_INFO" --version 2>&1 | sed -n 1p)
HEAD_UCX=
command -v ucx_info >/dev/null && HEAD_UCX=$(ucx_info -v 2>&1 | sed -n 1p)
HEAD_LIBMPI=
if [[ -x $BLINK/pairwise_nb ]] && command -v ldd >/dev/null; then
    HEAD_LIBMPI=$(ldd "$BLINK/pairwise_nb" 2>&1 | awk '$1 ~ /^libmpi\.so/ { print $3; exit }')
fi

# ---- the script each host runs ---------------------------------------------------------------
# Positional args: is_head checkout blink head_mpirun head_ompi head_ucx head_libmpi head_name
#                  sysfs programs head_ld_library_path. It prints "<check> <status> <detail>" lines.
REMOTE=$(
    cat <<'REMOTE_SCRIPT'
set -u
is_head=$1 checkout=$2 blink=$3 head_mpirun=$4 head_ompi=$5 head_ucx=$6
head_libmpi=$7 head_name=$8 sysfs=$9 progs=${10} head_ldpath=${11}

emit() { local d=${3//$'\n'/ }; printf '%s %s %s\n' "$1" "$2" "$d"; }

emit ssh OK "as $(uname -n)"

if [[ -d $checkout ]]; then
    emit checkout OK "$checkout"
else
    emit checkout FAIL "$checkout does not exist"
fi

if [[ $is_head == 1 ]]; then
    if ! command -v exportfs >/dev/null; then
        emit mount WARN "exportfs not found, cannot tell whether $checkout is exported"
    elif exports=$(exportfs 2>/dev/null); then
        hit=
        while read -r p _; do
            [[ $p == /* ]] || continue
            if [[ $checkout == "$p" || $checkout == "${p%/}/"* ]]; then hit=$p; break; fi
        done <<<"$exports"
        if [[ -n $hit ]]; then
            emit mount OK "exported via $hit"
        else
            emit mount FAIL "no NFS export covers $checkout"
        fi
    else
        emit mount WARN "exportfs failed, cannot tell whether $checkout is exported"
    fi
elif ! command -v findmnt >/dev/null; then
    emit mount WARN "findmnt not found, cannot tell whether $checkout is on NFS"
else
    fstype=$(findmnt -n -o FSTYPE --target "$checkout" 2>/dev/null)
    fstype=${fstype%%$'\n'*}
    if [[ $fstype == nfs* ]]; then
        emit mount OK "$fstype"
    else
        emit mount FAIL "${fstype:-no mount found} is not NFS: ranks need the head's checkout here"
    fi
fi

# ranks write their run directories under <checkout>/data
if [[ ! -d $checkout ]]; then
    emit data FAIL "$checkout does not exist"
elif err=$(mkdir -p "$checkout/data" 2>&1) && probe=$(mktemp -p "$checkout/data" .preflight.XXXXXX 2>&1); then
    rm -f "$probe"
    emit data OK "$checkout/data is writable"
else
    emit data FAIL "${err:-$probe}"
fi

read -ra proglist <<<"$progs"
if [[ ! -d $blink || ! -r $blink ]]; then
    emit blink FAIL "$blink is not a readable directory"
else
    missing=()
    for p in "${proglist[@]}"; do
        [[ -f $blink/$p && -x $blink/$p ]] || missing+=("$p")
    done
    if ((${#missing[@]})); then
        emit blink FAIL "missing or not executable in $blink: ${missing[*]}"
    else
        emit blink OK "${#proglist[@]} programs in $blink"
    fi
fi

if [[ ! -x $head_mpirun ]]; then
    emit mpirun FAIL "the head's $head_mpirun does not exist here"
else
    here=$(command -v mpirun 2>/dev/null || true)
    if [[ $here == "$head_mpirun" ]]; then
        emit mpirun OK "$here"
    elif [[ -z $here ]]; then
        emit mpirun WARN "no mpirun on the non-interactive PATH, the head's $head_mpirun exists"
    else
        emit mpirun WARN "non-interactive PATH gives $here, the head has $head_mpirun"
    fi
fi

if ! command -v ldd >/dev/null; then
    emit ldd WARN "ldd not found"
elif [[ ! -x $blink/pairwise_nb ]]; then
    emit ldd FAIL "no pairwise_nb in $blink to check"
else
    # ranks run with the head's environment (Open MPI starts its daemons with srun)
    out=$(LD_LIBRARY_PATH=$head_ldpath ldd "$blink/pairwise_nb" 2>&1)
    notfound=$(awk '/not found/ { printf "%s%s", sep, $1; sep = " " }' <<<"$out")
    libmpi=$(awk '$1 ~ /^libmpi\.so/ { print $3; exit }' <<<"$out")
    if [[ -n $notfound ]]; then
        emit ldd FAIL "pairwise_nb: not found: $notfound"
    elif [[ -n $head_libmpi && $libmpi != "$head_libmpi" ]]; then
        emit ldd FAIL "libmpi resolves to ${libmpi:-nothing}, the head has $head_libmpi"
    else
        emit ldd OK "libmpi ${libmpi:-not linked}"
    fi
fi

ompi_info_bin=${head_mpirun%/*}/ompi_info
[[ -x $ompi_info_bin ]] || ompi_info_bin=$(command -v ompi_info || true)
if [[ -z $ompi_info_bin ]]; then
    emit ompi FAIL "ompi_info not found"
else
    v=$("$ompi_info_bin" --version 2>&1 | sed -n 1p)
    if [[ -z $head_ompi ]]; then
        emit ompi WARN "the head has no ompi_info version to compare, here: $v"
    elif [[ $v == "$head_ompi" ]]; then
        emit ompi OK "$v"
    else
        emit ompi FAIL "'$v' here, '$head_ompi' on the head"
    fi
fi

py=$checkout/.venv/bin/python
if [[ ! -x $py ]]; then
    emit python FAIL "$py does not exist"
elif err=$("$py" -c 'import crab' 2>&1); then
    emit python OK "import crab"
else
    emit python FAIL "import crab failed: ${err##*$'\n'}"
fi

if ! command -v ucx_info >/dev/null; then
    emit ucx WARN "ucx_info not found"
else
    v=$(ucx_info -v 2>&1 | sed -n 1p)
    if [[ -z $head_ucx ]]; then
        emit ucx WARN "the head has no ucx_info to compare, here: $v"
    elif [[ $v == "$head_ucx" ]]; then
        emit ucx OK "$v"
    else
        emit ucx FAIL "'$v' here, '$head_ucx' on the head"
    fi
fi

if [[ $is_head != 1 ]]; then
    ip_addr=$(getent hosts "$head_name" 2>/dev/null | awk '{ print $1; exit }')
    if [[ -z $ip_addr ]]; then
        emit route FAIL "cannot resolve $head_name"
    elif ! command -v ip >/dev/null; then
        emit route WARN "ip not found, cannot tell which netdev reaches $head_name"
    else
        dev=$(ip route get "$ip_addr" 2>/dev/null | awk '{ for (i = 1; i < NF; i++) if ($i == "dev") { print $(i + 1); exit } }')
        if [[ -z $dev ]]; then
            emit route FAIL "no route to $head_name ($ip_addr)"
        else
            rdma=
            for r in "$sysfs/class/net/$dev/device/infiniband"/*; do
                [[ -e $r ]] && { rdma=${r##*/}; break; }
            done
            if [[ -n $rdma ]]; then
                emit route WARN "route to $head_name ($ip_addr) uses $dev, the netdev of RDMA device $rdma"
            else
                emit route OK "route to $head_name ($ip_addr) uses $dev, no RDMA device behind it"
            fi
        fi
    fi
fi
exit 0
REMOTE_SCRIPT
)

# ---- per host --------------------------------------------------------------------------------
width=4
for h in "${HOSTS[@]}"; do ((${#h} > width)) && width=${#h}; done

ERR=$(mktemp)
trap 'rm -f "$ERR"' EXIT
fails=0
warns=0

report() { # host check status detail
    printf '%-*s %-8s %-4s %s\n' "$width" "$1" "$2" "$3" "$4"
    case $3 in
        FAIL) fails=$((fails + 1)) ;;
        WARN) warns=$((warns + 1)) ;;
    esac
}

for host in "${HOSTS[@]}"; do
    is_head=0
    [[ $host == "$HEAD" || ${host%%.*} == "${HEAD%%.*}" ]] && is_head=1
    # ssh joins its arguments into one remote command line: quote each value for the remote shell
    remote_cmd=$(printf '%q ' bash -s -- "$is_head" "$CRAB_DIR" "$BLINK" "$HEAD_MPIRUN" "$HEAD_OMPI" \
        "$HEAD_UCX" "$HEAD_LIBMPI" "$HEAD" "$SYSFS" "$PROGRAMS" "${LD_LIBRARY_PATH:-}")
    out=$("${SSH[@]}" -o BatchMode=yes -o ConnectTimeout=5 "$host" "$remote_cmd" <<<"$REMOTE" 2>"$ERR")
    rc=$?
    if ((rc != 0)); then
        detail=$(tail -n 1 "$ERR")
        report "$host" ssh FAIL "${detail:-ssh exited $rc}"
        continue
    fi
    if [[ -z $out ]]; then
        report "$host" ssh FAIL "no output from the remote checks"
        continue
    fi
    while read -r check status detail; do
        report "$host" "$check" "$status" "$detail"
    done <<<"$out"
done

echo "preflight: $fails FAIL, $warns WARN on ${#HOSTS[@]} hosts"
((fails == 0))
