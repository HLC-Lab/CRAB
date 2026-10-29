#!/usr/bin/env bash
# Sourced by gpu_lab.sh (it needs that file's screen, log and result helpers): the `dual-port`
# step, and the allocation helper `lab_alloc` that the later steps reuse.
#
# gpu_lab.sh dual-port [CARD]: how much does the second port of one H100 card add when both ports
# send from GPU memory, and does it hit a shared PCIe x16 cap? CARD is 0-7 (default 0); a CX8 card
# has two PCI functions on one bus, even mlx5_N (f0) and odd mlx5_N+1 (f1), and its GPU sits behind
# the same PCIe switch. Four measured runs between two nodes, each wrapped in a counter snapshot/diff:
#   ucx 1 port    Blink pingpong_b -gpu through mpirun, UCX_NET_DEVICES=f0, UCX_MAX_RNDV_RAILS=1
#   ucx 2 ports   the same with f0 and f1, UCX_MAX_RNDV_RAILS=2
#   raw 1 port    ib_write_bw --use_cuda on f0 (server on node B, client on node A, ssh)
#   raw 2 ports   ib_write_bw --use_cuda on f0 and on f1 at the same time
# Gb/s is 1e9 bit/s. Blink: one timed sample is half a round trip of one message (pingpong_b.c:105-114,
# one send and one receive per sample), so Gb/s = msg_size*8/1e9/t with t the median over the samples
# of the slowest rank's time (the Maximum column). ib_write_bw: its "BW average" with --report_gbits.
# The measurement states facts: FAIL only when a run did not give a number, WARN for a suspicious one.
# Needs 2 nodes: outside a Slurm allocation the step runs itself again under salloc (see lab_alloc).
# Extra env of this step: NODES ("a b": the two nodes; same-leaf pairs give the cleanest numbers),
# SALLOC (the salloc executable), GPU_LAB_ALLOC=0 (do not wrap: already allocated), ALLOC_TIMEOUT
# (seconds to wait for the allocation, default 600), STEP_TIMEOUT (seconds each launched command may
# run, default 180), SIZE (message bytes, default 134217728), DUR (ib_write_bw seconds, default 10),
# DUAL_ITER and DUAL_WARMUP (Blink -iter and -warmup, default 50 and 10), RAW_SETTLE (seconds
# between the ib_write_bw servers and clients, default 3), LAB (default /root/crab/lab), BLINK
# (default $LAB/blink-gpu/build-cuda/bin), CUDA_LIB (CUDA libraries, as gpu_preflight.sh).

# Card k (0..7): the number N of its even function mlx5_N, the core of its socket that its rank
# runs on (socket 0 holds cards 0-3, socket 1 cards 4-7).
DUAL_F0=(0 2 4 8 10 12 14 16)
DUAL_CORES=(0 1 2 3 40 41 42 43)
DUAL_EXPECT_MAX=435 # Gb/s: top of the 418-435 range both ports of a card reached on the host (PCIe Gen5 x16)
DUAL_PORT_RATE=400  # line rate of one port, Gb/s
DUAL_ERR_RE='error|fail|unable|abort|cannot|couldn|refused|unreachable|timed out|not found|no such|denied'
LAB_POINTED=0

# ---- allocation ------------------------------------------------------------------------------

