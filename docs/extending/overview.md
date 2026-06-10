# Extending CRAB

CRAB treats applications as black boxes, so teaching it to run one it doesn't support yet — whether
something you wrote or an existing application not yet integrated — never requires touching the
framework core. You add small, self-contained files.

## What you need to add

There are two artifacts, and only the first is always required:

| Artifact | Required? | What it does | Where |
|----------|-----------|--------------|-------|
| **[Wrapper](wrappers.md)** | **Always** | Teaches CRAB how to *launch* the application and *parse* its output into metrics. | `wrappers/*.py` |
| **[Recipe](recipes.md)** | Optional | Teaches `crab setup` how to *download and build* the application. | `src/crab/setup/recipes/*.py` |

These connect through a **[receipt](receipts.md)** — a small JSON file recording where the built
binary lives on this machine. A recipe *produces* a receipt; a wrapper *reads* one.

```mermaid
flowchart LR
    R["Recipe<br/>(optional)"] -->|crab setup builds it| T["Receipt<br/>config/environments/&lt;id&gt;.json"]
    T -->|read at run time| W["Wrapper<br/>(required)"]
    W --> M["uniform metrics"]
```

## Two paths

**I already have the binary** (built it myself, or it's installed on the cluster):

1. Write a [wrapper](wrappers.md).
2. Point it at the binary — either via a hand-written [receipt](receipts.md#writing-a-receipt-by-hand)
   or by running `crab setup` and choosing the *manual path* / *environment module* strategy (which
   works even without a recipe, if a recipe exists for that id).

**I want CRAB to build it for me:**

1. Write a [recipe](recipes.md) so `crab setup` can clone and compile it.
2. Write a [wrapper](wrappers.md) that shares the recipe's `benchmark_id`.
3. Run `crab setup` — it builds the benchmark and writes the receipt automatically.

## How this maps to system-dependent vs system-independent

The split from [Concepts](../concepts/system-dependent-vs-independent.md) tells you exactly which
piece holds what:

- A wrapper's **parsing logic** (`read_data`) and its `metadata` are **system-independent** — the
  same on every machine.
- The **binary location** is **system-dependent** — so a wrapper never hard-codes a path; it reads
  it from the receipt (via its `benchmark_id`). That indirection is what lets one wrapper work on
  every cluster.

Start with [Writing a wrapper](wrappers.md).
