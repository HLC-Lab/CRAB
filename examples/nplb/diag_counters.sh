#!/usr/bin/env bash
# Did the NICs see ECN/CNP marks while a job ran? One paged report. Run it through the dispatcher
# (`nplb diag counters snapshot|diff [HOST...]`) or directly:
#   examples/nplb/diag_counters.sh snapshot [HOST...]   save the NIC counters in $WORK/counters_snapshot.txt
#   examples/nplb/diag_counters.sh diff [HOST...]       read them again, show what moved
# Take a snapshot, run the job, then take the diff. Both wrap `gpu_preflight.sh counters snapshot|diff`
# (nothing is changed on a node; the only file written here is the snapshot, through gpu_preflight.sh,
# and the pager's report). With no HOST, gpu_preflight.sh picks the nodes (diff: the hosts of the snapshot).
# The counters are a proxy: they show that the NICs saw ECN marks or CNPs, not the NPLB or AR setting of
# the switch. Narration goes to stderr as "[HH:MM:SS] text"; stdout is the report, paged by lib/pages.sh
# and saved whole as diag-counters-snapshot or diag-counters-diff. Its first line is "VERDICT: OK|WARN ...".
# The diff report holds: the verdict, the age of the snapshot, a table per host (ECN marked, CNP sent,
# CNP handled: sums over the host's devices; ports with traffic), the ports with traffic, the proxy
# sentence, hosts that were unreachable, and from page 2 on every raw delta line "<host> <dev> <name> <delta>".
# Exit: 0 for OK and WARN of a complete run, 1 when gpu_preflight.sh reports a failure (a host is
# unreachable), 2 for bad usage, a missing or malformed snapshot, or a missing gpu_preflight.sh.
# Env: WORK (default <checkout>/local/nplb-work), GPU_PREFLIGHT (default gpu_preflight.sh next to this
#      script), DIAG_NOW (epoch seconds, replaces the clock when the age of the snapshot is computed),
#      NPLB_REPORT_DIR (the pager's folder).
set -uo pipefail

unset PYTHONPATH

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CHECKOUT=$(cd "$HERE/../.." && pwd)
WORK=${WORK:-${NPLB_WORK:-$CHECKOUT/local/nplb-work}}
GPU_PREFLIGHT=${GPU_PREFLIGHT:-$HERE/gpu_preflight.sh}
SNAPSHOT=$WORK/counters_snapshot.txt
WIDTH=100
PROXY_FALLBACK="proxy: counters show whether the NICs saw ECN marks/CNPs, not the NPLB setting of the switch"

# shellcheck source=lib/pages.sh
. "$HERE/lib/pages.sh"

note() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() {
    echo "diag counters: $*" >&2
    exit 2
}
usage() {
    echo "usage: nplb diag counters snapshot|diff [HOST...]" >&2
    exit 2
}
wrap() { fold -s -w "$WIDTH"; }

STUB_OUT=
STUB_ERR=
STUB_RC=0

# run_stub ARGS...: run gpu_preflight.sh; its stderr is shown at once and kept in STUB_ERR.
# Exit 2 here when it failed in a way that leaves nothing to report.
run_stub() {
    local errf
    [[ -f $GPU_PREFLIGHT ]] || die "not found: $GPU_PREFLIGHT (set GPU_PREFLIGHT)"
    errf=$(mktemp) || die "cannot create a temp file"
    STUB_OUT=$(bash "$GPU_PREFLIGHT" "$@" 2>"$errf" </dev/null)
    STUB_RC=$?
    STUB_ERR=$(cat "$errf")
    rm -f "$errf"
    [[ -z $STUB_ERR ]] || printf '%s\n' "$STUB_ERR" >&2
    if ((STUB_RC > 1)); then
        ((STUB_RC == 2)) || echo "diag counters: gpu_preflight.sh exited $STUB_RC" >&2
        exit 2
    fi
}

