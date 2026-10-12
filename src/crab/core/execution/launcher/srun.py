"""The srun launcher."""

from __future__ import annotations

from dataclasses import dataclass

from crab.core.execution.launcher.base import Placement

_CPU_BIND_FLAGS = ("--cpu-bind", "--cpu_bind")


def _without_cpu_bind(flags: tuple[str, ...]) -> list[str]:
    """The flags minus every `--cpu-bind` one, in both its `=value` and two-token forms."""
    kept: list[str] = []
    words = iter(flags)
    for word in words:
        if word in _CPU_BIND_FLAGS:
            next(words, None)
        elif not word.startswith(tuple(f"{flag}=" for flag in _CPU_BIND_FLAGS)):
            kept.append(word)
    return kept


def _binding(placement: Placement) -> list[str]:
    """The words CRAB binds the ranks with; `--overlap` lets co-run steps share the nodes."""
    overlap = ["--overlap"] if placement.co_run else []
    if placement.cpus:
        cores = ",".join(str(core) for core in placement.cpus)
        return [*overlap, f"--cpu-bind=verbose,map_cpu:{cores}"]
    if placement.co_run == "disjoint":
        return ["--exact", "--mem=0", "--cpu-bind=verbose,cores"]
    if placement.co_run or placement.bind_none:
        return [*overlap, "--cpu-bind=verbose,none"]
    return []


@dataclass(frozen=True)
class SrunLauncher:
    """`<command> --export=ALL --nodelist <hosts> <flags> <binding> -n <ranks> -N <hosts>`.

    When CRAB binds the ranks (a co-run, a core list or `bind_none`) its binding replaces any
    `--cpu-bind` in the flags; `--ntasks-per-node` is added for a co-run and when the job's
    tasks per node differ from this app's.
    """

    command: tuple[str, ...]
    flags: tuple[str, ...]

    def prefix(self, placement: Placement) -> list[str]:
        binding = _binding(placement)
        flags = _without_cpu_bind(self.flags) if binding else list(self.flags)
        job_ppn = placement.job_ranks_per_host
        per_node = placement.co_run or (job_ppn is not None and job_ppn != placement.ranks_per_host)
        return [
            *self.command,
            "--export=ALL",
            "--nodelist",
            ",".join(placement.hosts),
            *flags,
            *binding,
            *([f"--ntasks-per-node={placement.ranks_per_host}"] if per_node else []),
            "-n",
            str(placement.total_ranks),
            "-N",
            str(len(placement.hosts)),
        ]
