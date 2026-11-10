# shellcheck shell=bash
# Sourced by node_tools.sh, never run on its own: the code of `node_tools.sh fix [--sr 0|1] [--ar 0|1]
# [HOST...]` (cmd_fix and the steps it runs). It defines functions only; nothing runs when it is
# sourced and it sets no shell options.
# Expects from node_tools.sh: the helpers usage die note join_by first_line quote run_tool tool_missing
# item nfs_state, REPORT and FIX_FAILS, HEAD, NFS_PATH, PARTITION, SR_SET, AR_SET, RUN_OUT, RUN_RC,
# and nplb_paged (lib/pages.sh).

# ---- fix ---------------------------------------------------------------------------------------------

# resolve_addr NAME: sets ADDR to the first IPv4 address `getent hosts` gives (else its first one), or ""
resolve_addr() {
    local first="" any="" re='^[0-9]+(\.[0-9]+){3}$'
    ADDR=""
    while read -r first _; do
        [[ -n $any ]] || any=$first
        if [[ $first =~ $re ]]; then
            ADDR=$first
            return
        fi
    done < <(getent hosts "$1" 2>/dev/null)
    ADDR=$any
}

# confirm: the "Proceed? [y/N]" question; NPLB_YES=1 yes, a terminal asks, anything else is no
confirm() {
    local ans=""
    if [[ ${NPLB_YES:-} == 1 ]]; then
        echo "Proceed? [y/N] yes (NPLB_YES=1)" >&2
        return 0
    fi
    if [[ -t 0 ]]; then
        read -r -p "Proceed? [y/N] " ans
        [[ $ans == [yY] || $ans == [yY][eE][sS] ]]
        return
    fi
    echo "Proceed? [y/N] no (stdin is not a terminal; set NPLB_YES=1 to answer yes)" >&2
    return 1
}