# lab_finish STEP [TAIL_START]: the pointer to the log when something failed, the verdict line, the
# screen. TAIL_START is the index in SCREEN of the first line that must survive a cut (default: the
# lines added here). Returns 0 when nothing failed.
lab_finish() {
    local step=$1 start=${2:-${#SCREEN[@]}} verdict
    if ((FAILS > 0 && LAB_POINTED == 0)); then
        LAB_POINTED=1
        note "  details: gpu_lab.sh show $step"
    fi
    printf -v verdict '%s: %d FAIL, %d WARN' "$step" "$FAILS" "$WARNS"
    say ""
    say "$verdict"
    log_section "verdict"
    log "$verdict"
    log "$(date '+%F %T') finished"
    fit_screen "$step" $((${#SCREEN[@]} - start))
    ((FAILS == 0))
}

# lab_alloc STEP ARG...: returns 0 when this process is inside an allocation (SLURM_JOB_ID set,
# GPU_LAB_ALLOC=0, or the marker GPU_LAB_IN_ALLOC=1 that the wrapped run carries). Otherwise it runs
# `gpu_lab.sh STEP ARG...` again under salloc, sets ALLOC_RC to its exit status and returns 1; the
# caller then returns ALLOC_RC. The wait for the allocation is bounded by ALLOC_TIMEOUT (salloc
# --immediate=N waits up to N seconds and then gives up); on a timeout there is one FAIL line. The
# wrapped run touches the file GPU_LAB_ALLOC_FLAG when it starts: no file means no allocation was
# made. Nothing of other users is touched.
lab_alloc() {
    local step=$1 flagdir flag errfile rc list names=() args
    shift
    ALLOC_RC=0
    if [[ -n ${SLURM_JOB_ID:-} || ${GPU_LAB_ALLOC:-1} == 0 || ${GPU_LAB_IN_ALLOC:-} == 1 ]]; then
        [[ -n ${GPU_LAB_ALLOC_FLAG:-} ]] && : >"$GPU_LAB_ALLOC_FLAG"
        return 0
    fi
    mkdir -p "$WORK/gpu_lab" || {
        echo "gpu_lab: cannot create $WORK/gpu_lab" >&2
        ALLOC_RC=2
        return 1
    }
    flagdir=$(mktemp -d "$WORK/gpu_lab/.alloc.XXXXXX") || {
        echo "gpu_lab: cannot create a folder under $WORK/gpu_lab" >&2
        ALLOC_RC=2
        return 1
    }
    flag=$flagdir/started
    errfile=$flagdir/salloc.err
    args=(-N 2 -p "$PARTITION" --exclusive "--immediate=$ALLOC_TIMEOUT")
    if [[ -n ${NODES:-} ]]; then
        read -ra names <<<"$NODES"
        list=$(IFS=,; echo "${names[*]}")
        args+=("--nodelist=$list")
    fi
    env GPU_LAB_IN_ALLOC=1 GPU_LAB_ALLOC_FLAG="$flag" "${SALLOC:-salloc}" "${args[@]}" -- \
        bash "$HERE/gpu_lab.sh" "$step" "$@" 2>"$errfile"
    rc=$?
    if [[ -e $flag ]]; then
        cat "$errfile" >&2
        rm -rf "$flagdir"
        ALLOC_RC=$rc
        return 1
    fi
    start_run "$step"
    sayf 'gpu_lab %s  %s  log: gpu_lab/%s/%s.log' "$step" "${RUN_DIR##*/}" "${RUN_DIR##*/}" "$step"
    log "salloc ${args[*]} (exit $rc):"
    log "$(<"$errfile")"
    result allocation FAIL "no allocation of 2 nodes in $PARTITION within ${ALLOC_TIMEOUT}s (salloc exit $rc)"
    note "  $(last_line "$(<"$errfile")")"
    rm -rf "$flagdir"
    lab_finish "$step"
    ALLOC_RC=$?
    return 1
}

# ---- settings --------------------------------------------------------------------------------

# dual_settings: defaults and checks of the environment settings; one line on stderr and return 1
# when one is not a whole number.
dual_settings() {
    local name
    LAB=${LAB:-/root/crab/lab}
    BLINK=${BLINK:-$LAB/blink-gpu/build-cuda/bin}
    CUDA_LIB=${CUDA_LIB:-/usr/local/cuda-12.3/targets/x86_64-linux/lib}
    SIZE=${SIZE:-134217728}
    DUR=${DUR:-10}
    DUAL_ITER=${DUAL_ITER:-50}
    DUAL_WARMUP=${DUAL_WARMUP:-10}
    STEP_TIMEOUT=${STEP_TIMEOUT:-180}
    ALLOC_TIMEOUT=${ALLOC_TIMEOUT:-600}
    RAW_SETTLE=${RAW_SETTLE:-3}
    for name in SIZE DUR DUAL_ITER STEP_TIMEOUT ALLOC_TIMEOUT; do
        if ! [[ ${!name} =~ ^[1-9][0-9]*$ ]]; then
            echo "gpu_lab: $name is a whole number above 0 (got '${!name}')" >&2
            return 1
        fi
    done
    for name in DUAL_WARMUP RAW_SETTLE; do
        if ! [[ ${!name} =~ ^[0-9]+$ ]]; then
            echo "gpu_lab: $name is a whole number (got '${!name}')" >&2
            return 1
        fi
    done
    SSH=(${PREFLIGHT_SSH:-ssh})
}

# dual_nodes: sets NODE_A and NODE_B from the allocation (scontrol show hostnames), else from NODES.
# Returns 0, 1 with NODES_ERR set when the allocation does not have exactly 2 nodes, 2 with one line
# on stderr when there is nothing to take them from.
dual_nodes() {
    local list=() raw
    NODES_ERR=
    if [[ -n ${SLURM_JOB_NODELIST:-} ]]; then
        if ! command -v scontrol >/dev/null; then
            NODES_ERR="no scontrol to list the nodes of $SLURM_JOB_NODELIST"
            return 1
        fi
        mapfile -t list < <(scontrol show hostnames "$SLURM_JOB_NODELIST" 2>&1)
    elif [[ -n ${NODES:-} ]]; then
        read -ra list <<<"$NODES"
    else
        echo "gpu_lab: no allocation (SLURM_JOB_NODELIST is empty): run inside salloc -N 2 or leave GPU_LAB_ALLOC on" >&2
        return 2
    fi
    raw=${SLURM_JOB_NODELIST:-$NODES}
    if ((${#list[@]} != 2)); then
        NODES_ERR="${#list[@]} nodes in $raw, the step needs exactly 2"
        return 1
    fi
    NODE_A=${list[0]}
    NODE_B=${list[1]}
}

# ---- counters --------------------------------------------------------------------------------

# dual_snapshot TAG: the NIC counters of both nodes before a run. Sets SNAP_OK.
dual_snapshot() {
    local out rc err errfile=$RUN_DIR/dual-port.counters.err
    SNAP_OK=0
    SNAP_ERR=
    log_section "gpu_preflight.sh counters snapshot ($1)"
    out=$(bash "$GPU_PREFLIGHT" counters snapshot "$RUN_DIR/dual-port.counters.$1" "$NODE_A" "$NODE_B" 2>"$errfile")
    rc=$?
    err=$(<"$errfile")
    rm -f "$errfile"
    log "$out"
    log "$err"
    log "exit $rc"
    if ((rc == 0)); then
        SNAP_OK=1
        return
    fi
    SNAP_ERR="snapshot failed: $(first_line "$err")"
    [[ $SNAP_ERR == "snapshot failed: " ]] && SNAP_ERR="snapshot failed (exit $rc)"
}

# dual_diff TAG: the counters again after the run. Sets TRAFFIC (the "host/dev" tokens that carried
# traffic), ECN (yes, no, unknown) and DIFF_OK.
dual_diff() {
    local out rc err line errfile=$RUN_DIR/dual-port.counters.err
    DIFF_OK=0
    TRAFFIC=
    ECN=unknown
    DIFF_ERR=
    ((SNAP_OK)) || return 0
    log_section "gpu_preflight.sh counters diff ($1)"
    out=$(bash "$GPU_PREFLIGHT" counters diff "$RUN_DIR/dual-port.counters.$1" "$NODE_A" "$NODE_B" 2>"$errfile")
    rc=$?
    err=$(<"$errfile")
    rm -f "$errfile"
    log "$out"
    log "$err"
    log "exit $rc"
    if ((rc != 0)); then
        DIFF_ERR="diff failed: $(first_line "$err")"
        [[ $DIFF_ERR == "diff failed: " ]] && DIFF_ERR="diff failed (exit $rc)"
        return
    fi
    while IFS= read -r line; do
        case $line in
            "ports with traffic:"*) TRAFFIC=${line#ports with traffic:} ;;
            "ECN/CNP activity:"*) ECN=${line#ECN/CNP activity: } ;;
        esac
    done <<<"$out"
    DIFF_OK=1
}

# dual_carried DEV: yes when DEV carried traffic on either node in the last diff.
dual_carried() {
    local token
    for token in $TRAFFIC; do
        [[ ${token#*/} == "$1" ]] && {
            echo yes
            return
        }
    done
    echo no
}

# dual_counters TAG LABEL EXPECT RUN_OK: after run TAG, the line of the counters section in COUNTER_LINES
# and the warnings in COUNTER_WARN (only for a run that gave a number: otherwise there is nothing
# to judge). EXPECT is single (only the first port) or dual (both).
dual_counters() {
    local tag=$1 label=$2 expect=$3 ok=$4 c0 c1 problem=
    COUNTER_RUNS=$((COUNTER_RUNS + 1))
    if ((!SNAP_OK)); then
        problem=$SNAP_ERR
    elif ((!DIFF_OK)); then
        problem=$DIFF_ERR
    fi
    if [[ -n $problem ]]; then
        COUNTER_LINES+=("$(printf '  %-12s n/a' "$label")")
        COUNTER_PROBLEMS+=("$problem")
        return
    fi
    c0=$(dual_carried "$F0")
    c1=$(dual_carried "$F1")
    COUNTER_LINES+=("$(printf '  %-12s %s %-3s  %s %-3s  ECN/CNP %s' "$label" "$F0" "$c0" "$F1" "$c1" "$ECN")")
    ((ok)) || return 0
    if [[ $c0 == no ]]; then
        COUNTER_WARN+=("ctr $label|first port carried no traffic ($F0)")
    fi
    if [[ $expect == dual && $c1 == no ]]; then
        COUNTER_WARN+=("ctr $label|second port carried no traffic ($F1)")
    fi
    if [[ $expect == single && $c1 == yes ]]; then
        COUNTER_WARN+=("ctr $label|the second port carried traffic in a one-port run ($F1)")
    fi
}

# ---- numbers ---------------------------------------------------------------------------------

# dual_blink_gbps FILE: sets BLINK_GBPS from the CSV rows Blink printed. A row is five numbers
# (Average,Minimum,Maximum,Median,MainRank), one per measured sample; the banner and the "Ran N
# iterations" line are not rows. Gb/s = SIZE*8/1e9 / median over the samples of the Maximum column.
dual_blink_gbps() {
    local t
    BLINK_GBPS=
    t=$(awk -F, 'function num(x) { return x ~ /^[0-9]+(\.[0-9]*)?([eE][-+]?[0-9]+)?$/ }
        NF == 5 && num($1) && num($2) && num($3) && num($4) && num($5) { print $3 }' "$1" |
        sort -g | awk '{ v[NR] = $1 } END { if (NR) print (NR % 2) ? v[(NR + 1) / 2] : (v[NR / 2] + v[NR / 2 + 1]) / 2 }')
    [[ -n $t ]] || return 0
    BLINK_GBPS=$(awk -v s="$SIZE" -v t="$t" 'BEGIN { if (t + 0 > 0) printf "%.1f", s * 8 / 1e9 / t }')
}

# dual_ratio TWO ONE: TWO/ONE with 2 decimals.
dual_ratio() { awk -v a="$1" -v b="$2" 'BEGIN { printf "%.2f", a / b }'; }

# dual_fmt VALUE: Gb/s as a whole number.
dual_fmt() { awk -v a="$1" 'BEGIN { printf "%.0f", a }'; }

# dual_errors FILE: up to 4 lines that look like errors, else the last 2 lines.
dual_errors() {
    local out
    out=$(grep -a -i -E "$DUAL_ERR_RE" "$1" 2>/dev/null | head -n 4)
    [[ -n $out ]] || out=$(grep -a -v '^[[:space:]]*$' "$1" 2>/dev/null | tail -n 2)
    printf '%s\n' "$out"
}

# dual_fail LABEL REASON FILE: a FAIL line with the first error line of FILE, then up to 3 more.
dual_fail() {
    local label=$1 reason=$2 errs=() line
    mapfile -t errs < <(dual_errors "$3")
    result "$label" FAIL "$reason${errs[0]:+: ${errs[0]}}"
    for line in "${errs[@]:1:3}"; do
        [[ -n $line ]] && note "  $line"
    done
}

# dual_exit_reason RC: why a launched command did not give a number (nothing when RC is 0).
dual_exit_reason() {
    if (($1 == 124 || $1 == 137)); then
        echo "timed out after ${STEP_TIMEOUT}s"
    elif (($1 != 0)); then
        echo "exited $1"
    fi
}

# ---- the UCX runs ----------------------------------------------------------------------------

# dual_ucx_run TAG LABEL RAILS DEVICES EXPECT: Blink pingpong_b -gpu, 1 rank per node. Sets UCX_VALUE
# (Gb/s, empty when the run did not give a number) and prints its FAIL/OK line.
dual_ucx_run() {
    local tag=$1 label=$2 rails=$3 devs=$4 expect=$5 rc reason mpirun out
    local core=${DUAL_CORES[CARD]} ld cmd
    out=$RUN_DIR/dual-port.$tag.out
    mpirun=$LAB/ompi-cuda/bin/mpirun
    UCX_VALUE=
    COUNTER_OK=0
    ld="$LAB/ucx-cuda/lib:$CUDA_LIB:$LAB/ompi-cuda/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    cmd=("$mpirun" -n 2 -H "$NODE_A,$NODE_B" --allow-run-as-root --mca pml ucx --mca btl '^openib'
        --cpu-list "$core" --bind-to cpu-list:ordered
        -x "UCX_NET_DEVICES=$devs" -x "UCX_MAX_RNDV_RAILS=$rails" -x UCX_IB_ROCE_REACHABILITY_MODE=all
        -x CUDA_DEVICE_ORDER=PCI_BUS_ID -x "CUDA_VISIBLE_DEVICES=$CARD" -x LD_LIBRARY_PATH
        "$BLINK/pingpong_b" -msgsize "$SIZE" -iter "$DUAL_ITER" -warmup "$DUAL_WARMUP" -gpu)
    log_section "$label: LD_LIBRARY_PATH=$ld ${cmd[*]}"
    dual_snapshot "$tag"
    if [[ ! -x $mpirun ]]; then
        log "mpirun not found: $mpirun"
        result "$label" FAIL "mpirun not found: LAB/ompi-cuda/bin/mpirun, LAB=$LAB"
    else
        PATH="$LAB/ompi-cuda/bin:$PATH" LD_LIBRARY_PATH=$ld timeout -k 5 "$STEP_TIMEOUT" "${cmd[@]}" >"$out" 2>&1 </dev/null
        rc=$?
        log "$(<"$out")"
        log "exit $rc"
        dual_blink_gbps "$out"
        reason=$(dual_exit_reason "$rc")
        if [[ -n $reason ]]; then
            dual_fail "$label" "mpirun $reason" "$out"
        elif [[ -z $BLINK_GBPS ]]; then
            dual_fail "$label" "no result rows in the Blink output" "$out"
        else
            UCX_VALUE=$BLINK_GBPS
            COUNTER_OK=1
            result "$label" OK "$(dual_fmt "$UCX_VALUE") Gb/s  Blink pingpong_b -gpu  UCX_NET_DEVICES=$devs RAILS=$rails"
        fi
    fi
    rm -f "$out"
    dual_diff "$tag"
    dual_counters "$tag" "$label" "$expect" "$COUNTER_OK"
}

# ---- the raw runs ----------------------------------------------------------------------------

# The script each node runs to find the IPv4 address of the netdev of each given RDMA device.
IFS= read -r -d '' DUAL_IPS_SCRIPT <<'SCRIPT' || true
sysfs=$1
shift
for d in "$@"; do
    nd=$(ls "$sysfs/class/infiniband/$d/device/net" 2>/dev/null | head -1)
    ip=$(ip -4 -o addr show dev "$nd" 2>/dev/null | awk '{print $4; exit}' | cut -d/ -f1)
    echo "$d $ip"
done
SCRIPT

# dual_raw_ips: sets RAW_IP (dev -> IPv4 address on node B); returns 1 with RAW_IPS_ERR when not found.
dual_raw_ips() {
    local out rc dev ip
    declare -gA RAW_IP=()
    RAW_IPS_ERR=
    out=$(timeout -k 5 "$STEP_TIMEOUT" "${SSH[@]}" -o BatchMode=yes -o ConnectTimeout=5 "$NODE_B" \
        "bash -s -- ${GPU_PREFLIGHT_SYSFS:-/sys} $F0 $F1" <<<"$DUAL_IPS_SCRIPT" 2>&1)
    rc=$?
    log_section "port addresses on $NODE_B (exit $rc)"
    log "$out"
    if ((rc != 0)); then
        RAW_IPS_ERR="cannot read the port addresses on $NODE_B: $(last_line "$out")"
        return 1
    fi
    while read -r dev ip; do
        [[ -n $dev ]] && RAW_IP[$dev]=$ip
    done <<<"$out"
    for dev in "$F0" "$F1"; do
        if [[ -z ${RAW_IP[$dev]:-} ]]; then
            RAW_IPS_ERR="no IPv4 address for $dev on $NODE_B"
            return 1
        fi
    done
}

# dual_ibw_cmd ROLE INDEX DEV: the command one node runs: ib_write_bw on DEV, one core per process of
# the run (the card's core, the next ones +4: two polling processes must not share a core), GPU
# memory next to the card; the client also gets the address of the server.
dual_ibw_cmd() {
    local role=$1 index=$2 dev=$3 core port target=
    core=$((${DUAL_CORES[CARD]} + 4 * index))
    port=$((18500 + ${dev#mlx5_}))
    [[ $role == client ]] && target=" ${RAW_IP[$dev]}"
    echo "timeout -k 5 $((DUR + 30)) env CUDA_DEVICE_ORDER=PCI_BUS_ID taskset -c $core ib_write_bw -d $dev -R --use_cuda=$CARD -s $SIZE -D $DUR --report_gbits -p $port$target"
}

# dual_raw_run TAG LABEL EXPECT DEV...: servers on node B, clients on node A, all DEVs at once.
# Sets RAW_VALUE (the sum of "BW average" in Gb/s, empty on a failure) and RAW_DETAIL.
dual_raw_run() {
    local tag=$1 label=$2 expect=$3 devs=("${@:4}") i dev rc reason v sum=0 failed=0 detail=
    local spids=() cpids=() sfile cfile
    RAW_VALUE=
    COUNTER_OK=0
    dual_snapshot "$tag"
    for i in "${!devs[@]}"; do
        dev=${devs[i]}
        log_section "$label server $dev on $NODE_B: $(dual_ibw_cmd server "$i" "$dev")"
        timeout -k 5 "$STEP_TIMEOUT" "${SSH[@]}" -o BatchMode=yes -o ConnectTimeout=5 "$NODE_B" \
            "$(dual_ibw_cmd server "$i" "$dev")" >"$RUN_DIR/dual-port.$tag.$dev.server" 2>&1 </dev/null &
        spids+=($!)
    done
    ((RAW_SETTLE > 0)) && sleep "$RAW_SETTLE"
    for i in "${!devs[@]}"; do
        dev=${devs[i]}
        log_section "$label client $dev on $NODE_A: $(dual_ibw_cmd client "$i" "$dev")"
        timeout -k 5 "$STEP_TIMEOUT" "${SSH[@]}" -o BatchMode=yes -o ConnectTimeout=5 "$NODE_A" \
            "$(dual_ibw_cmd client "$i" "$dev")" >"$RUN_DIR/dual-port.$tag.$dev.client" 2>&1 </dev/null &
        cpids+=($!)
    done
    for i in "${!devs[@]}"; do
        dev=${devs[i]}
        wait "${cpids[i]}"
        rc=$?
        cfile=$RUN_DIR/dual-port.$tag.$dev.client
        wait "${spids[i]}"
        sfile=$RUN_DIR/dual-port.$tag.$dev.server
        log_section "$label $dev client output (exit $rc)"
        log "$(<"$cfile")"
        log_section "$label $dev server output"
        log "$(<"$sfile")"
        v=$(awk -v s="$SIZE" '$1 == s && NF >= 5 { print $4; exit }' "$cfile")
        if [[ $v =~ ^[0-9]+(\.[0-9]+)?$ ]] && awk -v v="$v" 'BEGIN { exit !(v > 0) }'; then
            sum=$(awk -v a="$sum" -v b="$v" 'BEGIN { printf "%.1f", a + b }')
            detail+="${detail:+ + }$dev $(dual_fmt "$v")"
        else
            failed=1
            reason=$(dual_exit_reason "$rc")
            if [[ -n $reason ]]; then
                reason="$dev: ib_write_bw $reason"
            elif [[ -n $v ]]; then
                reason="$dev: unparsable BW average '$v'"
            else
                reason="$dev: no BW average line in the client output"
            fi
            if [[ -s $cfile ]]; then
                dual_fail "$label" "$reason" "$cfile"
            else
                dual_fail "$label" "$reason (server side)" "$sfile"
            fi
        fi
        rm -f "$cfile" "$sfile"
    done
    if ((!failed)); then
        RAW_VALUE=$sum
        COUNTER_OK=1
        RAW_DETAIL=$detail
        if ((${#devs[@]} == 1)); then
            result "$label" OK "$(dual_fmt "$sum") Gb/s  ib_write_bw --use_cuda=$CARD  ${devs[0]}"
        else
            result "$label" OK "$(dual_fmt "$sum") Gb/s  ib_write_bw --use_cuda=$CARD  $detail, at once"
        fi
    fi
    dual_diff "$tag"
    dual_counters "$tag" "$label" "$expect" "$COUNTER_OK"
}

# dual_raw_cuda: sets RAW_SKIP to a reason when ib_write_bw on node A cannot run (an empty RAW_SKIP
# is ok); RAW_FAIL when it is not there at all.
dual_raw_cuda() {
    local out rc
    RAW_SKIP=
    RAW_FAIL=
    out=$(timeout -k 5 "$STEP_TIMEOUT" "${SSH[@]}" -o BatchMode=yes -o ConnectTimeout=5 "$NODE_A" \
        "ib_write_bw --help" 2>&1 </dev/null)
    rc=$?
    log_section "ib_write_bw --help on $NODE_A (exit $rc)"
    log "$out"
    if [[ $out == *use_cuda* ]]; then
        return 0
    fi
    if ((rc == 127 || rc == 255 || rc == 124 || rc == 137)) || [[ -z $out ]]; then
        RAW_FAIL="cannot run ib_write_bw on $NODE_A: $(last_line "${out:-exit $rc, no output}")"
        return 0
    fi
    RAW_SKIP="skipped: ib_write_bw on $NODE_A has no --use_cuda (perftest built without CUDA)"
}

# ---- verdict ---------------------------------------------------------------------------------

# dual_verdict RATIO DUAL_GBPS: what the two figures say, as facts (ratio and the figure with 2 ports).
dual_verdict() {
    awk -v r="$1" -v d="$2" -v cap="$DUAL_EXPECT_MAX" 'BEGIN {
        if (r >= 1.6) t = "second port adds bandwidth"
        else if (r >= 1.1) t = "partial gain"
        else t = "no gain from the second port"
        if (r < 1.6 && d <= cap * 1.05) t = t ", consistent with a shared PCIe x16 cap"
        printf "%s (%.2fx, %.0f Gb/s)", t, r, d }'
}

# dual_sanity LABEL ONE TWO: WARN for values that cannot be right.
dual_sanity() {
    local label=$1 one=$2 two=$3
    if awk -v v="$one" -v l="$DUAL_PORT_RATE" 'BEGIN { exit !(v > l * 1.05) }'; then
        result "$label 1 port" WARN "$(dual_fmt "$one") Gb/s is above the $DUAL_PORT_RATE Gb/s line rate of a port"
    fi
    if awk -v v="$two" -v l="$DUAL_PORT_RATE" 'BEGIN { exit !(v > 2 * l * 1.05) }'; then
        result "$label 2 ports" WARN "$(dual_fmt "$two") Gb/s is above the $((2 * DUAL_PORT_RATE)) Gb/s line rate of two ports"
    fi
    if awk -v a="$one" -v b="$two" 'BEGIN { exit !(b < 0.9 * a) }'; then
        result "$label 2 ports" WARN "two ports gave less than one ($(dual_fmt "$two") vs $(dual_fmt "$one") Gb/s)"
    fi
}

# ---- the step --------------------------------------------------------------------------------

cmd_dual() {
    local u1= u2= r1= r2= x y xs ys entry start rcut=
    unset UCX_LOG_LEVEL
    export LC_ALL=C # decimal points in sort -g and awk, English error lines for the grep below
    CARD=0
    if (($# > 1)); then
        echo "gpu_lab: dual-port takes at most one CARD (0-7), got $# arguments" >&2
        return 2
    fi
    (($# == 1)) && CARD=$1
    if ! [[ $CARD =~ ^[0-7]$ ]]; then
        echo "gpu_lab: dual-port CARD is a number from 0 to 7 (got '$CARD')" >&2
        return 2
    fi
    dual_settings || return 2
    lab_alloc dual-port "$@" || return "$ALLOC_RC"
    F0=mlx5_${DUAL_F0[CARD]}
    F1=mlx5_$((DUAL_F0[CARD] + 1))
    dual_nodes
    case $? in
        1)
            start_run dual-port
            sayf 'gpu_lab dual-port  %s  log: gpu_lab/%s/dual-port.log' "${RUN_DIR##*/}" "${RUN_DIR##*/}"
            result nodes FAIL "$NODES_ERR"
            lab_finish dual-port
            return
            ;;
        2) return 2 ;;
    esac
    start_run dual-port
    COUNTER_LINES=()
    COUNTER_WARN=()
    COUNTER_PROBLEMS=()
    COUNTER_RUNS=0
    sayf 'gpu_lab dual-port  %s  log: gpu_lab/%s/dual-port.log' "${RUN_DIR##*/}" "${RUN_DIR##*/}"
    cut_host 24 "$NODE_A"
    x=$SHOWN
    cut_host 24 "$NODE_B"
    sayf 'nodes %s -> %s  card %d: %s + %s  GPU %d  core %d  message %d MiB' "$x" "$SHOWN" "$CARD" "$F0" "$F1" \
        "$CARD" "${DUAL_CORES[CARD]}" $((SIZE / 1048576))
    log "$(date '+%F %T') dual-port started; nodes $NODE_A $NODE_B; LAB=$LAB BLINK=$BLINK size $SIZE dur $DUR iter $DUAL_ITER warmup $DUAL_WARMUP"
    say ""
    dual_ucx_run ucx1 "ucx 1 port" 1 "$F0:1" single
    u1=$UCX_VALUE
    dual_ucx_run ucx2 "ucx 2 ports" 2 "$F0:1,$F1:1" dual
    u2=$UCX_VALUE
    dual_raw_cuda
    if [[ -n $RAW_FAIL ]]; then
        result raw FAIL "$RAW_FAIL"
    elif [[ -n $RAW_SKIP ]]; then
        result raw WARN "$RAW_SKIP"
        rcut=$RAW_SKIP
    elif ! dual_raw_ips; then
        result raw FAIL "$RAW_IPS_ERR"
    else
        dual_raw_run raw1 "raw 1 port" single "$F0"
        r1=$RAW_VALUE
        dual_raw_run raw2 "raw 2 ports" dual "$F0" "$F1"
        r2=$RAW_VALUE
    fi
    [[ -n $u1 && -n $u2 ]] && dual_sanity ucx "$u1" "$u2"
    [[ -n $r1 && -n $r2 ]] && dual_sanity raw "$r1" "$r2"
    say ""
    x="n/a"
    y="n/a"
    [[ -n $u1 && -n $u2 ]] && x=$(dual_ratio "$u2" "$u1")
    [[ -n $r1 && -n $r2 ]] && y=$(dual_ratio "$r2" "$r1")
    xs=$x
    ys=$y
    [[ $x == n/a ]] || xs=${x}x
    [[ $y == n/a ]] || ys=${y}x
    note "ratio 2 ports / 1 port:  ucx $xs   raw $ys"
    note "reference: both ports of a card, PCIe Gen5 x16: 418-435 Gb/s on the host; one port: 400 Gb/s"
    note "units: Gb/s = 1e9 bit/s = bytes*8/1e9/s (Gibit/s = 2^30 bit/s reads 7.4% lower)"
    say ""
    note "ports that carried traffic, from the counter diff of each run on both nodes:"
    for entry in "${COUNTER_LINES[@]}"; do note "$entry"; done
    if ((${#COUNTER_PROBLEMS[@]})); then
        result counters WARN "no counters in ${#COUNTER_PROBLEMS[@]} of $COUNTER_RUNS runs: ${COUNTER_PROBLEMS[0]}"
    fi
    for entry in "${COUNTER_WARN[@]}"; do result "${entry%%|*}" WARN "${entry#*|}"; done
    say ""
    start=${#SCREEN[@]}
    if [[ -n $u1 && -n $u2 ]]; then
        note "verdict ucx: $(dual_verdict "$x" "$(dual_fmt "$u2")")"
    else
        note "verdict ucx: not measured (see the FAIL above)"
    fi
    if [[ -n $rcut ]]; then
        note "verdict raw: $rcut"
    elif [[ -n $r1 && -n $r2 ]]; then
        note "verdict raw: $(dual_verdict "$y" "$(dual_fmt "$r2")")"
    else
        note "verdict raw: not measured (see the FAIL above)"
    fi
    lab_finish dual-port "$start"
}
