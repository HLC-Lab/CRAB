"""Launchers build today's launch line from the same env vars.

Port of the srun/mpirun cases in tests/test_slurm_characterization.py and of
tests/test_wl_manager_local.py, run against the launcher package.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from crab.core.execution.launcher import (
    DirectLauncher,
    EnvMpirunLauncher,
    Placement,
    SrunLauncher,
    launch_line,
    launch_mode,
    launcher_for,
)

_LAUNCH_ENV_VARS = (
    "CRAB_MPIRUN",
    "CRAB_MPIRUN_ADDITIONAL_FLAGS",
    "CRAB_MPIRUN_MAP_BY_NODE_FLAG",
    "CRAB_PINNING_FLAGS",
)


@pytest.fixture(autouse=True)
def clean_launch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _LAUNCH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _line(
    hosts: list[str],
    ppn: int,
    command: str,
    environ: Mapping[str, str] | None = None,
    override: str | None = None,
    mode: str = "slurm",
) -> str:
    launcher = launcher_for(mode, override, environ or {})
    return launch_line(launcher, Placement(tuple(hosts), ppn), command)


def test_srun_default_one_node() -> None:
    assert _line(["n1"], 1, "./app") == "srun --export=ALL --nodelist n1 -n 1 -N 1 ./app"


def test_srun_three_nodes_with_pinning_flags() -> None:
    line = _line(
        ["n1", "n2", "n3"], 4, "./app -x 1", environ={"CRAB_PINNING_FLAGS": "--cpu-bind=cores"}
    )
    assert line == "srun --export=ALL --nodelist n1,n2,n3 --cpu-bind=cores -n 12 -N 3 ./app -x 1"


def test_srun_override_with_options() -> None:
    line = _line(["n1"], 1, "./app", override="srun --mpi=pmix")
    assert line == "srun --mpi=pmix --export=ALL --nodelist n1 -n 1 -N 1 ./app"


def test_mpirun_from_env_with_flags() -> None:
    environ = {
        "CRAB_MPIRUN": "mpirun",
        "CRAB_MPIRUN_ADDITIONAL_FLAGS": "--bind-to core",
        "CRAB_MPIRUN_MAP_BY_NODE_FLAG": "--map-by node",
        "CRAB_PINNING_FLAGS": "--cpu-bind=cores",
    }
    # The node list and the pinning flags are not used by mpirun.
    assert (
        _line(["n1", "n2"], 4, "./app", environ=environ)
        == "mpirun --bind-to core --map-by node -np 8 ./app"
    )


def test_mpirun_override_wins_over_env() -> None:
    environ = {"CRAB_MPIRUN": "srun", "CRAB_MPIRUN_ADDITIONAL_FLAGS": "--bind-to core"}
    line = _line(["n1", "n2"], 2, "./app", environ=environ, override="/opt/ompi/bin/mpirun")
    assert line == "/opt/ompi/bin/mpirun --bind-to core -np 4 ./app"


def test_whitespace_collapses_inside_quotes() -> None:
    cmd = """./app --msg "hello   world"  'a    b'"""
    # As it is today: runs of spaces inside quoted arguments are collapsed too.
    assert (
        _line(["n1"], 1, cmd)
        == """srun --export=ALL --nodelist n1 -n 1 -N 1 ./app --msg "hello world" 'a b'"""
    )


def test_surrounding_whitespace_of_env_and_override_is_stripped() -> None:
    assert _line(["n1"], 2, "./app", environ={"CRAB_MPIRUN": "  mpirun  "}) == "mpirun -np 2 ./app"
    assert _line(["n1"], 2, "./app", override="  mpirun  ") == "mpirun -np 2 ./app"


def test_empty_crab_mpirun_drops_the_launcher_word() -> None:
    # Today's behavior, pinned as is: an empty CRAB_MPIRUN is not "unset", so the line
    # starts at "--export=ALL" with no launcher binary.
    line = _line(["n1"], 1, "./app", environ={"CRAB_MPIRUN": ""})
    assert line == "--export=ALL --nodelist n1 -n 1 -N 1 ./app"


def test_direct_returns_command_unchanged() -> None:
    assert _line(["localhost"], 1, "echo hello", mode="local") == "echo hello"


def test_direct_ignores_hosts_ppn_override_and_env() -> None:
    environ = {"CRAB_MPIRUN": "mpirun", "CRAB_PINNING_FLAGS": "--cpu-bind=cores"}
    line = _line(
        ["node01", "node02"],
        8,
        "python wrapper.py",
        environ=environ,
        override="mpirun",
        mode="local",
    )
    assert line == "python wrapper.py"


def test_direct_returns_command_exactly_without_collapsing_whitespace() -> None:
    # wl_manager/local.py returned `cmd` as is: no strip, no collapse.
    cmd = '  ./app --msg "hello   world"  '
    assert _line(["n1"], 1, cmd, mode="local") == cmd


@pytest.mark.parametrize("mode", ["mpi", "workerpool"])
def test_unported_modes_select_but_refuse_to_build(mode: str) -> None:
    launcher = launcher_for(mode, None, {})
    with pytest.raises(NotImplementedError, match=f"'{mode}'.*has not been ported"):
        launch_line(launcher, Placement(("n1",), 1), "./app")


def test_launch_mode_defaults_to_slurm() -> None:
    assert launch_mode({}) == "slurm"


def test_launch_mode_accepts_local() -> None:
    assert launch_mode({"CRAB_WL_MANAGER": "local"}) == "local"


@pytest.mark.parametrize("value", ["../../evil", "notreal"])
def test_launch_mode_rejects_unknown_values(value: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        launch_mode({"CRAB_WL_MANAGER": value})
    message = str(excinfo.value)
    assert "CRAB_WL_MANAGER" in message
    assert repr(value) in message
    assert "['local', 'mpi', 'slurm', 'workerpool']" in message


def test_prefix_is_a_list_without_the_command() -> None:
    placement = Placement(("n1",), 1)
    assert SrunLauncher(("srun",), ()).prefix(placement) == [
        "srun",
        "--export=ALL",
        "--nodelist",
        "n1",
        "-n",
        "1",
        "-N",
        "1",
    ]
    assert EnvMpirunLauncher(("mpirun",), ("--bind-to", "core")).prefix(placement) == [
        "mpirun",
        "--bind-to",
        "core",
        "-np",
        "1",
    ]
    assert DirectLauncher().prefix(placement) == []
