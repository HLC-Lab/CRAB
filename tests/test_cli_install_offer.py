"""The optional-dependency installer must never install PyPI's unrelated `crab` package.

A checkout installs itself in editable mode; a pip install asks for the `crab-hpc`
distribution. `pip install crab[web]` would install someone else's code into the user's
environment.
"""

import sys
from pathlib import Path

import pytest

from crab import paths
from crab.cli.main import _local_install_args, cli_router


def test_install_args_point_at_the_checkout_in_editable_mode(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'crab'\n")

    args = _local_install_args("web", root=tmp_path)

    assert args == [sys.executable, "-m", "pip", "install", "-e", f"{tmp_path}[web]"]


def test_no_checkout_means_no_automatic_install(tmp_path: Path):
    assert _local_install_args("web", root=tmp_path) is None


def test_default_root_is_this_repository():
    args = _local_install_args("web")

    assert args is not None
    assert not any(a.startswith(("crab[", "crab-hpc[")) for a in args)
    assert Path(args[-1].removesuffix("[web]"), "pyproject.toml").is_file()


def test_installed_layout_installs_the_crab_hpc_distribution(monkeypatch):
    monkeypatch.setattr(paths, "is_checkout", lambda: False)

    args = _local_install_args("web")

    assert args == [sys.executable, "-m", "pip", "install", "crab-hpc[web]"]


def test_installed_layout_takes_the_extra_it_is_given(monkeypatch):
    monkeypatch.setattr(paths, "is_checkout", lambda: False)

    assert _local_install_args("dev") == [sys.executable, "-m", "pip", "install", "crab-hpc[dev]"]


def test_tui_is_not_a_command(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["crab", "tui"])

    with pytest.raises(SystemExit) as exit_info:
        cli_router()

    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_help_does_not_list_tui(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["crab", "--help"])

    with pytest.raises(SystemExit) as exit_info:
        cli_router()

    assert exit_info.value.code == 0
    assert "tui" not in capsys.readouterr().out.lower()
