"""`crab sbatchman-preset`: print the `sbatchman configure slurm` command for this CRAB install.

The paths CRAB knows (the venv `bin` directory, the checkout root, the wrappers folder) are
filled in; the Slurm values it cannot know come from flags or stay as `<placeholders>`.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# What may appear unquoted in the command: no whitespace, quote, `$`, `;` or other shell syntax.
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9._:,+/@%=-]+")


def _token(flag: str, value: str | None, placeholder: str) -> str:
    """The value for an unquoted slot, or its placeholder; ValueError when it is not one token."""
    if value is None:
        return placeholder
    if not _SAFE_TOKEN.fullmatch(value):
        raise ValueError(
            f"{flag} {value!r} must be one token of letters, digits and . _ : , + / @ % = -"
        )
    return value


def _quoted(flag: str, value: str) -> str:
    """A value for a single-quoted `--env` entry; ValueError when it holds a single quote."""
    if "'" in value:
        raise ValueError(f"{flag} {value!r} must not contain a single quote")
    return value


def build_configure_command(
    *,
    venv_bin: Path,
    crab_root: Path,
    wrappers_dir: Path,
    name: str,
    partition: str | None,
    account: str | None,
    qos: str | None,
    time: str | None,
    nodes: str | None,
    tasks_per_node: str | None,
    system: str | None,
    exclusive: bool,
    cpu_bind: str | None,
) -> str:
    """The `sbatchman configure slurm` command, one option per line, without a final newline.

    A `None` Slurm value keeps its `<placeholder>`; `qos` is left out when `None`. Raises
    ValueError for a value that would break the command (a single quote, whitespace).
    """
    options = [
        f"--name {_token('--name', name, '')}",
        f"--partition {_token('--partition', partition, '<partition>')}",
        f"--account {_token('--account', account, '<account>')}",
    ]
    if qos is not None:
        options.append(f"--qos {_token('--qos', qos, '')}")
    options += [
        f"--time {_token('--time', time, '<hh:mm:ss>')}",
        f"--nodes {_token('--nodes', nodes, '<nodes>')}",
        f"--tasks-per-node {_token('--tasks-per-node', tasks_per_node, '<tasks per node>')}",
    ]
    if exclusive:
        options.append("--exclusive")
    env = [
        f"PATH={_quoted('venv bin', str(venv_bin))}:$PATH",
        f"CRAB_ROOT={_quoted('crab root', str(crab_root))}",
        f"CRAB_SYSTEM={_token('--system', system, '<cluster name>')}",
        f"CRAB_PATH_WRAPPERS={_quoted('wrappers dir', str(wrappers_dir))}",
        "SBATCHMAN_JOB_DIR={EXP_DIR}",
    ]
    if cpu_bind is not None:
        env.append(f"SLURM_CPU_BIND={_token('--cpu-bind', cpu_bind, '')}")
    options += [f"--env '{entry}'" for entry in env]
    return "sbatchman configure slurm \\\n" + " \\\n".join(f"  {option}" for option in options)


def handle(args: argparse.Namespace) -> None:
    """Print the command for the running install; exit 2 with a one-line error on a bad value."""
    from crab.core import wrapper_paths

    # The shared wrappers folders as the worker resolves them (the first entry is `local/wrappers`).
    shared = wrapper_paths.wrapper_search_path()[1:]
    try:
        command = build_configure_command(
            venv_bin=Path(sys.executable).parent.absolute(),
            crab_root=Path(wrapper_paths._CRAB_ROOT).absolute(),
            wrappers_dir=Path(os.pathsep.join(shared)),
            name=args.name,
            partition=args.partition,
            account=args.account,
            qos=args.qos,
            time=args.time,
            nodes=args.nodes,
            tasks_per_node=args.tasks_per_node,
            system=args.system,
            exclusive=args.exclusive,
            cpu_bind=args.cpu_bind,
        )
    except ValueError as exc:
        print(f"crab sbatchman-preset: {exc}", file=sys.stderr)
        sys.exit(2)
    hint = wrapper_paths.missing_wrappers_hint()
    if hint:
        print(f"warning: {hint}", file=sys.stderr)
    print(command)
