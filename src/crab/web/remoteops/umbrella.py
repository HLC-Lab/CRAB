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
import shlex
from dataclasses import dataclass, field
from typing import Any

# PyYAML is a dependency of the web extra (pyproject).
import yaml  # type: ignore[import-untyped]

from crab.web.connections.transport import Transport
from crab.web.errors import CrabWebError, InputError
from crab.web.remoteops.crab_cli import remote_path_expr
from crab.web.store.profiles import Profile

# A benchmark folder is one path segment: no slash, no `..`, no leading dot.
BENCHMARK_FOLDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")

_SCRIPT = """\
cd {root} 2>/dev/null || exit 2
test -d benchmarks || exit 3
set -- .local/configs.*.yaml
test -f "$1" || exit 4
echo '@@root'
pwd
echo '@@configs'
echo "$1"
echo '@@benchmarks'
for d in benchmarks/*/; do test -d "$d" && basename "$d"; done
echo '@@content'
cat -- "$1" 2>/dev/null || true
exit 0
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


def _parse(output: str) -> UmbrellaTarget:
    """Read the probe's output. Anything before the first marker (a login banner) is ignored."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in output.splitlines():
        if line in ("@@root", "@@configs", "@@benchmarks", "@@content"):
            current = line[2:]
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    root = next((x.strip() for x in sections.get("root", []) if x.strip()), "")
    relative = next((x.strip() for x in sections.get("configs", []) if x.strip()), "")
    if not root.startswith("/") or not relative:
        return PLAIN
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
    transport: Transport | None, profile: Profile, *, timeout: float = 30.0
) -> UmbrellaTarget:
    """One command on the cluster; ``PLAIN`` when it is not an umbrella or anything fails."""
    if transport is None or profile.is_local():
        return PLAIN
    command = "bash -c " + shlex.quote(_SCRIPT.format(root=remote_path_expr(profile.remote_crab)))
    try:
        result = await transport.run(command, timeout=timeout)
    except CrabWebError:
        return PLAIN
    if not result.ok:
        return PLAIN
    return _parse(result.stdout)
