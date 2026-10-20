"""The srun launcher."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from crab.core.execution.launcher.base import Placement

_CPU_BIND_FLAGS = ("--cpu-bind", "--cpu_bind")


def flag_words(flags: Iterable[str]) -> Iterator[str]:
    """The words of the flags: an element may hold several (`"--mpi=pmix --cpu-bind=cores"`).

    Split on whitespace only: the launch line is a shell line (`$VAR` and quotes stay as written,
    and its whitespace is collapsed anyway).
    """
    for flag in flags:
        yield from flag.split()


def is_cpu_bind(word: str) -> bool:
    """Whether a flag word is `--cpu-bind` or `--cpu_bind`, with or without `=value`."""
    return word.split("=")[0] in _CPU_BIND_FLAGS


def _without_cpu_bind(flags: tuple[str, ...]) -> list[str]:
    """The flag words minus every `--cpu-bind` one, in both its `=value` and two-token forms."""
    kept: list[str] = []
    words = flag_words(flags)
    for word in words:
        if not is_cpu_bind(word):
            kept.append(word)
        elif "=" not in word:
            next(words, None)
    return kept


def _binding(placement: Placement) -> list[str]:
    """The words CRAB binds the ranks with; `--overlap` lets co-run steps share the nodes."""
    overlap = ["--overlap"] if placement.co_run else []
    if placement.cpus:
        cores = ",".join(str(core) for core in placement.cpus)
        return [*overlap, f"--cpu-bind=verbose,map_cpu:{cores}"]
    if placement.co_run == "disjoint":
        return ["--exact", "--mem=0", "--cpu-bind=verbose,cores"]
    if placement.co_run:
        return [*overlap, "--cpu-bind=verbose,none"]
    return []


@dataclass(frozen=True)
class SrunLauncher:
    """`<command> --export=ALL --nodelist <hosts> <flags> <binding> -n <ranks> -N <hosts>`.

    When CRAB binds the ranks (a co-run or a core list) its binding replaces any
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
