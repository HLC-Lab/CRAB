#!/usr/bin/env bash
# Rebuild the GPU Blink from `main` and prove what was built, or just check the GPU software stack.
# Run it through the dispatcher (`nplb build blink [HOST...]`, `nplb build check [HOST...]`) or directly:
#   examples/nplb/blink_build.sh build [HOST...]   update the Blink checkout, move the old build-cuda
#                                                  aside, configure and build from scratch, then check
#   examples/nplb/blink_build.sh check [HOST...]   read-only: ompi-cuda, ucx-cuda, CUDA, the Blink
#                                                  source, the three binaries, the libraries per host
# With no HOST, the nodes of $PARTITION (sinfo), else this node only. The steps narrate live on stderr
# as "[HH:MM:SS] text"; stdout is the final report, paged by the pager of lib/pages.sh and saved whole
# as build-blink or build-check. Its first content line is "VERDICT: OK|FAIL ..."; then one
# "OK|WARN|FAIL <item>: <detail>" line per check. The old build dir is moved to
# <lab>/archive/build-cuda-<stamp>, never deleted: a CMake build dir keeps the MPI it was configured
# with, so a rebuild in place would silently keep the old one.
# Exit: 0 on OK or WARN only, 1 on a FAIL, 2 on bad usage or a missing prerequisite (nothing changed).
# Env: NPLB_LAB (lab stack, default $HOME/crab/lab: ompi-cuda, ucx-cuda, blink-gpu, out, archive),
#      CUDA_ROOT (default /usr/local/cuda-12.3, else the newest cuda-12.*), JOBS (16), BLINK_REPO (default
#      https://github.com/HLC-Lab/blink), SSH (default ssh), PARTITION (default h100_4nodes),
#      NPLB_REPORT_DIR (the pager's folder).
set -u

unset PYTHONPATH
export GIT_TERMINAL_PROMPT=0

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
LAB=${NPLB_LAB:-${HOME:-}/crab/lab}
# shellcheck source=lib/cuda.sh
. "$HERE/lib/cuda.sh"
CUDA_ROOT=${CUDA_ROOT:-$(nplb_cuda_root)}
JOBS=${JOBS:-16}
BLINK_REPO=${BLINK_REPO:-https://github.com/HLC-Lab/blink}
PARTITION=${PARTITION:-h100_4nodes}
read -ra SSH_CMD <<<"${SSH:-ssh}"
SRC=$LAB/blink-gpu
BIN=$SRC/build-cuda/bin
BINARIES=(alltoall_comm_only allgather_comm_only allreduce_b)
LD_PATH=$LAB/ucx-cuda/lib:$LAB/ompi-cuda/lib:$CUDA_ROOT/targets/x86_64-linux/lib
LOCAL_NAME=${HOSTNAME:-$(uname -n)}
LOCAL_NAME=${LOCAL_NAME%%.*}
STAMP=$(date +%Y%m%d_%H%M%S)

# shellcheck source=lib/pages.sh
. "$HERE/lib/pages.sh"

REPORT=() # the report lines after the verdict
FAILS=()  # names of the FAIL items
WARNS=()  # names of the WARN items
HOSTS=()

usage() { echo "usage: blink_build.sh build|check [HOST...]" >&2; }
note() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() {
    echo "blink_build: $*" >&2
    exit 2
}

# raw TEXT: a report line that is not a check result
raw() { REPORT+=("$1"); }

# item OK|WARN|FAIL NAME DETAIL: one check result
item() {
    REPORT+=("$1 $2: $3")
    case $1 in
        FAIL) FAILS+=("$2") ;;
        WARN) WARNS+=("$2") ;;
    esac
}

join_by() {
    local sep=$1 out="" first=1 word
    shift
    for word in "$@"; do
        if ((first)); then
            out=$word
            first=0
        else
            out+="$sep$word"
        fi
    done
    printf '%s' "$out"
}

