#!/usr/bin/env bash
# Which leaf switch each NPLB node hangs on, and what the placement layouts make of it. Run it through
# the dispatcher (`nplb topo [--from TSV] [HOST...]`) or directly:
#   examples/nplb/topo_tools.sh topo [--from TSV] [HOST...]
# It runs the LLDP probe (topology.sh, which listens for up to $LLDP_WAIT seconds, 40 by default, so
# the run takes about a minute), keeps the probe's own screen in $WORK/topo_probe.log, then builds
# $WORK/node_map.json with topo_map.py: per node the SET of leaf switches heard on its active ports
# (null when none was heard), and for the layouts split-i, split-l and shared which nodes the victim
# and the aggressor get, whether each pair is same-leaf, cross-leaf, partial or unknown, and which
# leaves host both. Steps narrate live on stderr as "[HH:MM:SS] text"; stdout is the report, paged by
# lib/pages.sh and saved whole as topo. Its first content line is "VERDICT: OK|WARN ...".
# With no HOST, topology.sh takes the nodes of $PARTITION. A node with no LLDP gets its leaf from
# NODE_LEAF ("host=leaf,host=leaf") or from the file $WORK/node_leaf.txt ("host leaf" per line, "#"
# comments); the env var wins, and both beat LLDP for the hosts they name.
#   --from TSV   skip the probe: build the map and report from an existing probe TSV (the
#                $WORK/topology_<time>.tsv of an earlier run); the probe is never called.
# Exit: 0 when the map was built (OK or WARN), 1 when the probe failed and no leaf is known, 2 on bad
# usage or a probe that could not run (no nodes, no sinfo, no TSV, unreadable --from file).
# Env: TOPOLOGY_SH (default topology.sh next to this script), WORK (default <checkout>/local/nplb-work),
#      PARTITION (default h100_4nodes), LLDP_WAIT (passed to the probe), NODE_LEAF, NPLB_REPORT_DIR
#      (the pager's folder). Python is <checkout>/.venv/bin/python when it exists, else python3.
set -u

unset PYTHONPATH

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CHECKOUT=$(cd "$HERE/../.." && pwd)
TOPOLOGY_SH=${TOPOLOGY_SH:-$HERE/topology.sh}
WORK=${WORK:-$CHECKOUT/local/nplb-work}
PARTITION=${PARTITION:-h100_4nodes}
export WORK PARTITION
[[ -n ${LLDP_WAIT:-} ]] && export LLDP_WAIT
PY="$CHECKOUT/.venv/bin/python"
[[ -x $PY ]] || PY=python3

# shellcheck source=lib/pages.sh
. "$HERE/lib/pages.sh"

usage() { echo "usage: topo_tools.sh topo [--from TSV] [HOST...]" >&2; }
note() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() {
    echo "topo: $*" >&2
    exit 2
}

# cmd_topo [--from TSV] [HOST...]
cmd_topo() {
    local from="" tsv probe_rc=0 hosts=() hostarg=() map_rc report
    while (($# > 0)); do
        case $1 in
            --from)
                (($# >= 2)) || {
                    usage
                    exit 2
                }
                from=$2
                shift 2
                ;;
            -*)
                usage
                exit 2
                ;;
            *)
                hosts+=("$1")
                shift
                ;;
        esac
    done

    if [[ -n $from ]]; then
        [[ -f $from ]] || die "no such file: $from"
        tsv=$from
        note "using the probe TSV $tsv (no probe)"
    else
        mkdir -p "$WORK" || die "cannot create $WORK"
        note "probing ${hosts[*]:-the nodes of $PARTITION}: waiting up to ${LLDP_WAIT:-40}s for LLDP"
        bash "$TOPOLOGY_SH" "${hosts[@]}" >"$WORK/topo_probe.log" 2>&1
        probe_rc=$?
        note "probe finished with exit $probe_rc (its screen: $WORK/topo_probe.log)"
        if ((probe_rc > 1)); then
            tail -n 5 "$WORK/topo_probe.log" >&2
            die "the probe could not run (exit $probe_rc)"
        fi
        tsv=$(printf '%s\n' "$WORK"/topology_*.tsv | sort | tail -n 1)
        [[ -f $tsv ]] || die "the probe wrote no topology_*.tsv under $WORK"
    fi

    if ((${#hosts[@]} > 0)); then
        hostarg=(--hosts "$(IFS=,; echo "${hosts[*]}")")
    fi
    note "building the node map from $tsv"
    report=$(mktemp) || die "cannot create a temporary file"
    "$PY" "$HERE/topo_map.py" build --tsv "$tsv" --probe-rc "$probe_rc" "${hostarg[@]}" >"$report"
    map_rc=$?
    if ((map_rc == 2)); then
        rm -f "$report"
        exit 2
    fi
    nplb_paged topo <"$report"
    rm -f "$report"
    exit "$map_rc"
}

verb=${1:-}
[[ $# -gt 0 ]] && shift
case $verb in
    topo) cmd_topo "$@" ;;
    *)
        usage
        exit 2
        ;;
esac
