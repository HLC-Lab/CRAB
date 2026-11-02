"""The Leonardo GPU examples: Blink on GPU memory, one rank per GPU, no cluster value in the file.

`examples/leonardo/gpu/` holds an all-to-all and an allgather. The GPU request and the topology belong to the
`leonardo_gpu` preset; the configs only say how many ranks run and how each rank is bound.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "leonardo" / "gpu"
WRAPPER_OF = {
    "alltoall.json": "blink/alltoall/alltoall_comm_only.py",
    "allgather.json": "blink/allgather/allgather_comm_only.py",
}
FILES = sorted(WRAPPER_OF)


def load(name: str) -> dict[str, Any]:
    config: dict[str, Any] = json.loads((EXAMPLES / name).read_text())
    return config


def msgsize_of(args: str) -> str:
    words = args.split()
    return words[words.index("-msgsize") + 1]


@pytest.mark.parametrize("name", FILES)
def test_the_gpu_examples_exist(name: str) -> None:
    assert (EXAMPLES / name).is_file()
    assert load(name)["schema_version"] == 2


@pytest.mark.parametrize("name", FILES)
def test_every_app_is_one_rank_per_gpu_on_gpu_memory(name: str) -> None:
    config = load(name)
    assert config["global_options"]["ppn"] == "4"
    for exp, body in config["experiments"].items():
        assert list(body["apps"]) == ["0"], exp
        app = body["apps"]["0"]
        assert app["bind"] == {"gpu": "per-rank", "nic": "nearest", "cores": "nearest"}, exp
        assert "-gpu" in app["args"].split(), exp
        assert "-msgsize" in app["args"].split(), exp
        assert app["collect"] is True, exp
        assert app["path"] == WRAPPER_OF[name], exp


@pytest.mark.parametrize("name", FILES)
def test_the_examples_hold_no_cluster_value(name: str) -> None:
    text = (EXAMPLES / name).read_text()
    for forbidden in (
        "mlx5",
        "cpus",
        '"nics"',
        "partition",
        "gres",
        "CUDA_VISIBLE",
        "UCX_NET",
        "account",
    ):
        assert forbidden not in text, (name, forbidden)


@pytest.mark.parametrize("name", FILES)
def test_each_file_has_two_message_sizes_that_differ(name: str) -> None:
    experiments = load(name)["experiments"]
    assert len(experiments) >= 2
    sizes = {msgsize_of(body["apps"]["0"]["args"]) for body in experiments.values()}
    assert len(sizes) >= 2
