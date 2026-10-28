"""
setup/recipes/qe_v6.py and qe_v7.py: the clone runs from the folder above the target.

A clone started from "." fails when the process was launched inside a folder that setup
removed; the recipes pass the target's parent, which always exists.
"""

import os
from unittest.mock import patch

import pytest

from crab.setup.recipes.blink import BlinkRecipe
from crab.setup.recipes.g500 import G500Recipe
from crab.setup.recipes.qe_v6 import QERecipeV6
from crab.setup.recipes.qe_v7 import QERecipeV7

RECIPES = [QERecipeV6, QERecipeV7]


@pytest.mark.parametrize("recipe_class", RECIPES)
def test_clone_runs_from_the_folder_above_the_target(tmp_path, recipe_class):
    parent = tmp_path / "benchmarks"
    parent.mkdir()
    recipe = recipe_class()

    with patch.object(recipe, "run_command_streamed", return_value=False) as mock_run:
        ok, _result, _msg = recipe.download_and_build(str(parent / "qe"), {}, {}, None)

    assert ok is False
    clone_cwd = mock_run.call_args_list[0].args[1]
    assert clone_cwd == str(parent)
    assert os.path.isabs(clone_cwd)
    assert mock_run.call_args_list[0].args[0][:2] == ["git", "clone"]


@pytest.mark.parametrize("recipe_class", RECIPES)
def test_clone_folder_is_created_when_missing(tmp_path, recipe_class):
    parent = tmp_path / "does" / "not" / "exist"
    recipe = recipe_class()

    with patch.object(recipe, "run_command_streamed", return_value=False) as mock_run:
        recipe.download_and_build(str(parent / "qe"), {}, {}, None)

    assert parent.is_dir()
    assert mock_run.call_args_list[0].args[1] == str(parent)


@pytest.mark.parametrize("recipe_class", [BlinkRecipe, G500Recipe, QERecipeV6, QERecipeV7])
def test_relative_target_is_refused_before_any_command(recipe_class):
    recipe = recipe_class()

    with patch.object(recipe, "run_command_streamed", return_value=True) as mock_run:
        with pytest.raises(ValueError, match="absolute"):
            recipe.download_and_build("rel/blink", {}, {}, None)

    mock_run.assert_not_called()
