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
    return LauncherSpec("mpirun", (command,), "openmpi4", flags, ())


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
    # Explicit geometry: every host repeated once per slot (Open MPI 4), preset flags last.
    # binding report always on since C4
    assert _line(spec, ["n1", "n2"], 2, "./app") == (
        "mpirun -n 4 -H n1,n1,n2,n2 --report-bindings --bind-to core --map-by node ./app"
    )


def test_mpirun_command_from_the_spec() -> None:
    spec = _mpirun("--bind-to", "core", command="/opt/ompi/bin/mpirun")
    line = _line(spec, ["n1", "n2"], 2, "./app")
    # binding report always on since C4
    assert line == "/opt/ompi/bin/mpirun -n 4 -H n1,n1,n2,n2 --report-bindings --bind-to core ./app"


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
    # binding report always on since C4
    assert launcher_for(_mpirun("-x")) == MpirunLauncher(
        ("mpirun",), ("-x",), "openmpi4", report_bindings=True
    )
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
    assert MpirunLauncher(("mpirun",), ("--bind-to", "core"), "openmpi4").prefix(placement) == [
        "mpirun",
        "-n",
        "1",
        "-H",
        "n1",
        "--bind-to",
        "core",
    ]
    assert DirectLauncher().prefix(placement) == []


def _srun(placement: Placement, *flags: str) -> list[str]:
    return SrunLauncher(("srun",), flags).prefix(placement)


_HEAD = ["srun", "--export=ALL", "--nodelist", "n1,n2"]
_TAIL = ["-n", "4", "-N", "2"]


def test_srun_without_new_fields_is_unchanged() -> None:
    assert _srun(Placement(("n1", "n2"), 2), "--mpi=pmix") == [*_HEAD, "--mpi=pmix", *_TAIL]


def test_srun_disjoint_co_run_binds_to_exclusive_cores() -> None:
    line = _srun(Placement(("n1", "n2"), 2, co_run="disjoint"))
    assert line == [
        *_HEAD,
        "--exact",
        "--mem=0",
        "--cpu-bind=verbose,cores",
        "--ntasks-per-node=2",
        *_TAIL,
    ]


def test_srun_co_run_with_cpus_overlaps_and_maps_the_cores_in_order() -> None:
    placement = Placement(("n1", "n2"), 2, cpus=(3, 1, 2, 0), co_run="disjoint")
    assert _srun(placement) == [
        *_HEAD,
        "--overlap",
        "--cpu-bind=verbose,map_cpu:3,1,2,0",
        "--ntasks-per-node=2",
        *_TAIL,
    ]


def test_srun_shared_co_run_overlaps_without_binding() -> None:
    line = _srun(Placement(("n1", "n2"), 2, co_run="shared"))
    assert line == [*_HEAD, "--overlap", "--cpu-bind=verbose,none", "--ntasks-per-node=2", *_TAIL]


def test_srun_bind_none_alone_only_turns_binding_off() -> None:
    line = _srun(Placement(("n1", "n2"), 2, bind_none=True))
    assert line == [*_HEAD, "--cpu-bind=verbose,none", *_TAIL]


def test_srun_lone_app_with_cpus_maps_without_overlap() -> None:
    line = _srun(Placement(("n1", "n2"), 2, cpus=(4, 5)))
    assert line == [*_HEAD, "--cpu-bind=verbose,map_cpu:4,5", *_TAIL]


@pytest.mark.parametrize(
    "flags",
    [("--mpi=pmix", "--cpu-bind=socket"), ("--cpu-bind", "cores", "--mpi=pmix")],
)
def test_srun_binding_replaces_the_preset_cpu_bind(flags: tuple[str, ...]) -> None:
    for placement in (
        Placement(("n1", "n2"), 2, co_run="disjoint"),
        Placement(("n1", "n2"), 2, cpus=(0, 1)),
        Placement(("n1", "n2"), 2, bind_none=True),
    ):
        prefix = _srun(placement, *flags)
        assert "--mpi=pmix" in prefix
        assert not any(word.startswith("--cpu-bind=") and "verbose" not in word for word in prefix)
        assert "socket" not in prefix and "cores" not in prefix
        assert len([w for w in prefix if w.startswith("--cpu-bind")]) == 1


def test_srun_keeps_the_preset_cpu_bind_when_crab_does_not_bind() -> None:
    placement = Placement(("n1", "n2"), 2)
    assert _srun(placement, "--mpi=pmix", "--cpu-bind=socket") == [
        *_HEAD,
        "--mpi=pmix",
        "--cpu-bind=socket",
        *_TAIL,
    ]
    assert _srun(placement, "--cpu-bind", "cores", "--mpi=pmix") == [
        *_HEAD,
        "--cpu-bind",
        "cores",
        "--mpi=pmix",
        *_TAIL,
    ]


def test_srun_stripping_keeps_the_other_flags_in_order() -> None:
    line = _srun(Placement(("n1",), 1, co_run="shared"), "-a", "--cpu_bind", "x", "-b")
    assert line[4:7] == ["-a", "-b", "--overlap"]
    assert "x" not in line


def test_srun_ntasks_per_node_follows_the_job_geometry() -> None:
    differs = Placement(("n1",), 1, job_ranks_per_host=4)
    assert _srun(differs) == [
        "srun",
        "--export=ALL",
        "--nodelist",
        "n1",
        "--ntasks-per-node=1",
        "-n",
        "1",
        "-N",
        "1",
    ]
    assert "--ntasks-per-node=1" not in _srun(Placement(("n1",), 1, job_ranks_per_host=1))
    assert "--ntasks-per-node=1" not in _srun(Placement(("n1",), 1, job_ranks_per_host=None))
