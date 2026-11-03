"""Turns an app's `bind` into a device table for each local rank (ADR-043).

Pure and deterministic: (`Bind`, the preset's `Topology`, ppn, the cores other apps already hold)
in, `Resolved` out. Every pick uses one tie-break: distance class, then NUMA domain, then device name.
Every error is a `ValueError` that starts with where the problem is and gives the numbers it rests on;
nothing falls back silently (the one fallback, a GPU no NIC claims, is recorded as a warning).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from crab.core.execution.bind import Bind
from crab.core.execution.topology import DISTANCES, Nic, Topology

RESOLVER_VERSION = "1"


@dataclass(frozen=True)
class RankPlacement:
    """What one local rank gets. `gpu` is None when it sees every GPU or none; `nic_share` counts
    the ranks of the app that use this rank's first NIC (0 without a NIC)."""

    local_rank: int
    gpu: int | None
    nics: tuple[str, ...]
    cores: tuple[int, ...]
    dist: str | None
    nic_share: int


@dataclass(frozen=True)
class Resolved:
    ranks: tuple[RankPlacement, ...]
    warnings: tuple[str, ...]

    @property
    def cores_used(self) -> frozenset[int]:
        """Pass this as `taken_cores` for the next app that runs on the same nodes."""
        return frozenset(core for rank in self.ranks for core in rank.cores)


def _nic_key(nic: Nic) -> tuple[int, int, str]:
    """The tie-break: distance class (unknown last), NUMA domain (unknown last), device name."""
    dist = DISTANCES.index(nic.dist) if nic.dist in DISTANCES else len(DISTANCES)
    return (dist, nic.numa if nic.numa is not None else 1 << 30, nic.dev)


def _single_domain(topology: Topology) -> int | None:
    return next(iter(topology.numa)) if len(topology.numa) == 1 else None


def _gpu_domain(topology: Topology, gpu: int | None) -> int | None:
    if gpu is None:
        return None
    numa = topology.gpus[gpu].numa
    return numa if numa is not None else _single_domain(topology)


def _nic_domain(topology: Topology, nic: Nic) -> int | None:
    return nic.numa if nic.numa is not None else _single_domain(topology)


def _nic_domains(topology: Topology) -> list[int]:
    """The NUMA domains that hold at least one NIC, sorted."""
    found = (_nic_domain(topology, nic) for nic in topology.nics)
    return sorted({domain for domain in found if domain is not None})


def _core_domain(topology: Topology, core: int) -> int | None:
    return next((d for d, cores in sorted(topology.numa.items()) if core in cores), None)


def _gpu_order(topology: Topology, order: str) -> list[int]:
    """GPU indexes in the order ranks take them: topology order, or alternating NUMA domains."""
    indexes = list(range(len(topology.gpus)))
    if order == "pack":
        return indexes
    groups: dict[int, list[int]] = {}
    for index in indexes:
        groups.setdefault(_gpu_domain(topology, index) or 0, []).append(index)
    columns = [groups[d] for d in sorted(groups)]
    return [
        group[i]
        for i in range(max(map(len, columns), default=0))
        for group in columns
        if i < len(group)
    ]


def _assign_gpus(bind: Bind, topology: Topology, ppn: int, where: str) -> list[int | None]:
    if bind.gpu in ("none", "all"):
        return [None] * ppn
    count = len(topology.gpus)
    if count == 0:
        raise ValueError(f'{where}: bind.gpu "{bind.gpu}" needs GPUs, but the topology lists none.')
    order = _gpu_order(topology, bind.order)
    if bind.gpu == "per-rank" and ppn > count:
        raise ValueError(
            f"{where}: bind.gpu per-rank needs {ppn} GPUs per node (ppn {ppn}), the topology has {count}; "
            'lower ppn or use bind.gpu "shared".'
        )
    return [order[rank % count] for rank in range(ppn)]


def _domain_cores(topology: Topology, domain: int, siblings: bool) -> list[int]:
    """The domain's physical cores (all its cores when the topology does not say), or its siblings."""
    every = list(topology.numa[domain])
    physical = list(topology.physical.get(domain, every))
    return [core for core in every if core not in physical] if siblings else physical


def _first_free(
    topology: Topology, domains: Sequence[int], smt: bool, used: set[int]
) -> int | None:
    """The first free core: physical cores of the domains in order, then (with `smt`) their siblings."""
    passes = [False, True] if smt else [False]
    return next(
        (
            c
            for siblings in passes
            for d in domains
            for c in _domain_cores(topology, d, siblings)
            if c not in used
        ),
        None,
    )


