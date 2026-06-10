#!/usr/bin/env python3
"""
Heatmap of efficiency: baseline_time / congested_time.
  - value = 1.0  → no impact
  - value < 1.0  → congested is slower (worse)
X-axis: burst_gap, Y-axis: burst_duration.
One merged figure with one row per message size and one column per congestion type.
"""

import re
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from pathlib import Path


MSG_SIZES = {
    0: 8,
    1: 64,
    2: 512,
    3: 4096,
    4: 32768,
    5: 262144,
    6: 2097152,
    7: 16777216,
}


def size_label(nbytes):
    if nbytes < 1024:
        return f"{nbytes} B"
    elif nbytes < 1024 ** 2:
        return f"{nbytes // 1024} KB"
    else:
        return f"{nbytes // 1024 ** 2} MB"


def load_timings(folder: Path, col_suffix: str) -> dict:
    """Return {app_id -> mean timing} for all data_app_*.csv in folder.

    Each data_app_N.csv uses column 'N_<col_suffix>'.
    """
    results = {}
    for csv_file in sorted(folder.glob("data_app_*.csv")):
        app_id = int(re.search(r"data_app_(\d+)", csv_file.name).group(1))
        col_name = f"{app_id}_{col_suffix}"
        df = pd.read_csv(csv_file)
        results[app_id] = df[col_name].mean()
    return results


def fmt_val(v):
    return f"{v:.0e}"


def plot_heatmaps(data_dir: Path, output_path: Path, col_suffix: str):
    # Load baseline
    baseline_mean = load_timings(data_dir / "baseline", col_suffix)

    # Parse all congested experiments
    pattern = re.compile(r"^cong_(\w+)_([\d.eE+-]+)_([\d.eE+-]+)$")
    congested = {}  # (cong_type, burst_gap, burst_dur, app_id) -> mean_timing
    for folder in sorted(data_dir.iterdir()):
        m = pattern.match(folder.name)
        if not m:
            continue
        cong_type = m.group(1)
        burst_gap = float(m.group(2))
        burst_dur = float(m.group(3))
        for app_id, mean_val in load_timings(folder, col_suffix).items():
            congested[(cong_type, burst_gap, burst_dur, app_id)] = mean_val

    cong_types = sorted(set(ct for ct, *_ in congested))
    burst_gaps = sorted(set(bg for _, bg, _, _ in congested))
    burst_durs = sorted(set(bd for _, _, bd, _ in congested))
    app_ids = sorted(baseline_mean.keys())

    GRID_ROWS, GRID_COLS = 2, 4  # 8 subplots = one per message size

    # Build all matrices first to get a global color scale
    all_vals = []
    matrices = {}  # (app_id, cong_type) -> 2D array
    for app_id in app_ids:
        baseline_val = baseline_mean.get(app_id)
        if baseline_val is None:
            continue
        for cong_type in cong_types:
            mat = np.full((len(burst_durs), len(burst_gaps)), np.nan)
            for i, bd in enumerate(burst_durs):
                for j, bg in enumerate(burst_gaps):
                    key = (cong_type, bg, bd, app_id)
                    if key in congested:
                        # baseline / congested: <1 means congested is slower
                        mat[i, j] = baseline_val / congested[key]
            matrices[(app_id, cong_type)] = mat
            all_vals.extend(mat[~np.isnan(mat)].tolist())

    if not all_vals:
        print("No data found.")
        return

    cmap = mcolors.LinearSegmentedColormap.from_list(
        "speedup_red_to_green_to_white",
        [
            (0.00, "#680C17"),
            (0.20, "#B2182B"),
            (0.65, "#FD8B7A"),
            (0.90, "#FDD17A"),
            (0.95, "#B7E4A8"),
            (1.00, "#1A9850"),
        ],
        N=256,
    )
    norm = mcolors.Normalize(vmin=0.0, vmax=1.0)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # One figure per congestion type, 2 rows × 4 cols of message sizes
    for cong_type in cong_types:
        fig, axes = plt.subplots(
            GRID_ROWS, GRID_COLS,
            figsize=(5.5 * GRID_COLS, 4.5 * GRID_ROWS),
            squeeze=False,
        )

        for idx, app_id in enumerate(app_ids):
            row, col = divmod(idx, GRID_COLS)
            ax = axes[row][col]
            mat = matrices.get((app_id, cong_type))
            if mat is None:
                ax.set_visible(False)
                continue

            im = ax.imshow(
                np.clip(mat, 0.0, 1.0), aspect="auto", origin="lower",
                cmap=cmap, norm=norm,
            )
            ax.set_xticks(range(len(burst_gaps)))
            ax.set_xticklabels([fmt_val(v) for v in burst_gaps], rotation=45, ha="right")
            ax.set_yticks(range(len(burst_durs)))
            ax.set_yticklabels([fmt_val(v) for v in burst_durs])
            ax.set_xlabel("Burst Gap (s)")
            ax.set_ylabel("Burst Duration (s)")
            ax.set_title(size_label(MSG_SIZES.get(app_id, app_id)), fontsize=11)

            for i in range(mat.shape[0]):
                for j in range(mat.shape[1]):
                    val = mat[i, j]
                    if not np.isnan(val):
                        ax.text(
                            j, i, f"{val:.2f}",
                            ha="center", va="center",
                            fontsize=9, fontweight="bold",
                            color="black",
                        )

            plt.colorbar(im, ax=ax, label="baseline / congested")

        # Hide any unused axes
        for idx in range(len(app_ids), GRID_ROWS * GRID_COLS):
            row, col = divmod(idx, GRID_COLS)
            axes[row][col].set_visible(False)

        fig.suptitle(
            f"Speedup heatmap — congestion: {cong_type}  "
            f"(baseline / congested)  |  green = 1 = no impact, red < 1 = congested slower",
            fontsize=13,
        )
        plt.tight_layout()

        stem = output_path.stem
        suffix = output_path.suffix
        out = output_path.with_name(f"{stem}_{cong_type}{suffix}")
        plt.savefig(out, dpi=150, bbox_inches="tight")
        print(f"Saved: {out}")
        plt.close(fig)


DATA_ROOT = Path("data/leonardo")


def resolve_data_dir(folder: str) -> Path:
    """Accept either a full path or a bare folder name under data/leonardo/."""
    p = Path(folder)
    if p.exists():
        return p
    candidate = DATA_ROOT / folder
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        f"Could not find experiment directory: '{folder}'\n"
        f"Tried: {p} and {candidate}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Plot speedup heatmaps from CRAB experiment data."
    )
    parser.add_argument(
        "folder",
        help="Experiment folder: bare name (e.g. 2026-04-03_13-11-17-878013) or full path",
    )
    parser.add_argument(
        "--output",
        default="plots/heatmap.png",
        help="Output image path (default: plots/heatmap.png)",
    )
    parser.add_argument(
        "--timing-col",
        default="Avg-Duration_s",
        choices=[
            "Avg-Duration_s",
            "Min-Duration_s",
            "Max-Duration_s",
            "Median-Duration_s",
            "MainRank-Duration_s",
        ],
        help="Timing column suffix (default: Avg-Duration_s)",
    )
    args = parser.parse_args()

    try:
        data_dir = resolve_data_dir(args.folder)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1

    plot_heatmaps(data_dir, Path(args.output), args.timing_col)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
