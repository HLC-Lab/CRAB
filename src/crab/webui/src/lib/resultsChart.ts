// Pure chart data/config builders, ported from crab_dashboard.html's ChartRenderer
// module. No DOM access — ResultsChart.vue wires these into Chart.js and a <canvas>,
// so this stays unit-testable without a real canvas.

export type ResultRow = Record<string, unknown>;
export type ChartKind = "scatter" | "line" | "bar" | "violin";
export type ScaleKind = "linear" | "logarithmic";

export interface Unit {
  div: number;
  label: string;
}

const SIZE_COLS = ["msg_size", "message_size", "size", "bytes", "count", "n"];
const TIME_COLS_RE = /duration|time|latency|bw|bandwidth/i;

export function isSizeCol(col: string): boolean {
  return SIZE_COLS.includes((col || "").toLowerCase());
}

export function isTimeCol(col: string): boolean {
  return TIME_COLS_RE.test(col || "");
}

function finitePositive(vals: number[]): number[] {
  return vals.filter((v) => Number.isFinite(v) && v > 0);
}

export function autoUnit(vals: number[]): Unit {
  const finite = finitePositive(vals);
  const max = finite.length ? Math.max(...finite) : NaN;
  if (!Number.isFinite(max) || max === 0) return { div: 1, label: "" };
  if (max < 1e-9) return { div: 1e-12, label: "ps" };
  if (max < 1e-6) return { div: 1e-9, label: "ns" };
  if (max < 1e-3) return { div: 1e-6, label: "μs" };
  if (max < 1) return { div: 1e-3, label: "ms" };
  return { div: 1, label: "s" };
}

export function autoUnitGeneric(vals: number[]): Unit {
  const finite = finitePositive(vals);
  const max = finite.length ? Math.max(...finite) : NaN;
  if (!Number.isFinite(max) || max === 0) return { div: 1, label: "" };
  if (max >= 1e12) return { div: 1e12, label: "T" };
  if (max >= 1e9) return { div: 1e9, label: "G" };
  if (max >= 1e6) return { div: 1e6, label: "M" };
  if (max >= 1e3) return { div: 1e3, label: "K" };
  return { div: 1, label: "" };
}

export function unitForCol(col: string, vals: number[]): Unit {
  return isTimeCol(col) ? autoUnit(vals) : autoUnitGeneric(vals);
}

export function formatBytes(b: number): string {
  if (b == null || Number.isNaN(b)) return String(b);
  if (b < 1024) return `${b} B`;
  if (b < 1048576) return `${(b / 1024).toFixed(0)} KiB`;
  if (b < 1073741824) return `${(b / 1048576).toFixed(1)} MiB`;
  return `${(b / 1073741824).toFixed(2)} GiB`;
}

export function formatVal(col: string, v: unknown): string {
  if (isSizeCol(col) && typeof v === "number") return formatBytes(v);
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toPrecision(4);
  return String(v);
}

export function numericCols(rows: ResultRow[]): string[] {
  if (!rows.length) return [];
  return Object.keys(rows[0]).filter((k) => typeof rows[0][k] === "number");
}

// 20-color perceptually-spaced palette (avoids adjacent conflicts), same as the
// legacy dashboard's DataStore.PALETTE.
const PALETTE = [
  "#4e79a7",
  "#f28e2b",
  "#e15759",
  "#76b7b2",
  "#59a14f",
  "#edc948",
  "#b07aa1",
  "#ff9da7",
  "#9c755f",
  "#bab0ac",
  "#499894",
  "#86bcb6",
  "#d4a6c8",
  "#f1ce63",
  "#d37295",
  "#a0cbe8",
  "#ffbe7d",
  "#8cd17d",
  "#b6992d",
  "#fabfd2",
];

export function assignColors(names: string[]): Record<string, string> {
  const colors: Record<string, string> = {};
  names.forEach((name, i) => {
    colors[name] = PALETTE[i % PALETTE.length];
  });
  return colors;
}

export function binByX(rows: ResultRow[], xCol: string, yCol: string): Record<string, number[]> {
  const bins: Record<string, number[]> = {};
  rows.forEach((r) => {
    const x = r[xCol];
    const y = r[yCol];
    if (x == null || y == null) return;
    const key = String(x);
    if (!bins[key]) bins[key] = [];
    bins[key].push(y as number);
  });
  return bins;
}

export function xScaleType(
  kind: ChartKind,
  scale: ScaleKind,
): "category" | "linear" | "logarithmic" {
  if (kind === "bar" || kind === "violin") return "category";
  return scale;
}

