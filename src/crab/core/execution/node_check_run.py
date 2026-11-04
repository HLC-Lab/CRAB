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
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from crab.core.execution.launcher import Placement, launch_line, launcher_for
from crab.core.execution.node_check import Used, compare_nodes
from crab.core.execution.settings import LauncherSpec
from crab.core.execution.topology import Topology
from crab.core.execution.topology import to_json as topology_to_json

CHECK_TIMEOUT_S = 150
CHECK_ATTEMPTS = 2
RETRY_PAUSE_S = 3.0
LOG_NAME = "node_check.log"
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class AppDevices:
    """What one `bind` app relies on: its nodes, NIC entries (a rank may hold several, comma-joined),
    GPU indexes and the cores it is bound to. `key` (its config key) and `ppn` name the app's local ranks
    for the node check's messages; an app without them is still checked, only not named."""

    nodes: tuple[str, ...]
    nics: tuple[str, ...]
    gpus: tuple[int, ...]
    cores: tuple[int, ...]
    key: str = ""
    ppn: int = 0


def _needer_order(pair: tuple[str, int]) -> tuple[int, int, str, int]:
    """Sort key of an (app key, local rank): numeric keys by value ("2" before "10"), then other keys as text."""
    key, rank = pair
    if key.isdecimal():  # not isdigit: "²" is a digit that int() rejects
        return (0, int(key), "", rank)
    return (1, 0, key, rank)


def used_by(apps: Sequence[AppDevices]) -> Used:
    """The NIC ports, GPU use and cores of all the apps together, and who needs each port.

    Local rank r holds entry `nics[r % len(nics)]` (`process/manager.py`), which may be several ports.
    """
    nics = frozenset(port for app in apps for entry in app.nics for port in entry.split(","))
    holders: dict[str, set[tuple[str, int]]] = {}
    for app in apps:
        if not app.key or app.ppn <= 0 or not app.nics:
            continue
        for rank in range(app.ppn):
            for port in app.nics[rank % len(app.nics)].split(","):
                holders.setdefault(port, set()).add((app.key, rank))
    needed_by = {port: tuple(sorted(found, key=_needer_order)) for port, found in holders.items()}
    return Used(
        nics,
        any(app.gpus for app in apps),
        frozenset(c for app in apps for c in app.cores),
        needed_by,
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
    attempts: int = CHECK_ATTEMPTS,
    pause: float = RETRY_PAUSE_S,
    sleep: Callable[[float], None] = time.sleep,
    *,
    notes: list[str] | None = None,
) -> list[str]:
    """Check every node the apps use; return what is wrong, one sentence each (empty when all is well).

    The spec file is written into `spec_dir`, and the raw output of every attempt goes to
    `node_check.log` there. A node that gives no answer, or whose check crashed, is tried again
    (a first step on a fresh allocation can miss a node); only the last attempt counts. Under the
    `direct` launcher there is one process, so only this host answers and not hearing from the
    other nodes is not a problem.

    What a node could not check (its optional `notes`) is not a problem: when `notes` is a list, the
    host-prefixed notes, sorted by host, are appended to it and written to the log after the attempts.
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
                "needed_by": {
                    port: [[key, rank] for key, rank in used.needed_by[port]]
                    for port in sorted(used.needed_by)
                },
                "gpus": used.gpus,
                "cores": sorted(used.cores),
            },
            handle,
        )
    command = (
        f"{shlex.quote(sys.executable)} -m crab.core.execution.node_check {shlex.quote(spec_path)}"
    )
    line = launch_line(launcher_for(launcher_spec), Placement(nodes, 1), command)
    argv = ["bash", "-c", f"timeout {CHECK_TIMEOUT_S} {line}"]
    log: list[str] = []
    for attempt in range(1, attempts + 1):
        done = run(argv)
        answers = parse_answers(done.stdout)
        log.append(
            f"=== attempt {attempt}: exit {done.returncode}\n--- stdout\n{done.stdout}"
            f"\n--- stderr\n{done.stderr}\n"
        )
        expected = [short(n) for n in nodes] if launcher_spec.kind != "direct" else list(answers)
        complete = bool(answers) and all(
            host in answers and isinstance(answers[host]["facts"], dict) for host in expected
        )
        if complete or attempt == attempts:
            break
        sleep(pause)
    found: list[str] = []
    for host, doc in sorted(answers.items()):
        raw = doc.get("notes")  # a note must never fail the run: take a list, ignore anything else
        found += [f"{host}: {note}" for note in (raw if isinstance(raw, list) else [])]
    if found:
        log.append("=== notes\n" + "\n".join(found) + "\n")
    if notes is not None:
        notes.extend(found)
    log_note = f"raw output in {LOG_NAME}"
    try:
        with open(os.path.join(spec_dir, LOG_NAME), "w") as handle:
            handle.write("\n".join(log))
    except OSError as error:
        log_note = f"raw output could not be saved: {error}"
    tail = (done.stderr or "").strip().splitlines()[-3:]
    if not answers:
        return [
            f"the node check did not run (exit {done.returncode}): {' | '.join(tail) or 'no output'}"
        ]
    problems: list[str] = []
    for host in expected:
        if host not in answers:
            problems.append(
                f"{host}: no answer from the node check after {attempts} attempt(s) "
                f"(launcher error tail: {' | '.join(tail[-2:]) or 'none'}; {log_note})"
            )
    for host in sorted(answers):
        problems += [f"{host}: {problem}" for problem in answers[host]["problems"]]
    facts: Mapping[str, Mapping[str, Any]] = {
        host: doc["facts"] for host, doc in answers.items() if isinstance(doc["facts"], dict)
    }
    return problems + compare_nodes(facts)
