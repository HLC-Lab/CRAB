# Writing a wrapper

A wrapper is a Python file that tells CRAB how to launch one application and how to turn its
output into measurements. It is the only piece strictly required to support a new application.
Wrappers are shared: one wrapper per application means everyone who runs it parses its output the
same way. This page describes version 1 of the wrapper contract
([ADR-031](../dev/dashboard/decisions/adr-031-wrapper-contract-v1.md)).

## Start from the scaffold

```bash
crab wrappers new myapp            # myapp/myapp.py, myapp/README.md, myapp/samples/example/
crab wrappers test myapp --strict  # passes as generated
```

The wrapper is created in the shared wrappers folder (the first folder of `CRAB_PATH_WRAPPERS`, or
`wrappers/` in the checkout). Use `--local` to create it in `local/wrappers/` instead, which is
git-ignored and meant for private or in-progress wrappers, or `--dir <folder>` for any other
folder.

## Where CRAB looks for wrappers

A config names a wrapper by `path`, for example `blink/a2a_b.py`. A relative path is looked up in
these folders, and the first match wins:

1. `local/wrappers/` in the CRAB checkout;
2. each folder in `CRAB_PATH_WRAPPERS` (separate several with `:`), or the checkout's `wrappers/`
   when the variable is not set.

An absolute `path` is used as is. `crab wrappers list` shows the search path and every wrapper
found on it.

## The contract

Define a class named **`app`** that subclasses `base`:

```python
from crab.wrappers.base import base


class app(base):
    executable = "osu_latency"   # looked up on PATH when no binary is configured
    benchmark_id = "osu"         # receipt name, for `crab receipts set osu --binary ...`
    keys = ["size"]              # sweep dimensions every row carries
    metadata = [
        {"name": "latency", "unit": "us", "conv": True},
    ]

    def read_data(self):
        rows = []
        for line in self.stdout.splitlines():
            if line and not line.startswith("#"):
                size, latency = line.split()
                rows.append({"size": int(size), "latency": float(latency)})
        return rows
```

| Attribute or method | Meaning |
|---|---|
| `metadata` | One entry per measured value: `name`, `unit`, and `conv` (`True` means CRAB repeats runs until this value converges). Optional `type`: `"float"` (default), `"int"`, `"str"` or `"bool"`. Text and boolean fields are recorded but never used for convergence. |
| `keys` | Names of the sweep dimensions, for an application that prints results for several settings in one run (for example one line per message size). Leave it empty when a run gives single values. |
| `read_data()` | Returns a list of rows, one dict per sample, holding every key and every metric and nothing else. The application's output is in `self.stdout` and `self.stderr`. |
| `executable` | Program name found on `PATH` when neither the config nor a receipt gives a binary. |
| `benchmark_id` | Name of the [receipt](receipts.md) that records where the binary lives. |
| `get_extra_artifacts()` | Optional: paths or glob patterns, relative to `self.run_dir`, of files to keep with the results. |

**When the output is not what you expect, raise an exception.** CRAB checks every result against
`keys` and `metadata`. A result that raises, misses a field, has an undeclared field or holds a
value of the wrong type fails that run, with the reason in the log. Nothing is recorded for it, and
the experiment goes on with the next run. Never return zeros or placeholders: they would look like
real measurements.

### Sweeps and convergence

With `keys`, convergence is computed separately for every key value: in the example, the latency
of each message size has to converge on its own. In the CSV, the key columns come right after
`run_id` and `msg_size`, followed by one `<app>_<metric>_<unit>` column per metric.

### Correctness checks

A metric declared with `"role": "check"` and `"type": "bool"` is a verdict on the run (for example
a benchmark's own validation). When a row's check is false, that row is still written to the CSV,
but the run counts as failed and the row is left out of convergence.

```python
metadata = [
    {"name": "gflops", "unit": "GF", "conv": True},
    {"name": "passed", "unit": "", "conv": False, "type": "bool", "role": "check"},
]
```

### The older shape

Wrappers written before version 1 return one list of samples per metric from `read_data()`, in
`metadata` order. That shape is still accepted and converted to rows, and CRAB logs a warning once
per experiment. It stops being accepted at v1.0, so new wrappers should return rows.

## Finding the binary

For a wrapper that uses the default launch (`binary + " " + args`), CRAB takes the binary from, in
order:

1. the app's `binary` key in the experiment config;
2. the wrapper's receipt (`get_binary_path()`, which reads the receipt named by `benchmark_id`);
3. `executable`, looked up on `PATH` in the job's environment.

When none of them gives a binary, the experiment fails before its first run, with a message listing
what was tried. A wrapper whose receipt stores a directory rather than the executable can override
`get_binary_path()` to add the file name:

```python
    def get_binary_path(self):
        receipt = self.get_receipt()
        if not receipt:
            return None
        return os.path.join(receipt.get("binary_path", ""), "my_executable")
```

A wrapper can also override `run_app()` to build the whole command itself; CRAB then does not
check for a binary before the run.

## Files the application writes

Each app runs in its own working directory, `<experiment>/run_<n>/app_<id>/`, available to the
wrapper as `self.run_dir`, so co-running apps never overwrite each other's files. `read_data()` may
read files there as well as stdout. Files listed by `get_extra_artifacts()` are copied to
`<experiment>/artifacts/run_<n>/app_<id>/` after every run, including failed ones, so they survive
`retain_files: false`.

## Testing with real samples

A sample case is real output plus the rows the wrapper must produce for it:

```
myapp/samples/<case>/
  case.json       {"wrapper": "myapp.py", "args": "-n 4", "set": {}}
  stdout.txt      the application's output (plus stderr.txt and any files it wrote)
  expected.json   [{"size": 8, "latency": 1.5}, ...]
```

`crab wrappers test [app ...]` runs every case through the same parser a run uses and compares
the rows with `expected.json`. With `--strict`, a wrapper that no case covers fails too, unless
the folder's `unverified.txt` lists it. To try a single output by hand:

```bash
crab parse myapp/myapp.py output.txt --args "-n 4" --json
crab parse myapp/myapp.py samples/<case>/stdout.txt --check samples/<case>/expected.json
```

`case.json`'s `set` holds extra config keys for the wrapper (see below); `crab parse` takes them as
`--set key=value`.

## Custom configuration keys

Any key in an app's config entry other than the reserved `path`, `args`, `collect`, `start`, `end`
and `partition` becomes an attribute on the wrapper instance, so a wrapper can take its own
settings from the JSON (for example an input file name).

## Suite pattern: one helper, many thin wrappers

When one build produces many related binaries, put the shared logic (`benchmark_id`, `metadata`,
`read_data` and a path helper) in a helper module whose name starts with `_`, so CRAB does not list
it as a wrapper, and make each wrapper a few lines that name its binary:

```python
import os
import sys

sys.path.append(os.path.dirname(__file__))
from _suite_common import suite


class app(suite):
    def get_binary_path(self):
        return self.get_path("alltoall")
```

## Hooks from the receipt

Commands to run before each launch and a launcher override come from the
[receipt](receipts.md): `base.get_pre_commands()` and `base.get_launcher_override()` read them, so
wrappers normally do not override them. For every method you can override, see the
[Wrapper & Recipe API](../reference/api.md).
