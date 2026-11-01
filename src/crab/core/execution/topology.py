"""A preset's `topology`: the GPUs, NICs and NUMA domains of one node type (ADR-043).

The block is plain data, handwritten from a cluster's documentation or written by `crab probe`;
both use this schema. Nothing here reads the machine, and nothing applies the block yet: it is
parsed, validated and carried in `ExecutionSettings` and `execution.json`.

Every error is a `ValueError` that starts with where the problem is, such as
`preset 'lab': topology.nics[2].near_gpu`. Imports nothing from `placement.py`, which imports
`settings.py`, which imports this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

GPU_VENDORS = ("nvidia", "amd", "intel", "none")
DISTANCES = ("PIX", "PXB", "PHB", "NODE", "SYS")
LINK_LAYERS = ("InfiniBand", "Ethernet")
SOURCE_KINDS = ("handwritten", "probe")
TOPOLOGY_KEYS = ("gpu_vendor", "nic_env", "numa", "gpus", "nics", "source")
GPU_KEYS = ("numa", "pci")
NIC_KEYS = (
    "dev",
    "numa",
    "near_gpu",
    "card",
    "pci",
    "dist",
    "tags",
    "rate_mbps",
    "link_layer",
)
SOURCE_KEYS = ("kind", "version", "date")

PCI_ADDRESS = re.compile(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]")
PCI_CARD = re.compile(r"[0-9a-f]+(:[0-9a-f]+){1,2}")
NIC_NAME = re.compile(r"[A-Za-z0-9_.:-]+")
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
TAG = re.compile(r"[A-Za-z0-9_.-]+")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


@dataclass(frozen=True)
class Gpu:
    """One GPU. Its id is its position in `Topology.gpus` (PCI bus order)."""

    numa: int | None
    pci: str | None


@dataclass(frozen=True)
class Nic:
    """One NIC port as UCX names it, such as `mlx5_0:1`, and what is known about its place."""

    dev: str
    numa: int | None
    near_gpu: int | None
    card: str | None
    pci: str | None
    dist: str | None
    tags: tuple[str, ...]
    rate_mbps: int | None
    link_layer: str | None


@dataclass(frozen=True)
class Topology:
    """The facts about one node type. `nic_env` is the variable that selects a NIC per rank."""

    gpu_vendor: str
    nic_env: str | None
    numa: Mapping[int, tuple[int, ...]]
    gpus: tuple[Gpu, ...]
    nics: tuple[Nic, ...]
    source: Mapping[str, str]


def _object(value: object, where: str, allowed: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: must be an object.")
    for key in value:
        if key not in allowed:
            raise ValueError(f"{where}: unknown key {key!r}; allowed: {', '.join(allowed)}.")
    return value


def _index(value: object, where: str) -> int | None:
    """An optional non-negative integer (a NUMA id or a GPU index)."""
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"{where}: must be a non-negative integer, got {value!r}.")
    return value


def _text(value: object, where: str, pattern: re.Pattern[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{where}: {value!r} is not valid (expected {pattern.pattern}).")
    return value


def _choice(value: object, where: str, allowed: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    if value not in allowed:
        raise ValueError(f"{where}: must be one of {', '.join(allowed)}, got {value!r}.")
    return str(value)


def _parse_core_list(text: object, where: str) -> tuple[int, ...]:
    """ "0-15,32" as the sorted cores; the same syntax as an app's `cpus`."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{where}: must be a core list such as '0-15,32', got {text!r}.")
    cores: list[int] = []
    for item in text.split(","):
        item = item.strip()
        span = re.fullmatch(r"([0-9]+)(?:-([0-9]+))?", item)
        if span is None or (span[2] is not None and int(span[1]) > int(span[2])):
            raise ValueError(f"{where}: {text!r} has the invalid item {item!r}; use N or A-B.")
        cores.extend(range(int(span[1]), int(span[2] or span[1]) + 1))
    if len(set(cores)) != len(cores):
        raise ValueError(f"{where}: {text!r} lists a core more than once.")
    return tuple(sorted(cores))


