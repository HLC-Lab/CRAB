"""Golden mpirun argv per MPI dialect, and how `launcher_for` picks the dialect.

Expected lists are written out literally from the flag table in the execution-backends research
note: Open MPI 4 repeats a host name per slot, Open MPI 5 and Hydra take `host:slots`.
"""

from __future__ import annotations

import pytest

from crab.core.execution.launcher import MpirunLauncher, Placement, dialect, launcher_for
from crab.core.execution.settings import LauncherSpec

PRESET_FLAGS = ("--mca", "btl", "self,tcp")


def _prefix(dialect_name: str, placement: Placement, **kwargs) -> list[str]:
    launcher = MpirunLauncher(("mpirun",), (), dialect_name, **kwargs)
    return launcher.prefix(placement)


def test_openmpi4_one_host_one_rank() -> None:
    assert _prefix("openmpi4", Placement(("n1",), 1)) == ["mpirun", "-n", "1", "-H", "n1"]


def test_openmpi5_one_host_one_rank() -> None:
    assert _prefix("openmpi5", Placement(("n1",), 1)) == ["mpirun", "-n", "1", "--host", "n1:1"]


def test_hydra_one_host_one_rank() -> None:
    assert _prefix("hydra", Placement(("n1",), 1)) == ["mpirun", "-n", "1", "-hosts", "n1:1"]


def test_openmpi4_two_hosts_four_ranks() -> None:
    assert _prefix("openmpi4", Placement(("n1", "n2"), 4)) == [
        "mpirun",
        "-n",
        "8",
        "-H",
        "n1,n1,n1,n1,n2,n2,n2,n2",
    ]


def test_openmpi5_two_hosts_four_ranks() -> None:
    assert _prefix("openmpi5", Placement(("n1", "n2"), 4)) == [
        "mpirun",
        "-n",
        "8",
        "--host",
        "n1:4,n2:4",
    ]


def test_hydra_two_hosts_four_ranks() -> None:
    assert _prefix("hydra", Placement(("n1", "n2"), 4)) == [
        "mpirun",
        "-n",
        "8",
        "-hosts",
        "n1:4,n2:4",
    ]


def test_openmpi4_three_hosts_two_ranks() -> None:
    assert _prefix("openmpi4", Placement(("a", "b", "c"), 2)) == [
        "mpirun",
        "-n",
        "6",
        "-H",
        "a,a,b,b,c,c",
    ]


def test_openmpi5_three_hosts_two_ranks() -> None:
    assert _prefix("openmpi5", Placement(("a", "b", "c"), 2)) == [
        "mpirun",
        "-n",
        "6",
        "--host",
        "a:2,b:2,c:2",
    ]


def test_hydra_three_hosts_two_ranks() -> None:
    assert _prefix("hydra", Placement(("a", "b", "c"), 2)) == [
        "mpirun",
        "-n",
        "6",
        "-hosts",
        "a:2,b:2,c:2",
    ]


def test_openmpi4_exports_one_x_per_variable() -> None:
    export = ("UCX_TLS", "OMP_NUM_THREADS")
    assert _prefix("openmpi4", Placement(("n1",), 2), export=export) == [
        "mpirun",
        "-n",
        "2",
        "-H",
        "n1,n1",
        "-x",
        "UCX_TLS",
        "-x",
        "OMP_NUM_THREADS",
    ]


def test_openmpi5_exports_one_x_per_variable() -> None:
    export = ("UCX_TLS", "OMP_NUM_THREADS")
    assert _prefix("openmpi5", Placement(("n1",), 2), export=export) == [
        "mpirun",
        "-n",
        "2",
        "--host",
        "n1:2",
        "-x",
        "UCX_TLS",
        "-x",
        "OMP_NUM_THREADS",
    ]


def test_hydra_adds_no_env_flag_because_it_exports_everything() -> None:
    export = ("UCX_TLS", "OMP_NUM_THREADS")
    assert _prefix("hydra", Placement(("n1",), 2), export=export) == [
        "mpirun",
        "-n",
        "2",
        "-hosts",
        "n1:2",
    ]


def test_openmpi4_report_bindings() -> None:
    assert _prefix("openmpi4", Placement(("n1",), 1), report_bindings=True) == [
        "mpirun",
        "-n",
        "1",
        "-H",
        "n1",
        "--report-bindings",
    ]


def test_openmpi5_report_bindings() -> None:
    assert _prefix("openmpi5", Placement(("n1",), 1), report_bindings=True) == [
        "mpirun",
        "-n",
        "1",
        "--host",
        "n1:1",
        "--display",
        "bindings",
    ]


def test_hydra_report_bindings() -> None:
    assert _prefix("hydra", Placement(("n1",), 1), report_bindings=True) == [
        "mpirun",
        "-n",
        "1",
        "-hosts",
        "n1:1",
        "-report-bindings",
    ]


