# shellcheck shell=bash
# Sourced by node_tools.sh, never run on its own: the code of `node_tools.sh diag HOST` (cmd_diag, one
# collect_* function per check, diag_verdict). It defines functions and the diag state variables only;
# nothing runs when it is sourced and it sets no shell options.
# Expects from node_tools.sh: the helpers usage note join_by short_join first_line is_local run_tool
# tool_missing item nfs_state, REPORT and EVIDENCE, HEAD, NFS_PATH, SR_STATE, EXPECT_PORTS,
# EXPECT_CX8, EXPECT_GPUS, DMESG_RE, PCI_RE, CX_RE, NOT_CX8_RE, RUN_OUT, RUN_RC, and nplb_paged
# (lib/pages.sh).

# ---- diag: one collector per check -------------------------------------------------------------------

declare -A PORT_STATE=()  # mlx5 device -> Up|Down
declare -A PORT_NETDEV=() # mlx5 device -> its netdev ("" when it has none)
PORTS_KNOWN=0
PORTS_NOT_UP=() # expected devices that are Down or not listed
CABLE_N=0       # ports named by "Cable unplugged" dmesg lines
CX8_COUNT=-1    # -1: lspci did not tell
GPU_COUNT=-1
HW_WHY=() SW_WHY=() UNKNOWN_WHY=() NEXT_PARTS=()

# parse_ibdev2netdev TEXT: lines like "mlx5_0 port 1 ==> ens1f0np0 (Up)"; a port without a netdev has none
parse_ibdev2netdev() {
    local line re='^(mlx5_[0-9]+) port [0-9]+ ==> *([^ ]*) *\((Up|Down)\)'
    while IFS= read -r line; do
        if [[ $line =~ $re ]]; then
            PORT_STATE[${BASH_REMATCH[1]}]=${BASH_REMATCH[3]}
            PORT_NETDEV[${BASH_REMATCH[1]}]=${BASH_REMATCH[2]}
        fi
    done <<<"$1"
}

# parse_rdma_link TEXT: lines like "link mlx5_0/1 state ACTIVE physical_state LINK_UP netdev ens1f0np0"
parse_rdma_link() {
    local line dev state re='^link (mlx5_[0-9]+)/[0-9]+ state ([A-Z_]+)' net='netdev ([^ ]+)'
    while IFS= read -r line; do
        [[ $line =~ $re ]] || continue
        dev=${BASH_REMATCH[1]}
        state=Down
        [[ ${BASH_REMATCH[2]} == ACTIVE ]] && state=Up
        PORT_STATE[$dev]=$state
        PORT_NETDEV[$dev]=""
        [[ $line =~ $net ]] && PORT_NETDEV[$dev]=${BASH_REMATCH[1]}
    done <<<"$1"
}

