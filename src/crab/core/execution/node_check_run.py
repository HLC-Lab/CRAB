"""Runs `node_check` on every node an experiment's `bind` apps use, through the experiment's launcher (ADR-043).

One task per node, started the way the apps are (srun, mpirun or direct), so the check sees each node as a
rank would. Each node prints one JSON line; the lines are checked against the preset's `topology` and
against each other. The worker calls this before the first run and stops if anything is off.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from crab.core.execution.launcher import Placement, launch_line, launcher_for
from crab.core.execution.node_check import Used, compare_nodes
from crab.core.execution.settings import LauncherSpec
from crab.core.execution.topology import Topology
from crab.core.execution.topology import to_json as topology_to_json

CHECK_TIMEOUT_S = 150
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class AppDevices:
    """What one `bind` app relies on: its nodes, NIC entries (a rank may hold several, comma-joined),
    GPU indexes and the cores it is bound to."""

    nodes: tuple[str, ...]
    nics: tuple[str, ...]
    gpus: tuple[int, ...]
    cores: tuple[int, ...]


def used_by(apps: Sequence[AppDevices]) -> Used:
    """The NIC ports, GPU use and cores of all the apps together."""
    nics = frozenset(port for app in apps for entry in app.nics for port in entry.split(","))
    return Used(
        nics, any(app.gpus for app in apps), frozenset(c for app in apps for c in app.cores)
    )


def short(host: str) -> str:
    """A host name without its domain: Slurm lists `n1`, the node may call itself `n1.cluster`."""
    return host.split(".")[0]


def _subprocess_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=CHECK_TIMEOUT_S + 30, check=False
    )


def parse_answers(stdout: str) -> dict[str, dict[str, Any]]:
    """The per-node JSON lines in a launcher's output, by short host name; other lines are noise."""
    answers: dict[str, dict[str, Any]] = {}
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict) and {"host", "problems", "facts"} <= set(doc):
            answers[short(str(doc["host"]))] = doc
    return answers


def run_node_check(
    topology: Topology,
    apps: Sequence[AppDevices],
    launcher_spec: LauncherSpec,
    spec_dir: str,
    run: Runner = _subprocess_runner,
) -> list[str]:
    """Check every node the apps use; return what is wrong, one sentence each (empty when all is well).

    The spec file is written into `spec_dir`. Under the `direct` launcher there is one process, so only
    this host answers and not hearing from the other nodes is not a problem.
    """
    nodes = tuple(dict.fromkeys(node for app in apps for node in app.nodes))
    used = used_by(apps)
    os.makedirs(spec_dir, exist_ok=True)
    spec_path = os.path.abspath(os.path.join(spec_dir, "node_check_spec.json"))
    with open(spec_path, "w") as handle:
        json.dump(
            {
                "topology": topology_to_json(topology),
                "nics": sorted(used.nics),
                "gpus": used.gpus,
                "cores": sorted(used.cores),
            },
            handle,
        )
    command = (
        f"{shlex.quote(sys.executable)} -m crab.core.execution.node_check {shlex.quote(spec_path)}"
    )
    line = launch_line(launcher_for(launcher_spec), Placement(nodes, 1), command)
    done = run(["bash", "-c", f"timeout {CHECK_TIMEOUT_S} {line}"])
    answers = parse_answers(done.stdout)
    if not answers:
        tail = (done.stderr or "").strip().splitlines()[-3:]
        return [
            f"the node check did not run (exit {done.returncode}): {' | '.join(tail) or 'no output'}"
        ]
    problems: list[str] = []
    expected = [short(n) for n in nodes] if launcher_spec.kind != "direct" else list(answers)
    for host in expected:
        if host not in answers:
            problems.append(f"{host}: no answer from the node check")
    for host in sorted(answers):
        problems += [f"{host}: {problem}" for problem in answers[host]["problems"]]
    facts: Mapping[str, Mapping[str, Any]] = {host: doc["facts"] for host, doc in answers.items()}
    return problems + compare_nodes(facts)
