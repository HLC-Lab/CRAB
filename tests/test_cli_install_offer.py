"""The optional-dependency installer must target this checkout, never PyPI's `crab`.

`crab` on PyPI is an unrelated package, so `pip install crab[web]` would install someone
else's code into the user's environment.
"""

import sys
from pathlib import Path

import pytest

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
    assert not any(a.startswith("crab[") for a in args)
    assert Path(args[-1].removesuffix("[web]"), "pyproject.toml").is_file()


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
