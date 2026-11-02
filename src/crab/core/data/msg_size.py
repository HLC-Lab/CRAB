"""An app's message size, read from its `-msgsize` argument, or the wrapper's declared default when
the argument is absent. The CSV `msg_size` column holds it."""


def msg_size_of(args: str, default: int = 0) -> int:
    """Return the integer after `-msgsize` in an app's argument string.

    Args:
        args: the app's argument string, split on whitespace.
        default: the size returned when the args hold no `-msgsize` token.

    Returns:
        The value of the first `-msgsize` token's successor. `default` when the flag is absent; 0
        when the flag is last or followed by something `int()` rejects (the program itself aborts
        on such a value).
    """
    tokens = str(args).split()
    if "-msgsize" not in tokens:
        return default
    try:
        return int(tokens[tokens.index("-msgsize") + 1])
    except (ValueError, IndexError):
        return 0
