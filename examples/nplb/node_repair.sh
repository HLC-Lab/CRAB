# shellcheck shell=bash
# Sourced by node_tools.sh, never run on its own: the four opt-in repairs of `node_tools.sh fix`
# (--rescan, --links, --peermem, --nfs-head). Each one reads the state first and touches only what is
# wrong, runs its commands through run_tool, and ends with a read-back item per host; failures go to
# FIX_FAILS. /etc/exports is never written. It defines functions and two settings; nothing runs when it
# is sourced and it sets no shell options.
# Expects from node_tools.sh: the helpers note join_by short_join first_line quote run_tool tool_missing
# item, REPORT and FIX_FAILS, HEAD, NFS_PATH, EXPECT_PORTS, EXPECT_CX8, RUN_OUT, RUN_RC; from node_diag.sh:
# parse_ibdev2netdev parse_rdma_link count_cx8 PORT_STATE PORT_NETDEV; from node_fix.sh: FIX_HOSTS and the
# FIX_RESCAN FIX_LINKS FIX_PEERMEM FIX_NFS_HEAD switches.

# The file that a PCI rescan is written to; a test points it at a temporary file.
PCI_RESCAN_PATH=${PCI_RESCAN_PATH:-/sys/bus/pci/rescan}
NETDEV_RE='^[A-Za-z0-9][A-Za-z0-9_.:-]*$'

# fix_fail NAME: adds NAME to FIX_FAILS once
fix_fail() {
    local f
    for f in "${FIX_FAILS[@]}"; do
        [[ $f == "$1" ]] && return
    done
    FIX_FAILS+=("$1")
}

# wait_secs VALUE DEFAULT: VALUE when it is a whole number of seconds, else DEFAULT
wait_secs() {
    if [[ $1 =~ ^[0-9]+$ ]]; then printf '%s' "$1"; else printf '%s' "$2"; fi
}

# plan_repairs: the plan lines of the repairs that were asked for. Called from print_plan, inside its
# output group: it counts up print_plan's `step`.
plan_repairs() {
    local link_wait rescan_wait
    link_wait=$(wait_secs "${FIX_LINK_WAIT:-}" 10)
    rescan_wait=$(wait_secs "${FIX_RESCAN_WAIT:-}" 3)
    if ((FIX_RESCAN)); then
        echo "  $step. ${FIX_HOSTS[*]}: while lspci shows fewer than $EXPECT_CX8 CX8 functions: sh -c 'echo 1 > $PCI_RESCAN_PATH', wait ${rescan_wait}s, count again"
        echo "     rescan is harmless on a running node; a missing card usually stays missing"
        step=$((step + 1))
    fi
    if ((FIX_LINKS)); then
        echo "  $step. ${FIX_HOSTS[*]}: for each RDMA port that is not Up: ip link set <netdev> up, wait up to ${link_wait}s, read the ports again"
        step=$((step + 1))
    fi
    if ((FIX_PEERMEM)); then
        echo "  $step. ${FIX_HOSTS[*]}: when lsmod lacks nvidia_peermem: modprobe nvidia_peermem, read lsmod again"
        step=$((step + 1))
    fi
    if ((FIX_NFS_HEAD)); then
        echo "  $step. $HEAD (head): when $NFS_PATH is not in exportfs -v: systemctl restart nfs-server (else service nfs-kernel-server restart), exportfs -ra, exportfs -v; /etc/exports is never edited"
        step=$((step + 1))
    fi
}

# ---- --rescan ----------------------------------------------------------------------------------------

# count_cx8_on HOST: sets CX8_N from lspci -nn on HOST; returns 1 and sets REPAIR_ERR when lspci cannot be read
count_cx8_on() {
    run_tool "$1" lspci -nn
    if tool_missing; then
        REPAIR_ERR="lspci is not installed on $1"
        return 1
    elif ((RUN_RC != 0)); then
        REPAIR_ERR="lspci failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
        return 1
    fi
    count_cx8 "$RUN_OUT"
}

