// Plotly trace/layout builders, replacing resultsChart.ts's Chart.js-shaped
// makeChartData/buildChartOptions/xScaleType. Chart aesthetic is print/paper
// styled (decision 3): white background and a serif font regardless of the
// app's own dark/light theme -- deliberate, scoped to the chart canvas only.
import Plotly from "plotly.js-cartesian-dist-min";
import type { Data, Layout } from "plotly.js";
import {
  binByX,
  formatBytes,
  isSizeCol,
  isTimeCol,
  type ChartKind,
  type ResultRow,
  type ScaleKind,
  type Unit,
} from "@/lib/resultsChart";

const PRINT_FONT = "'Georgia', 'Times New Roman', serif";
const PAPER_BG = "#ffffff";
const AXIS_LINE = "#333333";
const GRID_LINE = "#e2e2e2";
const TEXT_COLOR = "#1a1a1a";

/** One experiment's rows, as a Plotly trace for the given axes/kind. */
export function makePlotlyTraces(
  rows: ResultRow[],
  xCol: string,
  yCol: string,
  kind: ChartKind,
  yUnit: Unit,
  color: string,
  label: string,
): Data[] {
  const validRows = rows.filter((r) => r[xCol] != null && r[yCol] != null);

  if (kind === "scatter" || kind === "line") {
    return [
      {
        type: "scatter",
        mode: kind === "line" ? "lines" : "markers",
        name: label,
        x: validRows.map((r) => r[xCol] as number | string),
        y: validRows.map((r) => (r[yCol] as number) / yUnit.div),
        marker: { color, size: 7 },
        line: { color, shape: kind === "line" ? "linear" : undefined },
      } as unknown as Data,
    ];
  }

  const bins = binByX(validRows, xCol, yCol);
  const keys = Object.keys(bins).sort((a, b) => Number(a) - Number(b));
  const displayKey = (k: string) => (isSizeCol(xCol) ? formatBytes(Number(k)) : k);

  if (kind === "bar") {
    return [
      {
        type: "bar",
        name: label,
        x: keys.map(displayKey),
        y: keys.map((k) => {
          const vs = bins[k];
          return vs.reduce((a, v) => a + v, 0) / vs.length / yUnit.div;
        }),
        marker: { color },
      } as unknown as Data,
    ];
  }

  // violin: one categorical x-value per raw point, repeated per x-bin.
  const x: (string | number)[] = [];
  const y: number[] = [];
  keys.forEach((k) => {
    bins[k].forEach((v) => {
      x.push(displayKey(k));
      y.push(v / yUnit.div);
    });
  });
  return [
    {
      type: "violin",
      name: label,
      x,
      y,
      marker: { color },
      line: { color },
      box: { visible: true },
      meanline: { visible: true },
      points: false,
    } as unknown as Data,
  ];
}

/** Print/paper-themed Plotly `layout` for one chart. */
export function makePlotlyLayout(
  xCol: string,
  yCol: string,
  xUnit: Unit,
  yUnit: Unit,
  kind: ChartKind,
  scale: ScaleKind,
  showLegend: boolean,
): Partial<Layout> {
  const isCategory = kind === "bar" || kind === "violin";
  const xLabel = xCol + (xUnit.label ? ` (${xUnit.label})` : "");
  const yLabel = yCol + (yUnit.label ? ` (${yUnit.label})` : "");

  return {
    paper_bgcolor: PAPER_BG,
    plot_bgcolor: PAPER_BG,
    font: { family: PRINT_FONT, color: TEXT_COLOR, size: 13 },
    showlegend: showLegend,
    margin: { l: 70, r: 30, t: 20, b: 60 },
    violingap: 0.3,
    xaxis: {
      title: { text: xLabel },
      type: isCategory ? "category" : scale === "logarithmic" ? "log" : "linear",
      gridcolor: GRID_LINE,
      linecolor: AXIS_LINE,
      zeroline: false,
      showline: true,
    },
    yaxis: {
      title: { text: yLabel },
      gridcolor: GRID_LINE,
      linecolor: AXIS_LINE,
      zeroline: false,
      showline: true,
    },
  } as unknown as Partial<Layout>;
}

/** Decision 5's default-axis heuristic: a sweep/size-shaped column as X, a
 * metric-shaped (time/bandwidth) column as Y -- falls back to column order. */
export function defaultAxisPair(cols: string[]): { x: string; y: string } {
  if (!cols.length) return { x: "", y: "" };
  const x = cols.find(isSizeCol) ?? cols[0];
  const y = cols.find((c) => c !== x && isTimeCol(c)) ?? cols.find((c) => c !== x) ?? x;
  return { x, y };
}

/** Print-quality raster/vector export of a rendered chart. */
export async function exportChartImage(
  gd: HTMLElement,
  format: "png" | "svg",
  filename: string,
): Promise<void> {
  await Plotly.downloadImage(gd, { format, width: 1600, height: 900, filename });
}
