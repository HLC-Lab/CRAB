#!/usr/bin/env bash
# The first look at the lab, in one command (type it after `cd <checkout> && git pull`):
#   bash examples/nplb/lab_start.sh
# It checks the checkout (branch nplb, no local changes), brings it up to date (git fetch, then
# git pull --ff-only when behind), and runs the read-only checks `nplb build check` and
# `nplb node check`, each printing its own page on stdout and pausing for Enter between pages when stdin
# is a terminal. A failing check is remembered, not fatal. It never switches branches and never fixes
# anything: a wrong branch or a modified tracked file stops it at the summary.
# The last page is the summary: a VERDICT line first, one line per step, and, when all is OK, the two
# `next:` lines the person types (env.sh must be sourced in their own shell; a script cannot do that).
# Narration `[HH:MM:SS] step N/6: text` goes to stderr, the pages to stdout. Exit 0 when all OK, else 1.
# Env: NPLB_BIN (the dispatcher, default examples/nplb/nplb in the checkout; tests set it to a stub).
# Bash only.
set -u

CHECKOUT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
BIN=${NPLB_BIN:-examples/nplb/nplb}
ROWS=()
FIRST_FAIL=
STOP=0

say() { printf '[%s] step %s/6: %s\n' "$(date +%H:%M:%S)" "$1" "$2" >&2; }
# row STATUS TEXT: one summary line, cut to 100 columns; the first FAIL names the verdict
row() {
    ROWS+=("$(printf '%-4s %s' "$1" "$2" | cut -c1-100)")
    if [[ $1 == FAIL && -z $FIRST_FAIL ]]; then FIRST_FAIL=$2; fi
}
pause() { if [[ -t 0 ]]; then read -r -p "Enter for the next page" _ || true; fi; }

say 1 "go to the checkout $CHECKOUT"
cd "$CHECKOUT" || { echo "FAIL cannot cd to $CHECKOUT"; exit 1; }
# shellcheck disable=SC1091
[[ -f local/nplb_env.sh ]] && . local/nplb_env.sh
unset PYTHONPATH
export GIT_TERMINAL_PROMPT=0

say 2 "branch, commit and tracked changes"
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>&1 | head -n 1)
echo "branch: $BRANCH"
echo "HEAD:   $(git rev-parse --short HEAD 2>/dev/null)"
git status -s --untracked-files=no | head -n 5

say 3 "git fetch origin nplb (60 s limit)"
if FETCH=$(timeout 60 git fetch origin nplb 2>&1); then
    row OK "fetch origin nplb"
else
    STOP=1
    MSG="fetch: $(printf '%s\n' "$FETCH" | head -n 1)"
    echo "FAIL $MSG; if it hangs, the proxy is not set: check local/nplb_env.sh"
    row FAIL "$MSG"
fi

if [[ $STOP -eq 0 ]]; then
    say 4 "branch, local changes, pull"
    CHANGED=$(git status -s --untracked-files=no)
    if [[ $BRANCH != nplb ]]; then
        STOP=1
        echo "FAIL branch is $BRANCH, not nplb: run git checkout nplb"
        row FAIL "branch is $BRANCH, not nplb: run git checkout nplb"
    elif [[ -n $CHANGED ]]; then
        STOP=1
        printf '%s\n' "$CHANGED" | head -n 12
        echo "FAIL local changes: send me this screen"
        row FAIL "local changes ($(printf '%s\n' "$CHANGED" | wc -l) tracked files): send me this screen"
    else
        row OK "branch nplb, no local changes"
        BEHIND=$(git rev-list --count HEAD..origin/nplb)
        AHEAD=$(git rev-list --count origin/nplb..HEAD)
        if [[ $BEHIND -gt 0 ]]; then
            if git pull --ff-only origin nplb >/dev/null 2>&1; then
                echo "pull: brought $BEHIND commits"
                row OK "pull: brought $BEHIND commits"
            else
                STOP=1
                echo "FAIL pull: not a fast-forward (ahead $AHEAD, behind $BEHIND): send me this screen"
                row FAIL "pull: not a fast-forward (ahead $AHEAD, behind $BEHIND)"
            fi
        elif [[ $AHEAD -gt 0 ]]; then
            echo "ahead of origin/nplb by $AHEAD commits (nothing to pull)"
            row WARN "ahead of origin/nplb by $AHEAD commits, nothing to pull"
        else
            echo "level with origin/nplb: nothing to pull"
            row OK "level with origin/nplb"
        fi
    fi
fi

if [[ $STOP -eq 0 ]]; then
    say 5 "nplb build check, node check"
    for verb in "build check" "node check"; do
        # shellcheck disable=SC2086  # the verb is one or two words on purpose
        "$BIN" $verb
        code=$?
        if [[ $code -eq 0 ]]; then row OK "$verb"; else row FAIL "$verb failed (exit $code)"; fi
        pause
    done
fi

say 6 "summary"
SHORT=$(git rev-parse --short HEAD 2>/dev/null)
if [[ -z $FIRST_FAIL ]]; then
    echo "VERDICT: OK lab_start: branch nplb at $SHORT, build check/node check all OK"
else
    echo "VERDICT: FAIL lab_start: $FIRST_FAIL" | cut -c1-100
fi
printf '%s\n' "${ROWS[@]}"
if [[ -n $FIRST_FAIL ]]; then exit 1; fi
echo "next: . examples/nplb/env.sh"
echo "next: nplb build blink"