def _parse_numa(value: object, where: str) -> Mapping[int, tuple[int, ...]]:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: must be an object of NUMA id to core list.")
    domains: dict[int, tuple[int, ...]] = {}
    taken: dict[int, int] = {}
    for key, text in value.items():
        if not isinstance(key, str) or not key.isdigit():
            raise ValueError(f"{where}: key {key!r} must be a NUMA domain number.")
        cores = _parse_core_list(text, f"{where}.{key}")
        for core in cores:
            if core in taken:
                raise ValueError(
                    f"{where}.{key}: core {core} is also in domain {taken[core]}; "
                    "the domains must not overlap."
                )
            taken[core] = int(key)
        domains[int(key)] = cores
    return MappingProxyType(domains)


def _check_domain(numa: int | None, domains: Mapping[int, tuple[int, ...]], where: str) -> None:
    if numa is not None and domains and numa not in domains:
        known = ", ".join(str(d) for d in sorted(domains))
        raise ValueError(f"{where}: NUMA domain {numa} is not in topology.numa ({known}).")


def _parse_gpus(
    value: object, where: str, domains: Mapping[int, tuple[int, ...]]
) -> tuple[Gpu, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{where}: must be a list of GPU objects.")
    gpus = []
    for position, item in enumerate(value):
        here = f"{where}[{position}]"
        body = _object(item, here, GPU_KEYS)
        numa = _index(body.get("numa"), f"{here}.numa")
        _check_domain(numa, domains, f"{here}.numa")
        gpus.append(Gpu(numa, _text(body.get("pci"), f"{here}.pci", PCI_ADDRESS)))
    return tuple(gpus)


def _parse_nic(
    item: object, here: str, domains: Mapping[int, tuple[int, ...]], gpu_count: int
) -> Nic:
    body = _object(item, here, NIC_KEYS)
    dev = _text(body.get("dev"), f"{here}.dev", NIC_NAME)
    if dev is None:
        raise ValueError(f'{here}.dev: required, such as "mlx5_0:1".')
    numa = _index(body.get("numa"), f"{here}.numa")
    _check_domain(numa, domains, f"{here}.numa")
    near = _index(body.get("near_gpu"), f"{here}.near_gpu")
    if near is not None and near >= gpu_count:
        raise ValueError(
            f"{here}.near_gpu: {near} is not a GPU index "
            f"(topology.gpus has {gpu_count} entr{'y' if gpu_count == 1 else 'ies'})."
        )
    tags = body.get("tags", [])
    if not isinstance(tags, list):
        raise ValueError(f"{here}.tags: must be a list of strings.")
    rate = body.get("rate_mbps")
    if rate is not None and (type(rate) is not int or rate < 1):
        raise ValueError(f"{here}.rate_mbps: must be a positive integer, got {rate!r}.")
    return Nic(
        dev=dev,
        numa=numa,
        near_gpu=near,
        card=_text(body.get("card"), f"{here}.card", PCI_CARD),
        pci=_text(body.get("pci"), f"{here}.pci", PCI_ADDRESS),
        dist=_choice(body.get("dist"), f"{here}.dist", DISTANCES),
        tags=tuple(str(_text(tag, f"{here}.tags", TAG)) for tag in tags),
        rate_mbps=rate,
        link_layer=_choice(body.get("link_layer"), f"{here}.link_layer", LINK_LAYERS),
    )


def _parse_nics(
    value: object, where: str, domains: Mapping[int, tuple[int, ...]], gpu_count: int
) -> tuple[Nic, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{where}: must be a list of NIC objects.")
    nics = tuple(
        _parse_nic(item, f"{where}[{i}]", domains, gpu_count) for i, item in enumerate(value)
    )
    seen: dict[str, int] = {}
    for position, nic in enumerate(nics):
        if nic.dev in seen:
            raise ValueError(
                f"{where}[{position}].dev: {nic.dev!r} is already entry {seen[nic.dev]}."
            )
        seen[nic.dev] = position
    return nics


def _parse_source(value: object, where: str) -> Mapping[str, str]:
    body = _object(value, where, SOURCE_KEYS)
    kind = _choice(body.get("kind"), f"{where}.kind", SOURCE_KINDS)
    if kind is None:
        raise ValueError(f"{where}.kind: required, one of {', '.join(SOURCE_KINDS)}.")
    source = {"kind": kind}
    for key in ("version", "date"):
        if key in body:
            if not isinstance(body[key], str) or not body[key]:
                raise ValueError(f"{where}.{key}: must be a non-empty string.")
            source[key] = body[key]
    if "date" in source and not DATE.fullmatch(source["date"]):
        raise ValueError(f"{where}.date: must look like 2026-11-01, got {source['date']!r}.")
    return MappingProxyType(source)


def parse_topology(value: object, where: str) -> Topology:
    """Validate a preset's `topology` block. `where` names the preset, e.g. `preset 'lab'`.

    Raises:
        ValueError: a key is unknown, a value has the wrong type or form, a NUMA domain or GPU
            index is out of range, a core is in two domains, or a NIC device appears twice.
    """
    here = f"{where}: topology"
    body = _object(value, here, TOPOLOGY_KEYS)
    vendor = _choice(body.get("gpu_vendor", "none"), f"{here}.gpu_vendor", GPU_VENDORS)
    nic_env = _text(body.get("nic_env"), f"{here}.nic_env", ENV_NAME)
    domains = _parse_numa(body["numa"], f"{here}.numa") if "numa" in body else MappingProxyType({})
    gpus = _parse_gpus(body.get("gpus", []), f"{here}.gpus", domains)
    if gpus and vendor == "none":
        raise ValueError(f'{here}.gpu_vendor: GPUs are listed, so it cannot be "none".')
    nics = _parse_nics(body.get("nics", []), f"{here}.nics", domains, len(gpus))
    source = _parse_source(body.get("source", {"kind": "handwritten"}), f"{here}.source")
    return Topology(str(vendor), nic_env, domains, gpus, nics, source)


def _present(**fields: Any) -> dict[str, Any]:
    """The fields that are set: `None` and an empty tuple are left out."""
    return {
        k: list(v) if isinstance(v, tuple) else v for k, v in fields.items() if v not in (None, ())
    }


def to_json(topology: Topology) -> dict[str, Any]:
    """The block in the preset shape; `parse_topology` reads it back to an equal `Topology`."""
    data: dict[str, Any] = {"gpu_vendor": topology.gpu_vendor}
    if topology.nic_env is not None:
        data["nic_env"] = topology.nic_env
    if topology.numa:
        data["numa"] = {str(d): _cores_text(c) for d, c in sorted(topology.numa.items())}
    data["gpus"] = [_present(numa=g.numa, pci=g.pci) for g in topology.gpus]
    data["nics"] = [
        _present(
            dev=n.dev,
            numa=n.numa,
            near_gpu=n.near_gpu,
            card=n.card,
            pci=n.pci,
            dist=n.dist,
            tags=n.tags,
            rate_mbps=n.rate_mbps,
            link_layer=n.link_layer,
        )
        for n in topology.nics
    ]
    data["source"] = dict(topology.source)
    return data


def _cores_text(cores: tuple[int, ...]) -> str:
    """The cores as a list of runs, `(0, 1, 2, 8)` -> `0-2,8`."""
    runs: list[str] = []
    start = previous = cores[0]
    for core in (*cores[1:], None):
        if core is not None and core == previous + 1:
            previous = core
            continue
        runs.append(str(start) if start == previous else f"{start}-{previous}")
        if core is not None:
            start = previous = core
    return ",".join(runs)


def topology_sha256(topology: Topology) -> str:
    """A hash of what placement depends on: everything except `source`, in canonical form."""
    data = to_json(topology)
    del data["source"]
    text = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()
