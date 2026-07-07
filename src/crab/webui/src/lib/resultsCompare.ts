// Pure overlay-merge logic, ported from crab_dashboard.html's CompareRenderer
// module (buildForCompare). No DOM access — CompareView.vue wires this into
// Chart.js and a <canvas> per mode (overlay vs small-multiples).
import {
  binByX,
  formatBytes,
  isSizeCol,
  makeChartData,
  type ChartDataSpec,
  type ChartKind,
  type ResultRow,
  type Unit,
} from "./resultsChart";

export interface CompareExperiment {
  name: string;
  color: string;
  rows: ResultRow[];
}

/** Strip a numeric prefix ("1_Avg-Duration_s" -> "Avg-Duration_s") and find the
 * matching column in `rows` that may carry a different prefix (per-app CSVs
 * number their columns independently). Falls back to `col` unchanged. */
export function resolveCol(rows: ResultRow[], col: string): string {
  if (!rows.length || col in rows[0]) return col;
  const suffix = col.replace(/^\d+_/, "");
  if (suffix === col) return col;
  const match = Object.keys(rows[0]).find((c) => c.replace(/^\d+_/, "") === suffix);
  return match ?? col;
}

/** Merge several experiments' rows into one Chart.js data spec: multiple
 * datasets sharing one pair of axes (overlay mode), or a small-multiples
 * caller can instead call makeChartData once per experiment directly. */
export function makeOverlayChartData(
  experiments: CompareExperiment[],
  xCol: string,
  yCol: string,
  kind: ChartKind,
  yUnit: Unit,
): ChartDataSpec {
  const resolved = experiments.map((exp) => ({
    exp,
    xC: resolveCol(exp.rows, xCol),
    yC: resolveCol(exp.rows, yCol),
  }));

  if (kind === "scatter" || kind === "line") {
    return {
      datasets: resolved.flatMap(({ exp, xC, yC }) => {
        const mapped = exp.rows.map((r) => ({ ...r, [xCol]: r[xC], [yCol]: r[yC] }));
        return makeChartData(mapped, xCol, yCol, kind, yUnit, exp.color, exp.name).datasets;
      }),
    };
  }

  const perExp = resolved.map(({ exp, xC, yC }) => {
    const rows = exp.rows.filter((r) => r[xC] != null && r[yC] != null);
    return { exp, bins: binByX(rows, xC, yC) };
  });
  const allLabels = [...new Set(perExp.flatMap(({ bins }) => Object.keys(bins)))].sort(
    (a, b) => Number(a) - Number(b),
  );

  return {
    labels: allLabels.map((l) => (isSizeCol(xCol) ? formatBytes(Number(l)) : l)),
    datasets: perExp.map(({ exp, bins }) => ({
      label: exp.name,
      data:
        kind === "bar"
          ? allLabels.map((l) => {
              const vs = bins[l];
              return vs ? vs.reduce((a, v) => a + v, 0) / vs.length / yUnit.div : null;
            })
          : allLabels.map((l) => (bins[l] ? bins[l].map((v) => v / yUnit.div) : [])),
      backgroundColor: exp.color + (kind === "bar" ? "cc" : "66"),
      borderColor: exp.color,
      borderWidth: 1,
      ...(kind !== "bar"
        ? { outlierColor: exp.color, medianColor: exp.color, padding: 0.15, itemRadius: 0 }
        : {}),
    })),
  };
}
