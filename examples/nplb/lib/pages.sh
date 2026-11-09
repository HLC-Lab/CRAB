# Bash wrapper around the report pager (examples/nplb/pages.py). Source it, do not run it:
#   . "$(dirname "${BASH_SOURCE[0]}")/lib/pages.sh"
#   some_report | nplb_paged NAME   save the report whole, print page 1 (at most 38 x 100)
#   nplb_page [N|next|prev] [REPORT]  replay a page
# It finds the engine next to itself, runs it with <checkout>/.venv/bin/python when that exists
# (else python3), and leaves the caller's cwd and shell options alone. Every name it defines
# starts with nplb_page or _nplb_pages_.

# _nplb_pages_run ENGINE_ARGS...: run the engine, stdin and exit status pass through.
_nplb_pages_run() {
    local here py
    here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd) || return 1
    py="$here/../../../.venv/bin/python"
    [[ -x $py ]] || py=python3
    "$py" "$here/../pages.py" "$@"
}

nplb_paged() {
    if [[ $# -ne 1 ]]; then
        echo "usage: nplb_paged NAME < report" >&2
        return 2
    fi
    _nplb_pages_run emit "$1"
}

nplb_page() {
    _nplb_pages_run page "$@"
}
