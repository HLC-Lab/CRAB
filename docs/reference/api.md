# Wrapper & Recipe API

The base-class signatures you implement against when [extending CRAB](../extending/overview.md).
Source: `src/crab/wrappers/base.py` and `src/crab/setup/recipes/base.py`.

## Wrapper base — `crab.wrappers.base.base`

Subclass this as a class named `app` (or an intermediate suite class). Methods you typically
override are marked **override**.

| Member | Signature | Notes |
|--------|-----------|-------|
| `__init__` | `(self, id_num, collect_flag, args)` | Set by CRAB on instantiation. Call `super().__init__(...)` if you override. |
| `wrapper_api` | class attr: `int` | The wrapper contract version the base implements (`1`). |
| `benchmark_id` | `property -> str` | **override** (or a plain class attribute): the id linking to a [receipt](../extending/receipts.md). Default `""` (no receipt). |
| `metadata` | class attr: `list[dict]` | **override**: `[{"name", "unit", "conv", "type"?, "role"?}, ...]`. Default `[]`. |
| `keys` | class attr: `list[str]` | Sweep dimensions every row carries. Default `[]`. |
| `executable` | class attr: `str` | Program looked up on `PATH` when no binary is configured. Default `""`. |
| `read_data` | `(self) -> list[dict]` | **override**: parse `self.stdout` (and files in `self.run_dir`); return one dict per sample with every key and metric. The older one-list-per-metric return is still accepted. Default `[]`. |
| `get_extra_artifacts` | `(self) -> list[str]` | **override** (optional): paths or globs, relative to `self.run_dir`, to keep with the results. Default `[]`. |
| `get_binary_path` | `(self) -> str \| None` | **override** for suites: default returns the receipt's `binary_path`. |
| `find_binary` | `(self) -> (str \| None, str)` | The binary and where it came from: `"config"`, `"receipt"`, `"path"`, or `(None, "missing")`. |
| `resolve_binary` | `(self) -> str` | The binary from `find_binary`, or raises `MissingBinaryError` listing what was tried. |
| `get_receipt` | `(self) -> dict \| None` | Loads the receipt for `benchmark_id`. |
| `get_pre_commands` | `(self) -> list` | Returns the receipt's `hooks.pre_run`. |
| `get_launcher_override` | `(self) -> str` | Returns the receipt's `launcher_override`. |
| `run_app` | `(self) -> str` | The launch command: `resolve_binary() + " " + self.args`. Override it to build the whole command yourself. |
| `set_output` | `(self, stdout, stderr)` | Called by CRAB; decodes streams into `self.stdout` / `self.stderr`. |
| `set_nodes` | `(self, node_list)` | Called by CRAB after allocation. |
| `get_bench_name` | `(self) -> str` | **override** (optional) — a display name. |
| `get_bench_input` | `(self) -> str` | **override** (optional): a human label for the input (e.g. message size). Not read by the engine. |

Useful attributes available at run time: `self.args` (argument string), `self.id_num`,
`self.collect_flag`, `self.node_list` / `self.num_nodes`, `self.run_dir` (the app's working
directory for the current run), `self.stdout` / `self.stderr` (after the run), plus any extra
config keys injected from the JSON app entry (for example `self.binary`).

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
| `launcher_override` | `""` | Force a launcher: `""` (use the preset's), `"srun"` or `"mpirun"`. Recorded into the receipt. Any other value makes the experiment fail at setup with an error naming the app. |
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