def test_order_is_ranks_hosts_env_report_then_preset_flags() -> None:
    launcher = MpirunLauncher(
        ("mpirun",), PRESET_FLAGS, "openmpi5", export=("UCX_TLS",), report_bindings=True
    )
    assert launcher.prefix(Placement(("n1", "n2"), 1)) == [
        "mpirun",
        "-n",
        "2",
        "--host",
        "n1:1,n2:1",
        "-x",
        "UCX_TLS",
        "--display",
        "bindings",
        "--mca",
        "btl",
        "self,tcp",
    ]


@pytest.mark.parametrize(
    ("dialect_name", "host_flags"),
    [("openmpi4", ["-H", "n1"]), ("openmpi5", ["--host", "n1:1"]), ("hydra", ["-hosts", "n1:1"])],
)
def test_preset_flags_come_last(dialect_name: str, host_flags: list[str]) -> None:
    launcher = MpirunLauncher(("mpirun",), PRESET_FLAGS, dialect_name)
    assert launcher.prefix(Placement(("n1",), 1)) == [
        "mpirun",
        "-n",
        "1",
        *host_flags,
        "--mca",
        "btl",
        "self,tcp",
    ]


def test_a_full_command_path_leads_the_argv() -> None:
    launcher = MpirunLauncher(("/opt/ompi/bin/mpirun",), (), "openmpi4")
    assert launcher.prefix(Placement(("n1", "n2"), 1)) == [
        "/opt/ompi/bin/mpirun",
        "-n",
        "2",
        "-H",
        "n1,n2",
    ]


# --------------------------------------------------------------------------- #
# launcher_for resolves the dialect
# --------------------------------------------------------------------------- #
def _spec(dialect_name: str, *flags: str, export: tuple[str, ...] = ()) -> LauncherSpec:
    return LauncherSpec("mpirun", ("/opt/ompi/bin/mpirun",), dialect_name, flags, export)


def test_an_explicit_dialect_is_used_without_probing(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_probe(command: str) -> str:
        raise AssertionError(f"probe({command!r}) must not run for an explicit dialect")

    monkeypatch.setattr(dialect, "probe", no_probe)
    launcher = launcher_for(
        _spec("openmpi5", "--bind-to", "core", export=("UCX_TLS",)), {"UCX_TLS": "rc"}
    )
    # binding report always on since C4
    assert launcher == MpirunLauncher(
        ("/opt/ompi/bin/mpirun",),
        ("--bind-to", "core"),
        "openmpi5",
        ("UCX_TLS",),
        report_bindings=True,
    )


def test_auto_probes_the_command_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_probe(command: str) -> str:
        calls.append(command)
        return "hydra"

    monkeypatch.setattr(dialect, "probe", fake_probe)
    launcher = launcher_for(_spec("auto"))
    assert calls == ["/opt/ompi/bin/mpirun"]
    # binding report always on since C4
    assert launcher == MpirunLauncher(("/opt/ompi/bin/mpirun",), (), "hydra", report_bindings=True)


def test_a_probe_error_propagates_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    error = ValueError("Cannot find 'mpirun' to detect its MPI.")

    def failing_probe(command: str) -> str:
        raise error

    monkeypatch.setattr(dialect, "probe", failing_probe)
    with pytest.raises(ValueError) as caught:
        launcher_for(_spec("auto"))
    assert caught.value is error


# --------------------------------------------------------------------------- #
# launcher_for filters the export set by the launching environment
# --------------------------------------------------------------------------- #
def test_only_names_set_in_the_environment_are_exported() -> None:
    spec = _spec("openmpi5", export=("UCX_TLS", "PATH", "LD_LIBRARY_PATH", "CRAB_IB_DEVICES"))
    launcher = launcher_for(spec, {"PATH": "/p", "UCX_TLS": "rc"})
    assert isinstance(launcher, MpirunLauncher)
    assert launcher.export == ("UCX_TLS", "PATH")


def test_every_crab_variable_is_added_sorted_and_not_twice() -> None:
    spec = _spec("openmpi5", export=("PATH", "CRAB_ROOT"))
    environ = {"PATH": "/p", "CRAB_ROOT": "/c", "CRAB_B": "1", "CRAB_A": "2", "OTHER": "3"}
    launcher = launcher_for(spec, environ)
    assert isinstance(launcher, MpirunLauncher)
    assert launcher.export == ("PATH", "CRAB_ROOT", "CRAB_A", "CRAB_B")


def test_the_supervisor_descriptor_is_never_exported() -> None:
    environ = {"CRAB_SUPERVISOR_FD": "9", "CRAB_ROOT": "/c"}
    for export in ((), ("CRAB_SUPERVISOR_FD",)):
        launcher = launcher_for(_spec("openmpi5", export=export), environ)
        assert isinstance(launcher, MpirunLauncher)
        assert launcher.export == ("CRAB_ROOT",)


def test_the_process_environment_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UCX_TLS", "rc")
    launcher = launcher_for(_spec("openmpi5", export=("UCX_TLS",)))
    assert isinstance(launcher, MpirunLauncher)
    assert "UCX_TLS" in launcher.export
