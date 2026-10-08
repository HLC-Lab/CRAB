"""A resolved launcher spec builds the launch line.

Port of the srun/mpirun cases in tests/test_slurm_characterization.py and of the old local
launch mode, run against the launcher package.
"""

from __future__ import annotations

import pytest

from crab.core.execution.launcher import (
    DirectLauncher,
    MpirunLauncher,
    Placement,
    SrunLauncher,
    launch_line,
    launcher_for,
)
from crab.core.execution.settings import LauncherSpec

SRUN = LauncherSpec("srun", ("srun",), "", (), ())
DIRECT = LauncherSpec("direct", (), "", (), ())


def _mpirun(*flags: str, command: str = "mpirun") -> LauncherSpec:
    return LauncherSpec("mpirun", (command,), "auto", flags, ())


def _line(spec: LauncherSpec, hosts: list[str], ppn: int, command: str) -> str:
    return launch_line(launcher_for(spec), Placement(tuple(hosts), ppn), command)


def test_srun_default_one_node() -> None:
    assert _line(SRUN, ["n1"], 1, "./app") == "srun --export=ALL --nodelist n1 -n 1 -N 1 ./app"


def test_srun_three_nodes_with_flags() -> None:
    spec = LauncherSpec("srun", ("srun",), "", ("--cpu-bind=cores",), ())
    line = _line(spec, ["n1", "n2", "n3"], 4, "./app -x 1")
    assert line == "srun --export=ALL --nodelist n1,n2,n3 --cpu-bind=cores -n 12 -N 3 ./app -x 1"


def test_mpirun_with_flags() -> None:
    spec = _mpirun("--bind-to", "core", "--map-by", "node")
    # The node list is not passed to mpirun.
    assert (
        _line(spec, ["n1", "n2"], 4, "./app") == "mpirun --bind-to core --map-by node -np 8 ./app"
    )


def test_mpirun_command_from_the_spec() -> None:
    spec = _mpirun("--bind-to", "core", command="/opt/ompi/bin/mpirun")
    line = _line(spec, ["n1", "n2"], 2, "./app")
    assert line == "/opt/ompi/bin/mpirun --bind-to core -np 4 ./app"


def test_whitespace_collapses_inside_quotes() -> None:
    cmd = """./app --msg "hello   world"  'a    b'"""
    # As it is today: runs of spaces inside quoted arguments are collapsed too.
    assert (
        _line(SRUN, ["n1"], 1, cmd)
        == """srun --export=ALL --nodelist n1 -n 1 -N 1 ./app --msg "hello world" 'a b'"""
    )


def test_direct_returns_command_unchanged() -> None:
    assert _line(DIRECT, ["localhost"], 1, "echo hello") == "echo hello"


def test_direct_ignores_hosts_and_ppn() -> None:
    assert _line(DIRECT, ["node01", "node02"], 8, "python wrapper.py") == "python wrapper.py"


def test_direct_returns_command_exactly_without_collapsing_whitespace() -> None:
    cmd = '  ./app --msg "hello   world"  '
    assert _line(DIRECT, ["n1"], 1, cmd) == cmd


def test_an_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="'workerpool'"):
        launcher_for(LauncherSpec("workerpool", (), "", (), ()))


def test_launcher_for_picks_the_launcher_class() -> None:
    assert launcher_for(SRUN) == SrunLauncher(("srun",), ())
    assert launcher_for(_mpirun("-x")) == MpirunLauncher(("mpirun",), ("-x",))
    assert isinstance(launcher_for(DIRECT), DirectLauncher)


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
    assert MpirunLauncher(("mpirun",), ("--bind-to", "core")).prefix(placement) == [
        "mpirun",
        "--bind-to",
        "core",
        "-np",
        "1",
    ]
    assert DirectLauncher().prefix(placement) == []