hosts_text() {
    if (($# == 0)); then
        echo "the nodes gpu_preflight.sh picks"
    else
        echo "$*"
    fi
}

plural() { # plural N WORD
    if (($1 == 1)); then echo "$1 $2"; else echo "$1 ${2}s"; fi
}

cmd_snapshot() {
    local ok= total= verdict rc=0
    mkdir -p "$WORK" || die "cannot create $WORK"
    note "saving the NIC counters of $(hosts_text "$@") to $SNAPSHOT"
    run_stub counters snapshot "$SNAPSHOT" "$@"
    if [[ $STUB_OUT =~ wrote\ [0-9]+\ counter\ lines\ for\ ([0-9]+)\ of\ ([0-9]+)\ hosts ]]; then
        ok=${BASH_REMATCH[1]}
        total=${BASH_REMATCH[2]}
    fi
    if ((STUB_RC == 0)); then
        if [[ -n $ok ]]; then
            verdict="VERDICT: OK snapshot of $(plural "$ok" host) saved"
        else
            verdict="VERDICT: OK snapshot saved"
        fi
    else
        rc=1
        if [[ -n $ok ]]; then
            verdict="VERDICT: WARN snapshot of $ok of $total hosts"
        else
            verdict="VERDICT: WARN snapshot incomplete"
        fi
    fi
    note "snapshot done (gpu_preflight exit $STUB_RC)"
    {
        printf '%s\n' "$verdict" | wrap
        echo "file: $SNAPSHOT"
        [[ -z $STUB_OUT ]] || printf '%s\n' "$STUB_OUT" | wrap
        [[ -z $STUB_ERR ]] || printf '%s\n' "$STUB_ERR" | wrap
        if ((rc == 0)); then
            echo "next: run the job, then: nplb diag counters diff"
        else
            echo "next: the snapshot lacks the hosts above; name the hosts that answer, or retry:"
            echo "      nplb diag counters snapshot HOST..."
        fi
    } | nplb_paged diag-counters-snapshot || rc=1
    exit "$rc"
}

# snapshot_age: AGE_LINE from the snapshot file's mtime against DIAG_NOW (else the clock).
snapshot_age() {
    local now=${DIAG_NOW:-} mtime secs
    [[ $now =~ ^[0-9]+$ ]] || now=$(date +%s)
    mtime=$(stat -c %Y "$SNAPSHOT" 2>/dev/null) || mtime=
    if [[ ! $mtime =~ ^[0-9]+$ ]] || ((mtime > now)); then
        AGE_LINE="snapshot age unknown"
        return
    fi
    secs=$((now - mtime))
    if ((secs < 60)); then
        AGE_LINE="snapshot taken ${secs}s ago"
    elif ((secs < 3600)); then
        AGE_LINE=$(printf 'snapshot taken %dm %02ds ago' $((secs / 60)) $((secs % 60)))
    elif ((secs < 86400)); then
        AGE_LINE=$(printf 'snapshot taken %dh %02dm ago' $((secs / 3600)) $((secs % 3600 / 60)))
    else
        AGE_LINE=$(printf 'snapshot taken %dd %02dh ago' $((secs / 86400)) $((secs % 86400 / 3600)))
    fi
}

# Filled by parse_diff from the stub's output.
DELTAS=()    # "<host> <dev> <name> <delta>" lines
EXTRA=()     # any other line, kept so nothing is hidden
NOMOVE=0
PORTS_TEXT=none
ACTIVITY=unknown
PROXY=
UNREACH=
declare -A ECN_SUM=() SENT_SUM=() HANDLED_SUM=() PORT_COUNT=()

parse_diff() {
    local line host dev name delta extra tok
    [[ -n $STUB_OUT ]] || return 0
    while IFS= read -r line; do
        case $line in
            "") ;;
            "no counter moved") NOMOVE=1 ;;
            "ports with traffic: "*) PORTS_TEXT=${line#ports with traffic: } ;;
            "ECN/CNP activity: "*) ACTIVITY=${line#ECN/CNP activity: } ;;
            "proxy: "*) PROXY=$line ;;
            "unreachable, not in this diff: "*) UNREACH=${line#unreachable, not in this diff: } ;;
            *)
                host= dev= name= delta= extra=
                read -r host dev name delta extra <<<"$line"
                if [[ -n $delta && -z $extra && $delta =~ ^[+-]?[0-9]+$ ]]; then
                    DELTAS+=("$line")
                    delta=${delta#+}
                    case $name in
                        np_ecn_marked_roce_packets) ECN_SUM[$host]=$((${ECN_SUM[$host]:-0} + delta)) ;;
                        np_cnp_sent) SENT_SUM[$host]=$((${SENT_SUM[$host]:-0} + delta)) ;;
                        rp_cnp_handled) HANDLED_SUM[$host]=$((${HANDLED_SUM[$host]:-0} + delta)) ;;
                    esac
                else
                    EXTRA+=("$line")
                fi
                ;;
        esac
    done <<<"$STUB_OUT"
    ((${#DELTAS[@]} > 0)) || NOMOVE=1
    [[ -n $PROXY ]] || PROXY=$PROXY_FALLBACK
    if [[ $PORTS_TEXT != none ]]; then
        for tok in $PORTS_TEXT; do
            host=${tok%/*}
            PORT_COUNT[$host]=$((${PORT_COUNT[$host]:-0} + 1))
        done
    fi
}

# table_hosts HOST...: TABLE_HOSTS = the asked hosts (else those of the snapshot), minus the
# unreachable ones, plus any host that shows up in the deltas.
table_hosts() {
    local host listed=" " line
    local candidates=()
    if (($# > 0)); then
        candidates=("$@")
    else
        while IFS= read -r line; do
            candidates+=("$line")
        done < <(awk '!seen[$1]++ { print $1 }' "$SNAPSHOT")
    fi
    for line in "${DELTAS[@]}"; do
        candidates+=("${line%% *}")
    done
    TABLE_HOSTS=()
    for host in "${candidates[@]}"; do
        [[ $listed == *" $host "* ]] && continue
        [[ " $UNREACH " == *" $host "* ]] && continue
        listed+="$host "
        TABLE_HOSTS+=("$host")
    done
}

diff_verdict() {
    local host moved_hosts=0 ports=0 level=OK text
    [[ $PORTS_TEXT == none ]] || ports=$(wc -w <<<"$PORTS_TEXT")
    for host in "${TABLE_HOSTS[@]}"; do
        if ((${ECN_SUM[$host]:-0} != 0 || ${SENT_SUM[$host]:-0} != 0 || ${HANDLED_SUM[$host]:-0} != 0)); then
            moved_hosts=$((moved_hosts + 1))
        fi
    done
    if ((NOMOVE)); then
        level=WARN
        text="no counter moved (the snapshot may be too fresh or the job did not run)"
    elif [[ $ACTIVITY == yes ]]; then
        text="ECN/CNP activity: yes (ECN/CNP moved on $moved_hosts of ${#TABLE_HOSTS[@]} hosts, $(plural "$ports" "port") with traffic)"
    elif [[ $ACTIVITY == no ]]; then
        text="ECN/CNP activity: no (${#TABLE_HOSTS[@]} hosts, $(plural "$ports" "port") with traffic)"
    else
        level=WARN
        text="ECN/CNP activity: unknown (no ECN counter could be read)"
    fi
    if [[ -n $UNREACH ]]; then
        level=WARN
        text="unreachable: $UNREACH, not in this diff; $text"
    fi
    VERDICT="VERDICT: $level $text"
}

host_table() {
    local width=4 host ecn sent handled
    for host in "${TABLE_HOSTS[@]}"; do
        ((${#host} > width)) && width=${#host}
    done
    printf '%-*s  %12s  %12s  %12s  %s\n' "$width" host "ECN marked" "CNP sent" "CNP handled" "ports with traffic"
    for host in "${TABLE_HOSTS[@]}"; do
        ecn=${ECN_SUM[$host]:-0}
        sent=${SENT_SUM[$host]:-0}
        handled=${HANDLED_SUM[$host]:-0}
        if [[ $ACTIVITY == unknown ]]; then
            ecn=- sent=- handled=-
        fi
        printf '%-*s  %12s  %12s  %12s  %s\n' "$width" "$host" "$ecn" "$sent" "$handled" "${PORT_COUNT[$host]:-0}"
    done
}

cmd_diff() {
    local rc=0 line
    if [[ ! -r $SNAPSHOT ]]; then
        {
            echo "VERDICT: FAIL no snapshot: nplb diag counters snapshot"
            echo "expected: $SNAPSHOT"
        } >&2
        exit 2
    fi
    note "reading the NIC counters of $(hosts_text "$@") and comparing with $SNAPSHOT"
    run_stub counters diff "$SNAPSHOT" "$@"
    ((STUB_RC == 0)) || rc=1
    parse_diff
    table_hosts "$@"
    snapshot_age
    diff_verdict
    note "diff done: ${#DELTAS[@]} counters moved on ${#TABLE_HOSTS[@]} hosts"
    {
        printf '%s\n' "$VERDICT" | wrap
        echo "$AGE_LINE"
        echo
        host_table
        echo
        printf '%s\n' "ports with traffic: $PORTS_TEXT" | wrap
        printf '%s\n' "$PROXY" | wrap
        [[ -z $UNREACH ]] || printf '%s\n' "unreachable, not in this diff: $UNREACH" | wrap
        if ((${#DELTAS[@]} + ${#EXTRA[@]} > 0)) || [[ -n $STUB_ERR ]]; then
            printf '\f\n'
            if ((${#DELTAS[@]} > 0)); then
                echo "raw deltas (host device counter change):"
                printf '%s\n' "${DELTAS[@]}"
            fi
            if ((${#EXTRA[@]} > 0)); then
                echo "other lines from gpu_preflight.sh:"
                for line in "${EXTRA[@]}"; do printf '%s\n' "$line" | wrap; done
            fi
            if [[ -n $STUB_ERR ]]; then
                echo "stderr of gpu_preflight.sh:"
                printf '%s\n' "$STUB_ERR" | wrap
            fi
        fi
    } | nplb_paged diag-counters-diff || rc=1
    exit "$rc"
}

verb=${1:-}
[[ $# -gt 0 ]] && shift
case $verb in
    snapshot) cmd_snapshot "$@" ;;
    diff) cmd_diff "$@" ;;
    *) usage ;;
esac
