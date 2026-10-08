"""Parsing a preset's `hosts` list and the text of a hostfile into `Host` values.

Pure: nothing here reads a file or the environment. Every malformed input raises a
`ValueError` whose message starts with where the problem is.
"""

from __future__ import annotations

from dataclasses import dataclass

LOCALHOST = "localhost"
"""The only name that may omit its core count (matched exactly)."""


@dataclass(frozen=True)
class Host:
    """A host and its core count; `cores` is None only for `localhost` given without one."""

    name: str
    cores: int | None


def _parse_cores(text: str, where: str) -> int:
    if not (text.isascii() and text.isdigit()) or int(text) < 1:
        raise ValueError(f"{where}: the core count {text!r} must be a positive integer.")
    return int(text)


def _make_host(name: str, cores_text: str | None, where: str) -> Host:
    """Validate a name and an optional core count and build the `Host`."""
    if not name:
        raise ValueError(f"{where}: the host name is empty.")
    if any(ch.isspace() for ch in name):
        raise ValueError(f"{where}: the host name {name!r} must not contain whitespace.")
    if cores_text is None:
        if name != LOCALHOST:
            raise ValueError(f"{where}: {name!r} needs a core count, as 'name:cores'.")
        return Host(name, None)
    return Host(name, _parse_cores(cores_text, where))


def _split_name_cores(word: str, where: str) -> tuple[str, str | None]:
    """Split 'name' or 'name:cores'; the core part stays text for `_make_host`."""
    parts = word.split(":")
    if len(parts) > 2:
        raise ValueError(f"{where}: {word!r} must be 'name' or 'name:cores'.")
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]


def _reject_duplicate(host: Host, seen: set[str], where: str) -> None:
    if host.name in seen:
        raise ValueError(f"{where}: duplicate host {host.name!r}.")
    seen.add(host.name)


def parse_hosts(entries: object, where: str = "hosts") -> tuple[Host, ...]:
    """Parse a list of 'name' or 'name:cores' strings.

    `entries` is raw JSON, so it is checked here. `where` prefixes every error message.

    Raises:
        ValueError: the input is not a non-empty list of valid, distinct entries.
    """
    if not isinstance(entries, list):
        raise ValueError(f"{where}: must be a list of strings.")
    if not entries:
        raise ValueError(f"{where}: the list is empty.")
    hosts: list[Host] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        here = f"{where}[{index}]"
        if not isinstance(entry, str):
            raise ValueError(f"{here}: must be a string.")
        word = entry.strip()
        if not word:
            raise ValueError(f"{here}: the entry is empty.")
        name, cores_text = _split_name_cores(word, here)
        host = _make_host(name, cores_text, here)
        _reject_duplicate(host, seen, here)
        hosts.append(host)
    return tuple(hosts)


def parse_hostfile(text: str, source: str) -> tuple[Host, ...]:
    """Parse hostfile text: one host per line as 'h', 'h:N' or 'h slots=N'.

    `#` starts a comment; blank lines are skipped. `source` names the file in error messages,
    which also give the 1-based line number.

    Raises:
        ValueError: a line is malformed, a host repeats, or the file has no hosts.
    """
    hosts: list[Host] = []
    seen: set[str] = set()
    for number, raw in enumerate(text.splitlines(), start=1):
        words = raw.split("#", 1)[0].split()
        if not words:
            continue
        here = f"{source} line {number}"
        name, cores_text = _split_name_cores(words[0], here)
        if len(words) > 1:
            slots = words[1]
            if len(words) > 2 or not slots.startswith("slots="):
                extra = slots if not slots.startswith("slots=") else words[2]
                raise ValueError(f"{here}: unknown token {extra!r}; expected 'slots=N'.")
            if cores_text is not None:
                raise ValueError(f"{here}: give the core count as 'name:N' or 'slots=N', not both.")
            cores_text = slots[len("slots=") :]
        host = _make_host(name, cores_text, here)
        _reject_duplicate(host, seen, here)
        hosts.append(host)
    if not hosts:
        raise ValueError(f"{source}: no hosts.")
    return tuple(hosts)
