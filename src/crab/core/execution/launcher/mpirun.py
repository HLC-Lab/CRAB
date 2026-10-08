"""The mpirun launcher: one command line per MPI dialect, with explicit geometry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from crab.core.execution.launcher.base import Placement
from crab.core.execution.launcher.dialect import Dialect


def _host_slots(placement: Placement) -> str:
    return ",".join(f"{host}:{placement.ranks_per_host}" for host in placement.hosts)


def _openmpi4_hosts(placement: Placement) -> list[str]:
    # Open MPI 4: repeating a name adds a slot; the `:N` suffix is not in the 4.1 man page.
    names = [host for host in placement.hosts for _ in range(placement.ranks_per_host)]
    return ["-H", ",".join(names)]


def _openmpi5_hosts(placement: Placement) -> list[str]:
    # Open MPI 5 (PRRTE): `host:slots`; no suffix means one slot.
    return ["--host", _host_slots(placement)]


def _hydra_hosts(placement: Placement) -> list[str]:
    # MPICH Hydra: `host:slots`; no suffix means one slot.
    return ["-hosts", _host_slots(placement)]


_HOST_FLAGS: dict[Dialect, Callable[[Placement], list[str]]] = {
    "openmpi4": _openmpi4_hosts,
    "openmpi5": _openmpi5_hosts,
    "hydra": _hydra_hosts,
}
_REPORT_BINDINGS: dict[Dialect, list[str]] = {
    "openmpi4": ["--report-bindings"],
    "openmpi5": ["--display", "bindings"],  # `--report-bindings` is deprecated in Open MPI 5.
    "hydra": ["-report-bindings"],
}
# Hydra exports the whole environment by default (`-genvall`); only Open MPI names variables.
_EXPORT_WITH_X: tuple[Dialect, ...] = ("openmpi4", "openmpi5")


@dataclass(frozen=True)
class MpirunLauncher:
    """`<command> -n <ranks> <hosts> <-x NAME...> <report flag> <preset flags>`.

    The ranks and hosts are always explicit: a host listed with `P` slots and `-n P*k` gets `P`
    ranks in every dialect's default slot mapping, so no `--map-by` or `-ppn` is added and a
    preset's own mapping flags do not clash. Inside a Slurm allocation the host list is a filter
    that has to stay inside the allocation, which lets an app run on a subset of it.
    """

    command: tuple[str, ...]
    flags: tuple[str, ...]
    dialect: Dialect
    export: tuple[str, ...] = ()
    report_bindings: bool = False

    def prefix(self, placement: Placement) -> list[str]:
        argv = [
            *self.command,
            "-n",
            str(placement.total_ranks),
            *_HOST_FLAGS[self.dialect](placement),
        ]
        if self.dialect in _EXPORT_WITH_X:
            for name in self.export:
                argv += ["-x", name]
        if self.report_bindings:
            argv += _REPORT_BINDINGS[self.dialect]
        return [*argv, *self.flags]