def _assign_cores(
    bind: Bind,
    topology: Topology,
    gpus: Sequence[int | None],
    taken: frozenset[int],
    where: str,
    warnings: list[str],
    preferred: Sequence[tuple[int, str] | None] = (),
) -> list[tuple[int, ...]]:
    """`preferred[rank]` is the (NUMA domain, NIC dev) a rank without a GPU domain tries first."""
    if bind.cores == "launcher":
        return [() for _ in gpus]
    if not topology.numa:
        raise ValueError(
            f"{where}: bind.cores nearest needs topology.numa, the preset lists no NUMA domains."
        )
    if not topology.physical:
        warnings.append(
            f"{where}: the topology has no physical core list, so every core counts as physical (SMT unknown)"
        )
    used = set(taken)
    placed: list[tuple[int, ...]] = []
    for rank, gpu in enumerate(gpus):
        fixed = _gpu_domain(topology, gpu)
        domains = [fixed] if fixed is not None else sorted(topology.numa)
        near = preferred[rank] if fixed is None and rank < len(preferred) else None
        choice = None
        if near is not None and near[0] in topology.numa:
            choice = _first_free(topology, [near[0]], bind.smt, used)
        if choice is None:
            choice = _first_free(topology, domains, bind.smt, used)
            if choice is not None and near is not None:
                warnings.append(
                    f"{where}: bind.cores nearest: local rank {rank} has no free core in NUMA domain "
                    f"{near[0]} of its NIC {near[1]}; took a core of another domain"
                )
        if choice is None:
            total = sum(
                len(topology.numa[d]) if bind.smt else len(_domain_cores(topology, d, False))
                for d in domains
            )
            theirs = len(taken & {c for d in domains for c in topology.numa[d]})
            where_text = (
                f"NUMA domain {fixed} (near GPU {gpu})" if fixed is not None else "any NUMA domain"
            )
            raise ValueError(
                f"{where}: bind.cores nearest: local rank {rank} has no free core in {where_text}; its "
                f"{total} {'cores' if bind.smt else 'physical cores'} are used, {theirs} by other apps "
                f"and {len(used) - len(taken)} by earlier ranks of this app."
            )
        used.add(choice)
        placed.append((choice,))
    return placed


def _with_ports(best: Nic, nics: Sequence[Nic], ports: int, where: str) -> tuple[Nic, ...]:
    if ports == 1:
        return (best,)
    if best.card is None:
        raise ValueError(
            f'{where}: bind.nic ports {ports}: NIC {best.dev} has no "card" in the topology; '
            "run crab probe or add it."
        )
    group = sorted((n for n in nics if n.card == best.card and n is not best), key=_nic_key)
    if len(group) + 1 < ports:
        raise ValueError(
            f"{where}: bind.nic ports {ports}: card {best.card} has {len(group) + 1} port(s) in the topology."
        )
    return (best, *group[: ports - 1])


def _pick_nearest(
    topology: Topology, gpu: int | None, domain: int | None, where: str, warnings: list[str]
) -> Nic:
    """The NIC claimed by the GPU, else one in its NUMA domain (recorded), else one in the core's domain."""
    if gpu is not None:
        claimed = [n for n in topology.nics if n.near_gpu == gpu]
        if claimed:
            return min(claimed, key=_nic_key)
        domain = _gpu_domain(topology, gpu)
        local = [
            n for n in topology.nics if domain is not None and _nic_domain(topology, n) == domain
        ]
        if local:
            warnings.append(
                f"{where}: no NIC is near GPU {gpu}; took the NICs of its NUMA domain {domain}"
            )
            return min(local, key=_nic_key)
        raise ValueError(
            f"{where}: bind.nic nearest: no NIC is near GPU {gpu} and none shares its NUMA domain."
        )
    local = [n for n in topology.nics if domain is not None and _nic_domain(topology, n) == domain]
    if not local:
        raise ValueError(
            f"{where}: bind.nic nearest: the topology has no NIC in NUMA domain {domain}."
        )
    return min(local, key=_nic_key)