collect_ports() {
    local host=$1 src="" dev up=0 down=() unlisted=() detail
    run_tool "$host" ibdev2netdev
    if tool_missing; then
        item WARN ibdev2netdev "not installed"
    elif ((RUN_RC == 0)); then
        parse_ibdev2netdev "$RUN_OUT"
        ((${#PORT_STATE[@]} > 0)) && src=ibdev2netdev
    fi
    run_tool "$host" rdma link show
    if tool_missing; then
        item WARN rdma "not installed"
    elif ((RUN_RC == 0)) && [[ -z $src ]]; then
        parse_rdma_link "$RUN_OUT"
        ((${#PORT_STATE[@]} > 0)) && src="rdma link"
    fi
    if [[ -z $src ]]; then
        item WARN ports "neither ibdev2netdev nor rdma link answered"
        return
    fi
    PORTS_KNOWN=1
    for dev in "${EXPECT_PORTS[@]}"; do
        case ${PORT_STATE[$dev]:-} in
            Up) up=$((up + 1)) ;;
            Down) down+=("$dev") ;;
            *) unlisted+=("$dev") ;;
        esac
    done
    PORTS_NOT_UP=("${down[@]}" "${unlisted[@]}")
    if ((${#PORTS_NOT_UP[@]} == 0)); then
        item OK ports "$up/${#EXPECT_PORTS[@]} Up ($src; mlx5_6, mlx5_7 ignored)"
        return
    fi
    detail="$up/${#EXPECT_PORTS[@]} Up ($src)"
    ((${#down[@]} > 0)) && detail+="; Down: $(short_join " " 6 "${down[@]}")"
    ((${#unlisted[@]} > 0)) && detail+="; not listed: $(short_join " " 6 "${unlisted[@]}")"
    item FAIL ports "$detail"
}

# collect_netdevs HOST: the netdevs of the 16 ports, as `ip -br link` shows them
collect_netdevs() {
    local host=$1 dev name state total=0 present=0 missing=() notup=() detail
    declare -A LINK=()
    ((PORTS_KNOWN)) || return
    run_tool "$host" ip -br link
    if tool_missing; then
        item WARN netdevs "ip not installed (netdev state not checked)"
        return
    elif ((RUN_RC != 0)); then
        item WARN netdevs "ip -br link failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
        return
    fi
    while read -r name state _; do
        LINK[${name%%@*}]=$state
    done <<<"$RUN_OUT"
    for dev in "${EXPECT_PORTS[@]}"; do
        name=${PORT_NETDEV[$dev]:-}
        [[ -n $name ]] || continue
        total=$((total + 1))
        if [[ -z ${LINK[$name]:-} ]]; then
            missing+=("$name")
        else
            present=$((present + 1))
            [[ ${LINK[$name]} == UP ]] || notup+=("$name")
        fi
    done
    if ((total == 0)); then
        item WARN netdevs "the port tool names no netdev for the 16 ports"
    elif ((${#missing[@]} == 0 && ${#notup[@]} == 0)); then
        item OK netdevs "$total/$total in ip -br link, all UP"
    else
        detail="$((total - ${#missing[@]} - ${#notup[@]}))/$total UP in ip -br link"
        ((${#missing[@]} > 0)) && detail+="; missing: $(short_join " " 6 "${missing[@]}")"
        ((${#notup[@]} > 0)) && detail+="; not UP: $(short_join " " 6 "${notup[@]}")"
        item WARN netdevs "$detail"
    fi
}

# collect_dmesg HOST: the cable and mlx5 error lines; the last five go to EVIDENCE
collect_dmesg() {
    local host=$1 line low n=0 cable=0 shown first=0 matched=()
    declare -A ADDR=()
    run_tool "$host" dmesg
    if tool_missing; then
        item WARN dmesg "not installed"
        return
    elif ((RUN_RC != 0)); then
        item WARN dmesg "cannot be read (exit $RUN_RC): $(first_line "$RUN_OUT")"
        return
    fi
    while IFS= read -r line; do
        low=${line,,}
        [[ $low =~ $DMESG_RE ]] || continue
        matched+=("$line")
        n=$((n + 1))
        if [[ $low == *"cable unplugged"* ]]; then
            cable=$((cable + 1))
            [[ $low =~ $PCI_RE ]] && ADDR[${BASH_REMATCH[0]}]=1
        fi
    done <<<"$RUN_OUT"
    CABLE_N=${#ADDR[@]}
    ((CABLE_N == 0)) && CABLE_N=$cable
    if ((n == 0)); then
        item OK dmesg "no cable or mlx5 error line"
        return
    fi
    ((n > 5)) && first=$((n - 5))
    for shown in "${matched[@]:first}"; do
        EVIDENCE+=("  dmesg: $shown")
    done
    if ((CABLE_N > 0 && ${#PORTS_NOT_UP[@]} > 0)); then
        item FAIL dmesg "$n matching lines, cable unplugged on $CABLE_N ports (last ones below)"
    elif ((CABLE_N > 0)); then
        item WARN dmesg "$n matching lines, cable unplugged on $CABLE_N ports earlier, all ports are Up now"
    else
        item WARN dmesg "$n matching lines (last ones below)"
    fi
}

# collect_pci HOST: the CX8 functions in lspci (a CX5 and virtual functions do not count)
collect_pci() {
    local host=$1 line low n=0
    run_tool "$host" lspci -nn
    if tool_missing; then
        item WARN cx8-pci "lspci not installed"
        return
    elif ((RUN_RC != 0)); then
        item WARN cx8-pci "lspci failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
        return
    fi
    while IFS= read -r line; do
        low=${line,,}
        [[ $low =~ $CX_RE ]] || continue
        [[ $low =~ $NOT_CX8_RE ]] && continue
        n=$((n + 1))
    done <<<"$RUN_OUT"
    if ((n == 0)); then
        item WARN cx8-pci "lspci shows no Mellanox/ConnectX line (no card, or a device string this script does not know)"
        return
    fi
    CX8_COUNT=$n
    if ((n == EXPECT_CX8)); then
        item OK cx8-pci "$n/$EXPECT_CX8 CX8 functions in lspci"
    elif ((n < EXPECT_CX8)); then
        item FAIL cx8-pci "$n/$EXPECT_CX8 CX8 functions in lspci"
    else
        item WARN cx8-pci "$n CX8 functions in lspci, expected $EXPECT_CX8"
    fi
}

collect_gpus() {
    local host=$1 line n=0
    run_tool "$host" nvidia-smi -L
    if tool_missing; then
        item WARN gpus "nvidia-smi not installed"
        return
    elif ((RUN_RC != 0)); then
        item FAIL gpus "nvidia-smi -L failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
        SW_WHY+=("nvidia-smi -L fails: driver state, the lab's call")
        return
    fi
    while IFS= read -r line; do
        [[ $line == "GPU "* ]] && n=$((n + 1))
    done <<<"$RUN_OUT"
    GPU_COUNT=$n
    if ((n == EXPECT_GPUS)); then
        item OK gpus "$n/$EXPECT_GPUS GPUs (nvidia-smi -L)"
    else
        item FAIL gpus "$n/$EXPECT_GPUS GPUs (nvidia-smi -L)"
    fi
}

# collect_speed HOST: ethtool speed of every Up netdev, in one remote command
collect_speed() {
    local host=$1 dev name speed nets=() detail="" tally_name
    local script='command -v ethtool >/dev/null 2>&1 || exit 127; for n in "$@"; do s=$(ethtool "$n" 2>/dev/null | while read -r k v; do [ "$k" = "Speed:" ] && echo "$v"; done); echo "$n ${s:-?}"; done'
    declare -A SPEEDS=()
    for dev in "${EXPECT_PORTS[@]}"; do
        name=${PORT_NETDEV[$dev]:-}
        [[ ${PORT_STATE[$dev]:-} == Up && $name =~ ^[A-Za-z0-9_.:-]+$ ]] && nets+=("$name")
    done
    if ((${#nets[@]} == 0)); then
        item WARN speed "no Up netdev to ask"
        return
    fi
    run_tool "$host" "sh -c '$script' sh ${nets[*]}"
    if tool_missing; then
        item WARN ethtool "not installed (link speed not checked)"
        return
    elif ((RUN_RC != 0)); then
        item WARN speed "ethtool failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
        return
    fi
    while read -r name speed; do
        [[ -n $name ]] && SPEEDS[$speed]=$((${SPEEDS[$speed]:-0} + 1))
    done <<<"$RUN_OUT"
    while read -r tally_name; do
        detail+="${detail:+, }${SPEEDS[$tally_name]} x $tally_name"
    done < <(printf '%s\n' "${!SPEEDS[@]}" | sort)
    if ((${#SPEEDS[@]} == 1)) && [[ -z ${SPEEDS["?"]:-} && -z ${SPEEDS["Unknown!"]:-} ]]; then
        item OK speed "$detail (ethtool)"
    else
        item WARN speed "$detail (ethtool)"
    fi
}

collect_mst() {
    local host=$1 line n=0 shown=()
    run_tool "$host" mst status -v
    if tool_missing; then
        item WARN mst "not installed"
        return
    elif ((RUN_RC != 0)); then
        item WARN mst "mst status -v failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
        return
    fi
    while IFS= read -r line; do
        if [[ $line =~ [A-Za-z] ]] && ((n < 2)); then
            shown+=("$line")
            n=$((n + 1))
        fi
    done <<<"$RUN_OUT"
    item OK mst "mst status -v: ${shown[*]:-no text}"
}

collect_nfs() {
    local host=$1
    if [[ $host == "$HEAD" ]]; then
        item OK nfs "head node: it exports $NFS_PATH, no mount expected"
        return
    fi
    nfs_state "$host"
    case $NFS_STATE in
        mounted) item OK nfs "$NFS_PATH mounted ($NFS_DETAIL)" ;;
        unmounted)
            item FAIL nfs "$NFS_PATH is not mounted (${NFS_DETAIL})"
            SW_WHY+=("NFS $NFS_PATH not mounted, run: nplb node fix")
            NEXT_PARTS+=("nplb node fix")
            ;;
        *) item WARN nfs "cannot tell: $NFS_DETAIL" ;;
    esac
}

# tally_text NAME: "0 x8, 1 x8" from the associative array of that name (value -> count), values sorted
tally_text() {
    local -n counts=$1
    local v out=""
    while read -r v; do
        out+="${out:+, }$v x${counts[$v]}"
    done < <(printf '%s\n' "${!counts[@]}" | sort -n)
    printf '%s' "$out"
}

# collect_sr HOST: SR and AR of every active device, as sr_state.sh reads them
collect_sr() {
    local host=$1 out rc line sr_n=0 re='SR=([0-9]+) AR=([0-9?]+)$' last problems=0 sr_val ar_val
    declare -A SR_COUNT=() AR_COUNT=()
    if [[ ! -f $SR_STATE ]]; then
        item WARN sr "not found: $SR_STATE (set SR_STATE)"
        return
    fi
    note "reading SR and AR of $host with sr_state.sh"
    out=$(timeout 120 bash "$SR_STATE" "$host" 2>&1 </dev/null)
    rc=$?
    while IFS= read -r line; do
        if [[ $line =~ $re ]]; then
            sr_n=$((sr_n + 1))
            SR_COUNT[${BASH_REMATCH[1]}]=$((${SR_COUNT[${BASH_REMATCH[1]}]:-0} + 1))
            [[ ${BASH_REMATCH[2]} == "?" ]] || AR_COUNT[${BASH_REMATCH[2]}]=$((${AR_COUNT[${BASH_REMATCH[2]}]:-0} + 1))
        fi
    done <<<"$out"
    last=${out##*$'\n'}
    if ((sr_n == 0)); then
        item WARN sr "sr_state.sh read no device (exit $rc): ${last:-no output}"
        return
    fi
    if ((${#SR_COUNT[@]} > 1)); then
        problems=1
        SW_WHY+=("SR values differ ($(tally_text SR_COUNT)), run: nplb node fix --sr 0|1")
    fi
    if ((${#AR_COUNT[@]} > 1)); then
        problems=1
        SW_WHY+=("AR values differ ($(tally_text AR_COUNT)), run: nplb node fix --ar 0|1")
    fi
    if ((problems)); then
        NEXT_PARTS+=("nplb node fix")
        item FAIL sr "SR: $(tally_text SR_COUNT); AR: $(tally_text AR_COUNT) (sr_state.sh)"
    elif ((rc != 0)); then
        item WARN sr "sr_state.sh exit $rc: ${last:-no output}"
    else
        sr_val=${!SR_COUNT[*]}
        ar_val=${!AR_COUNT[*]}
        item OK sr "SR=$sr_val AR=${ar_val:-?} on $sr_n devices (sr_state.sh)"
    fi
}

# diag_verdict: sets VERDICT and RC from what the collectors found
diag_verdict() {
    local down_text
    RC=1
    if ((PORTS_KNOWN && ${#PORTS_NOT_UP[@]} > 0 && CABLE_N > 0)); then
        HW_WHY+=("cable unplugged on $CABLE_N ports")
    fi
    if ((CX8_COUNT >= 0 && CX8_COUNT < EXPECT_CX8)); then
        HW_WHY+=("$((EXPECT_CX8 - CX8_COUNT)) of $EXPECT_CX8 CX8 functions missing from lspci")
    fi
    if ((GPU_COUNT >= 0 && GPU_COUNT < EXPECT_GPUS)); then
        HW_WHY+=("$GPU_COUNT of $EXPECT_GPUS GPUs listed by nvidia-smi -L")
    fi
    if ((${#HW_WHY[@]} == 0 && PORTS_KNOWN && ${#PORTS_NOT_UP[@]} > 0)); then
        down_text=$(short_join ", " 3 "${PORTS_NOT_UP[@]}")
        if ((CX8_COUNT >= 0)); then
            SW_WHY=("$down_text not Up, cards in lspci, no cable message: driver or link state" "${SW_WHY[@]}")
            NEXT_PARTS+=("check the dmesg lines below; reloading mlx5_core is the lab's call")
        else
            UNKNOWN_WHY+=("$down_text not Up and lspci gave no CX8 count")
        fi
    fi
    if ((${#HW_WHY[@]} > 0)); then
        VERDICT="VERDICT: HARDWARE ($(join_by "; " "${HW_WHY[@]}"))"
        NEXT_PARTS=("take this report to the lab staff (cables, card or GPUs); nothing here repairs it")
    elif ((${#SW_WHY[@]} > 0)); then
        VERDICT="VERDICT: SOFTWARE ($(join_by "; " "${SW_WHY[@]}"))"
    elif ((${#UNKNOWN_WHY[@]} > 0)); then
        VERDICT="VERDICT: UNKNOWN ($(join_by "; " "${UNKNOWN_WHY[@]}"))"
        NEXT_PARTS=("fix the WARN or FAIL lines below, then run diag again")
    else
        VERDICT="VERDICT: OK"
        RC=0
    fi
}

cmd_diag() {
    local host=${1:-} how=worker via=ssh
    if (($# != 1)) || [[ $host == -* ]]; then
        usage
        exit 2
    fi
    [[ $host == "$HEAD" ]] && how=head
    is_local "$host" && via=local
    note "diagnosing $host ($how, $via)"
    run_tool "$host" uname -n
    if ((RUN_RC != 0)); then
        VERDICT="VERDICT: UNKNOWN (cannot reach $host)"
        item FAIL "$via" "exit $RUN_RC: $(first_line "$RUN_OUT")"
        NEXT_PARTS=("check that ssh to $host works, then run diag again")
        RC=1
    else
        note "ports (ibdev2netdev, rdma link)"
        collect_ports "$host"
        note "netdevs, dmesg, lspci"
        collect_netdevs "$host"
        collect_dmesg "$host"
        collect_pci "$host"
        note "GPUs, link speed, mst"
        collect_gpus "$host"
        collect_speed "$host"
        collect_mst "$host"
        note "NFS and SR/AR"
        collect_nfs "$host"
        collect_sr "$host"
        diag_verdict
    fi
    {
        printf '%s\n' "$VERDICT"
        ((${#NEXT_PARTS[@]} > 0)) && printf 'next: %s\n' "$(join_by "; " "${NEXT_PARTS[@]}")"
        printf 'host: %s (%s, %s)\n' "$host" "$how" "$via"
        printf '%s\n' "${REPORT[@]}"
        ((${#EVIDENCE[@]} > 0)) && printf '%s\n' "${EVIDENCE[@]}"
    } | nplb_paged node-diag || RC=1
    exit "$RC"
}