# print_plan: the actions fix will take, on stderr
print_plan() {
    local step=1
    {
        echo "planned actions:"
        echo "  $step. $HEAD (head): check that $NFS_PATH is exported (exportfs -v, read only; /etc/exports is never edited)"
        step=$((step + 1))
        if ((${#FIX_WORKERS[@]} == 0)); then
            echo "  $step. no worker to mount"
        elif [[ -z $HEAD_ADDR ]]; then
            echo "  $step. workers ${FIX_WORKERS[*]}: not mounted, getent hosts $HEAD gives no address"
        else
            echo "  $step. ${FIX_WORKERS[*]}: umount $NFS_PATH while it is mounted, then mount -t nfs $HEAD_ADDR:$NFS_PATH $NFS_PATH"
        fi
        step=$((step + 1))
        [[ -n $FIX_SR ]] && echo "  $step. ${FIX_HOSTS[*]}: set SR=$FIX_SR with sr_set.sh, read it back" && step=$((step + 1))
        [[ -n $FIX_AR ]] && echo "  $step. ${FIX_HOSTS[*]}: set AR=$FIX_AR with ar_set.sh, read it back"
    } >&2
}

# export_line: the /etc/exports line the lab has to add, one address per worker
export_line() {
    local w parts=()
    for w in "${FIX_WORKERS[@]}"; do
        resolve_addr "$w"
        parts+=("${ADDR:-$w}(rw,sync,no_root_squash,no_subtree_check)")
    done
    printf '%s %s' "$NFS_PATH" "${parts[*]}"
}

# fix_export: checks the export on the head; sets EXPORT_OK to 1 unless it is missing
fix_export() {
    local first listed=0
    EXPORT_OK=1
    note "checking the export of $NFS_PATH on $HEAD"
    run_tool "$HEAD" exportfs -v
    if tool_missing; then
        item WARN "head export" "exportfs is not installed on $HEAD: the export is not verified"
        return
    elif ((RUN_RC != 0)); then
        item WARN "head export" "exportfs -v failed (exit $RUN_RC): $(first_line "$RUN_OUT"); the export is not verified"
        return
    fi
    while read -r first _; do
        [[ $first == "$NFS_PATH" ]] && listed=1
    done <<<"$RUN_OUT"
    if ((listed)); then
        item OK "head export" "$NFS_PATH is exported"
        return
    fi
    EXPORT_OK=0
    item FAIL "head export" "$NFS_PATH is not exported; add this line to /etc/exports (lab-owned, not edited here): $(export_line) then run: exportfs -ra"
    FIX_FAILS+=("head export missing")
    ((${#FIX_WORKERS[@]} > 0)) && FIX_FAILS+=("workers not mounted")
}

# fix_mount WORKER: unmount while mounted, mount from the head, read it back
fix_mount() {
    local w=$1 inner q_path q_inner
    q_path=$(quote "$NFS_PATH")
    inner="while findmnt -n $q_path >/dev/null 2>&1; do umount $q_path || exit 3; done; mount -t nfs $(quote "$HEAD_ADDR:$NFS_PATH") $q_path"
    q_inner=$(quote "$inner")
    note "remounting $NFS_PATH on $w"
    run_tool "$w" "sh -c $q_inner"
    if ((RUN_RC == 3)); then
        item FAIL "$w mount" "umount $NFS_PATH failed, a process may use it: $(first_line "$RUN_OUT")"
        FIX_FAILS+=("mount $w")
        return
    elif ((RUN_RC != 0)); then
        item FAIL "$w mount" "exit $RUN_RC: $(first_line "$RUN_OUT")"
        FIX_FAILS+=("mount $w")
        return
    fi
    nfs_state "$w"
    if [[ $NFS_STATE == mounted ]]; then
        item OK "$w mounted" "$NFS_DETAIL"
    else
        item FAIL "$w read-back" "$NFS_PATH is not mounted after the mount command: $NFS_DETAIL"
        FIX_FAILS+=("mount $w")
    fi
}

fix_mounts() {
    local w
    ((${#FIX_WORKERS[@]} > 0)) || return
    if ((!EXPORT_OK)); then
        return
    elif [[ -z $HEAD_ADDR ]]; then
        item FAIL "head address" "getent hosts $HEAD printed no address: the workers are not mounted"
        FIX_FAILS+=("workers not mounted")
        return
    fi
    for w in "${FIX_WORKERS[@]}"; do
        fix_mount "$w"
    done
}

# fix_bit NAME VALUE SCRIPT: sr_set.sh / ar_set.sh on all hosts; its last line is the read-back
fix_bit() {
    local name=$1 value=$2 script=$3 out rc last line shown=0
    if [[ ! -f $script ]]; then
        item FAIL "$name" "not found: $script"
        FIX_FAILS+=("$name")
        return
    fi
    note "setting ${name^^}=$value on ${FIX_HOSTS[*]}"
    out=$(timeout 300 bash "$script" "$value" "${FIX_HOSTS[@]}" 2>&1 </dev/null)
    rc=$?
    last=${out##*$'\n'}
    if ((rc == 0)); then
        item OK "$name" "${last:-no output}"
        return
    fi
    item FAIL "$name" "${last:-no output (exit $rc)}"
    FIX_FAILS+=("$name")
    while IFS= read -r line; do
        [[ $line == *FAIL* && $line != "$last" ]] || continue
        REPORT+=("  $line")
        shown=$((shown + 1))
        ((shown >= 3)) && break
    done <<<"$out"
}

# fix_hosts_default: HOST... from sinfo
fix_hosts_default() {
    command -v sinfo >/dev/null 2>&1 || die "no HOST given and no sinfo to list the nodes of $PARTITION"
    mapfile -t FIX_HOSTS < <(sinfo -h -N -p "$PARTITION" -o %N 2>/dev/null | sort -u)
    ((${#FIX_HOSTS[@]} > 0)) || die "no HOST given and sinfo lists no nodes in $PARTITION"
}

cmd_fix() {
    local h rc=0 verdict
    FIX_SR="" FIX_AR="" FIX_HOSTS=() FIX_WORKERS=()
    while (($# > 0)); do
        case $1 in
            --sr | --ar)
                if [[ ${2:-} != 0 && ${2:-} != 1 ]]; then
                    usage
                    exit 2
                fi
                if [[ $1 == --sr ]]; then FIX_SR=$2; else FIX_AR=$2; fi
                shift 2
                ;;
            -*)
                usage
                exit 2
                ;;
            *)
                FIX_HOSTS+=("$1")
                shift
                ;;
        esac
    done
    ((${#FIX_HOSTS[@]} > 0)) || fix_hosts_default
    for h in "${FIX_HOSTS[@]}"; do
        [[ $h == "$HEAD" ]] || FIX_WORKERS+=("$h")
    done
    resolve_addr "$HEAD"
    HEAD_ADDR=$ADDR
    print_plan
    if ! confirm; then
        echo "declined: nothing changed" >&2
        exit 1
    fi
    fix_export
    fix_mounts
    [[ -n $FIX_SR ]] && fix_bit sr "$FIX_SR" "$SR_SET"
    [[ -n $FIX_AR ]] && fix_bit ar "$FIX_AR" "$AR_SET"
    if ((${#FIX_FAILS[@]} > 0)); then
        verdict="VERDICT: FAIL fix ($(join_by ", " "${FIX_FAILS[@]}"))"
        rc=1
    else
        verdict="VERDICT: OK fix"
    fi
    {
        printf '%s\n' "$verdict"
        printf 'head: %s (%s), workers: %s\n' "$HEAD" "${HEAD_ADDR:-no address}" "${FIX_WORKERS[*]:-none}"
        printf '%s\n' "${REPORT[@]}"
    } | nplb_paged node-fix || rc=1
    exit "$rc"
}
