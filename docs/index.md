# 🦀 CRAB

**CRAB** (Co-Running Applications Benchmarking framework) is a framework for executing,
collecting, and analyzing high-performance computing (HPC) benchmarks on clusters managed by
**Slurm**. Its purpose is to run **multiple applications at the same time** on a supercomputer
and measure whether — and how much — they **interfere** with one another (network congestion,
shared-resource contention).

## The core idea

CRAB treats the applications it runs as black boxes. Any executable can be benchmarked, because
each one is described by a small Python **wrapper** that teaches the framework two things:

1. **How to launch it** — the binary path and arguments.
2. **How to read it** — how to parse the program's output into numeric metrics.

Because every application funnels through the same wrapper contract, the collected performance
data is stored in a **uniform format** (CSV) regardless of which benchmark produced it — ready
for analysis with pandas, R, or the bundled `crab_dashboard.html` viewer.

## What you can express

- **Application mixes.** Run several applications concurrently, designating *victims* (the
  applications you measure) and *aggressors* (applications whose only job is to create
  interference).
- **Scheduling.** Start applications after a delay, or only once another application finishes;
  terminate them naturally, after a fixed time, or as soon as the victims complete.
- **Node layout.** Place applications across the allocated nodes linearly, interleaved, or in
  dedicated victim/aggressor partitions.
- **Statistical rigor.** Repeat each experiment until its metrics reach a configurable
  confidence interval, so results are converged rather than single-shot.

## Two interfaces

| Interface | Command | Use it for |
|-----------|---------|------------|
| **CLI**   | `crab run …` | Automation, scripting, batch studies |
| **TUI**   | `crab tui`   | Interactive, visual configuration and monitoring |

## Where to go next

- **New to CRAB?** Start with the concepts:
  [System-dependent vs system-independent](concepts/system-dependent-vs-independent.md) then
  [Architecture](concepts/architecture.md) — together they explain the whole mental model.
- **Want to run benchmarks?** [Install CRAB and set up benchmarks](using/installation.md),
  [configure your cluster](using/presets.md), then
  [write a config](using/writing-configs.md) and [run it](using/running.md).
- **Need to support a new application?** See [Extending CRAB](extending/overview.md) —
  writing a [wrapper](extending/wrappers.md) and, optionally, a [build recipe](extending/recipes.md).
- **Looking something up?** The [Reference](reference/configuration.md) section has the full
  configuration schema, CLI, preset format, and API, and the [Glossary](glossary.md) defines every
  term.
