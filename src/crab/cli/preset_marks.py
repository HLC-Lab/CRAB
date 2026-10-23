"""Required marks in presets: values the user must supply, written `<YOUR_NAME>`.

A mark may sit in an env value, in a `--name=value` flag item (sbatch or launcher) or in a launcher
`command`, so a user's override file can patch it (ADR-041). `unset_marks` and `override_snippet` read
an effective preset; `check_file_marks` reads one file's preset body and refuses a mark no patch can
reach. All three skip values of the wrong type: `from_preset` reports those.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

MARK = re.compile(r"<YOUR_[A-Z0-9_]+>")

KEYED_ITEM = re.compile(r"--[^=\s]+=")
"""A `--name=value` flag item: the only kind an override can match by name."""


def _has_mark(value: Any) -> bool:
    return isinstance(value, str) and MARK.search(value) is not None


def _marked_flags(flags: Any) -> list[str]:
    """The items of a flag list that hold a mark."""
    if not isinstance(flags, list):
        return []
    return [item for item in flags if _has_mark(item)]


def _flag_label(item: str) -> str:
    """The option name of a keyed item (`--account`); an item that is not keyed is its own label."""
    match = KEYED_ITEM.match(item)
    return match.group(0)[:-1] if match is not None else item


def _marked_launchers(preset: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """kind -> the marked parts of that launcher: `command` and the marked `flags`."""
    launchers = preset.get("launchers")
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(launchers, Mapping):
        return result
    for kind, body in launchers.items():
        if not isinstance(body, Mapping):
            continue
        marked: dict[str, Any] = {}
        if _has_mark(body.get("command")):
            marked["command"] = body["command"]
        flags = _marked_flags(body.get("flags"))
        if flags:
            marked["flags"] = flags
        if marked:
            result[kind] = marked
    return result


def _marked_env(preset: Mapping[str, Any]) -> dict[str, str]:
    env = preset.get("env")
    if not isinstance(env, Mapping):
        return {}
    return {key: value for key, value in env.items() if _has_mark(value)}


def unset_marks(preset: Mapping[str, Any]) -> list[str]:
    """What still holds a mark, in order env, sbatch, launchers; each label once.

    A label is an env key (`UCX_DIR`), a flag's option name (`--account`) or `launchers.<kind>.command`.
    """
    labels = list(_marked_env(preset))
    labels += [_flag_label(item) for item in _marked_flags(preset.get("sbatch"))]
    for kind, marked in _marked_launchers(preset).items():
        if "command" in marked:
            labels.append(f"launchers.{kind}.command")
        labels += [_flag_label(item) for item in marked.get("flags", [])]
    return list(dict.fromkeys(labels))


def override_snippet(name: str, preset: Mapping[str, Any]) -> dict[str, Any]:
    """`{name: patch}` holding only the marked items of `preset`, in the shape of an override file."""
    patch: dict[str, Any] = {}
    env = _marked_env(preset)
    if env:
        patch["env"] = env
    sbatch = _marked_flags(preset.get("sbatch"))
    if sbatch:
        patch["sbatch"] = sbatch
    launchers = _marked_launchers(preset)
    if launchers:
        patch["launchers"] = launchers
    return {name: patch}


def check_file_marks(path: Path, name: str, body: Mapping[str, Any]) -> None:
    """Refuse a mark in a preset file where an override cannot patch it.

    Raises:
        ValueError: a `header` line holds a mark, or a sbatch or launcher flag item holds one but
            is not `--name=value`; the message names `path` and the preset.
    """
    header = body.get("header")
    if isinstance(header, list) and _marked_flags(header):
        raise ValueError(
            f"{path}: preset {name!r}: header lines cannot be overridden, so they cannot hold a "
            "<YOUR_...> mark; put the value in an env value or a --name=<YOUR_...> flag."
        )
    flag_lists = [body.get("sbatch")]
    launchers = body.get("launchers")
    if isinstance(launchers, Mapping):
        flag_lists += [b.get("flags") for b in launchers.values() if isinstance(b, Mapping)]
    for flags in flag_lists:
        for item in _marked_flags(flags):
            if KEYED_ITEM.match(item) is None:
                raise ValueError(
                    f"{path}: preset {name!r}: {item!r} holds a <YOUR_...> mark but is not a "
                    "--name=value item, which an override cannot patch; write it as "
                    "--name=<YOUR_...>."
                )