repair_rescan() {
    local h before inner wait
    wait=$(wait_secs "${FIX_RESCAN_WAIT:-}" 3)
    inner="echo 1 > $(quote "$PCI_RESCAN_PATH")"
    for h in "${FIX_HOSTS[@]}"; do
        note "PCI rescan on $h"
        if ! count_cx8_on "$h"; then
            item FAIL "$h rescan" "$REPAIR_ERR"
            fix_fail "rescan $h"
            continue
        fi
        before=$CX8_N
        if ((before >= EXPECT_CX8)); then
            item OK "$h rescan" "$before/$EXPECT_CX8 CX8 functions already, nothing to do"
            continue
        fi
        run_tool "$h" "sh -c $(quote "$inner")"
        if ((RUN_RC != 0)); then
            item FAIL "$h rescan" "the rescan failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
            fix_fail "rescan $h"
            continue
        fi
        sleep "$wait"
        if ! count_cx8_on "$h"; then
            item FAIL "$h rescan" "cannot count again: $REPAIR_ERR"
            fix_fail "rescan $h"
        elif ((CX8_N >= EXPECT_CX8)); then
            item OK "$h rescan" "count now $CX8_N/$EXPECT_CX8 CX8 functions"
        else
            item FAIL "$h rescan" "still $CX8_N of $EXPECT_CX8 CX8 functions: the card is not seen by the machine, a hardware call for the lab"
            fix_fail "rescan $h"
        fi
    done
}

# ---- --links -----------------------------------------------------------------------------------------

