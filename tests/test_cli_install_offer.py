"""The optional-dependency installer must target this checkout, never PyPI's `crab`.

`crab` on PyPI is an unrelated package, so `pip install crab[web]` would install someone
else's code into the user's environment.
"""

import sys
from pathlib import Path

from crab.cli.main import _local_install_args


def test_install_args_point_at_the_checkout_in_editable_mode(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'crab'\n")

    args = _local_install_args("web", root=tmp_path)

    assert args == [sys.executable, "-m", "pip", "install", "-e", f"{tmp_path}[web]"]


def test_no_checkout_means_no_automatic_install(tmp_path: Path):
    assert _local_install_args("tui", root=tmp_path) is None


def test_default_root_is_this_repository():
    args = _local_install_args("web")

    assert args is not None
    assert not any(a.startswith("crab[") for a in args)
    assert Path(args[-1].removesuffix("[web]"), "pyproject.toml").is_file()
