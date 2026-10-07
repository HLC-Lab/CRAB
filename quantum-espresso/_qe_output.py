"""Shared parsing for Quantum ESPRESSO output: the program's total wall time.

At the end of a run QE prints its first clock (labelled with the program name, `PWSCF` or
`PHONON`) as a total line, then `JOB DONE.`. The wall part of that line is written by
`print_this_clock` in UtilXlib/clocks_handler.f90 (same in 6.x and 7.x) in one of these forms:

    (4X,F5.2,"s WALL")               under a minute       "    12.34s WALL"
    (1X,I2,"m",F5.2,"s WALL")         under an hour        "  1m23.45s WALL", "  2m 3.05s WALL"
    (4X,I2,"h",I2,"m WALL")           under a day          "     1h 2m WALL"  (no seconds)
    (1X,I2,"d",I2,"h",I2,"m WALL")    a day or more        "  1d 3h 5m WALL"  (no seconds)
    (F9.2,"s WALL")                   built with __CLOCK_SECONDS

ph.x prints the PHONON total several times while it runs; the last one is the final total.
"""

import re

_TOTAL_LINE = r"^[ \t]*{label}[ \t]*:[ \t]*(?P<cpu>\S.*?)[ \t]+CPU[ \t]+(?P<wall>\S.*?)[ \t]+WALL[ \t]*$"
_DURATION = re.compile(
    r"^(?:(?P<d>\d+)d)?\s*(?:(?P<h>\d+)h)?\s*(?:(?P<m>\d+)m)?\s*(?:(?P<s>\d+(?:\.\d+)?)s)?$"
)


def _seconds(text):
    match = _DURATION.match(text.strip())
    if not match or not any(match.groupdict().values()):
        raise ValueError(f"cannot read the QE wall time {text.strip()!r}")
    d, h, m, s = (match.group(k) for k in ("d", "h", "m", "s"))
    return int(d or 0) * 86400 + int(h or 0) * 3600 + int(m or 0) * 60 + float(s or 0)


def read_wall_time(stdout, label):
    """The final `label` total wall time in `stdout`, in seconds, as one row.

    Raises:
        ValueError: if the run did not reach `JOB DONE.`, or no readable total line exists.
    """
    if "JOB DONE." not in stdout:
        raise ValueError(f"no 'JOB DONE.' in the output: the {label} run did not finish")
    totals = re.findall(_TOTAL_LINE.format(label=re.escape(label)), stdout, re.MULTILINE)
    if not totals:
        raise ValueError(f"no {label} total timing line ('{label} : ... CPU ... WALL') in the output")
    _cpu, wall = totals[-1]
    return [{"wall_time": _seconds(wall)}]
