# Writing a wrapper

A wrapper is a Python module in `wrappers/` that teaches CRAB how to launch one application and
parse its output. It is the only artifact strictly required to support a new application.

## The contract

Define a class named **`app`** that subclasses `base`:

```python
from crab.wrappers.base import base

class app(base):
    ...
```

!!! warning "Import path"
    Import the base class as `from crab.wrappers.base import base`. (The README shows
    `from wrappers.base import base` — that is outdated and does not match the installed package.)

CRAB loads the module *by file path* at run time and instantiates `app(id_num, collect_flag, args)`,
so your class is found automatically — no registration needed. You reference it from a config by its
file path (see [Configuration schema](../reference/configuration.md#the-apps-block)).

A wrapper needs three things: a way to **find the binary**, a description of its **metrics**, and a
way to **parse its output**.

## 1. Link to the binary

The cleanest approach is to set a `benchmark_id` matching a [receipt](receipts.md). The base class
then resolves the binary path from that receipt automatically:

```python
class app(base):
    @property
    def benchmark_id(self) -> str:
        return "mybench"      # → config/environments/mybench.json
```

If the receipt's `binary_path` points **directly at the executable**, that's all you need — `base`
returns it. If the receipt stores a **directory** (as the build recipes do), override
`get_binary_path` to append the specific binary name:

```python
    def get_binary_path(self):
        receipt = self.get_receipt()
        if not receipt:
            return None
        return os.path.join(receipt.get("binary_path", ""), "my_executable")
```

## 2. Declare metrics (`metadata`)

`metadata` is a list of metric descriptors, in the order `read_data` will return them:

```python
    metadata = [
        {"name": "performance", "unit": "GTEPS", "conv": True},
        {"name": "time",        "unit": "s",     "conv": False},
    ]
```

`conv: True` marks a metric as a [convergence](../glossary.md#convergence) target — CRAB keeps
repeating runs until those metrics stabilize (unless `convergeall` overrides this).

## 3. Parse the output (`read_data`)

`read_data` is the heart of a wrapper. CRAB captures the application's stdout into `self.stdout`
after the run; your job is to turn it into samples.

**Contract:** return a **list of lists** — one inner list per metric, in `metadata` order, each
containing that metric's samples from the run.

```python
    def read_data(self):
        performance, times = [], []
        for line in self.stdout.splitlines():
            if line.startswith("RESULT:"):
                _, p, t = line.split(",")
                performance.append(float(p))
                times.append(float(t))
        return [performance, times]   # same order as metadata
```

## How CRAB invokes your wrapper

1. It builds the command as `get_binary_path() + " " + self.args` (the `args` come from the config).
2. It launches that on the app's allocated nodes through the workload manager, capturing stdout.
3. On success, it calls `read_data()` and appends the returned samples to the per-metric data.

`self.args` is the argument string from the config. Any **extra keys** in the app's config entry
(beyond the reserved `path`/`args`/`collect`/`start`/`end`/`partition`) are injected as attributes
on your instance — so a wrapper can read custom configuration straight from the JSON.

## Suite pattern: shared base + thin wrappers

When one build produces many related binaries (as the Blink suite does), put the shared logic —
`benchmark_id`, `metadata`, `read_data`, and a path helper — in one intermediate class, and make
each per-binary wrapper tiny. This is exactly how the supported Blink wrappers are structured.

**`wrappers/blink/microbench_common.py`** — the shared base:

```python
from crab.wrappers.base import base, sizeof_fmt

class microbench(base):
    @property
    def benchmark_id(self) -> str:
        return "blink"

    metadata = [
        {"name": "Avg-Duration",    "unit": "s", "conv": True},
        {"name": "Min-Duration",    "unit": "s", "conv": False},
        # ...
    ]

    def get_path(self, name):
        receipt = self.get_receipt()
        if not receipt:
            return None
        return os.path.join(receipt.get("binary_path", ""), name)   # binary_path is the bin/ dir

    def read_data(self):
        rows = [[float(x) for x in line.split(",")]
                for line in self.stdout.splitlines()[2:-1]]
        return [list(col) for col in zip(*rows)]   # transpose rows → per-metric series
```

**`wrappers/blink/a2a_comm_only.py`** — one concrete benchmark:

```python
import sys, os
sys.path.append(os.path.dirname(__file__))
from microbench_common import microbench

class app(microbench):
    def get_binary_path(self):
        return self.get_path("a2a_comm_only")
```

The thin wrapper imports its sibling common module by adding its own directory to `sys.path`, then
only has to name its specific binary.

## Optional hooks

Pre-run commands and a launcher override come from the [receipt](receipts.md), not the wrapper —
`base.get_pre_commands()` and `base.get_launcher_override()` read them automatically. You generally
don't override these in the wrapper.

For the full list of methods you can override, see the [Wrapper & Recipe API](../reference/api.md).
Once your wrapper exists, point a [receipt](receipts.md) at the binary and it's usable in any
config.
