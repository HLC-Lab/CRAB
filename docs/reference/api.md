# Wrapper & Recipe API

The base-class signatures you implement against when [extending CRAB](../extending/overview.md).
Source: `src/crab/wrappers/base.py` and `src/crab/setup/recipes/base.py`.

## Wrapper base — `crab.wrappers.base.base`

Subclass this as a class named `app` (or an intermediate suite class). Methods you typically
override are marked **override**.

| Member | Signature | Notes |
|--------|-----------|-------|
| `__init__` | `(self, id_num, collect_flag, args)` | Set by CRAB on instantiation. Call `super().__init__(...)` if you override. |
| `benchmark_id` | `property -> str` | **override** — return the id linking to a [receipt](../extending/receipts.md). Default `""` (no receipt). |
| `metadata` | class/instance attr: `list[dict]` | **override** — `[{"name", "unit", "conv"}, ...]`, the order `read_data` returns. |
| `read_data` | `(self) -> list[list]` | **override** — parse `self.stdout`; return one sample-list per metric, in `metadata` order. Default `[]`. |
| `get_binary_path` | `(self) -> str \| None` | **override** for suites — default returns the receipt's `binary_path`. |
| `get_receipt` | `(self) -> dict \| None` | Loads the receipt for `benchmark_id`. |
| `get_pre_commands` | `(self) -> list` | Returns the receipt's `hooks.pre_run`. |
| `get_launcher_override` | `(self) -> str` | Returns the receipt's `launcher_override`. |
| `run_app` | `(self) -> str` | The launch command: `get_binary_path() + " " + self.args`. Rarely overridden. |
| `set_output` | `(self, stdout, stderr)` | Called by CRAB; decodes streams into `self.stdout` / `self.stderr`. |
| `set_nodes` | `(self, node_list)` | Called by CRAB after allocation. |
| `get_bench_name` | `(self) -> str` | **override** (optional) — a display name. |
| `get_bench_input` | `(self) -> str` | **override** (optional) — a human label for the input (e.g. message size). |

Useful attributes available at run time: `self.args` (argument string), `self.id_num`,
`self.collect_flag`, `self.node_list` / `self.num_nodes`, `self.stdout` / `self.stderr` (after the
run), plus any extra config keys injected from the JSON app entry.

Helper exported alongside `base`:

- `sizeof_fmt(num, suffix="B") -> str` — human-readable byte sizes (e.g. for `get_bench_input`).

## Recipe base — `crab.setup.recipes.base.BenchmarkRecipe`

Subclass this (in `src/crab/setup/recipes/`) to make a benchmark buildable by `crab setup`.
Auto-discovered — no registration.

**Abstract (must implement):**

| Member | Signature | Notes |
|--------|-----------|-------|
| `name` | `property -> str` | Display name. |
| `benchmark_id` | `property -> str` | Unique id; must match the wrapper's `benchmark_id`. |
| `check_dependencies` | `(self, env) -> (bool, str)` | Pre-flight tool/compiler check against the build env. |
| `download_and_build` | `(self, target_dir, params, env, log_callback=None) -> (bool, BuildResult \| None, str)` | Clone + compile. |
| `verify_existing` | `(self, path) -> bool` | Whether a path holds a valid build. |

**Optional (have defaults):**

| Member | Default | Notes |
|--------|---------|-------|
| `suite` | `name` | Group label for the wizard (multiple versions under one entry). |
| `launcher_override` | `""` | Force a launcher (e.g. `"mpirun"`). Recorded into the receipt. |
| `pre_run_hooks` | `[]` | Commands recorded into the receipt's `hooks.pre_run`. |
| `build_manifest` | `BuildManifest()` | Declares module requirement and build parameters. |
| `fast_search` | checks `<dir>/<id>` and `PATH` | Tier-1 auto-detect. |

**Provided helper:**

- `run_command_streamed(self, cmd, cwd, step_name, env, log_callback) -> bool` — run a build step,
  streaming output to the wizard. Returns success.

## Build dataclasses

```python
@dataclass
class BuildParameter:
    name: str
    description: str
    choices: Optional[List[str]] = None   # for a multiple-choice prompt
    default: str = ""

@dataclass
class BuildManifest:
    requires_modules: bool = True         # prompt for module loads before building
    parameters: List[BuildParameter] = []  # extra build-time inputs

@dataclass
class BuildResult:
    binary_path: str                       # written to the receipt
    metadata: Dict[str, Any] = {}          # merged into the receipt (e.g. target_arch)
```

See [Writing a wrapper](../extending/wrappers.md) and [Adding a build recipe](../extending/recipes.md)
for worked examples.