# read_ports HOST: fills PORT_STATE and PORT_NETDEV from ibdev2netdev, else rdma link; returns 1 and sets
# REPAIR_ERR when neither answers
read_ports() {
    local host=$1
    PORT_STATE=()
    PORT_NETDEV=()
    run_tool "$host" ibdev2netdev
    if ((RUN_RC == 255)); then
        REPAIR_ERR="cannot reach $host: $(first_line "$RUN_OUT")"
        return 1
    fi
    ((RUN_RC == 0)) && parse_ibdev2netdev "$RUN_OUT"
    if ((${#PORT_STATE[@]} == 0)); then
        run_tool "$host" rdma link show
        ((RUN_RC == 0)) && parse_rdma_link "$RUN_OUT"
    fi
    if ((${#PORT_STATE[@]} == 0)); then
        REPAIR_ERR="neither ibdev2netdev nor rdma link answered on $host"
        return 1
    fi
}

# devs_text VERB-SINGULAR VERB-PLURAL DEV...: "mlx5_4 is" or "mlx5_4 mlx5_5 are"
devs_text() {
    local one=$1 many=$2
    shift 2
    if (($# == 1)); then
        printf '%s %s' "$1" "$one"
    else
        printf '%s %s' "$(short_join " " 6 "$@")" "$many"
    fi
}

repair_links() {
    local h dev nd i wait up_n down=() nets=() nonet=() unlisted=() ok=() still=()
    wait=$(wait_secs "${FIX_LINK_WAIT:-}" 10)
    for h in "${FIX_HOSTS[@]}"; do
        note "RDMA links on $h"
        if ! read_ports "$h"; then
            item FAIL "$h links" "$REPAIR_ERR"
            fix_fail "links $h"
            continue
        fi
        down=() nets=() nonet=() unlisted=() ok=() still=() up_n=0
        for dev in "${EXPECT_PORTS[@]}"; do
            case ${PORT_STATE[$dev]:-} in
                Up) up_n=$((up_n + 1)) ;;
                Down)
                    nd=${PORT_NETDEV[$dev]:-}
                    if [[ $nd =~ $NETDEV_RE ]]; then
                        down+=("$dev")
                        nets+=("$nd")
                    else
                        nonet+=("$dev")
                    fi
                    ;;
                *) unlisted+=("$dev") ;;
            esac
        done
        if ((${#nonet[@]} > 0)); then
            item FAIL "$h links" "$(devs_text "has" "have" "${nonet[@]}") no usable netdev name in the port tool: not repaired"
            fix_fail "links $h"
        fi
        if ((${#down[@]} == 0)); then
            if ((${#nonet[@]} == 0)); then
                item OK "$h links" "all $up_n ports Up, nothing to do${unlisted[*]:+; ${#unlisted[@]} not listed by the port tool}"
            fi
            continue
        fi
        for i in "${!down[@]}"; do
            run_tool "$h" ip link set "$(quote "${nets[i]}")" up
            if ((RUN_RC != 0)); then
                item FAIL "$h links" "ip link set ${nets[i]} up failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
                fix_fail "links $h"
                down[i]=""
            fi
        done
        sleep "$wait"
        if ! read_ports "$h"; then
            item FAIL "$h links" "cannot read the ports back: $REPAIR_ERR"
            fix_fail "links $h"
            continue
        fi
        for dev in "${down[@]}"; do
            [[ -n $dev ]] || continue
            if [[ ${PORT_STATE[$dev]:-} == Up ]]; then ok+=("$dev"); else still+=("$dev"); fi
        done
        ((${#ok[@]} > 0)) && item OK "$h links" "$(devs_text "is" "are" "${ok[@]}") Up now"
        if ((${#still[@]} > 0)); then
            item FAIL "$h links" "$(short_join " " 6 "${still[@]}") still not Up: no signal from the far end: cable or switch port, a hardware call (run nplb node diag $h)"
            fix_fail "links $h"
        fi
    done
}

# ---- --peermem ---------------------------------------------------------------------------------------

# peermem_state HOST: sets PEER_STATE to loaded or missing (from lsmod), or unknown with REPAIR_ERR
peermem_state() {
    local name
    PEER_STATE=unknown
    run_tool "$1" lsmod
    if tool_missing; then
        REPAIR_ERR="lsmod is not installed on $1"
        return
    elif ((RUN_RC != 0)); then
        REPAIR_ERR="lsmod failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
        return
    fi
    PEER_STATE=missing
    while read -r name _; do
        [[ $name == nvidia_peermem ]] && PEER_STATE=loaded
    done <<<"$RUN_OUT"
}

repair_peermem() {
    local h
    for h in "${FIX_HOSTS[@]}"; do
        note "nvidia_peermem on $h"
        peermem_state "$h"
        if [[ $PEER_STATE == unknown ]]; then
            item FAIL "$h peermem" "$REPAIR_ERR"
            fix_fail "peermem $h"
            continue
        elif [[ $PEER_STATE == loaded ]]; then
            item OK "$h peermem" "nvidia_peermem is already loaded, nothing to do"
            continue
        fi
        run_tool "$h" modprobe nvidia_peermem
        if ((RUN_RC != 0)); then
            item FAIL "$h peermem" "modprobe nvidia_peermem failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
            fix_fail "peermem $h"
            continue
        fi
        peermem_state "$h"
        if [[ $PEER_STATE == loaded ]]; then
            item OK "$h peermem" "nvidia_peermem is loaded now"
        else
            item FAIL "$h peermem" "modprobe nvidia_peermem passed but lsmod does not list it${REPAIR_ERR:+: $REPAIR_ERR}"
            fix_fail "peermem $h"
        fi
    done
}

# ---- --nfs-head --------------------------------------------------------------------------------------

# head_exports: sets EXPORT_STATE to listed, missing or unknown (with REPAIR_ERR) from `exportfs -v` on HEAD
head_exports() {
    local first
    EXPORT_STATE=unknown
    run_tool "$HEAD" exportfs -v
    if tool_missing; then
        REPAIR_ERR="exportfs is not installed on $HEAD"
        return
    elif ((RUN_RC != 0)); then
        REPAIR_ERR="exportfs -v failed (exit $RUN_RC): $(first_line "$RUN_OUT")"
        return
    fi
    EXPORT_STATE=missing
    while read -r first _; do
        [[ $first == "$NFS_PATH" ]] && EXPORT_STATE=listed
    done <<<"$RUN_OUT"
}

repair_nfs_head() {
    local restart_note=""
    note "NFS server on $HEAD"
    head_exports
    if [[ $EXPORT_STATE == unknown ]]; then
        item FAIL "head nfs-server" "$REPAIR_ERR"
        fix_fail "head nfs-server"
        return
    elif [[ $EXPORT_STATE == listed ]]; then
        item OK "head nfs-server" "$NFS_PATH is already exported, nothing to do"
        return
    fi
    run_tool "$HEAD" systemctl restart nfs-server
    if tool_missing; then
        run_tool "$HEAD" service nfs-kernel-server restart
    fi
    if ((RUN_RC != 0)); then
        restart_note=" (the server restart failed, exit $RUN_RC: $(first_line "$RUN_OUT"))"
    fi
    run_tool "$HEAD" exportfs -ra
    head_exports
    if [[ $EXPORT_STATE == listed ]]; then
        item OK "head nfs-server" "$NFS_PATH is exported now$restart_note"
        return
    fi
    item FAIL "head nfs-server" "$NFS_PATH is still not exported${restart_note}; /etc/exports is not edited here, the lab adds this line:"
    REPORT+=("  $NFS_PATH *(rw,sync,no_root_squash,no_subtree_check)" "  then: exportfs -ra")
    fix_fail "head nfs-server"
}
