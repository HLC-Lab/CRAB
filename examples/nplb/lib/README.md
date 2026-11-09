# NPLB shell helpers

Helpers that the scripts in [`examples/nplb`](../README.md) source. They are not run on their own.

| File | What it does |
|---|---|
| [`pages.sh`](pages.sh) | Bash wrapper around the report pager [`pages.py`](../pages.py). `nplb_paged NAME` reads a report on stdin, saves it whole and prints page 1 (at most 38 lines x 100 columns); `nplb_page [N\|next\|prev] [REPORT]` replays a page. It runs the pager with the checkout's `.venv/bin/python` when that exists, else `python3`. |

Source it with `. examples/nplb/lib/pages.sh`. It leaves the caller's working folder and shell
options alone, and every name it defines starts with `nplb_page` or `_nplb_pages_`.
