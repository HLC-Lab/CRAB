"""`crab sbatchman-preset`: print the `sbatchman configure slurm` command for this CRAB install.

The paths CRAB knows (the venv `bin` directory, the checkout root, the wrappers folder) are
filled in; the Slurm values it cannot know come from flags or stay as `<placeholders>`.
With `--configs-file` the output is a SbatchMan configs file that sweeps `--nodes` instead: the
same Slurm values and the same job environment, in the key names SbatchMan's configs file reads.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

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


def _env_entries(
    *,
    venv_bin: Path,
    crab_root: Path,
    wrappers_dir: Path,
    system: str | None,
    cpu_bind: str | None,
) -> list[str]:
    """The job environment as `KEY=VALUE` strings, shared by the command and the configs file."""
    env = [
        f"PATH={_quoted('venv bin', str(venv_bin))}:$PATH",
        f"CRAB_ROOT={_quoted('crab root', str(crab_root))}",
        f"CRAB_SYSTEM={_token('--system', system, '<cluster name>')}",
        f"CRAB_PATH_WRAPPERS={_quoted('wrappers dir', str(wrappers_dir))}",
        "SBATCHMAN_JOB_DIR={EXP_DIR}",
    ]
    if cpu_bind is not None:
        env.append(f"SLURM_CPU_BIND={_token('--cpu-bind', cpu_bind, '')}")
    return env


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
    ValueError for a value that would break the command (a single quote, whitespace), and for a
    node list (`--nodes 4,8`), which only the configs file takes.
    """
    if nodes is not None and "," in nodes:
        raise ValueError(
            f"--nodes {nodes!r} is a list; a list of node counts needs --configs-file "
            "(the command form takes one node count)"
        )
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
    env = _env_entries(
        venv_bin=venv_bin,
        crab_root=crab_root,
        wrappers_dir=wrappers_dir,
        system=system,
        cpu_bind=cpu_bind,
    )
    options += [f"--env '{entry}'" for entry in env]
    return "sbatchman configure slurm \\\n" + " \\\n".join(f"  {option}" for option in options)


def _node_counts(nodes: str | None) -> list[int]:
    """`--nodes 4,8,16` as `[4, 8, 16]`; ValueError (naming --nodes) when absent or not integers."""
    if nodes is None:
        raise ValueError("--configs-file needs --nodes, for example --nodes 4,8,16")
    parts = nodes.split(",")
    if not all(part.isascii() and part.isdigit() and int(part) > 0 for part in parts):
        raise ValueError(
            f"--nodes {nodes!r} must be positive whole numbers separated by commas, "
            "for example 4,8,16"
        )
    return [int(part) for part in parts]


def _yaml_str(value: str) -> str:
    """A double-quoted YAML scalar (JSON string syntax is valid YAML), so `00:20:00` stays text."""
    return json.dumps(value, ensure_ascii=False)


# A cluster name written without quotes: a plain word that YAML does not read as a bool or null.
_PLAIN_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_YAML_WORDS = {"y", "n", "yes", "no", "on", "off", "true", "false", "null"}


def _yaml_key(value: str) -> str:
    if _PLAIN_KEY.fullmatch(value) and value.lower() not in _YAML_WORDS:
        return value
    return _yaml_str(value)


def build_configs_file(
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
    produced_by: str,
) -> str:
    """A SbatchMan configs file: one config `<name>_{nodes}` swept over the `--nodes` list.

    Same Slurm values and job environment as `build_configure_command`; keys follow SbatchMan's
    configs file (`tasks_per_node`, `exclusive`, `env` as a list of `KEY=VALUE`). `produced_by`
    is the command line for the header comment. No final newline. Raises ValueError like
    `build_configure_command`, and when `nodes` is missing or not a comma-separated list of
    positive integers.
    """
    counts = _node_counts(nodes)
    cluster = _token("--system", system, "<cluster name>")
    if cluster == "variables":
        raise ValueError("--system 'variables' clashes with the variables block of a configs file")
    config_name = _token("--name", name, "")
    tasks = _token("--tasks-per-node", tasks_per_node, "<tasks per node>")
    entry = [
        f"name: {_yaml_str(config_name + '_{nodes}')}",
        f"partition: {_yaml_str(_token('--partition', partition, '<partition>'))}",
        f"account: {_yaml_str(_token('--account', account, '<account>'))}",
    ]
    if qos is not None:
        entry.append(f"qos: {_yaml_str(_token('--qos', qos, ''))}")
    entry += [
        f"time: {_yaml_str(_token('--time', time, '<hh:mm:ss>'))}",
        'nodes: "{nodes}"',
        f"tasks_per_node: {tasks if tasks.isascii() and tasks.isdigit() else _yaml_str(tasks)}",
    ]
    if exclusive:
        entry.append("exclusive: true")
    entry.append("env:")
    env = _env_entries(
        venv_bin=venv_bin,
        crab_root=crab_root,
        wrappers_dir=wrappers_dir,
        system=system,
        cpu_bind=cpu_bind,
    )
    entry += [f"  - {_yaml_str(item)}" for item in env]
    lines = [
        f"# Generated by: {produced_by}",
        f"# Load it with `sbatchman configure --file <this file>`. The top-level key `{cluster}` "
        "must equal the output of `sbatchman set-cluster-name` on the cluster.",
        "variables:",
        f"  nodes: [{', '.join(str(count) for count in counts)}]",
        f"{_yaml_key(cluster)}:",
        "  scheduler: slurm",
        "  configs:",
        f"    - {entry[0]}",
    ]
    lines += [f"      {line}" for line in entry[1:]]
    return "\n".join(lines)


def handle(args: argparse.Namespace) -> None:
    """Print the command (or, with `--configs-file`, the configs file) for the running install.

    Exits 2 with a one-line error on a bad value.
    """
    from crab.core import wrapper_paths

    try:
        # The shared wrappers folders as the worker resolves them (the first entry is
        # `local/wrappers`). Inside the `try`: a relative CRAB_PATH_WRAPPERS entry raises here.
        shared = wrapper_paths.wrapper_search_path()[1:]
        common: dict[str, Any] = {
            "venv_bin": Path(sys.executable).parent.absolute(),
            "crab_root": Path(wrapper_paths._CRAB_ROOT).absolute(),
            "wrappers_dir": Path(os.pathsep.join(shared)),
            "name": args.name,
            "partition": args.partition,
            "account": args.account,
            "qos": args.qos,
            "time": args.time,
            "nodes": args.nodes,
            "tasks_per_node": args.tasks_per_node,
            "system": args.system,
            "exclusive": args.exclusive,
            "cpu_bind": args.cpu_bind,
        }
        if getattr(args, "configs_file", False):
            command = build_configs_file(**common, produced_by=shlex.join(["crab", *sys.argv[1:]]))
        else:
            command = build_configure_command(**common)
    except ValueError as exc:
        print(f"crab sbatchman-preset: {exc}", file=sys.stderr)
        sys.exit(2)
    hint = wrapper_paths.missing_wrappers_hint()
    if hint:
        print(f"warning: {hint}", file=sys.stderr)
    print(command)
