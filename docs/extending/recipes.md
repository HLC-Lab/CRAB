# Adding a build recipe

A recipe teaches `crab setup` how to **obtain and build** a benchmark on the current machine. It's
optional — you only need one if you want CRAB to build the benchmark for you rather than pointing
it at a binary you built yourself. Running a recipe produces a [receipt](receipts.md).

## The contract

Create a module in `src/crab/setup/recipes/` with a class subclassing `BenchmarkRecipe`. The setup
wizard **auto-discovers** any such subclass in that package — just dropping the file in is enough,
no registration.

```python
from .base import BenchmarkRecipe, BuildResult

class MyBenchRecipe(BenchmarkRecipe):
    @property
    def name(self) -> str:
        return "My Benchmark"

    @property
    def benchmark_id(self) -> str:
        return "mybench"     # MUST match the wrapper's benchmark_id
```

!!! warning "`benchmark_id` is the link"
    The recipe's `benchmark_id` is what ties the generated receipt to the
    [wrapper](wrappers.md#1-link-to-the-binary). They must be identical (here, `"mybench"`).

## Required methods

| Method | Returns | Purpose |
|--------|---------|---------|
| `name` (property) | `str` | Display name in the wizard. |
| `benchmark_id` (property) | `str` | Unique id; names the receipt and links the wrapper. |
| `check_dependencies(env)` | `(bool, str)` | Pre-flight check (compilers, tools present?) against the build environment. |
| `download_and_build(target_dir, params, env, log_callback)` | `(bool, BuildResult \| None, str)` | Clone and compile; return the build result or an error message. |
| `verify_existing(path)` | `bool` | Does this path already contain a valid build? |

## Optional overrides

| Member | Default | Purpose |
|--------|---------|---------|
| `suite` (property) | `name` | Groups multiple recipes under one wizard entry (e.g. QE v6 & v7). |
| `launcher_override` (property) | `""` | Force a launcher (e.g. `"mpirun"`) regardless of the cluster default. |
| `pre_run_hooks` (property) | `[]` | Commands recorded into the receipt to run before each launch. |
| `build_manifest` (property) | `BuildManifest()` | Declares build inputs — whether modules are needed and any `BuildParameter`s (e.g. a `cpu`/`gpu` choice). |
| `fast_search(dir)` | checks `<dir>/<id>` and `PATH` | Tier-1 auto-detect of an existing install. |

The base class also gives you **`run_command_streamed(cmd, cwd, step_name, env, log_callback)`** — run
a build command with its output streamed live into the wizard UI. Use it for every clone/compile
step.

## Worked example (Graph500)

```python
import os, shutil
from typing import Tuple, Optional, Callable, Dict
from .base import BenchmarkRecipe, BuildResult

class G500Recipe(BenchmarkRecipe):
    @property
    def name(self) -> str: return "Graph500"

    @property
    def benchmark_id(self) -> str: return "g500"

    def check_dependencies(self, env: Dict[str, str]) -> Tuple[bool, str]:
        if not shutil.which("mpicc", path=env.get("PATH")):
            return False, "MPI compiler (mpicc) not found."
        if not shutil.which("make", path=env.get("PATH")):
            return False, "Make is missing."
        return True, "Dependencies found."

    def download_and_build(self, target_dir, params, env, log_callback=None):
        if not self.run_command_streamed(
            ["git", "clone", "https://github.com/graph500/graph500.git", target_dir],
            ".", "Cloning Repository...", env, log_callback):
            return False, None, "Git clone failed."

        src_dir = os.path.join(target_dir, "src")
        if not self.run_command_streamed(
            ["make", "MPICC=mpicc -fcommon", "-j"],
            src_dir, "Compiling Binaries...", env, log_callback):
            return False, None, "Make compilation failed."

        if os.path.exists(os.path.join(src_dir, "graph500_reference_bfs")):
            return True, BuildResult(binary_path=src_dir), "Built successfully."
        return False, None, "Binaries missing after build."

    def verify_existing(self, path: str) -> bool:
        binary = os.path.join(path, "graph500_reference_bfs")
        return os.path.isfile(binary) and os.access(binary, os.X_OK)

    def fast_search(self, crab_benchmarks_dir: str) -> Optional[str]:
        target = os.path.join(crab_benchmarks_dir, "g500", "src")
        return target if self.verify_existing(target) else None
```

## Build parameters and metadata

To ask the user for build-time choices, declare a `build_manifest` with `BuildParameter`s and read
them from the `params` dict in `download_and_build`. Anything you put in
`BuildResult(binary_path=..., metadata={...})` is merged into the receipt — for example QE records
`{"target_arch": "gpu"}`, which CRAB later uses for an architecture guardrail.

```python
from .base import BuildManifest, BuildParameter

    @property
    def build_manifest(self) -> BuildManifest:
        return BuildManifest(
            requires_modules=True,
            parameters=[BuildParameter(name="arch", description="Target architecture",
                                       choices=["cpu", "gpu"], default="cpu")],
        )
```

## What the binary_path should point to

Return whatever directory or file your [wrapper](wrappers.md) expects. The convention used by the
shipped recipes is to return the **directory** containing the binaries (e.g. `bin/`, `src/`) and let
the wrapper append the specific executable name via `get_path`. Be consistent between the two.

Once the recipe exists, `crab setup` lists it, builds it, and writes the receipt — see
[From recipe to receipt](receipts.md). For the exact base-class signatures, see the
[Wrapper & Recipe API](../reference/api.md).
