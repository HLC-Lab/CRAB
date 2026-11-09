# Session setup for the NPLB bring-up on a cluster console. Source it (do not run it):
#   . examples/nplb/env.sh
# It puts the newest Open MPI's bin directory first in PATH (once, however often you source it),
# moves to the checkout, loads <checkout>/local/nplb_env.sh if it exists (site settings such as
# proxy exports; local/ is git-ignored), unsets PYTHONPATH (the lab's points at an old system
# argparse that breaks the jobs), warns when the checkout venv cannot import pandas (fix: nplb
# fix-venv), defines the `nplb` command (examples/nplb/nplb) and exports NPLB_HOME, sets BLINK to
# the directory of the Blink receipt, and prints what it found. A missing piece is a warning
# line, never an exit: this file is sourced.
# Env: NPLB_MPI_ROOT (where the openmpi-* directories live, default /usr/mpi/gcc).
# Bash only.

_nplb_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
_nplb_checkout=$(cd "$_nplb_dir/../.." && pwd)

# newest openmpi-*/bin first in PATH, without duplicates
_nplb_mpi=$(
    for _d in "${NPLB_MPI_ROOT:-/usr/mpi/gcc}"/openmpi-*/bin; do
        [ -d "$_d" ] && echo "$_d"
    done | sort -V | tail -n 1
)
if [ -n "$_nplb_mpi" ]; then
    _nplb_rest=$(printf '%s' "${PATH:-}" | tr ':' '\n' | grep -vxF -e "$_nplb_mpi" | paste -sd: -)
    PATH="$_nplb_mpi${_nplb_rest:+:$_nplb_rest}"
    export PATH
fi

cd "$_nplb_checkout" || echo "warning: cannot cd to $_nplb_checkout"

# shellcheck disable=SC1091
[ -f "$_nplb_checkout/local/nplb_env.sh" ] && . "$_nplb_checkout/local/nplb_env.sh"

# PYTHONPATH off, after the site file (which may export one): the lab shell's points at an old
# system argparse that sbatch copies into every job. Reported below, with the other output lines.
_nplb_pp_was=""
if [ -n "${PYTHONPATH:-}" ]; then
    _nplb_pp_was=$PYTHONPATH
fi
unset PYTHONPATH

_nplb_pandas=ok
"$_nplb_checkout/.venv/bin/python" -c 'import pandas' >/dev/null 2>&1 || _nplb_pandas=missing

# the nplb command: the dispatcher that lives next to this file
NPLB_HOME=$_nplb_dir
export NPLB_HOME
nplb() { "$NPLB_HOME/nplb" "$@"; }

_nplb_blink=$(
    "$_nplb_checkout/.venv/bin/python" -c \
        "import crab.setup.memory as m; print(m.get_receipt('blink')['binary_path'])" 2>/dev/null
)
if [ -n "$_nplb_blink" ]; then
    export BLINK="$_nplb_blink"
fi

if [ -n "$_nplb_pp_was" ]; then
    echo "PYTHONPATH: unset (was $_nplb_pp_was)"
fi
if [ "$_nplb_pandas" != ok ]; then
    echo "warning: the venv cannot import pandas, run: nplb fix-venv"
fi
if _nplb_cmd=$(command -v mpirun); then
    echo "mpirun: $_nplb_cmd"
else
    echo "warning: no mpirun in PATH (no openmpi-*/bin under ${NPLB_MPI_ROOT:-/usr/mpi/gcc})"
fi
if [ -n "${BLINK:-}" ] && [ -d "$BLINK" ]; then
    echo "blink: $BLINK"
else
    echo "warning: no Blink directory (receipt 'blink' missing or its binary_path is not a directory)"
fi

unset _nplb_dir _nplb_checkout _nplb_mpi _nplb_rest _nplb_blink _nplb_cmd _nplb_pp_was _nplb_pandas _d
return 0 2>/dev/null || true
