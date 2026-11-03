"""A preset's `topology` block (ADR-043): validation, the `execution.json` round trip, the hash."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from crab.cli.presets import resolve_presets
from crab.core.execution.settings import from_json, from_preset, to_json
from crab.core.execution.topology import (
    Topology,
    cores_text,
    parse_topology,
    topology_sha256,
)
from crab.core.execution.topology import to_json as topology_to_json

WHERE = "preset 'lab'"

MINIMAL: dict[str, Any] = {
    "gpu_vendor": "nvidia",
    "nic_env": "UCX_NET_DEVICES",
    "numa": {"0": "0-15", "1": "16-31"},
    "gpus": [{"numa": 0}, {"numa": 0}, {"numa": 1}, {"numa": 1}],
    "nics": [
        {"dev": "mlx5_0:1", "numa": 0, "near_gpu": 0},
        {"dev": "mlx5_1:1", "numa": 0, "near_gpu": 1},
        {"dev": "mlx5_2:1", "numa": 1, "near_gpu": 2},
        {"dev": "mlx5_3:1", "numa": 1, "near_gpu": 3},
    ],
}

FULL: dict[str, Any] = {
    "gpu_vendor": "nvidia",
    "nic_env": "UCX_NET_DEVICES",
    "numa": {"0": "0-39,80-119", "1": "40-79,120-159"},
    "gpus": [
        {"numa": 0, "pci": "0000:19:00.0"},
        {"numa": 1, "pci": "0000:bb:00.0"},
    ],
    "nics": [
        {
            "dev": "mlx5_0:1",
            "numa": 0,
            "near_gpu": 0,
            "card": "19:00",
            "pci": "0000:19:00.0",
            "dist": "PIX",
            "tags": ["leaf-L2"],
            "rate_mbps": 400000,
            "link_layer": "Ethernet",
        },
        {"dev": "mlx5_1:1", "numa": 0, "card": "19:00", "dist": "SYS"},
    ],
    "source": {"kind": "probe", "version": "1", "date": "2026-11-01"},
}

SLURM_PRESET: dict[str, Any] = {"scheduler": "slurm", "topology": MINIMAL}


def broken(**changes: Any) -> dict[str, Any]:
    """MINIMAL with the given top-level fields replaced."""
    return {**copy.deepcopy(MINIMAL), **changes}


def test_minimal_block_parses_and_gets_a_handwritten_source() -> None:
    topology = parse_topology(MINIMAL, WHERE)
    assert topology.gpu_vendor == "nvidia"
    assert topology.nic_env == "UCX_NET_DEVICES"
    assert topology.numa[1] == tuple(range(16, 32))
    assert [nic.dev for nic in topology.nics] == ["mlx5_0:1", "mlx5_1:1", "mlx5_2:1", "mlx5_3:1"]
    assert topology.nics[3].near_gpu == 3
    assert topology.source == {"kind": "handwritten"}


def test_full_block_parses_with_every_field() -> None:
    topology = parse_topology(FULL, WHERE)
    assert topology.gpus[1].pci == "0000:bb:00.0"
    nic = topology.nics[0]
    assert (nic.card, nic.dist, nic.tags, nic.rate_mbps, nic.link_layer) == (
        "19:00",
        "PIX",
        ("leaf-L2",),
        400000,
        "Ethernet",
    )
    assert topology.source == {"kind": "probe", "version": "1", "date": "2026-11-01"}


def test_an_empty_block_means_no_gpus_and_no_nics() -> None:
    topology = parse_topology({}, WHERE)
    assert (topology.gpu_vendor, topology.nic_env, topology.gpus, topology.nics) == (
        "none",
        None,
        (),
        (),
    )


@pytest.mark.parametrize("data", [MINIMAL, FULL])
def test_to_json_reads_back_to_an_equal_topology(data: dict[str, Any]) -> None:
    topology = parse_topology(data, WHERE)
    assert parse_topology(topology_to_json(topology), WHERE) == topology


def test_to_json_writes_core_lists_as_runs() -> None:
    assert cores_text((0, 1, 2, 8, 10, 11)) == "0-2,8,10-11"
    assert topology_to_json(parse_topology(FULL, WHERE))["numa"]["1"] == "40-79,120-159"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ("lab", "preset 'lab': topology: must be an object."),
        (broken(speed=1), "topology: unknown key 'speed'"),
        (broken(gpu_vendor="matrox"), "topology.gpu_vendor: must be one of nvidia, amd"),
        (broken(gpu_vendor="none"), 'topology.gpu_vendor: GPUs are listed, so it cannot be "none"'),
        (broken(nic_env="UCX NET"), "topology.nic_env: 'UCX NET' is not valid"),
        (broken(numa={"x": "0-3"}), "topology.numa: key 'x' must be a NUMA domain number"),
        (broken(numa={"0": "0-3", "1": "3-5"}), "topology.numa.1: core 3 is also in domain 0"),
        (broken(numa={"0": "5-3"}), "topology.numa.0: '5-3' has the invalid item '5-3'"),
        (broken(numa={"0": "1,1"}), "topology.numa.0: '1,1' lists a core more than once"),
        (broken(gpus={"numa": 0}), "topology.gpus: must be a list of GPU objects."),
        (
            broken(gpus=[{"numa": 7}]),
            "topology.gpus[0].numa: NUMA domain 7 is not in topology.numa",
        ),
        (broken(gpus=[{"pci": "19:00.0"}]), "topology.gpus[0].pci: '19:00.0' is not valid"),
        (broken(gpus=[{"cuda": 0}]), "topology.gpus[0]: unknown key 'cuda'"),
        (broken(nics=[{"numa": 0}]), 'topology.nics[0].dev: required, such as "mlx5_0:1".'),
        (broken(nics=[{"dev": "mlx5 0"}]), "topology.nics[0].dev: 'mlx5 0' is not valid"),
        (
            broken(nics=[{"dev": "mlx5_0:1", "near_gpu": 4}]),
            "topology.nics[0].near_gpu: 4 is not a GPU index (topology.gpus has 4 entries)",
        ),
        (
            broken(nics=[{"dev": "mlx5_0:1"}, {"dev": "mlx5_0:1"}]),
            "topology.nics[1].dev: 'mlx5_0:1' is already entry 0",
        ),
        (
            broken(nics=[{"dev": "mlx5_0:1", "dist": "FAR"}]),
            "topology.nics[0].dist: must be one of PIX, PXB, PHB, NODE, SYS",
        ),
        (
            broken(nics=[{"dev": "mlx5_0:1", "tags": "leaf"}]),
            "topology.nics[0].tags: must be a list of strings.",
        ),
        (
            broken(nics=[{"dev": "mlx5_0:1", "tags": [None]}]),
            "topology.nics[0].tags[0]: must be a string, got None.",
        ),
        (
            broken(nics=[{"dev": "mlx5_0:1", "tags": [3]}]),
            "topology.nics[0].tags[0]: must be a string, got 3.",
        ),
        (
            broken(nics=[{"dev": "mlx5_0:1", "tags": ["ok", None]}]),
            "topology.nics[0].tags[1]: must be a string, got None.",
        ),
        (
            broken(nics=[{"dev": "mlx5_0:1", "rate_mbps": 0}]),
            "topology.nics[0].rate_mbps: must be a positive integer",
        ),
        (broken(source={"date": "2026-11-01"}), "topology.source.kind: required"),
        (
            broken(source={"kind": "probe", "date": "yesterday"}),
            "topology.source.date: must look like 2026-11-01",
        ),
    ],
)
def test_invalid_blocks_name_the_field(data: Any, message: str) -> None:
    with pytest.raises(ValueError) as error:
        parse_topology(data, WHERE)
    assert message in str(error.value)
    assert str(error.value).startswith(WHERE)


def _gpus_at(*addresses: str | None) -> dict[str, Any]:
    """MINIMAL with one GPU per address (None leaves the pci out) and no NICs."""
    gpus = [
        {"numa": 0} if address is None else {"numa": 0, "pci": address} for address in addresses
    ]
    return broken(gpus=gpus, nics=[])


def test_gpus_listed_out_of_pci_order_are_rejected() -> None:
    with pytest.raises(ValueError) as error:
        parse_topology(_gpus_at("0000:c1:00.0", "0000:41:00.0"), WHERE)
    text = str(error.value)
    assert text.startswith(f"{WHERE}: topology.gpus[1].pci")
    assert "0000:41:00.0 comes after 0000:c1:00.0" in text
    assert "ascending PCI order" in text


def test_upper_case_pci_addresses_are_not_valid_at_all() -> None:
    """PCI_ADDRESS is lower-case only, so the order check never meets an upper-case address."""
    with pytest.raises(ValueError, match=r"gpus\[1\]\.pci: '0000:C1:00\.0' is not valid"):
        parse_topology(_gpus_at("0000:41:00.0", "0000:C1:00.0"), WHERE)


def test_gpus_in_ascending_pci_order_keep_their_text() -> None:
    addresses = ["0000:41:00.0", "0000:61:00.0", "0000:c1:00.0", "0000:e1:00.0"]
    parsed = parse_topology(_gpus_at(*addresses), WHERE)
    assert [gpu.pci for gpu in parsed.gpus] == addresses


def test_pci_order_is_not_checked_when_a_gpu_has_no_address() -> None:
    parsed = parse_topology(_gpus_at("0000:c1:00.0", None, "0000:41:00.0"), WHERE)
    assert [gpu.pci for gpu in parsed.gpus] == ["0000:c1:00.0", None, "0000:41:00.0"]


def test_two_gpus_cannot_share_a_pci_address() -> None:
    with pytest.raises(ValueError) as error:
        parse_topology(_gpus_at("0000:41:00.0", "0000:41:00.0"), WHERE)
    assert str(error.value).startswith(f"{WHERE}: topology.gpus[1].pci")
    assert "ascending PCI order" in str(error.value)


def test_hash_ignores_source_and_changes_with_the_devices() -> None:
    first = parse_topology(FULL, WHERE)
    reprobed = parse_topology({**FULL, "source": {"kind": "probe", "date": "2027-01-01"}}, WHERE)
    assert topology_sha256(first) == topology_sha256(reprobed)
    changed = copy.deepcopy(FULL)
    changed["nics"][0]["dev"] = "mlx5_9:1"
    assert topology_sha256(first) != topology_sha256(parse_topology(changed, WHERE))
    assert len(topology_sha256(first)) == 64


def test_hash_does_not_depend_on_key_order() -> None:
    reordered = dict(reversed(list(FULL.items())))
    assert topology_sha256(parse_topology(reordered, WHERE)) == topology_sha256(
        parse_topology(FULL, WHERE)
    )


def test_a_preset_carries_its_topology_in_the_settings_and_execution_json() -> None:
    settings = from_preset(SLURM_PRESET, "lab")
    assert isinstance(settings.topology, Topology)
    assert settings.topology == parse_topology(MINIMAL, WHERE)
    data = to_json(settings)
    assert data["topology"] == topology_to_json(settings.topology)
    assert from_json(data) == settings


def test_a_preset_without_topology_writes_none_into_execution_json() -> None:
    settings = from_preset({"scheduler": "slurm"}, "lab")
    assert settings.topology is None
    assert "topology" not in to_json(settings)


def test_a_bad_block_is_refused_with_the_preset_name() -> None:
    with pytest.raises(ValueError, match=r"preset 'lab': topology.gpu_vendor"):
        from_preset({"scheduler": "slurm", "topology": broken(gpu_vendor="matrox")}, "lab")


def test_a_variant_replaces_the_parents_topology_whole() -> None:
    parent = {"scheduler": "slurm", "topology": MINIMAL}
    variant_topology = {"gpu_vendor": "amd", "gpus": [{}, {}], "nics": [{"dev": "hsn0"}]}
    files = [
        (
            Path("shared/a.json"),
            {"base": parent, "gpu": {"extends": "base", "topology": variant_topology}},
        )
    ]
    resolved = resolve_presets([files], [])
    assert resolved["base"]["topology"] == MINIMAL
    assert resolved["gpu"]["topology"] == variant_topology


def test_a_variant_without_topology_inherits_its_parents() -> None:
    parent = {"scheduler": "slurm", "topology": MINIMAL}
    files = [(Path("shared/a.json"), {"base": parent, "plain": {"extends": "base"}})]
    assert resolve_presets([files], [])["plain"]["topology"] == MINIMAL


def test_an_override_replaces_the_shared_topology_whole() -> None:
    shared = [(Path("shared/a.json"), {"lab": {"scheduler": "slurm", "topology": MINIMAL}})]
    patch = {"gpu_vendor": "nvidia", "gpus": [{}], "nics": [{"dev": "mlx5_0:1"}]}
    override = [(Path("override/mine.json"), {"lab": {"topology": patch}})]
    assert resolve_presets([shared], override)["lab"]["topology"] == patch


SMT: dict[str, Any] = {
    "gpu_vendor": "none",
    "numa": {"0": "0-3,8-11", "1": "4-7,12-15"},
    "physical": {"0": "0-3", "1": "4-7"},
}


def test_physical_cores_parse_round_trip_and_change_the_hash() -> None:
    topology = parse_topology(SMT, WHERE)
    assert topology.physical == {0: (0, 1, 2, 3), 1: (4, 5, 6, 7)}
    assert topology_to_json(topology)["physical"] == {"0": "0-3", "1": "4-7"}
    assert parse_topology(topology_to_json(topology), WHERE) == topology
    without = parse_topology({k: v for k, v in SMT.items() if k != "physical"}, WHERE)
    assert without.physical == {} and "physical" not in topology_to_json(without)
    assert topology_sha256(topology) != topology_sha256(without)


@pytest.mark.parametrize(
    ("physical", "message"),
    [
        ("0-3", "topology.physical: must be an object of NUMA id to core list."),
        ({"x": "0-3"}, "topology.physical: key 'x' must be a NUMA domain of topology.numa (0, 1)."),
        ({"2": "0-3"}, "topology.physical: key '2' must be a NUMA domain of topology.numa (0, 1)."),
        ({"0": "0-3,12"}, "topology.physical.0: core 12 is not in topology.numa.0."),
        ({"0": "3-1"}, "topology.physical.0: '3-1' has the invalid item '3-1'"),
    ],
)
def test_invalid_physical_blocks_name_the_field(physical: Any, message: str) -> None:
    with pytest.raises(ValueError) as error:
        parse_topology({**SMT, "physical": physical}, WHERE)
    assert message in str(error.value) and str(error.value).startswith(WHERE)


def test_physical_without_numa_names_the_missing_domains() -> None:
    with pytest.raises(ValueError, match=r"topology.numa \(none\)"):
        parse_topology({"physical": {"0": "0-3"}}, WHERE)
