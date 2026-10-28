"""
setup/recipes/g500.py: the build command must not override the Makefile's own CFLAGS.

Found via the gcc/MPI compat matrix: a command-line `make CFLAGS=-fcommon` replaces the
Makefile's own CFLAGS wholesale (GNU Make override semantics), dropping -I../aml and breaking
the aml.h include -- `-fcommon` must ride on MPICC instead, matching the documented example in
docs/extending/recipes.md. Also targets graph500_reference_bfs explicitly, not the default
"all" target, since the _sssp variant has an unrelated upstream bug.
"""

import os
import unittest
from unittest.mock import patch

from crab.setup.recipes.g500 import G500Recipe


class TestG500BuildCommand(unittest.TestCase):
    def test_build_command_does_not_override_cflags(self):
        recipe = G500Recipe()
        with patch.object(recipe, "run_command_streamed", return_value=True) as mock_run:
            with patch("os.path.exists", return_value=True):
                recipe.download_and_build("/tmp/g500", {}, {}, None)

        build_call = mock_run.call_args_list[1]
        build_cmd = build_call.args[0]
        self.assertNotIn(
            "CFLAGS=-fcommon", build_cmd, "CFLAGS=... on the command line wipes -I../aml"
        )
        self.assertTrue(
            any(arg.startswith("MPICC=") and "-fcommon" in arg for arg in build_cmd),
            "-fcommon must ride on MPICC, not a standalone CFLAGS override",
        )

    def test_build_command_targets_bfs_explicitly_not_sssp(self):
        recipe = G500Recipe()
        with patch.object(recipe, "run_command_streamed", return_value=True) as mock_run:
            with patch("os.path.exists", return_value=True):
                recipe.download_and_build("/tmp/g500", {}, {}, None)

        build_cmd = mock_run.call_args_list[1].args[0]
        self.assertIn("graph500_reference_bfs", build_cmd)
        self.assertNotIn("-j", build_cmd)
        self.assertNotIn("all", build_cmd)


def test_clone_runs_from_the_folder_above_the_target(tmp_path):
    parent = tmp_path / "benchmarks"
    parent.mkdir()
    recipe = G500Recipe()

    with patch.object(recipe, "run_command_streamed", return_value=False) as mock_run:
        recipe.download_and_build(str(parent / "g500"), {}, {}, None)

    clone_cwd = mock_run.call_args_list[0].args[1]
    assert clone_cwd == str(parent)
    assert os.path.isabs(clone_cwd)


def test_clone_folder_is_created_when_missing(tmp_path):
    parent = tmp_path / "does" / "not" / "exist"
    recipe = G500Recipe()

    with patch.object(recipe, "run_command_streamed", return_value=False) as mock_run:
        recipe.download_and_build(str(parent / "g500"), {}, {}, None)

    assert parent.is_dir()
    assert mock_run.call_args_list[0].args[1] == str(parent)


if __name__ == "__main__":
    unittest.main()
