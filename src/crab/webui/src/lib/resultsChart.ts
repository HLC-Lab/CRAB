// Pure chart-data helpers, ported from crab_dashboard.html's ChartRenderer
// module: column-shape detection, unit scaling, color assignment, binning.
// No DOM/chart-library access, so this stays unit-testable in isolation.
// Chart-library-specific trace/layout builders live in lib/resultsPlot.ts.

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
