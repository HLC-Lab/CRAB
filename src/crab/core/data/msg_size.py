"""An app's message size, read from its `-msgsize` argument. The CSV `msg_size` column holds it."""


def msg_size_of(args: str) -> int:
    """Return the integer after `-msgsize` in an app's argument string.

    Args:
        args: the app's argument string, split on whitespace.

    Returns:
        The value of the first `-msgsize` token's successor, or 0 when the flag is absent, last, or
        followed by something `int()` rejects.
    """
    tokens = str(args).split()
    if "-msgsize" not in tokens:
        return 0
    try:
        return int(tokens[tokens.index("-msgsize") + 1])
    except (ValueError, IndexError):
        return 0
