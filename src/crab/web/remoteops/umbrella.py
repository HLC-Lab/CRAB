"""Tell an umbrella cluster from a plain CRAB install.

An umbrella install keeps its SbatchMan projects in ``<root>/benchmarks/<name>/`` and the
cluster's configs file in ``<root>/.local/configs.<cluster>.yaml``, where ``<root>`` is the
profile's install directory (``remote_crab``). The dashboard writes a campaign into one of
those project folders so ``sbatchman launch`` needs no hand copy. A plain install has
neither, and the campaign stays in the staging directory.

The probe is a convenience: any failure (not an SSH profile, a command that fails, a
configs file that does not parse) answers "plain" or an empty name list, never an error,
so authoring is not blocked by it.
"""

from __future__ import annotations

import re
import secrets
import shlex
from dataclasses import dataclass, field
from typing import Any

# PyYAML is a dependency of the web extra (pyproject).
import yaml  # type: ignore[import-untyped]

from crab.web.connections.transport import Transport
from crab.web.errors import CrabWebError, InputError, RemoteCommandError
from crab.web.remoteops.crab_cli import output_tail, remote_path_expr
from crab.web.store.profiles import Profile

# A benchmark folder is one path segment: no slash, no `..`, no leading dot.
BENCHMARK_FOLDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")

_SCRIPT = """\
cd {root} 2>/dev/null || exit 2
test -d benchmarks || exit 3
set -- .local/configs.*.yaml
test -f "$1" || exit 4
echo '@@{mark}:root'
pwd
echo '@@{mark}:configs'
echo "$1"
echo '@@{mark}:benchmarks'
for d in benchmarks/*/; do test -d "$d" && basename "$d"; done
{content}exit 0
"""

# The configs file, capped, as the last section. Left out when only the folders are wanted.
_CONTENT = """echo '@@{mark}:content'
head -c 1000000 -- "$1" 2>/dev/null || true
"""


@dataclass(frozen=True)
class UmbrellaTarget:
    """What the write target looks like. ``root`` and ``configs_file`` are absolute paths
    on the cluster, empty for a plain install."""

    kind: str = "plain"
    root: str = ""
    configs_file: str = ""
    configs_names: list[str] = field(default_factory=list)
    benchmarks: list[str] = field(default_factory=list)


PLAIN = UmbrellaTarget()

# The script's own "this is not an umbrella" exits: no install directory, no `benchmarks/`,
# no configs file. Any other failure says nothing about the install.
_PLAIN_EXITS = (2, 3, 4)


def check_benchmark_folder(folder: str) -> str:
    """The folder name itself, or ``InputError`` when it is not one safe path segment."""
    if not BENCHMARK_FOLDER.fullmatch(folder):
        raise InputError(
            "The benchmark folder must be a single name of letters, digits, dots, "
            "dashes and underscores, at most 60 characters, starting with a letter or digit."
        )
    return folder


def config_names(text: str) -> list[str]:
    """The config names a SbatchMan configs file defines, in file order, without repeats.

    The file has one block per cluster, each with a ``configs:`` list of mappings that
    carry a ``name``. Anything else (the top-level ``variables:`` block, a malformed entry,
    text that is not YAML) is skipped.
    """
    try:
        doc: Any = yaml.safe_load(text)
    except (yaml.YAMLError, ValueError, RecursionError):
        return []
    if not isinstance(doc, dict):
        return []
    names: list[str] = []
    for block in doc.values():
        entries = block.get("configs") if isinstance(block, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            name = entry.get("name") if isinstance(entry, dict) else None
            if isinstance(name, str) and name.strip() and name not in names:
                names.append(name)
    return names


def _parse(output: str, mark: str) -> UmbrellaTarget | None:
    """Read the probe's output. Anything before the first marker (a login banner) is ignored.

    ``mark`` is random per call, so no file or folder name on the cluster can print a
    marker line and redirect the parse. The configs file comes last, and once its section
    starts nothing switches section again.
    """
    names = {
        f"@@{mark}:{section}": section for section in ("root", "configs", "benchmarks", "content")
    }
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in output.splitlines():
        if current != "content" and line in names:
            current = names[line]
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    root = next((x.strip() for x in sections.get("root", []) if x.strip()), "")
    relative = next((x.strip() for x in sections.get("configs", []) if x.strip()), "")
    if not root.startswith("/") or not relative:
        return None
    benchmarks = sorted(
        {x.strip() for x in sections.get("benchmarks", []) if BENCHMARK_FOLDER.fullmatch(x.strip())}
    )
    return UmbrellaTarget(
        kind="umbrella",
        root=root,
        configs_file=f"{root}/{relative}",
        configs_names=config_names("\n".join(sections.get("content", []))),
        benchmarks=benchmarks,
    )


async def probe_umbrella(
    transport: Transport | None,
    profile: Profile,
    *,
    strict: bool = False,
    with_content: bool = True,
    timeout: float = 30.0,
) -> UmbrellaTarget:
    """One command on the cluster: the umbrella it found, or ``PLAIN``.

    Not strict (previews and suggestions): anything that goes wrong reads as plain, since a
    failed probe must not block authoring. Strict (a write that depends on the answer): only
    the script's own "not an umbrella" exits read as plain; a dropped connection, a timeout, any
    other exit or output that is not the probe's raises, so the caller never falls back
    to another directory by accident. ``with_content=False`` skips reading the configs file
    (``configs_names`` stays empty), for callers that only need the root and the folders.
    """
    if transport is None or profile.is_local():
        return PLAIN
    mark = secrets.token_hex(8)
    script = _SCRIPT.format(
        root=remote_path_expr(profile.remote_crab),
        mark=mark,
        content=_CONTENT.format(mark=mark) if with_content else "",
    )
    command = "bash -c " + shlex.quote(script)
    try:
        result = await transport.run(command, timeout=timeout)
    except CrabWebError:
        if strict:
            raise
        return PLAIN
    if not result.ok and result.rc in _PLAIN_EXITS:
        return PLAIN
    found = _parse(result.stdout, mark) if result.ok else None
    if found is None:
        if strict:
            raise RemoteCommandError(
                "Could not check whether this cluster has a benchmarks folder, so nothing "
                "was written. Try again.",
                detail=output_tail(result),
            )
        return PLAIN
    return found
