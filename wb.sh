#!/usr/bin/env bash
# ib_write_bw between two nodes, no MPI/UCX/Blink: does one socket also cap its cards here?
# Run it on node A, inside the allocation (or give the two node names):
#   salloc -N 2 --ntasks-per-node=8 --nodelist=<a>,<b>
#   bash ~/crab/lab/wb.sh            (or: bash ~/crab/lab/wb.sh <a> <b>)
# Servers run on B, clients on A, started over ssh. Each port is pinned to a core of its card's
# socket (the same cores as the MPI runs) and connects to the same port on B (-R: by its IP).
# Tests, DUR seconds each, ib_write_bw --report_gbits (Gb/s = 10^9 bit/s):
#   T1u  mlx5_0 alone, one direction (A writes to B)
#   T1b  mlx5_0 alone, both directions (-b)
#   T2u  the 4 ports of socket 0 at once, one direction
#   T2b  the 4 ports of socket 0 at once, both directions          (like R4)
#   T3b  4 ports split 2 + 2 over the sockets, both directions     (like R2)
#   T5b  all 8 ports, both directions                              (like R3)
#   T4b  T2b with 2 MiB messages
# Screen: "BW average" per port as ib_write_bw reports it, and the sum. Full output in
# $OUT/wb_<time>.log. Env: SIZE (134217728), DUR (10), OUT, SSH (ssh), TESTS (all).
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SIZE=${SIZE:-134217728}
DUR=${DUR:-10}
OUT=${OUT:-$HERE/out}
SSH=${SSH:-ssh}
TESTS=${TESTS:-T1u T1b T2u T2b T3b T5b T4b}

die() { echo "wb: $*" >&2; exit 2; }
if (($# == 2)); then
    A=$1 B=$2
else
    [[ -n ${SLURM_JOB_NODELIST:-} ]] || die "run it inside an allocation of two nodes, or: wb.sh <a> <b>"
    mapfile -t NODES < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
    ((${#NODES[@]} == 2)) || die "needs exactly 2 nodes, the allocation has ${#NODES[@]}"
    A=${NODES[0]} B=${NODES[1]}
fi
mkdir -p "$OUT" || die "cannot create $OUT"
LOG=$OUT/wb_$(date +%Y%m%d_%H%M%S).log
TMP=$(mktemp -d) || die "mktemp failed"
trap 'rm -rf "$TMP"' EXIT
say() { echo "$*" | tee -a "$LOG"; }
log() { cat >>"$LOG"; }

# port -> core of its socket, as in the MPI runs
declare -A CORE=([mlx5_0]=0 [mlx5_2]=1 [mlx5_4]=2 [mlx5_8]=3 [mlx5_10]=40 [mlx5_12]=41 [mlx5_14]=42 [mlx5_16]=43)
SOCKET0="mlx5_0 mlx5_2 mlx5_4 mlx5_8"
SPLIT="mlx5_0 mlx5_4 mlx5_10 mlx5_14"
ALL="mlx5_0 mlx5_2 mlx5_4 mlx5_8 mlx5_10 mlx5_12 mlx5_14 mlx5_16"

# the IPv4 address of each port on B (the clients connect to it)
declare -A IP
$SSH "$B" 'for d in '"$ALL"'; do nd=$(ls "${LAB_SYSFS:-/sys}/class/infiniband/$d/device/net" 2>/dev/null | head -1)
    echo "$d $(ip -4 -o addr show dev "$nd" 2>/dev/null | awk "{print \$4; exit}" | cut -d/ -f1)"; done' \
    >"$TMP/ips" 2>&1
log <"$TMP/ips"
while read -r d ip; do IP[$d]=$ip; done <"$TMP/ips"

say "wb A=$A (clients) B=$B (servers) $(ib_write_bw --version 2>&1 | head -1) | log: $LOG"
say "ports on B: $(for d in $ALL; do printf '%s=%s ' "${d#mlx5_}" "${IP[$d]:-?}"; done)"
say "BW average per port in Gb/s as ib_write_bw reports it (with -b check T1b vs T1u: likely both directions added)"

# run_test LABEL SIZE BIDIR(0|1) PORT...
run_test() {
    local label=$1 size=$2 bi=$3; shift 3
    local bflag="" scmd="" ccmd="" d p row sum spid
    ((bi)) && bflag=-b
    for d in "$@"; do
        p=$((18500 + ${d#mlx5_}))
        scmd+="taskset -c ${CORE[$d]} ib_write_bw -d $d -R $bflag -s $size -D $DUR --report_gbits -p $p >/tmp/wb_s_$d.txt 2>&1 & "
        ccmd+="taskset -c ${CORE[$d]} ib_write_bw -d $d -R $bflag -s $size -D $DUR --report_gbits -p $p ${IP[$d]:-missing} >/tmp/wb_c_$d.txt 2>&1 & "
    done
    timeout $((DUR + 60)) $SSH "$B" "$scmd wait" >/dev/null 2>&1 &
    spid=$!
    sleep 3
    timeout $((DUR + 60)) $SSH "$A" "$ccmd wait" >/dev/null 2>&1
    wait "$spid"
    $SSH "$B" "pkill -x ib_write_bw" >/dev/null 2>&1
    $SSH "$A" "for d in $*; do echo \"== client \$d\"; cat /tmp/wb_c_\$d.txt; done" >"$TMP/c" 2>&1
    $SSH "$B" "for d in $*; do echo \"== server \$d\"; cat /tmp/wb_s_\$d.txt; done" >"$TMP/s" 2>&1
    { echo "#### $label size $size bidir $bi ports $*"; cat "$TMP/c" "$TMP/s"; } | log
    row="" sum=0
    for d in "$@"; do
        v=$(awk -v d="$d" -v s="$size" '$1 == "==" && $2 == "client" {on = ($3 == d)}
            on && $1 == s && NF >= 5 {print $4; exit}' "$TMP/c")
        if [[ -z $v ]]; then
            v=$(awk -v d="$d" '$1 == "==" {on = ($3 == d)} on && /[Ee]rr|[Ff]ail|[Uu]nable|[Cc]ouldn/ {print "FAIL"; exit}' "$TMP/c")
            v=${v:-?}
        else
            sum=$(awk -v a="$sum" -v b="$v" 'BEGIN {print a + b}')
        fi
        row+=$(printf '%s:%-7s ' "${d#mlx5_}" "$v")
    done
    say "$(printf '%-34s' "$label") $row| sum $sum"
}

for t in $TESTS; do
    case $t in
    T1u) run_test "T1u mlx5_0 alone, one direction" "$SIZE" 0 mlx5_0 ;;
    T1b) run_test "T1b mlx5_0 alone, both directions" "$SIZE" 1 mlx5_0 ;;
    T2u) run_test "T2u socket 0 x4, one direction" "$SIZE" 0 $SOCKET0 ;;
    T2b) run_test "T2b socket 0 x4, both directions" "$SIZE" 1 $SOCKET0 ;;
    T3b) run_test "T3b 2+2 sockets, both directions" "$SIZE" 1 $SPLIT ;;
    T5b) run_test "T5b all 8, both directions" "$SIZE" 1 $ALL ;;
    T4b) run_test "T4b socket 0 x4, both dir, 2 MiB" 2097152 1 $SOCKET0 ;;
    *) say "unknown test $t" ;;
    esac
done
say "done: $LOG"
