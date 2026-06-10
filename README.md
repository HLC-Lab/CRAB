# 🦀 CRAB — Co-Running Applications Benchmarking framework

[![Python Version](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Documentation Status](https://readthedocs.org/projects/hlc-crab/badge/?version=latest)](https://hlc-crab.readthedocs.io/en/latest/?badge=latest)
[![CI](https://github.com/HLC-Lab/CRAB/actions/workflows/ci.yml/badge.svg)](https://github.com/HLC-Lab/CRAB/actions/workflows/ci.yml)

**CRAB** runs **multiple HPC applications simultaneously** on Slurm-managed clusters to measure
their performance and quantify how they **interfere** with one another (network congestion,
shared-resource contention). Applications are described by small Python **wrappers** that teach
CRAB how to launch them and parse their output, so results are collected uniformly regardless of
the application — designate *victims* (measured) and *aggressors* (interference generators), and
CRAB orchestrates the rest.

![asciicast](https://user-images.githubusercontent.com/11363902/203875389-918931a5-e110-4107-8854-c8c3656ab3e2.gif)

## ✨ Key features

* **Dual interface** — a **Command Line Interface** for automation and a **Textual UI** for interactive use.
* **System-portable experiments** — the same experiment config runs on any cluster; per-system details live in a centralized **preset** system (`leonardo`, `lumi`, …).
* **Complex application mixes** — run many applications at once, designating *victims* and *aggressors*.
* **Automated, converged data collection** — gathers performance data and stops once statistical convergence is reached.
* **Standard output** — results saved as CSV, ready for pandas or R.
* **Built-in dashboard** — `crab export` produces a self-contained HTML file with scatter, line, bar, and violin charts, a **Compare** tab for cross-experiment overlays, and dark/light themes. No server needed to share results.
* **Extensible** — support a new benchmark by adding a Python wrapper; no core changes needed.

## 📖 Documentation

**Full documentation: [hlc-crab.readthedocs.io](https://hlc-crab.readthedocs.io/)** — installation,
configuring a cluster, writing experiments, extending CRAB with new benchmarks, and the complete
reference.

Quick links:
[Tutorial](https://hlc-crab.readthedocs.io/en/latest/tutorial/) ·
[Concepts](https://hlc-crab.readthedocs.io/en/latest/concepts/system-dependent-vs-independent/) ·
[Installation & setup](https://hlc-crab.readthedocs.io/en/latest/using/installation/) ·
[Configuring your cluster](https://hlc-crab.readthedocs.io/en/latest/using/presets/) ·
[Writing experiment configs](https://hlc-crab.readthedocs.io/en/latest/using/writing-configs/) ·
[Extending CRAB](https://hlc-crab.readthedocs.io/en/latest/extending/overview/) ·
[Reference](https://hlc-crab.readthedocs.io/en/latest/reference/configuration/)

To build the docs locally:

```bash
pip install -r docs/requirements.txt
mkdocs serve        # → http://127.0.0.1:8000
```

## 🚀 Quick start

**Prerequisites:** Python 3.10+, Git, and access to a **Slurm** cluster — CRAB submits every run
with `sbatch`, so Slurm must be available (even the `local` preset, which only changes the
per-application launcher to `mpirun`).

> 💡 Prefer a guided walkthrough? The **[end-to-end tutorial](https://hlc-crab.readthedocs.io/en/latest/tutorial/)**
> runs a complete victim-vs-aggressor experiment step by step. The essentials:

```bash
git clone https://github.com/HLC-Lab/CRAB
cd CRAB
make                        # creates .venv, installs CRAB (editable), runs the setup wizard
source .venv/bin/activate   # activate before using `crab`
```

Build or locate the benchmarks you want to run:

```bash
crab setup
```

Run an experiment, or launch the interactive UI:

```bash
crab run <config.json> -p <preset>
crab tui
```

Results are written under `data/<system>/<name>_<timestamp>/`. Share them as a self-contained
HTML dashboard:

```bash
crab export data/<system>/<name>_<timestamp>/ -o results.html
```

See the [documentation](docs/using/installation.md) for configuring presets, writing experiment
configs, and adding your own benchmarks.

## 📜 License

Released under the MIT License. See the [`LICENSE`](LICENSE) file for details.