export interface ChartDataSpec {
  labels?: string[];
  // Chart.js dataset shape varies by chart type; kept loose here since this
  // module only builds the config, Chart.js itself validates it at render time.
  datasets: Record<string, unknown>[];
}

/** One experiment's rows, as a Chart.js dataset for the given axes/kind. */
export function makeChartData(
  rows: ResultRow[],
  xCol: string,
  yCol: string,
  kind: ChartKind,
  yUnit: Unit,
  color: string,
  label: string,
): ChartDataSpec {
  const validRows = rows.filter((r) => r[xCol] != null && r[yCol] != null);

  if (kind === "scatter" || kind === "line") {
    return {
      datasets: [
        {
          type: kind,
          label,
          data: validRows.map((r) => ({
            x: r[xCol],
            y: (r[yCol] as number) / yUnit.div,
          })),
          backgroundColor: color + (kind === "scatter" ? "cc" : "33"),
          borderColor: color,
          pointRadius: kind === "line" ? 2 : 3,
          pointHoverRadius: 6,
          showLine: kind === "line",
          tension: 0.2,
          fill: false,
        },
      ],
    };
  }

  const bins = binByX(validRows, xCol, yCol);
  const labels = Object.keys(bins).sort((a, b) => Number(a) - Number(b));

  if (kind === "bar") {
    return {
      labels: labels.map((l) => (isSizeCol(xCol) ? formatBytes(Number(l)) : l)),
      datasets: [
        {
          label,
          data: labels.map((l) => {
            const vs = bins[l];
            return vs.reduce((a, v) => a + v, 0) / vs.length / yUnit.div;
          }),
          backgroundColor: color + "cc",
          borderColor: color,
          borderWidth: 1,
        },
      ],
    };
  }

  // violin
  return {
    labels: labels.map((l) => {
      const n = bins[l].length;
      const base = isSizeCol(xCol) ? formatBytes(Number(l)) : l;
      return `${base} (n=${n})`;
    }),
    datasets: [
      {
        label,
        data: labels.map((l) => bins[l].map((v) => v / yUnit.div)),
        backgroundColor: color + "66",
        borderColor: color,
        borderWidth: 1,
        outlierColor: color,
        medianColor: color,
        padding: 0.15,
        itemRadius: 0,
      },
    ],
  };
}

export interface ThemeColors {
  text: string;
  text2: string;
  bg2: string;
  bg3: string;
  border: string;
}

/** Chart.js `options` for one chart. `multiDataset` toggles the legend (a
 * single-experiment chart doesn't need one). */
export function buildChartOptions(
  xCol: string,
  yCol: string,
  xUnit: Unit,
  yUnit: Unit,
  kind: ChartKind,
  scale: ScaleKind,
  multiDataset: boolean,
  theme: ThemeColors,
): Record<string, unknown> {
  const isCategory = kind === "bar" || kind === "violin";
  const xFmt = isCategory
    ? (v: unknown) => v
    : isSizeCol(xCol)
      ? (v: number) => formatBytes(v)
      : (v: number) => (v / xUnit.div).toPrecision(3) + (xUnit.label ? ` ${xUnit.label}` : "");

  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 250 },
    layout: { padding: { bottom: kind === "violin" ? 22 : 0 } },
    plugins: {
      legend: {
        display: multiDataset,
        labels: {
          color: theme.text2,
          font: { family: "'JetBrains Mono'", size: 11 },
          boxWidth: 10,
          padding: 14,
        },
      },
      tooltip: {
        backgroundColor: theme.bg2,
        borderColor: theme.border,
        borderWidth: 1,
        bodyColor: theme.text,
        titleFont: { family: "'JetBrains Mono'", size: 11, weight: "600" },
        bodyFont: { family: "'JetBrains Mono'", size: 11 },
      },
    },
    scales: {
      x: {
        type: xScaleType(kind, scale),
        title: {
          display: true,
          text: xCol + (xUnit.label ? ` (${xUnit.label})` : ""),
          color: theme.text2,
          font: { family: "'JetBrains Mono'", size: 11 },
        },
        ticks: {
          color: theme.text2,
          font: { family: "'JetBrains Mono'", size: 10 },
          callback: isCategory ? (v: unknown) => v : xFmt,
          maxTicksLimit: 12,
        },
        grid: { color: theme.bg3 },
      },
      y: {
        title: {
          display: true,
          text: `${yCol} (${yUnit.label})`,
          color: theme.text2,
          font: { family: "'JetBrains Mono'", size: 11 },
        },
        ticks: { color: theme.text2, font: { family: "'JetBrains Mono'", size: 10 } },
        grid: { color: theme.bg3 },
      },
    },
  };
}
