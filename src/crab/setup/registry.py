import importlib
import inspect
import pkgutil

from .recipes.base import BenchmarkRecipe


def discover_recipes() -> list[BenchmarkRecipe]:
    """
    Dynamically discovers and instantiates all benchmark recipes
    found in the 'crab.setup.recipes' package.
    """
    recipes = []

    # Import the recipes package to get its path
    import crab.setup.recipes as recipes_pkg

    # Iterate through all modules inside the recipes folder
    for _, module_name, ispkg in pkgutil.iter_modules(recipes_pkg.__path__):
        # Skip directories and the base class file
        if ispkg or module_name == "base":
            continue

        full_module_name = f"crab.setup.recipes.{module_name}"

        try:
            module = importlib.import_module(full_module_name)

            # Inspect the module for classes
            for _name, obj in inspect.getmembers(module, inspect.isclass):
                # Check if it inherits from BenchmarkRecipe and isn't the base class itself
                if issubclass(obj, BenchmarkRecipe) and obj is not BenchmarkRecipe:
                    recipes.append(obj())

        except Exception as e:
            print(f"[Warning] Failed to load recipe '{module_name}': {e}")

    return recipes
