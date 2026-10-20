#!/usr/bin/env bash
# Install a built wheel into a clean environment and run CRAB from it, away from the checkout.
#
# Usage: scripts/wheel_smoke.sh <wheel>
#
# Installs `<wheel>[web]` into a fresh venv in a temp dir, then, from an empty directory with
# CRAB_HOME, the user presets file and the wrappers path pointed at temp dirs, checks that:
#   - scripts/check_wheel.py accepts the wheel,
#   - `crab info --json` reports a crab_root under CRAB_HOME,
#   - `crab validate` and `crab run` work on scripts/wheel_smoke/smoke.json (local scheduler,
#     direct launcher) and the run leaves data_app_0.csv under CRAB_HOME,
#   - `crab web --help` works,
#   - nothing was written into the venv's site-packages or the repo by running CRAB.
# It prints "wheel smoke: ok" and exits 0, or names the failed check and exits 1.
#
# Environment:
#   KEEP=1   keep the temp dir (its path is printed) instead of deleting it on exit.
#   PIPX=1   install with `pipx install` instead of a venv, when pipx exists.
#   USE_PIP=1  create the venv with `python -m venv` and pip even when uv exists (CI does this).
#   TIMEOUT  seconds to wait for the job's results (default 60).
set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "usage: $0 <wheel>" >&2
    exit 2
fi

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
wheel="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
timeout_s="${TIMEOUT:-60}"
if [ ! -f "$wheel" ]; then
    echo "wheel smoke: FAILED (no such wheel: $wheel)" >&2
    exit 1
fi

tmp="$(mktemp -d)"
cleanup() {
    if [ "${KEEP:-0}" = "1" ]; then
        echo "wheel smoke: kept $tmp"
    else
        rm -rf "$tmp"
    fi
}
trap cleanup EXIT

fail() {
    echo "wheel smoke: FAILED at $1${2:+ ($2)}" >&2
    exit 1
}

# --- install -----------------------------------------------------------------------------------

if [ "${PIPX:-0}" = "1" ] && command -v pipx > /dev/null; then
    export PIPX_HOME="$tmp/pipx" PIPX_BIN_DIR="$tmp/bin"
    pipx install "$wheel[web]" || fail "install" "pipx install"
    venv="$PIPX_HOME/venvs/crab-hpc"
    crab="$PIPX_BIN_DIR/crab"
else
    venv="$tmp/venv"
    if command -v uv > /dev/null && [ "${USE_PIP:-0}" != "1" ]; then
        uv venv --quiet "$venv" || fail "install" "uv venv"
        uv pip install --quiet --python "$venv/bin/python" "$wheel[web]" || fail "install" "uv pip install"
    else
        python3 -m venv "$venv" || fail "install" "python -m venv"
        "$venv/bin/python" -m pip install --quiet "$wheel[web]" || fail "install" "pip install"
    fi
    crab="$venv/bin/crab"
fi
py="$venv/bin/python"
[ -x "$crab" ] || fail "install" "no crab command at $crab"

# --- the wheel's own member list ---------------------------------------------------------------

"$py" "$repo/scripts/check_wheel.py" "$wheel" || fail "check_wheel"

# --- baselines for the "nothing was written" check ---------------------------------------------

site="$("$py" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
list_site() { (cd "$site" && find . -not -name '__pycache__' -not -name '*.pyc' | LC_ALL=C sort); }
list_site > "$tmp/site.before"
git_status() { if git -C "$repo" rev-parse --git-dir > /dev/null 2>&1; then git -C "$repo" status --porcelain; fi; }
git_status > "$tmp/git.before"

# --- run CRAB from an empty directory with a private home ---------------------------------------

home="$tmp/home"
mkdir -p "$home" "$tmp/cwd"
# The preset repeats the wrappers path in its own env: the shipped `_common` env sets
# CRAB_PATH_WRAPPERS=__CWD__/wrappers, and that value reaches the job instead of the variable.
cat > "$tmp/p.json" << JSON
{
    "smoke": {
        "description": "Wheel smoke test: local scheduler, direct launcher",
        "scheduler": "local",
        "launcher": "direct",
        "env": {"CRAB_PATH_WRAPPERS": "$repo/tests/fixtures/wrappers"}
    }
}
JSON

# A subshell-free way to run crab with the smoke environment. XDG_STATE_HOME keeps the local
# scheduler's job records out of the real user state.
run_crab() {
    (
        cd "$tmp/cwd"
        unset VIRTUAL_ENV PYTHONPATH
        export CRAB_HOME="$home" CRAB_PATH_PRESETS="$tmp/p.json"
        export CRAB_PATH_WRAPPERS="$repo/tests/fixtures/wrappers" XDG_STATE_HOME="$tmp/state"
        "$crab" "$@"
    )
}
config="$repo/scripts/wheel_smoke/smoke.json"

run_crab info --json > "$tmp/info.json" || fail "crab info --json"
"$py" - "$tmp/info.json" "$home" << 'PY' || fail "crab info --json" "crab_root is not under CRAB_HOME, or a bad layout"
import json
import sys
from pathlib import Path

info = json.load(open(sys.argv[1]))
home = Path(sys.argv[2]).resolve()
root = Path(info["crab_root"]).resolve()
assert root == home or home in root.parents, f"crab_root {root} is not under {home}"
# `layout` is optional for now; when present it must say installed.
assert info.get("layout", "installed") == "installed", f"layout is {info['layout']!r}"
PY

run_crab validate "$config" -p smoke || fail "crab validate"

run_crab run "$config" -p smoke || fail "crab run"

# The local scheduler runs the job in the background. Wait until its supervisor wrote
# local_exit_code (the job is over, so no child outlives this script), then look for the results.
deadline=$((SECONDS + timeout_s))
has_file() { [ -n "$(find "$home/data" -name "$1" 2> /dev/null | head -n 1)" ]; }
while [ "$SECONDS" -lt "$deadline" ] && ! has_file local_exit_code; do
    sleep 1
done
csv="$(find "$home/data" -name data_app_0.csv 2> /dev/null | head -n 1 || true)"
show_logs() {
    echo "--- job files under $home/data ---" >&2
    find "$home/data" -type f 2> /dev/null | sort >&2 || true
    find "$home/data" -name 'slurm_*.log' 2> /dev/null | sort | while read -r log; do
        echo "--- $log ---" >&2
        cat "$log" >&2
    done
}
if [ -z "$csv" ]; then
    show_logs
    fail "crab run" "no data_app_0.csv under $home/data (waited up to ${timeout_s}s)"
fi

run_crab web --help > /dev/null || fail "crab web --help"

# --- nothing was written outside CRAB_HOME ------------------------------------------------------

list_site > "$tmp/site.after"
if ! diff -u "$tmp/site.before" "$tmp/site.after" > "$tmp/site.diff"; then
    cat "$tmp/site.diff" >&2
    fail "site-packages" "running CRAB changed the venv's site-packages"
fi
for stray in data benchmarks; do
    for dir in "$site/$stray" "$site/crab/$stray"; do
        [ ! -e "$dir" ] || fail "site-packages" "stray $dir"
    done
done
git_status > "$tmp/git.after"
if ! diff -u "$tmp/git.before" "$tmp/git.after" > "$tmp/git.diff"; then
    cat "$tmp/git.diff" >&2
    fail "git status" "running CRAB changed the repo"
fi

echo "wheel smoke: ok"
