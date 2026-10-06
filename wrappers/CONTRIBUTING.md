# Contributing a wrapper

A wrapper is shared by everyone who runs the same application, so the bar is: it parses real
output correctly, and a test proves it.

## 1. Start from the scaffold

In a CRAB checkout with this repository on its wrapper search path:

```bash
crab wrappers new myapp            # creates myapp/myapp.py, README.md and samples/example/
crab wrappers test myapp --strict  # passes as generated
```

Use `--local` to create it in your CRAB checkout's `local/wrappers/` while it is private.

## 2. Write the wrapper

The wrapper is a class named `app` that subclasses `crab.wrappers.base.base`:

- `executable`: the program name CRAB looks up on `PATH` when neither the config's `binary` key
  nor a receipt gives a path.
- `benchmark_id`: the receipt name, for `crab receipts set <id> --binary <path>`.
- `metadata`: one entry per measured value: `{"name": ..., "unit": ..., "conv": True|False}`.
  `conv: True` means CRAB repeats runs until that value converges. Optional: `"type"` (`"float"`
  by default, or `"int"`, `"str"` or `"bool"`); `"role": "check"` for a boolean correctness
  verdict, where false fails the run.
- `keys`: names of sweep dimensions every row carries, for example `["size"]` when one run prints
  results for several message sizes. Leave it empty when a run gives single values.
- `read_data()`: return a list of rows, one dict per sample, holding every key and every metric
  and nothing else. The application's output is in `self.stdout` and `self.stderr`, and files it
  wrote are in `self.run_dir`.
- `get_extra_artifacts()` (optional): paths or glob patterns, relative to `self.run_dir`, of
  files to keep with the results.

Rules:

- If the output is not what you expect, raise an exception. CRAB then marks the run as failed and
  logs why. Never return zeros or placeholders: they look like real measurements.
- Report what the application measures, in its own units. Ratios between runs (slowdowns,
  interference) belong to the analysis, not to the wrapper.

## 3. Add a real sample

Replace `samples/example/` with output from a real run. Put it in `samples/<case>/`:
`stdout.txt` (plus `stderr.txt` and any files the application wrote), `case.json` naming the
wrapper and the `args` it was run with, and `expected.json` with the rows the wrapper must
return. Say in the app's README where each sample comes from: system, date, application version.

Check a single case by hand with:

```bash
crab parse myapp/myapp.py myapp/samples/<case>/stdout.txt --check myapp/samples/<case>/expected.json
```

## 4. Open a pull request

Run `crab wrappers test --strict` from a CRAB checkout that uses this repository, then open the
pull request. CI runs the same command. If your wrapper fixes one listed in `unverified.txt`,
remove its line there.