# finish NAME: print the paged report and exit with its verdict
finish() {
    local name=$1 verdict rc=0
    if ((${#FAILS[@]} > 0)); then
        verdict="VERDICT: FAIL $(join_by ", " "${FAILS[@]}")"
        rc=1
    elif ((${#WARNS[@]} > 0)); then
        verdict="VERDICT: OK, WARN ${#WARNS[@]} ($(join_by ", " "${WARNS[@]}"))"
    else
        verdict="VERDICT: OK"
    fi
    {
        printf '%s\n' "$verdict"
        ((${#REPORT[@]} > 0)) && printf '%s\n' "${REPORT[@]}"
    } | nplb_paged "$name" || rc=1
    exit "$rc"
}

# ---- hosts ---------------------------------------------------------------------------------------

resolve_hosts() {
    if (($# > 0)); then
        HOSTS=("$@")
        raw "hosts: ${HOSTS[*]}"
    elif ! command -v sinfo >/dev/null; then
        HOSTS=(localhost)
        raw "hosts: this node only (no HOST given, no sinfo)"
    else
        mapfile -t HOSTS < <(sinfo -h -N -p "$PARTITION" -o %N 2>/dev/null | sort -u)
        if ((${#HOSTS[@]} == 0)); then
            HOSTS=(localhost)
            raw "hosts: this node only (no HOST given, sinfo lists no nodes in $PARTITION)"
        else
            raw "hosts: ${HOSTS[*]} (partition $PARTITION)"
        fi
    fi
}

# run_on HOST CMD: run CMD on HOST (ssh), or here when HOST is this node
run_on() {
    local host=$1 cmd=$2
    if [[ $host == localhost || $host == "$LOCAL_NAME" ]]; then
        bash -c "$cmd" 2>&1 </dev/null
    else
        "${SSH_CMD[@]}" -o BatchMode=yes -o ConnectTimeout=10 "$host" "$cmd" 2>&1 </dev/null
    fi
}

# ---- the checks (shared by build and check) ---------------------------------------------------------

check_mpi() {
    local mpirun=$LAB/ompi-cuda/bin/mpirun version
    if [[ ! -x $mpirun ]]; then
        item FAIL ompi-cuda "no $mpirun"
        return
    fi
    version=$("$mpirun" --version 2>&1 | head -n 1)
    item OK ompi-cuda "${version:-mpirun printed no version}"
}

check_ucx() {
    local ucx_info=$LAB/ucx-cuda/bin/ucx_info version
    if [[ ! -x $ucx_info ]]; then
        item FAIL ucx-cuda "no $ucx_info"
        return
    fi
    version=$("$ucx_info" -v 2>&1 | awk '/Library version/ {print $NF; exit}')
    if [[ -z $version ]]; then
        item WARN ucx-cuda "ucx_info -v printed no library version"
    else
        item OK ucx-cuda "ucx $version"
    fi
}

check_cuda() {
    if [[ -d $CUDA_ROOT ]]; then
        item OK "cuda root" "$CUDA_ROOT"
    else
        item FAIL "cuda root" "no $CUDA_ROOT"
    fi
}

check_source() {
    local head branch fix
    if [[ ! -d $SRC ]]; then
        item FAIL "blink source" "no checkout at $SRC"
        return
    fi
    if ! head=$(git -C "$SRC" log -1 --format='%h %ad %s' --date=short 2>/dev/null) || [[ -z $head ]]; then
        item FAIL "blink source" "$SRC is not a git checkout"
        return
    fi
    branch=$(git -C "$SRC" rev-parse --abbrev-ref HEAD 2>/dev/null)
    fix=$(git -C "$SRC" log -1 --grep='size_t' --format=%h HEAD 2>/dev/null)
    if [[ -n $fix ]]; then
        item OK "blink source" "$head (${branch:-?}) | size_t offset fix: yes"
    else
        item FAIL "blink source" "$head (${branch:-?}) | no size_t offset fix"
    fi
}

check_binary() {
    local name=$1 path=$BIN/$1 flags
    if [[ ! -e $path ]]; then
        item FAIL "$name" "not found in $BIN"
    elif [[ ! -x $path ]]; then
        item FAIL "$name" "$path is not executable"
    else
        flags=$(grep -a -c -e '-blength' "$path" 2>/dev/null)
        if [[ ${flags:-0} -gt 0 ]]; then
            item OK "$name" "executable, burst flags (-blength) present"
        else
            item FAIL "$name" "no -blength: built without the burst flags"
        fi
    fi
}

# check_libs HOST: ldd of the first Blink binary with the lab's library path, as an mpirun job sees it
check_libs() {
    local host=$1 probe=$BIN/${BINARIES[0]} out rc lib rest missing=() mpi dir
    if [[ ! -x $probe ]]; then
        item WARN "$host libs" "skipped, no ${BINARIES[0]} to check"
        return
    fi
    out=$(run_on "$host" "LD_LIBRARY_PATH='$LD_PATH' ldd '$probe'")
    rc=$?
    while read -r lib rest; do
        [[ $rest == *"not found"* ]] && missing+=("$lib")
    done <<<"$out"
    mpi=$(awk '$1 ~ /^libmpi\.so/ && $2 == "=>" && $3 != "not" {print $3; exit}' <<<"$out")
    if ((${#missing[@]} > 0)); then
        item FAIL "$host libs" "$(join_by ", " "${missing[@]}") not found"
    elif ((rc != 0)) && [[ $out != *"=>"* ]]; then
        item FAIL "$host libs" "ssh or ldd failed (exit $rc): $(head -n 1 <<<"$out" | cut -c1-60)"
    elif [[ -z $mpi ]]; then
        item WARN "$host libs" "no libmpi in the ldd output"
    else
        dir=${mpi%/lib/*}
        if [[ $dir == "$LAB/ompi-cuda" ]]; then
            item OK "$host libs" "ok (libmpi from $dir)"
        else
            item WARN "$host libs" "libmpi comes from $dir, not $LAB/ompi-cuda"
        fi
    fi
}

check_lab_clone() {
    local line
    if line=$(git -C "$LAB" log -1 --format='%h %s' 2>/dev/null) && [[ -n $line ]]; then
        item OK "lab clone" "$line"
    else
        item WARN "lab clone" "$LAB is not a git repo, cannot tell which nplb-lab commit it holds"
    fi
}

run_checks() {
    local name host
    check_mpi
    check_ucx
    check_cuda
    check_source
    for name in "${BINARIES[@]}"; do
        check_binary "$name"
    done
    for host in "${HOSTS[@]}"; do
        check_libs "$host"
    done
    check_lab_clone
}

cmd_check() {
    resolve_hosts "$@"
    note "check libs and tools on ${HOSTS[*]}"
    run_checks
    finish build-check
}

# ---- build ------------------------------------------------------------------------------------------

update_source() {
    note "update source: $SRC from main"
    if [[ -d $SRC/.git ]]; then
        if ! git -C "$SRC" checkout -q main >>"$LOG" 2>&1 ||
            ! git -C "$SRC" pull --ff-only origin main >>"$LOG" 2>&1; then
            item WARN "source update" "git pull failed (see the log), built what is there"
        fi
    elif ! git clone "$BLINK_REPO" "$SRC" >>"$LOG" 2>&1; then
        item FAIL "source update" "git clone of $BLINK_REPO failed (proxy? see $LOG)"
        finish build-blink
    fi
}

cmake_ok() {
    [[ -n $1 ]] && [[ $(printf '%s\n3.17\n' "$("$1" --version 2>/dev/null | awk 'NR == 1 {print $3}')" | sort -V | head -n 1) == 3.17 ]]
}

# find_cmake: sets CMAKE to a cmake >= 3.17, the node's or a pip venv in <lab>/cmake-venv
find_cmake() {
    CMAKE=$(command -v cmake)
    if ! cmake_ok "$CMAKE"; then
        note "cmake here: ${CMAKE:-none}, too old or missing, using the venv $LAB/cmake-venv"
        CMAKE=$LAB/cmake-venv/bin/cmake
        if ! cmake_ok "$CMAKE"; then
            if ! { python3 -m venv "$LAB/cmake-venv" >>"$LOG" 2>&1 && "$LAB/cmake-venv/bin/pip" install cmake >>"$LOG" 2>&1; }; then
                item FAIL cmake "no cmake >= 3.17 and pip install cmake failed (proxy? see $LOG)"
                finish build-blink
            fi
        fi
    fi
    note "cmake $("$CMAKE" --version | awk 'NR == 1 {print $3}') at $CMAKE"
}

move_old_build() {
    local old=$SRC/build-cuda dest=$LAB/archive/build-cuda-$STAMP
    if [[ ! -d $old ]]; then
        note "move old build: none at $old"
        raw "old build dir: none"
        return
    fi
    note "move old build: $old -> $dest"
    [[ -e $dest ]] && dest=$dest-$$
    if ! mkdir -p "$LAB/archive" || ! mv "$old" "$dest"; then
        item FAIL "move old build" "could not move $old to $dest"
        finish build-blink
    fi
    raw "old build dir moved to $dest (nothing deleted)"
}

# build_step NAME CMAKE_ARGS...: run cmake into the log; on failure report the last error lines
build_step() {
    local name=$1 rc line errs=()
    shift
    "$CMAKE" "$@" >>"$LOG" 2>&1
    rc=$?
    ((rc == 0)) && return 0
    item FAIL "$name" "cmake exited $rc, last error lines of the log:"
    mapfile -t errs < <(grep -i -E 'error|not found|fatal' "$LOG" | tail -n 8 | cut -c1-100)
    if ((${#errs[@]} == 0)); then
        mapfile -t errs < <(tail -n 8 "$LOG" | cut -c1-100)
    fi
    for line in "${errs[@]}"; do
        raw "  $line"
    done
    raw "full log: $LOG"
    finish build-blink
}

cmd_build() {
    [[ -n $LAB && $SRC == */blink-gpu ]] || die "bad lab folder '$LAB'"
    [[ -d $LAB ]] || die "no lab folder $LAB (set NPLB_LAB)"
    [[ -x $LAB/ompi-cuda/bin/mpicc ]] || die "no $LAB/ompi-cuda/bin/mpicc: the build needs the CUDA-aware Open MPI"
    [[ -d $CUDA_ROOT ]] || die "no CUDA folder $CUDA_ROOT (set CUDA_ROOT)"
    mkdir -p "$LAB/out" || die "cannot create $LAB/out"
    LOG=$LAB/out/blink_build_$STAMP.log
    export PATH=$LAB/ompi-cuda/bin:$PATH
    resolve_hosts "$@"
    raw "log: $LOG"

    update_source
    find_cmake
    move_old_build
    note "configure: $SRC/build-cuda (CUDA, MPI wrapper $LAB/ompi-cuda/bin/mpicc)"
    build_step configure -S "$SRC" -B "$SRC/build-cuda" -DBLINK_GPU=CUDA -DBLINK_MPI_WRAPPER=ON \
        -DBLINK_TESTS=OFF -DCUDAToolkit_ROOT="$CUDA_ROOT" -DCMAKE_BUILD_TYPE=Release
    note "build: -j$JOBS, a few minutes (full output in $LOG)"
    SECONDS=0
    build_step build --build "$SRC/build-cuda" -j"$JOBS"
    note "build done in ${SECONDS}s"
    note "check libs on ${HOSTS[*]}"
    run_checks
    finish build-blink
}

verb=${1:-}
[[ $# -gt 0 ]] && shift
case $verb in
    build) cmd_build "$@" ;;
    check) cmd_check "$@" ;;
    *)
        usage
        exit 2
        ;;
esac
