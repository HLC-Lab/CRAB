"""Topology shapes for the `bind` tests. None is named after a cluster: each is a layout that exists.

`SHAPES` maps a shape name to a function that builds its `Topology`. Tests that must hold on any
cluster run over all of them; a test that needs one layout's device names builds that shape itself.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from crab.core.execution.topology import Topology, parse_topology


def make(block: dict[str, Any]) -> Topology:
    return parse_topology(block, "preset 'shape'")


def four_gpus_four_nics() -> Topology:
    """Two sockets, two GPUs and two NICs on each, every GPU with a NIC of its own."""
    return make(
        {
            "gpu_vendor": "nvidia",
            "nic_env": "UCX_NET_DEVICES",
            "numa": {"0": "0-7", "1": "8-15"},
            "gpus": [{"numa": 0}, {"numa": 0}, {"numa": 1}, {"numa": 1}],
            "nics": [
                {"dev": f"nic{k}:1", "numa": 0 if k < 2 else 1, "near_gpu": k, "dist": "PIX"}
                for k in range(4)
            ],
        }
    )


def eight_gpus_two_nics() -> Topology:
    """Eight GPUs and one NIC per socket: most GPUs have no NIC claiming them."""
    return make(
        {
            "gpu_vendor": "nvidia",
            "nic_env": "UCX_NET_DEVICES",
            "numa": {"0": "0-15", "1": "16-31"},
            "gpus": [{"numa": 0}] * 4 + [{"numa": 1}] * 4,
            "nics": [
                {"dev": "nic0:1", "numa": 0, "near_gpu": 0, "dist": "PIX"},
                {"dev": "nic1:1", "numa": 1, "near_gpu": 4, "dist": "PIX"},
            ],
        }
    )


def two_gpus_two_port_cards() -> Topology:
    """One GPU per socket, each with a card of two ports."""
    return make(
        {
            "gpu_vendor": "nvidia",
            "nic_env": "UCX_NET_DEVICES",
            "numa": {"0": "0-7", "1": "8-15"},
            "gpus": [{"numa": 0}, {"numa": 1}],
            "nics": [
                {"dev": "rdma0:1", "numa": 0, "near_gpu": 0, "card": "1a:00", "dist": "PIX"},
                {"dev": "rdma1:1", "numa": 0, "near_gpu": 0, "card": "1a:00", "dist": "PIX"},
                {"dev": "rdma2:1", "numa": 1, "near_gpu": 1, "card": "2a:00", "dist": "PIX"},
                {"dev": "rdma3:1", "numa": 1, "near_gpu": 1, "card": "2a:00", "dist": "PIX"},
            ],
        }
    )


def amd_single_socket() -> Topology:
    """One NUMA domain, GPUs and NIC without a numa field (the single domain is implied)."""
    return make(
        {
            "gpu_vendor": "amd",
            "nic_env": "UCX_NET_DEVICES",
            "numa": {"0": "0-7"},
            "gpus": [{}, {}],
            "nics": [{"dev": "nic0:1", "near_gpu": 0, "dist": "PHB"}],
        }
    )


def smt_two_sockets() -> Topology:
    """Two sockets with SMT: siblings are 8 apart, `physical` lists the first thread of each core."""
    return make(
        {
            "gpu_vendor": "nvidia",
            "nic_env": "UCX_NET_DEVICES",
            "numa": {"0": "0-3,8-11", "1": "4-7,12-15"},
            "physical": {"0": "0-3", "1": "4-7"},
            "gpus": [{"numa": 0}, {"numa": 0}, {"numa": 1}, {"numa": 1}],
            "nics": [
                {"dev": "nic0:1", "numa": 0, "near_gpu": 0, "dist": "PIX"},
                {"dev": "nic1:1", "numa": 1, "near_gpu": 2, "dist": "PIX"},
            ],
        }
    )


def cpu_only() -> Topology:
    """A node without GPUs and one NIC."""
    return make(
        {
            "gpu_vendor": "none",
            "nic_env": "UCX_NET_DEVICES",
            "numa": {"0": "0-7"},
            "nics": [{"dev": "nic0:1", "numa": 0}],
        }
    )


def no_nic_selection() -> Topology:
    """GPUs and NICs, but a stack that cannot select a NIC per rank (`nic_env` is null)."""
    block = four_gpus_four_nics()
    return make(
        {
            "gpu_vendor": block.gpu_vendor,
            "numa": {"0": "0-7", "1": "8-15"},
            "gpus": [{"numa": 0}, {"numa": 0}, {"numa": 1}, {"numa": 1}],
            "nics": [{"dev": "nic0:1", "numa": 0, "near_gpu": 0}],
        }
    )


SHAPES: dict[str, Callable[[], Topology]] = {
    "four-gpus-four-nics": four_gpus_four_nics,
    "eight-gpus-two-nics": eight_gpus_two_nics,
    "two-gpus-two-port-cards": two_gpus_two_port_cards,
    "amd-single-socket": amd_single_socket,
    "smt-two-sockets": smt_two_sockets,
    "cpu-only": cpu_only,
    "no-nic-selection": no_nic_selection,
}
