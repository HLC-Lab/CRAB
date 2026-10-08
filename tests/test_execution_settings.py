"""Parsing a preset's execution settings, the `execution.json` round trip and launcher choice."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from crab.core.execution.hosts import Host
from crab.core.execution.settings import (
    DROPPED_KEYS,
    SLURM_DEFAULT,
    ExecutionSettings,
    LauncherSpec,
    check_dropped_keys,
    from_json,
    from_preset,
    resolve_launcher,
    to_json,
)

LAB: dict[str, Any] = {
    "description": "Two workstations",
    "scheduler": "local",
    "exclusive": False,
    "hosts": ["ws1:32", "ws2:32"],
    "launcher": "mpirun",
    "launchers": {
        "srun": {"flags": ["--cpu-bind=socket"]},
        "mpirun": {
            "command": "/opt/ompi/bin/mpirun",
            "dialect": "openmpi5",
            "flags": ["--map-by", "node"],
            "export": ["UCX_TLS"],
        },
    },
    "env": {"CRAB_ROOT": "/x", "OMP_NUM_THREADS": "1"},
    "sbatch": [],
    "header": [],
}


def preset(**fields: Any) -> dict[str, Any]:
    return {"scheduler": "slurm", **fields}


def lab(**changes: Any) -> dict[str, Any]:
    data = copy.deepcopy(LAB)
    data.update(changes)
    return data


def refuse(data: object, *fragments: str, name: str = "lab") -> None:
    with pytest.raises(ValueError) as info:
        from_preset(data, name)
    for fragment in fragments:
        assert fragment in str(info.value)


def test_minimal_slurm() -> None:
    s = from_preset({"scheduler": "slurm"}, "p")
    assert (s.scheduler, s.launcher, s.hosts, s.exclusive, dict(s.env)) == (
        "slurm",
        "srun",
        (),
        False,
        {},
    )
    assert s.srun == LauncherSpec("srun", ("srun",), "", (), ())
    assert s.mpirun == LauncherSpec("mpirun", ("mpirun",), "auto", (), ())


def test_minimal_local() -> None:
    s = from_preset({"scheduler": "local"}, "p")
    assert s.launcher == "direct"
    assert s.hosts == (Host("localhost", None),)
    assert s.exclusive is True


def test_full_example() -> None:
    s = from_preset(LAB, "lab")
    assert s.scheduler == "local"
    assert s.exclusive is False
    assert s.hosts == (Host("ws1", 32), Host("ws2", 32))
    assert s.launcher == "mpirun"
    assert s.srun.flags == ("--cpu-bind=socket",)
    assert s.mpirun == LauncherSpec(
        "mpirun", ("/opt/ompi/bin/mpirun",), "openmpi5", ("--map-by", "node"), ("UCX_TLS",)
    )
    assert dict(s.env) == {"CRAB_ROOT": "/x", "OMP_NUM_THREADS": "1"}


@pytest.mark.parametrize("path", ["~/h.txt", "/abs/h.txt"])
def test_hostfile_is_read_from_the_expanded_path(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", "/home/u")
    asked: list[str] = []

    def read_file(p: str) -> str:
        asked.append(p)
        return "a:4\nb slots=8  # comment\n"

    s = from_preset({"scheduler": "local", "hostfile": path}, "lab", read_file=read_file)
    assert asked == [path.replace("~", "/home/u")]
    assert s.hosts == (Host("a", 4), Host("b", 8))


def test_hostfile_syntax_error_names_the_file() -> None:
    refuse_args = {"scheduler": "local", "hostfile": "/abs/h.txt"}
    with pytest.raises(ValueError) as info:
        from_preset(refuse_args, "lab", read_file=lambda p: "a\n")
    assert "preset 'lab': hostfile" in str(info.value)
    assert "/abs/h.txt line 1" in str(info.value)


def test_unknown_top_level_key() -> None:
    refuse(lab(schedular="x"), "preset 'lab': unknown key 'schedular'", "scheduler", "launchers")


def test_preset_must_be_an_object() -> None:
    refuse([], "preset 'lab'", "object")


@pytest.mark.parametrize("value", [None, "pbs", 3])
def test_scheduler_is_required_and_checked(value: object) -> None:
    refuse({} if value is None else {"scheduler": value}, "preset 'lab': scheduler")


def test_exclusive_must_be_bool() -> None:
    refuse(lab(exclusive="yes"), "preset 'lab': exclusive", "true or false")


@pytest.mark.parametrize(
    ("field", "value"), [("exclusive", True), ("hosts", ["a:1"]), ("hostfile", "/h")]
)
def test_local_only_fields_under_slurm(field: str, value: object) -> None:
    refuse(preset(**{field: value}), f"preset 'lab': {field}", "local only")


def test_hosts_and_hostfile_together() -> None:
    refuse(lab(hostfile="/h"), "preset 'lab': hostfile", "hosts", "not both")


def test_hostfile_must_be_absolute() -> None:
    refuse(
        {"scheduler": "local", "hostfile": "h.txt"},
        "preset 'lab': hostfile",
        "must be absolute or start with ~",
    )


def test_hostfile_must_be_a_string() -> None:
    refuse({"scheduler": "local", "hostfile": 3}, "preset 'lab': hostfile")


def test_unreadable_hostfile() -> None:
    def read_file(path: str) -> str:
        raise FileNotFoundError(2, "No such file or directory", path)

    with pytest.raises(ValueError) as info:
        from_preset({"scheduler": "local", "hostfile": "/nope/h.txt"}, "lab", read_file=read_file)
    assert "preset 'lab': hostfile" in str(info.value)
    assert "/nope/h.txt" in str(info.value)


def test_bad_host_entry_carries_its_index() -> None:
    refuse(lab(hosts=["ws1:32", "ws2"]), "preset 'lab': hosts[1]")


@pytest.mark.parametrize("value", ["slurm", "", 3])
def test_launcher_kind_is_checked(value: object) -> None:
    refuse(lab(launcher=value), "preset 'lab': launcher")


def test_srun_needs_slurm() -> None:
    refuse(lab(launcher="srun"), "preset 'lab': launcher", "srun", "slurm")


@pytest.mark.parametrize(
    ("launchers", "where"),
    [
        ([], "launchers"),
        ({"direct": {}}, "launchers"),
        ({"srun": []}, "launchers.srun"),
        ({"srun": {"command": "srun"}}, "launchers.srun"),
        ({"mpirun": {"bogus": 1}}, "launchers.mpirun"),
        ({"mpirun": {"command": ""}}, "launchers.mpirun.command"),
        ({"mpirun": {"command": 3}}, "launchers.mpirun.command"),
        ({"mpirun": {"dialect": "intelmpi"}}, "launchers.mpirun.dialect"),
        ({"srun": {"flags": "--x"}}, "launchers.srun.flags"),
        ({"srun": {"flags": [1]}}, "launchers.srun.flags"),
        ({"mpirun": {"flags": "--x"}}, "launchers.mpirun.flags"),
        ({"mpirun": {"export": "UCX"}}, "launchers.mpirun.export"),
        ({"mpirun": {"export": [None]}}, "launchers.mpirun.export"),
    ],
)
def test_launchers_errors(launchers: object, where: str) -> None:
    refuse(lab(launchers=launchers), f"preset 'lab': {where}")


def test_dialect_error_notes_intel_mpi() -> None:
    refuse(
        lab(
            launchers={"mpirun": {"dialect": "intelmpi"}},
        ),
        "Intel MPI is not supported",
        "hydra",
    )


@pytest.mark.parametrize("env", [[], {"A": 1}, {"A": None}, "x"])
def test_env_must_be_strings(env: object) -> None:
    refuse(lab(env=env), "preset 'lab': env")


@pytest.mark.parametrize(
    ("key", "replacement"),
    [
        ("CRAB_WL_MANAGER", "launcher"),
        ("CRAB_SCHEDULER", "scheduler"),
        ("CRAB_MPIRUN", "launchers.mpirun.command"),
        ("CRAB_MPIRUN_ADDITIONAL_FLAGS", "launchers.mpirun.flags"),
        ("CRAB_MPIRUN_MAP_BY_NODE_FLAG", "launchers.mpirun.flags"),
        ("CRAB_MPIRUN_HOSTNAMES_FLAG", "nothing: CRAB passes the hosts to mpirun itself"),
        ("CRAB_PINNING_FLAGS", "launchers.srun.flags"),
    ],
)
def test_dropped_env_keys(key: str, replacement: str) -> None:
    refuse(lab(env={key: "x"}), f"preset 'lab': env.{key}", replacement)
    assert key in DROPPED_KEYS


def test_other_crab_env_keys_and_untouched_fields_pass() -> None:
    env = {"CRAB_ROOT": "/x", "CRAB_IB_DEVICES": "mlx5_0", "CRAB_SYSTEM": "s"}
    s = from_preset(lab(env=env, sbatch=["--exclusive"], header=["module load x"]), "lab")
    assert dict(s.env) == env


def test_check_dropped_keys() -> None:
    check_dropped_keys({"PATH": "/bin", "CRAB_ROOT": "/x"}, "worker environment")
    with pytest.raises(ValueError) as info:
        check_dropped_keys({"CRAB_MPIRUN": "srun"}, "worker environment")
    assert (
        str(info.value)
        == "worker environment: CRAB_MPIRUN is no longer read; use launchers.mpirun.command"
    )


@pytest.mark.parametrize("key", sorted(DROPPED_KEYS))
def test_check_dropped_keys_covers_every_key(key: str) -> None:
    with pytest.raises(ValueError, match=f"^here: {key} is no longer read; use "):
        check_dropped_keys({key: ""}, "here")


def hostfile_settings() -> ExecutionSettings:
    text = "localhost\nw1 slots=4\n"
    return from_preset({"scheduler": "local", "hostfile": "/h"}, "p", read_file=lambda p: text)


@pytest.mark.parametrize(
    "settings",
    [
        from_preset({"scheduler": "slurm"}, "p"),
        from_preset({"scheduler": "local"}, "p"),
        from_preset(LAB, "lab"),
        hostfile_settings(),
    ],
    ids=["slurm", "local", "lab", "hostfile"],
)
def test_json_round_trip(settings: ExecutionSettings) -> None:
    assert from_json(to_json(settings)) == settings


def test_to_json_is_a_plain_dict_in_preset_shape() -> None:
    data = to_json(hostfile_settings())
    assert data["scheduler"] == "local"
    assert data["hosts"] == ["localhost", "w1:4"]
    assert "hostfile" not in data
    assert data["launcher"] == "direct"
    assert data["exclusive"] is True
    assert type(data["env"]) is dict
    slurm = to_json(from_preset({"scheduler": "slurm"}, "p"))
    assert "hosts" not in slurm
    assert slurm["launchers"]["mpirun"]["command"] == "mpirun"
    assert type(to_json(from_preset(LAB, "lab"))["launchers"]["srun"]["flags"]) is list


def test_from_json_errors_name_execution_json() -> None:
    with pytest.raises(ValueError, match="preset 'execution.json': scheduler"):
        from_json({"hosts": ["a:1"]})


SLURM = from_preset(
    {
        "scheduler": "slurm",
        "launchers": {
            "srun": {"flags": ["--cpu-bind=socket"]},
            "mpirun": {
                "command": "/o/mpirun",
                "dialect": "hydra",
                "flags": ["-x"],
                "export": ["A"],
            },
        },
    },
    "s",
)
LOCAL = from_preset({"scheduler": "local"}, "l")


def test_kind_precedence() -> None:
    assert resolve_launcher(SLURM, {}, None).kind == "srun"
    assert resolve_launcher(SLURM, {"launcher": "mpirun"}, None).kind == "mpirun"
    assert resolve_launcher(SLURM, {"launcher": "mpirun"}, "srun").kind == "srun"
    assert resolve_launcher(SLURM, {"launcher": "direct"}, "mpirun").kind == "mpirun"
    assert resolve_launcher(SLURM, {"launcher": "mpirun"}, "").kind == "mpirun"
    assert resolve_launcher(LOCAL, {}, None).kind == "direct"


def test_launcher_flags_replace_the_chosen_kinds_flags() -> None:
    assert resolve_launcher(SLURM, {}, None).flags == ("--cpu-bind=socket",)
    assert resolve_launcher(SLURM, {"launcher_flags": ["--x"]}, None).flags == ("--x",)
    assert resolve_launcher(SLURM, {"launcher_flags": []}, None).flags == ()
    chosen = resolve_launcher(SLURM, {"launcher": "mpirun", "launcher_flags": ["--y"]}, None)
    assert chosen.flags == ("--y",)


def test_receipt_switch_keeps_the_preset_flags() -> None:
    switched = resolve_launcher(SLURM, {"launcher_flags": ["--x"]}, "mpirun")
    assert switched.kind == "mpirun"
    assert switched.flags == ("-x",)


def test_specs_carry_the_preset_values() -> None:
    assert resolve_launcher(SLURM, {"launcher": "mpirun"}, None) == LauncherSpec(
        "mpirun", ("/o/mpirun",), "hydra", ("-x",), ("A",)
    )
    assert resolve_launcher(SLURM, {}, None) == LauncherSpec(
        "srun", ("srun",), "", ("--cpu-bind=socket",), ()
    )
    assert resolve_launcher(LOCAL, {}, None) == LauncherSpec("direct", (), "", (), ())


@pytest.mark.parametrize("override", ["/opt/ompi/bin/mpirun", "direct", "slurm"])
def test_receipt_override_must_be_a_kind(override: str) -> None:
    with pytest.raises(ValueError) as info:
        resolve_launcher(SLURM, {}, override)
    assert 'launcher_override must be "srun" or "mpirun"' in str(info.value)
    assert repr(override) in str(info.value)


def test_option_errors() -> None:
    with pytest.raises(ValueError, match="options.launcher"):
        resolve_launcher(SLURM, {"launcher": "mpi"}, None)
    for bad in ("--x", [1], None):
        with pytest.raises(ValueError, match="options.launcher_flags"):
            resolve_launcher(SLURM, {"launcher_flags": bad}, None)


def test_srun_under_local_is_refused() -> None:
    with pytest.raises(ValueError, match="srun.*local"):
        resolve_launcher(LOCAL, {"launcher": "srun"}, None)
    with pytest.raises(ValueError, match="srun.*local"):
        resolve_launcher(LOCAL, {}, "srun")


def test_slurm_default_is_slurm_with_srun_and_no_flags() -> None:
    assert SLURM_DEFAULT.scheduler == "slurm"
    assert SLURM_DEFAULT.launcher == "srun"
    assert SLURM_DEFAULT.srun.flags == ()
    assert resolve_launcher(SLURM_DEFAULT, {}, None).kind == "srun"


def test_dropped_env_key_is_reported_before_the_missing_scheduler() -> None:
    """An old preset (env keys only) gets the migration message, not "scheduler ... got None"."""
    refuse(
        {"env": {"CRAB_MPIRUN": "srun"}},
        "preset 'lab': env.CRAB_MPIRUN is no longer read; use launchers.mpirun.command",
    )
