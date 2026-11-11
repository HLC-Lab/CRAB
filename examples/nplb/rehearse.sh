#!/usr/bin/env bash
# The lab flow once on this laptop, against a fake Blink: `nplb campaign --local`, `nplb run` for one row of
# each victim, follow, queue, next, report, compare, skip, pack, and a size check of every page.
#   .crab-web-dev/bin/sandbox examples/nplb/rehearse.sh [--keep] [--help]
# It ends with `REHEARSAL OK` (exit 0) or `REHEARSAL FAILED at <stage>: <reason>` (exit 1) as its last
# stdout line; narration `[HH:MM:SS] stage N/M: text` goes to stderr. Exit 2 on an unknown flag.
#
# Everything lives in a scratch folder `local/rehearsal-<YYYYmmdd-HHMMSS>/` of the checkout (git-ignored):
# the preset `local2` (two hosts, both this machine, scheduler local, launcher mpirun), the fake Blink
# (three launcher scripts named like the Blink programs, all running fake_blink.py), the campaign blocks
# (the planner's own `--local` files, patched there with `binary`, `datapath` and, for the contended
# conditions, NPLB_FAKE_CONTENTION=1 on the victims), the work folder, the saved reports, the data root
# and the state of CRAB's local scheduler (XDG_STATE_HOME), so nothing is written outside it.
# On success the scratch folder is MOVED to local/rehearsal-archive/ unless --keep is given; on failure
# it stays where it is. Nothing is ever deleted and no process is signalled: the local worker of each
# job ends by itself, and the aggressor of fake_blink.py ends when CRAB stops it at the victim's end.
#
# One adapter: `crab run --json` names a local job `local-N`, which `nplb run` (Slurm ids only) refuses;
# the `crab` the kit gets in $CRAB is a two-line script that strips `local-` from the printed job id.
# There is no squeue or scontrol here, so `nplb follow` decides from the job log alone, as it must when
# Slurm forgets a job.
#
# It takes about 2 minutes (3 jobs of about 20 s each). Bash only. `--source-only` defines the
# functions and returns without running anything (the tests use it).
set -u

unset PYTHONPATH SQUEUE SCONTROL PARTITION PAGE_LINES PAGE_COLS NPLB_NOW NPLB_WORK NPLB_YES

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CHECKOUT=$(cd "$HERE/../.." && pwd)
NPLB=$HERE/nplb
STAGES=10
STAGE_NO=0
STAGE=
SCRATCH=
ARCHIVE=$CHECKOUT/local/rehearsal-archive
LOGS=
PAGE_FILES=()
RAN_ROWS=()
KEEP=0

say() { printf '[%s] stage %s/%s: %s\n' "$(date +%H:%M:%S)" "$STAGE_NO" "$STAGES" "$*" >&2; }

# stage NAME TEXT: start a stage; its captured output goes to $LOGS/NAME.txt
stage() {
    STAGE=$1
    STAGE_NO=$((STAGE_NO + 1))
    shift
    [[ -n $LOGS ]] && : >"$LOGS/$STAGE.txt"
    say "$STAGE: $*"
}

usage() {
    cat <<'EOF'
usage: rehearse.sh [--keep] [--help]
  Runs the NPLB lab flow on this laptop with CRAB's local scheduler and a fake Blink, checks every
  page, and ends with REHEARSAL OK (exit 0) or REHEARSAL FAILED at STAGE: REASON (exit 1).
  --keep   leave the scratch folder in local/rehearsal-<time>/ (default: move it to local/rehearsal-archive/)
  Run it inside the sandbox: .crab-web-dev/bin/sandbox examples/nplb/rehearse.sh
EOF
}

# fail REASON: show the end of the stage's output, name the stage, leave the scratch folder in place
fail() {
    if [[ -n $LOGS && -s $LOGS/$STAGE.txt ]]; then
        tail -n 15 "$LOGS/$STAGE.txt" >&2
    fi
    [[ -n $SCRATCH ]] && echo "scratch kept: $SCRATCH" >&2
    echo "REHEARSAL FAILED at $STAGE: $*"
    exit 1
}

