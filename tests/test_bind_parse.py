"""An app's `bind` key (ADR-043): parsing, defaults and the combinations that are refused."""

from __future__ import annotations

from typing import Any

import pytest

from crab.core.execution.bind import Bind, NicRule, parse_bind

WHERE = "app 0"


def test_an_empty_bind_changes_nothing() -> None:
    assert parse_bind({}, WHERE) == Bind()
    assert Bind().gpu == "none" and Bind().nic == NicRule("default") and Bind().cores == "launcher"


def test_the_headline_example_parses() -> None:
    bind = parse_bind({"gpu": "per-rank", "nic": "nearest", "cores": "nearest"}, WHERE)
    assert bind == Bind(
        gpu="per-rank", nic=NicRule("nearest"), cores="nearest", smt=False, order="pack"
    )


@pytest.mark.parametrize("gpu", ["none", "per-rank", "shared", "all"])
def test_every_gpu_mode_parses(gpu: str) -> None:
    assert parse_bind({"gpu": gpu}, WHERE).gpu == gpu


@pytest.mark.parametrize("pick", ["default", "nearest", "far", "spread", "single"])
def test_every_nic_pick_parses_as_a_string(pick: str) -> None:
    body = {"gpu": "per-rank", "nic": pick}
    assert parse_bind(body, WHERE).nic == NicRule(pick)


def test_the_nic_object_forms() -> None:
    ports = parse_bind(
        {"gpu": "per-rank", "nic": {"pick": "nearest", "ports": 2, "exclusive": True}}, WHERE
    )
    assert ports.nic == NicRule("nearest", ports=2, exclusive=True)
    assert parse_bind({"nic": {"tag": "leaf-L2"}}, WHERE).nic == NicRule("tag", tag="leaf-L2")
    assert parse_bind({"nic": {"pick": "spread"}}, WHERE).nic == NicRule("spread", ports=1)


def test_the_cores_forms() -> None:
    assert parse_bind({"cores": "nearest"}, WHERE).smt is False
    smt = parse_bind({"cores": {"smt": True}}, WHERE)
    assert (smt.cores, smt.smt) == ("nearest", True)
    plain = parse_bind({"cores": {"smt": False}}, WHERE)
    assert (plain.cores, plain.smt) == ("nearest", False)


def test_order_spread_parses() -> None:
    assert parse_bind({"gpu": "per-rank", "order": "spread"}, WHERE).order == "spread"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("per-rank", "app 0: bind: must be an object."),
        ({"gpus": "per-rank"}, "app 0: bind: unknown key 'gpus'; allowed: gpu, nic, cores, order."),
        ({"gpu": "each"}, "app 0: bind.gpu: 'each' must be one of none, per-rank, shared, all."),
        (
            {"nic": "closest"},
            "app 0: bind.nic: 'closest' must be one of default, nearest, far, spread, single.",
        ),
        ({"nic": 3}, "app 0: bind.nic: must be an object."),
        (
            {"nic": {"ports": 2}},
            'app 0: bind.nic: needs "pick" (one of default, nearest, far, spread, single) or "tag".',
        ),
        ({"nic": {"exclusive": True}}, 'app 0: bind.nic: needs "pick"'),
        (
            {"nic": {"pick": "spread", "ports": 0}},
            "app 0: bind.nic.ports: must be an integer of at least 1, got 0.",
        ),
        (
            {"nic": {"pick": "spread", "ports": True}},
            "app 0: bind.nic.ports: must be an integer of at least 1, got True.",
        ),
        (
            {"nic": {"pick": "spread", "exclusive": "yes"}},
            "app 0: bind.nic.exclusive: must be true or false",
        ),
        (
            {"nic": {"pick": "spread", "speed": 1}},
            "app 0: bind.nic: unknown key 'speed'; allowed: pick, ports, exclusive, tag.",
        ),
        ({"nic": {"tag": "a b"}}, "app 0: bind.nic.tag: must be a label"),
        (
            {"nic": {"tag": "x", "pick": "far"}},
            "app 0: bind.nic: a tag rule takes no other key, got pick, tag.",
        ),
        ({"cores": "all"}, "app 0: bind.cores: 'all' must be one of launcher, nearest."),
        (
            {"cores": {"smt": "yes"}},
            'app 0: bind.cores: the object form is {"smt": true} or {"smt": false}',
        ),
        ({"cores": {"threads": 2}}, "app 0: bind.cores: unknown key 'threads'; allowed: smt."),
        ({"order": "random"}, "app 0: bind.order: 'random' must be one of pack, spread."),
    ],
)
def test_invalid_values_name_the_key_and_the_allowed_values(body: Any, message: str) -> None:
    with pytest.raises(ValueError) as error:
        parse_bind(body, WHERE)
    assert message in str(error.value)
    assert str(error.value).startswith(WHERE)


@pytest.mark.parametrize("pick", ["nearest", "far"])
@pytest.mark.parametrize("gpu", ["none", "all"])
def test_a_nic_near_nothing_is_refused(pick: str, gpu: str) -> None:
    with pytest.raises(ValueError, match="nothing places the rank") as error:
        parse_bind({"gpu": gpu, "nic": pick}, WHERE)
    assert f'"{pick}"' in str(error.value) and 'bind.gpu to "per-rank" or "shared"' in str(
        error.value
    )


@pytest.mark.parametrize(
    "body",
    [
        {"gpu": "per-rank", "nic": "nearest"},
        {"gpu": "shared", "nic": "far"},
        {"cores": "nearest", "nic": "nearest"},
        {"cores": {"smt": True}, "nic": "far"},
        {"gpu": "none", "nic": "spread"},
        {"gpu": "none", "nic": "single"},
        {"nic": {"tag": "x"}},
    ],
)
def test_a_nic_with_something_to_be_near_is_accepted(body: dict[str, Any]) -> None:
    parse_bind(body, WHERE)


def test_the_where_prefix_is_used_as_given() -> None:
    with pytest.raises(ValueError) as error:
        parse_bind({"gpu": "x"}, "experiment 'a2a', app 3")
    assert str(error.value).startswith("experiment 'a2a', app 3: bind.gpu")
