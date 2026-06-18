# From recipe to receipt

A **receipt** is the small JSON file that records where a benchmark's binary lives on this machine,
plus how to launch it. It is the bridge between building a benchmark and running it: a
[recipe](recipes.md) (or you, by hand) writes it; a [wrapper](wrappers.md) reads it.

Receipts live in `config/environments/<benchmark_id>.json` (the directory is created on first
setup).

## How `crab setup` generates one

When you run `crab setup` and configure a benchmark, the wizard's chosen strategy determines the
receipt's `type`:

| Wizard strategy | Receipt `type` | What gets recorded |
|-----------------|----------------|--------------------|
| Auto-detect / Manual path | `binary` | The located/entered path. |
| Environment module | `module` | The binary name, with the `module load …` command added as a pre-run hook. |
| Build from source | `source` | The freshly built path, plus any build metadata (e.g. `target_arch`). |

On success the wizard writes the receipt and prints a confirmation. See
[Installation → benchmark setup](../using/installation.md#set-up-benchmarks-crab-setup).

## Receipt structure

```json
{
    "id": "g500",
    "type": "source",
    "binary_path": "/home/me/CRAB/benchmarks/g500/src",
    "launcher_override": "",
    "hooks": {
        "pre_run": ["module load gcc openmpi"],
        "post_run": []
    }
}
```

| Field | Meaning |
|-------|---------|
| `id` | The `benchmark_id`. |
| `type` | `binary` / `module` / `source` — how it was configured. |
| `binary_path` | Where the executable (or its directory) lives. What the wrapper reads. |
| `launcher_override` | A launcher to use instead of the cluster default (e.g. `mpirun` for QE). Empty = use default. |
| `hooks.pre_run` | Shell commands run before each launch (e.g. module loads). |
| `target_arch` | (optional) Set from build metadata; used for the GPU/CPU partition guardrail. |

## How wrappers consume it

A wrapper with a matching `benchmark_id` reads the receipt through helper methods on `base`:

- `get_receipt()` → the parsed receipt dict.
- `get_binary_path()` → `binary_path` (override to append a specific binary name for suites).
- `get_pre_commands()` → `hooks.pre_run`.
- `get_launcher_override()` → `launcher_override`.

In addition, at run time the [orchestrator](../glossary.md#orchestrator) exports every receipt's
binary path as an environment variable **`CRAB_PATH_<ID>`** (uppercased id). Older wrappers read
that variable directly instead of calling `get_receipt()`; both work.

## Registering a custom binary with `crab setup`

For any binary that CRAB doesn't have a recipe for — something you built yourself, or an
application already installed on the cluster — run:

```bash
crab setup
```

and choose **"Register a custom already-installed benchmark"**. The wizard prompts you for the
`benchmark_id`, the path to the binary, any pre-run module loads, and an optional launcher
override, then writes the receipt for you. This is the recommended path: no JSON editing required.

## Writing a receipt by hand

If you prefer (or are scripting a setup), create the receipt directly —
write `config/environments/<benchmark_id>.json` pointing `binary_path` at your build:

```json
{
    "id": "mybench",
    "type": "binary",
    "binary_path": "/scratch/me/mybench/bin",
    "launcher_override": "",
    "hooks": { "pre_run": [], "post_run": [] }
}
```

As long as a [wrapper](wrappers.md) declares the same `benchmark_id` (`"mybench"`), it will resolve
the binary from this file.

!!! note "Receipts are system-dependent"
    A receipt describes one machine — its paths and module commands won't transfer. It is
    deliberately *not* committed (the `config/environments/` contents are local). Regenerate (or
    re-write) receipts on each system. See
    [System-dependent vs system-independent](../concepts/system-dependent-vs-independent.md).