# page_ok FILE: a page is 1 to 38 lines of at most 100 columns
page_ok() {
    [[ -s $1 ]] || return 1
    awk 'length>100{bad=1} END{exit NR>38||NR==0||bad}' "$1"
}

# row_field N COLUMN: a field of the last attempt of row N in the run log (COLUMN is 1-based)
row_field() {
    awk -F'\t' -v n="$1" -v c="$2" '$1==n{v=$c} END{print v}' "$WORK/campaign_runs.tsv"
}

# nplbv NAME ARGS...: one verb; stdout in $LOGS/NAME.out, stderr in NAME.err, both appended to the stage log
nplbv() {
    local name=$1 rc
    shift
    "$NPLB" "$@" >"$LOGS/$name.out" 2>"$LOGS/$name.err" </dev/null
    rc=$?
    {
        echo "--- nplb $* (exit $rc)"
        cat "$LOGS/$name.err" "$LOGS/$name.out"
    } >>"$LOGS/$STAGE.txt"
    return "$rc"
}

# expect_text NAME PATTERN REASON: the verb's stdout has a line matching the extended regex
expect_text() {
    grep -Eq -- "$2" "$LOGS/$1.out" || fail "$3"
    grep -q 'Traceback' "$LOGS/$1.out" "$LOGS/$1.err" && fail "$1 printed a traceback"
    return 0
}

# first_unrun_row [SKIP...]: the first schedule row that was not run and is not in SKIP
first_unrun_row() {
    local ran=" ${RAN_ROWS[*]} $* "
    awk -F'\t' -v ran="$ran" 'NR>1 && index(ran, " " $1 " ")==0 {print $1; exit}' "$CAMPAIGN_DIR/schedule.tsv"
}

stage_setup() {
    stage setup "scratch folder, preset local2, fake Blink"
    SCRATCH=$CHECKOUT/local/rehearsal-$(date +%Y%m%d-%H%M%S)
    mkdir -p "$SCRATCH" || fail "cannot make $SCRATCH"
    LOGS=$SCRATCH/log
    mkdir -p "$LOGS" "$SCRATCH/presets" "$SCRATCH/fake" "$SCRATCH/bin" "$SCRATCH/state" "$SCRATCH/data" \
        "$SCRATCH/reports" "$SCRATCH/work" || fail "cannot make the folders under $SCRATCH"
    : >"$LOGS/setup.txt"
    command -v mpirun >/dev/null 2>&1 || fail "no mpirun in PATH (the local preset launches with Open MPI)"
    command -v python3 >/dev/null 2>&1 || fail "no python3 in PATH"
    command -v sha256sum >/dev/null 2>&1 || fail "no sha256sum in PATH"
    local real=$CHECKOUT/.venv/bin/crab
    [[ -x $real ]] || real=$(command -v crab) || fail "no crab: neither $CHECKOUT/.venv/bin/crab nor crab in PATH"

    export XDG_STATE_HOME=$SCRATCH/state
    export CRAB=$SCRATCH/bin/crab
    export CRAB_PATH_PRESETS=$SCRATCH/presets
    export PRESET=local2
    export WORK=$SCRATCH/work CAMPAIGN_DIR=$SCRATCH/campaign NPLB_REPORT_DIR=$SCRATCH/reports
    export DATA_ROOT=$SCRATCH/data/local2

    printf '%s\n' '{"local2": {"scheduler": "local", "launcher": "mpirun", "hosts": ["localhost:6", "127.0.0.1:6"]}}' \
        >"$SCRATCH/presets/local2.json"
    local program
    for program in alltoall_comm_only allgather_comm_only allreduce_b; do
        printf '#!/bin/sh\nexec python3 %q %s "$@"\n' "$HERE/fake_blink.py" "$program" >"$SCRATCH/fake/$program"
        chmod +x "$SCRATCH/fake/$program"
    done
    printf '#!/usr/bin/env bash\n%q "$@" | sed -E '"'"'s/("job_id": ")local-/\\1/'"'"'\nexit "${PIPESTATUS[0]}"\n' \
        "$real" >"$CRAB"
    chmod +x "$CRAB"

    "$SCRATCH/fake/alltoall_comm_only" -msgsize 1024 -iter 3 -warmup 1 -maxsamples 3 >"$LOGS/fake.out" 2>&1 \
        || fail "the fake Blink does not run: $(tail -n 1 "$LOGS/fake.out")"
    grep -q '^Ran 4 iterations. Measured 3 iterations.$' "$LOGS/fake.out" || fail "the fake Blink prints no trailer"
    "$real" presets show local2 >>"$LOGS/setup.txt" 2>&1 || fail "crab does not find the preset local2 in $SCRATCH/presets"
}

