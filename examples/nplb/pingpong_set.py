"""The ping-pong set of the NPLB campaign: bandwidth ceilings per node pair (see make_configs.py).

One job per node pair, from the first node of the profile to the second, third and fourth, with
one rank per node over a size sweep and three port variants:

* `md`: the first card's first port, the campaign's UCX variables (one rail).
* `default`: the same port, no UCX variable at all (what an unconfigured application gets).
* `dual`: both ports of the first card, `UCX_NET_DEVICES` listing them and two rails. The
  `nics` key allows one device per rank, so the device list goes through `env`.

A last job runs a pairwise permutation with the profile's ranks per node (one port per card) on
the first and third node (the first and second with only two nodes): the node total, as `pw<N>`
for N ranks per node (`--ranks` narrows the profile). It has two experiments: with the
campaign's UCX variables, and (`pw<N>d`) without any.

This is the only set that writes node names (a `--nodelist` of each job, in the profile's order).
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from md_set import MD_SIZES
from naming import experiment_name

if TYPE_CHECKING:
    from make_configs import Built, Placement, Settings

PINGPONG = "blink/pingpong_b.py"
PAIRWISE = "blink/pairwise_nb.py"
PARTNERS = 3  # the first node against the second, third and fourth
PW_PARTNER = 3  # the node total runs on the first and third node (the second when only 2)
_FUNCTION = re.compile(r"^(?P<card>.+)\.(?P<function>\d+)$")


def card_of(pci: str) -> str:
    """The card of a PCI address: it without its function (`0000:19:00.1` -> `0000:19:00`)."""
    match = _FUNCTION.match(pci)
    return match["card"] if match else pci


def natural_key(name: str) -> list[int | str]:
    """Sort key putting `mlx5_2:1` before `mlx5_10:1`."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def sibling_port(nic: str, all_ports: dict[str, Any]) -> tuple[str | None, str]:
    """(the other port of `nic`'s card in the all-ports profile, or None; the reason when None)."""
    per_nic = all_ports.get("per_nic") or {}
    pci = (per_nic.get(nic) or {}).get("pci")
    if not pci:
        return None, f"{nic} has no PCI address in the all-ports profile"
    card = card_of(pci)
    others = sorted(
        (
            name
            for name, info in per_nic.items()
            if name != nic and info.get("pci") and card_of(info["pci"]) == card
        ),
        key=natural_key,
    )
    if not others:
        return None, f"{nic} has no second port on its card in the all-ports profile"
    return others[0], ""


def pair_label(first: int, second: int) -> str:
    """`n1n3` for the profile's first and third node (1-based)."""
    return f"n{first}n{second}"


def sweep(
    cfg: Settings, path: str, args: str, where: Placement, paths: int, **kwargs: Any
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The size sweep of one experiment: one app per size, chained, with the md set's `-grty`."""
    return cfg.chain(
        path,
        args,
        cfg.sizes(MD_SIZES),
        where,
        paths,
        head_start="0",
        partition=None,
        memory=("pp", 2),
        grty=True,
        **kwargs,
    )


def build_pair(
    cfg: Settings, stem: str, net: str, paths: int, pair: tuple[int, int], all_ports: dict[str, Any]
) -> Built:
    """One node pair: the md, default and dual variants (dual only when the card has a sibling)."""
    nodes = tuple(cfg.prof.nodes[i - 1] for i in pair)
    label = pair_label(*pair)
    out, where = cfg.collector(), cfg.single_rank_placement()
    nic = where.nics[0]
    what = f"Ping-pong between node {pair[0]} and node {pair[1]}, one rank per node, size sweep"
    apps, skipped = sweep(cfg, PINGPONG, "", where, paths)
    out.add(
        experiment_name("pp", f"{label}-md", net, paths),
        f"{what}, on {nic} with one rail (the campaign's UCX settings).",
        apps,
        skipped,
    )
    apps, skipped = sweep(cfg, PINGPONG, "", where, paths, ucx_env=False)
    out.add(
        experiment_name("pp", f"{label}-default", net, paths),
        f"{what}, on {nic} with no UCX variable set.",
        apps,
        skipped,
    )
    other, why = sibling_port(nic, all_ports)
    dual = experiment_name("pp", f"{label}-dual", net, paths)
    if other is None:
        out.skipped[dual] = [f"dual omitted: {why}"]
    else:
        env = {"UCX_NET_DEVICES": f"{nic},{other}", "UCX_MAX_RNDV_RAILS": "2"}
        apps, skipped = sweep(cfg, PINGPONG, "", replace(where, nics=()), paths, env=env)
        out.add(dual, f"{what}, on {nic} and {other} with two rails.", apps, skipped)
    return out.built(cfg.config(stem, net, "pingpong", None, ppn=1, nodelist=nodes))


def build_node_total(
    cfg: Settings, stem: str, net: str, paths: int, pair: tuple[int, int]
) -> Built:
    """The pairwise permutation with every rank of the node, one port per card.

    Two experiments over the same apps: `pw<N>` with the campaign's UCX variables, `pw<N>d`
    with none (the NIC choice stays in the `nics` key).
    """
    nodes = tuple(cfg.prof.nodes[i - 1] for i in pair)
    ppn = cfg.prof.ppn
    out, where, label = cfg.collector(), cfg.placement(), pair_label(*pair)
    text = (
        f"Pairwise permutation between node {pair[0]} and node {pair[1]}, {ppn} ranks per node "
        f"(one port per card), over a size sweep: the node total"
    )
    for suffix, ucx_env, what in (
        ("", True, "with the campaign's UCX settings"),
        ("d", False, "with no UCX variable set"),
    ):
        apps, skipped = sweep(
            cfg, PAIRWISE, f"-mode rot -offset {ppn}", where, paths, ucx_env=ucx_env
        )
        name = experiment_name("pp", f"{label}-pw{ppn}{suffix}", net, paths)
        out.add(name, f"{text}, {what}.", apps, skipped)
    return out.built(cfg.config(stem, net, "pingpong", None, ppn=ppn, nodelist=nodes))


def build_pingpong_files(
    cfg: Settings, prefix: str, net: str, paths: int, all_ports: dict[str, Any]
) -> dict[str, Built]:
    """The files of one network config and path count: {file stem: Built}.

    One per pair (the first node against up to three others) and the node total.
    """
    count = len(cfg.prof.nodes)
    files: dict[str, Built] = {}
    for second in range(2, min(count, PARTNERS + 1) + 1):
        stem = f"{prefix}pp_{pair_label(1, second)}_{net}_P{paths}"
        files[stem] = build_pair(cfg, stem, net, paths, (1, second), all_ports)
    total = (1, min(count, PW_PARTNER))
    stem = f"{prefix}pp_{pair_label(*total)}_pw{cfg.prof.ppn}_{net}_P{paths}"
    files[stem] = build_node_total(cfg, stem, net, paths, total)
    return files