def _assign_nics(
    bind: Bind,
    topology: Topology,
    gpus: Sequence[int | None],
    cores: Sequence[tuple[int, ...]],
    where: str,
    warnings: list[str],
) -> list[tuple[Nic, ...]]:
    rule = bind.nic
    if rule.pick == "default":
        return [() for _ in gpus]
    if topology.nic_env is None:
        raise ValueError(
            f'{where}: bind.nic "{rule.pick}": this topology cannot select a NIC per rank (nic_env is null).'
        )
    if not topology.nics:
        raise ValueError(f'{where}: bind.nic "{rule.pick}": the topology lists no NICs.')
    ordered = sorted(topology.nics, key=_nic_key)
    chosen: list[tuple[Nic, ...]] = []
    for rank, gpu in enumerate(gpus):
        domain = _gpu_domain(topology, gpu)
        if domain is None and cores[rank]:
            domain = _core_domain(topology, cores[rank][0])
        if rule.pick == "tag":
            tagged = [n for n in ordered if rule.tag in n.tags]
            if not tagged:
                declared = sorted({t for n in topology.nics for t in n.tags})
                known = ", ".join(declared) if declared else "none"
                raise ValueError(
                    f"{where}: bind.nic tag {rule.tag!r}: no NIC has that tag (the topology declares: {known})."
                )
            best = tagged[rank % len(tagged)]
        elif rule.pick == "spread":
            best = ordered[rank % len(ordered)]
        elif rule.pick == "single":
            first = chosen[0][0] if chosen else None
            best = first or (
                _pick_nearest(topology, gpu, domain, where, warnings)
                if gpu is not None or domain is not None
                else ordered[0]
            )
        elif rule.pick == "far":
            if domain is None:
                raise ValueError(
                    f"{where}: bind.nic far: the NUMA domain of local rank {rank} is unknown (set numa on the GPU)."
                )
            far = [n for n in ordered if _nic_domain(topology, n) not in (None, domain)]
            if not far:
                have = ", ".join(str(d) for d in _nic_domains(topology)) or "none"
                raise ValueError(
                    f"{where}: bind.nic far: no NIC outside NUMA domain {domain} (NICs are in domain(s): {have})."
                )
            best = far[rank % len(far)]
        else:
            best = _pick_nearest(topology, gpu, domain, where, warnings)
        chosen.append(_with_ports(best, topology.nics, rule.ports, where))
    return chosen


def _cores_follow_nics(bind: Bind, gpus: Sequence[int | None]) -> bool:
    """True when no GPU is bound and the NIC pick (tag, spread) does not depend on the core."""
    return (
        bind.cores == "nearest"
        and all(gpu is None for gpu in gpus)
        and bind.nic.pick in ("tag", "spread")
    )


def resolve(
    bind: Bind,
    topology: Topology,
    ppn: int,
    taken_cores: frozenset[int] = frozenset(),
    where: str = "app",
) -> Resolved:
    """The placement of each local rank 0..ppn-1 on one node.

    `taken_cores` are the cores earlier apps of the same co-run group hold, so apps are placed
    disjointly in app order. `where` names the app in messages, e.g. `app 0`.

    Raises:
        ValueError: the request cannot be met by this topology; the message gives the numbers.
    """
    if ppn < 1:
        raise ValueError(f"{where}: ppn must be at least 1, got {ppn}.")
    warnings: list[str] = []
    gpus = _assign_gpus(bind, topology, ppn, where)
    if _cores_follow_nics(bind, gpus):
        # tag and spread do not read the cores, so the NICs come first and the cores follow them.
        nics = _assign_nics(bind, topology, gpus, [()] * ppn, where, warnings)
        preferred = [
            (domain, group[0].dev)
            if group and (domain := _nic_domain(topology, group[0])) is not None
            else None
            for group in nics
        ]
        cores = _assign_cores(bind, topology, gpus, taken_cores, where, warnings, preferred)
    else:
        cores = _assign_cores(bind, topology, gpus, taken_cores, where, warnings)
        nics = _assign_nics(bind, topology, gpus, cores, where, warnings)
    first = [group[0].dev if group else None for group in nics]
    shares = {dev: first.count(dev) for dev in set(first) if dev is not None}
    if bind.nic.exclusive and any(count > 1 for count in shares.values()):
        dev, count = max(sorted(shares.items()), key=lambda item: item[1])
        raise ValueError(
            f"{where}: bind.nic exclusive: {count} ranks would share {dev} (ppn {ppn}, "
            f"the topology has {len(topology.nics)} NIC port(s))."
        )
    for dev, count in sorted(shares.items()):
        if count > 1:
            warnings.append(f"{where}: {count} ranks share NIC {dev}")
    if bind.order == "spread" and bind.gpu in ("per-rank", "shared"):
        domains = len({_gpu_domain(topology, g) for g in range(len(topology.gpus))})
        if domains > 1 and ppn % domains:
            warnings.append(
                f"{where}: bind.order spread is uneven: {ppn} ranks over {domains} NUMA domains"
            )
    ranks = tuple(
        RankPlacement(
            local_rank=rank,
            gpu=gpus[rank],
            nics=tuple(n.dev for n in nics[rank]),
            cores=cores[rank],
            dist=nics[rank][0].dist
            if nics[rank] and gpus[rank] is not None and nics[rank][0].near_gpu == gpus[rank]
            else None,
            nic_share=shares.get(first[rank] or "", 0),
        )
        for rank in range(ppn)
    )
    return Resolved(ranks, tuple(warnings))