stage_campaign() {
    stage campaign "nplb campaign --local, then binary and datapath into the blocks"
    nplbv campaign campaign --local --out "$CAMPAIGN_DIR" || fail "nplb campaign --local exited $?"
    [[ -s $CAMPAIGN_DIR/schedule.tsv ]] || fail "no schedule.tsv in $CAMPAIGN_DIR"
    local rows
    rows=$(awk -F'\t' 'NR>1' "$CAMPAIGN_DIR/schedule.tsv" | wc -l)
    ((rows >= 3)) || fail "schedule.tsv has $rows rows, expected at least 3"
    local file
    while IFS=$'\t' read -r file; do
        [[ -f $CAMPAIGN_DIR/$file ]] || fail "schedule.tsv names $file, which is not in $CAMPAIGN_DIR"
    done < <(awk -F'\t' 'NR>1 {print $NF}' "$CAMPAIGN_DIR/schedule.tsv")
    mkdir -p "$SCRATCH/campaign-generated"
    cp "$CAMPAIGN_DIR"/*.json "$SCRATCH/campaign-generated/" || fail "cannot copy the generated blocks"
    python3 - "$CAMPAIGN_DIR" "$SCRATCH/fake" "$SCRATCH/data" >>"$LOGS/campaign.txt" 2>&1 <<'PY' || fail "patching the blocks failed"
import json
import sys
from pathlib import Path

blocks, fake, data = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
for path in sorted(blocks.glob("*.json")):
    config = json.loads(path.read_text(encoding="utf-8"))
    config["global_options"]["datapath"] = data
    for name, experiment in config["experiments"].items():
        for app in experiment["apps"].values():
            app["binary"] = f"{fake}/{Path(app['path']).stem}"
            if app["collect"] and name != "alone":
                app["env"] = {**app.get("env", {}), "NPLB_FAKE_CONTENTION": "1"}
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print("patched", path.name)
PY
}

stage_run() {
    stage run "one row of each victim, each followed to its end"
    local n status dir csv
    local rows
    rows=$(awk -F'\t' 'NR>1 && !seen[$9]++ {print $1}' "$CAMPAIGN_DIR/schedule.tsv")
    for n in $rows; do
        say "run: nplb run $n (about 20 s)"
        nplbv "run-$n" run "$n" || fail "nplb run $n exited $?"
        RAN_ROWS+=("$n")
        status=$(row_field "$n" 9)
        [[ $status == OK || $status == WARN ]] || fail "row $n ended $status, expected OK or WARN (note: $(row_field "$n" 11))"
        dir=$(row_field "$n" 7)
        [[ -d $dir ]] || fail "row $n: no data folder at $dir"
        [[ -s $dir/schedule_row.json ]] || fail "row $n: no schedule_row.json in $dir"
        csv=$(find "$dir" -name 'data_app_*.csv' -size +0c | head -n 1)
        [[ -n $csv ]] || fail "row $n: no data_app_*.csv in $dir"
        head -n 1 "$csv" | grep -q 'Max-Duration_s' || fail "row $n: $csv has no Max-Duration_s column"
        # CRAB logs these two when an app outlives the grace after SIGTERM and is stopped by force
        grep -Eq 'still running after|still alive after' "$dir/slurm_output.log" \
            && fail "row $n: CRAB had to force-stop an app (the aggressor must end on SIGTERM)"
    done
    ((${#RAN_ROWS[@]} >= 3)) || fail "ran ${#RAN_ROWS[@]} rows, expected one per victim (3)"
}

stage_follow() {
    stage follow "nplb follow on a finished row"
    local n=${RAN_ROWS[0]}
    nplbv follow follow "$n" || fail "nplb follow $n exited $?"
    expect_text follow '^VERDICT: (OK|WARN) row '"$n"' ' "follow $n did not end with an OK or WARN summary"
    PAGE_FILES+=("$LOGS/follow.out")
}

stage_queue() {
    stage queue "queue and next"
    nplbv queue queue || fail "nplb queue exited $?"
    expect_text queue '^VERDICT: ' "nplb queue printed no verdict"
    nplbv next next || fail "nplb next exited $?"
    local want
    want=$(first_unrun_row)
    expect_text next '^VERDICT: NEXT row '"$want"'/' "nplb next does not propose row $want"
    PAGE_FILES+=("$LOGS/queue.out" "$LOGS/next.out")
}

stage_report() {
    stage report "nplb report --all"
    nplbv report report --all || fail "nplb report --all exited $?"
    expect_text report '^VERDICT: OK .*worst slowdown x[0-9.]+' "the report shows no slowdown table"
    local worst
    worst=$(grep -oE 'worst slowdown x[0-9.]+' "$LOGS/report.out" | head -n 1 | sed 's/.*x//')
    awk -v w="$worst" 'BEGIN{exit !(w > 1.0)}' || fail "worst slowdown is x$worst, the contended conditions should be slower than alone"
    PAGE_FILES+=("$LOGS/report.out")
}

stage_compare() {
    stage compare "nplb compare"
    nplbv compare compare || fail "nplb compare exited $?"
    expect_text compare '^VERDICT: OK compare ' "nplb compare printed no OK verdict"
    PAGE_FILES+=("$LOGS/compare.out")
}

stage_skip() {
    stage skip "skip a row, next, follow with no argument, skip --undo"
    local target after last=${RAN_ROWS[${#RAN_ROWS[@]} - 1]}
    target=$(first_unrun_row)
    after=$(first_unrun_row "$target")
    nplbv skip skip "$target" || fail "nplb skip $target exited $?"
    [[ $(row_field "$target" 9) == SKIPPED ]] || fail "row $target is $(row_field "$target" 9) in the run log, expected SKIPPED"
    nplbv next-skipped next || fail "nplb next exited $?"
    expect_text next-skipped '^VERDICT: NEXT row '"$after"'/' "after skipping row $target nplb next does not propose row $after"
    nplbv follow-skipped follow || fail "nplb follow (no argument) exited $?"
    expect_text follow-skipped '^VERDICT: (OK|WARN) row '"$last"' ' "nplb follow with no argument does not follow the last real run (row $last)"
    nplbv unskip skip "$target" --undo || fail "nplb skip $target --undo exited $?"
    [[ $(row_field "$target" 9) == CANCELLED ]] || fail "row $target is $(row_field "$target" 9) after --undo, expected CANCELLED"
    PAGE_FILES+=("$LOGS/next-skipped.out" "$LOGS/follow-skipped.out")
}

stage_pack() {
    stage pack "nplb pack, sha256sum -c, tar listing"
    nplbv pack pack --out "$SCRATCH/pack" || fail "nplb pack exited $?"
    expect_text pack '^VERDICT: OK pack' "nplb pack printed no OK verdict"
    local tarball
    tarball=$(find "$SCRATCH/pack" -name 'nplb-pack_*.tar.gz' | head -n 1)
    [[ -f $tarball && -f $tarball.sha256 ]] || fail "no tarball with its .sha256 in $SCRATCH/pack"
    (cd "$SCRATCH/pack" && sha256sum -c "$(basename "$tarball").sha256") >>"$LOGS/pack.txt" 2>&1 || fail "sha256sum -c failed"
    tar tzf "$tarball" >"$LOGS/pack-list.txt" 2>>"$LOGS/pack.txt" || fail "tar tzf cannot read the tarball"
    grep -qx 'pack_manifest.txt' "$LOGS/pack-list.txt" || fail "the tarball has no pack_manifest.txt"
    grep -qx 'work/campaign_runs.tsv' "$LOGS/pack-list.txt" || fail "the tarball has no work/campaign_runs.tsv"
    local folders
    folders=$(grep -c '^data/[^/]*/schedule_row.json$' "$LOGS/pack-list.txt")
    ((folders == ${#RAN_ROWS[@]})) || fail "the tarball holds $folders run folders, expected ${#RAN_ROWS[@]}"
    PAGE_FILES+=("$LOGS/pack.out")
}

stage_pages() {
    stage pages "every saved report page and every verb page is at most 38 x 100"
    local checked=0 file name total k
    for file in "${PAGE_FILES[@]}"; do
        page_ok "$file" || fail "the page printed by the verb is not within 38 x 100: $file ($(wc -l <"$file") lines)"
        checked=$((checked + 1))
    done
    for file in "$NPLB_REPORT_DIR"/*_*.txt; do
        [[ -f $file ]] || continue
        name=$(basename "$file" .txt)
        "$NPLB" page 1 "$name" >"$LOGS/page.txt" 2>>"$LOGS/pages.txt" || fail "nplb page 1 $name failed"
        total=$(sed -n '1s/.*| page 1\/\([0-9]*\) |.*/\1/p' "$LOGS/page.txt")
        [[ -n $total ]] || fail "cannot read the page count of $name from its header"
        for ((k = 1; k <= total; k++)); do
            "$NPLB" page "$k" "$name" >"$LOGS/page.txt" 2>>"$LOGS/pages.txt" || fail "nplb page $k $name failed"
            page_ok "$LOGS/page.txt" || fail "page $k of $name is not within 38 x 100 ($(wc -l <"$LOGS/page.txt") lines)"
            checked=$((checked + 1))
        done
    done
    ((checked >= 10)) || fail "only $checked pages were checked, expected at least 10"
    say "pages: $checked pages checked"
    PAGES_CHECKED=$checked
}

finish() {
    local where=$SCRATCH
    if ((KEEP == 0)); then
        mkdir -p "$ARCHIVE" || fail "cannot make $ARCHIVE"
        mv "$SCRATCH" "$ARCHIVE/" || fail "cannot move $SCRATCH to $ARCHIVE"
        where=$ARCHIVE/$(basename "$SCRATCH")
    fi
    echo "rows run: ${RAN_ROWS[*]}; pages checked: $PAGES_CHECKED"
    echo "scratch: $where"
    echo "REHEARSAL OK"
}

main() {
    local arg source_only=0
    for arg in "$@"; do
        case $arg in
            --keep) KEEP=1 ;;
            --source-only) source_only=1 ;;
            --help | -h)
                usage
                return 0
                ;;
            *)
                echo "rehearse.sh: unknown flag $arg" >&2
                usage >&2
                return 2
                ;;
        esac
    done
    ((source_only)) && return 0
    PAGES_CHECKED=0
    stage_setup
    stage_campaign
    stage_run
    stage_follow
    stage_queue
    stage_report
    stage_compare
    stage_skip
    stage_pack
    stage_pages
    finish
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    main "$@"
    exit $?
fi
return 0 2>/dev/null || true
