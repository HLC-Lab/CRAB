"""An app's `bind` key: what it wants placed, never which device (ADR-043).

`"bind": {"gpu": "per-rank", "nic": "nearest", "cores": "nearest"}` states intent; `bind_resolve`
turns it into devices with the preset's `topology`. Every error is a `ValueError` that starts with
where the problem is, such as `app 0: bind.nic`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

GPU_MODES = ("none", "per-rank", "shared", "all")
NIC_PICKS = ("default", "nearest", "far", "spread", "single")
CORES_MODES = ("launcher", "nearest")
ORDERS = ("pack", "spread")
BIND_KEYS = ("gpu", "nic", "cores", "order")
NIC_KEYS = ("pick", "ports", "exclusive", "tag")
TAG = re.compile(r"[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class NicRule:
    """How NICs are chosen. `tag` selects the NICs a preset labelled; otherwise `pick` does."""

    pick: str = "default"
    ports: int = 1
    exclusive: bool = False
    tag: str | None = None


@dataclass(frozen=True)
class Bind:
    """The parsed `bind` key. `smt` lets `cores: nearest` use the SMT siblings too."""

    gpu: str = "none"
    nic: NicRule = NicRule()
    cores: str = "launcher"
    smt: bool = False
    order: str = "pack"


def _choice(value: object, where: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise ValueError(f"{where}: {value!r} must be one of {', '.join(allowed)}.")
    return str(value)


def _object(value: object, where: str, allowed: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where}: must be an object.")
    for key in value:
        if key not in allowed:
            raise ValueError(f"{where}: unknown key {key!r}; allowed: {', '.join(allowed)}.")
    return value


def _parse_nic(value: object, where: str) -> NicRule:
    if isinstance(value, str):
        return NicRule(pick=_choice(value, where, NIC_PICKS))
    body = _object(value, where, NIC_KEYS)
    if "tag" in body:
        if set(body) != {"tag"}:
            raise ValueError(
                f"{where}: a tag rule takes no other key, got {', '.join(sorted(body))}."
            )
        tag = body["tag"]
        if not isinstance(tag, str) or not TAG.fullmatch(tag):
            raise ValueError(
                f"{where}.tag: must be a label of letters, digits and _ . -, got {tag!r}."
            )
        return NicRule(pick="tag", tag=tag)
    if "pick" not in body:
        raise ValueError(f'{where}: needs "pick" (one of {", ".join(NIC_PICKS)}) or "tag".')
    ports = body.get("ports", 1)
    if type(ports) is not int or ports < 1:
        raise ValueError(f"{where}.ports: must be an integer of at least 1, got {ports!r}.")
    exclusive = body.get("exclusive", False)
    if not isinstance(exclusive, bool):
        raise ValueError(f"{where}.exclusive: must be true or false, got {exclusive!r}.")
    return NicRule(
        pick=_choice(body["pick"], f"{where}.pick", NIC_PICKS), ports=ports, exclusive=exclusive
    )


def _parse_cores(value: object, where: str) -> tuple[str, bool]:
    """(mode, smt): `{"smt": true}` means `nearest` that also takes the SMT siblings."""
    if isinstance(value, str):
        return _choice(value, where, CORES_MODES), False
    body = _object(value, where, ("smt",))
    smt = body.get("smt")
    if not isinstance(smt, bool):
        raise ValueError(
            f'{where}: the object form is {{"smt": true}} or {{"smt": false}}, got {value!r}.'
        )
    return "nearest", smt


def parse_bind(value: object, where: str) -> Bind:
    """Validate an app's `bind`. `where` names the app, e.g. `app 0`.

    Raises:
        ValueError: an unknown key or value, or a combination that places nothing: `nic` nearest or
            far needs a GPU or cores to be near to.
    """
    here = f"{where}: bind"
    body = _object(value, here, BIND_KEYS)
    gpu = _choice(body.get("gpu", "none"), f"{here}.gpu", GPU_MODES)
    nic = _parse_nic(body.get("nic", "default"), f"{here}.nic")
    cores, smt = _parse_cores(body.get("cores", "launcher"), f"{here}.cores")
    order = _choice(body.get("order", "pack"), f"{here}.order", ORDERS)
    if nic.pick in ("nearest", "far") and gpu in ("none", "all") and cores == "launcher":
        raise ValueError(
            f'{here}.nic: "{nic.pick}" is near to something: nothing places the rank, '
            'so set bind.gpu to "per-rank" or "shared", or bind.cores to "nearest".'
        )
    return Bind(gpu=gpu, nic=nic, cores=cores, smt=smt, order=order)
