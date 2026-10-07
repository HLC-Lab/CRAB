"""Shared parsing for the Ember MPI motifs: rank 0 ends its output with one result line."""


def read_result_row(stdout, metadata):
    """The last non-comment line of `stdout` as one row: its first fields, in `metadata` order.

    Raises:
        ValueError: if there is no result line or it has too few fields.
    """
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines or lines[-1].lstrip().startswith("#"):
        raise ValueError("no result line after the header (the run did not finish?)")
    fields = lines[-1].split()
    names = [m["name"] for m in metadata]
    if len(fields) < len(names):
        raise ValueError(f"result line has {len(fields)} fields, expected {len(names)}: {lines[-1]!r}")
    return [dict(zip(names, (float(f) for f in fields[: len(names)])))]
