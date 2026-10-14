# Session setup for the NPLB bring-up on a cluster console. Source it (do not run it):
#   . examples/nplb/env.sh
# It puts the newest Open MPI's bin directory first in PATH (once, however often you source it),
# moves to the checkout, loads <checkout>/local/nplb_env.sh if it exists (site settings such as
# proxy exports; local/ is git-ignored), sets BLINK to the directory of the Blink receipt, and
# prints what it found. A missing piece is a warning line, never an exit: this file is sourced.
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

_nplb_blink=$(
    "$_nplb_checkout/.venv/bin/python" -c \
        "import crab.setup.memory as m; print(m.get_receipt('blink')['binary_path'])" 2>/dev/null
)
if [ -n "$_nplb_blink" ]; then
    export BLINK="$_nplb_blink"
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

unset _nplb_dir _nplb_checkout _nplb_mpi _nplb_rest _nplb_blink _nplb_cmd _d
return 0 2>/dev/null || true
