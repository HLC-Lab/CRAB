// Pure cross-job compare merge layer (plan 077 decisions 10, 11). Fresh
// implementation reusing the filename S9 freed when the single-job
// Chart.js-based CompareView was deleted -- not a resurrection of that code.
// `CompareSeries` is deliberately NOT scoped to one job: any
// (cluster, system, jobBasename, experiment, app) row-set can be added to
// one comparison canvas (decision 10). `resolveCol` is ported as-is from the
// deleted `resultsCompare.ts` (per-app CSVs number their columns
// independently, e.g. "1_Avg-Duration_s" vs "2_Avg-Duration_s" for the same
// metric). `sharedUnit` fixes a real bug found during 077's design: the old
// small-multiples mode picked each card's unit independently, so two cards
// could silently show "the same" axis in different units/scales -- this
// computes ONE unit across every selected series up front instead.
import {
  formatBytes,
  isSizeCol,
  numericCols,
  unitForCol,
  type ChartKind,
  type ResultRow,
  type Unit,
} from "@/lib/resultsChart";
import { makePlotlyTraces } from "@/lib/resultsPlot";
import type { Data } from "plotly.js";

export interface CompareSeries {
  id: string;
  label: string;
  color: string;
  rows: ResultRow[];
}

/** Identifies one (job, experiment, app) row-set a caller can add to the
 * canvas -- shared between the tree browser and the workbench page so both
 * agree on the same id for the same selection. */
export interface SeriesMeta {
  cluster: string;
  system: string;
  jobBasename: string;
  experiment: string;
  app: string;
}

export function seriesId(m: SeriesMeta): string {
  return `${m.cluster}|${m.system}|${m.jobBasename}|${m.experiment}|${m.app}`;
}

/** Strip a numeric prefix ("1_Avg-Duration_s" -> "Avg-Duration_s") and find the
 * matching column in `rows` that may carry a different prefix -- or none at
 * all, since `sharedColumns()` offers callers the CANONICAL (unprefixed)
 * name. Matching is always done on the canonical suffix regardless of
 * whether `col` itself has a prefix; falls back to `col` unchanged when no
 * such column exists. */
export function resolveCol(rows: ResultRow[], col: string): string {
  if (!rows.length || col in rows[0]) return col;
  const suffix = col.replace(/^\d+_/, "");
  const match = Object.keys(rows[0]).find((c) => c.replace(/^\d+_/, "") === suffix);
  return match ?? col;
}

/** The axis-pickable columns: numeric columns present in EVERY selected
 * series, matched by canonical (numeric-prefix-stripped) name -- never a
 * union. A union would let a user pick an axis only some series have,
 * silently rendering the others with no data (found during this plan's own
 * S17 render-verify pass: a series with no matching X column contributed an
 * empty, invisible trace instead of an error). */
export function sharedColumns(series: CompareSeries[]): string[] {
  if (!series.length) return [];
  const canonical = (rows: ResultRow[]) =>
    new Set(numericCols(rows).map((c) => c.replace(/^\d+_/, "")));
  const [first, ...rest] = series.map((s) => canonical(s.rows));
  return [...first].filter((c) => rest.every((set) => set.has(c)));
}

function collectValues(series: CompareSeries[], col: string): number[] {
  return series.flatMap((s) => {
    const resolved = resolveCol(s.rows, col);
    return s.rows.map((r) => r[resolved]).filter((v): v is number => typeof v === "number");
  });
}

/** One unit for `col`, computed across every selected series' values at once
 * (decision 11) -- never per series, so every card/overlay trace agrees. */
export function sharedUnit(series: CompareSeries[], col: string): Unit {
  return unitForCol(col, collectValues(series, col));
}

/** One min/max for `col` across every selected series' RAW values (divide by
 * the matching `Unit.div` before use) -- lets small multiples share one axis
 * range, not just one unit, so cards are actually comparable side by side
 * instead of each auto-scaling to its own data. `null` when no series has
 * any numeric value for `col`. */
export function sharedRange(series: CompareSeries[], col: string): [number, number] | null {
  const vals = collectValues(series, col);
  return vals.length ? [Math.min(...vals), Math.max(...vals)] : null;
}

/** The category axis order a bar/violin Overlay chart's shared `col` axis
 * should use, numerically sorted -- Plotly's own default `categoryorder`
 * ("trace") instead orders categories by which trace/series first mentions
 * them, i.e. SELECTION order (owner bug report: selecting msg_size 1k, then
 * 4k, then 2k plotted the axis in that click order, not 1k/2k/4k). Formatted
 * through the same `isSizeCol`/`formatBytes` rule `makePlotlyTraces` already
 * uses for bar/violin labels, so this array matches those traces' own `x`
 * strings exactly -- Plotly matches `categoryarray` entries by string. */
export function categoryOrder(series: CompareSeries[], col: string): string[] {
  const keys = new Set<string>();
  series.forEach((s) => {
    const resolved = resolveCol(s.rows, col);
    s.rows.forEach((r) => {
      const v = r[resolved];
      if (v != null) keys.add(String(v));
    });
  });
  const sorted = [...keys].sort((a, b) => Number(a) - Number(b));
  return isSizeCol(col) ? sorted.map((k) => formatBytes(Number(k))) : sorted;
}

/** One pass over `rows` remapping BOTH axis columns at once, instead of two
 * separate full-array passes (one per axis) each copying every row. */
function remapCols(rows: ResultRow[], xCol: string, yCol: string): ResultRow[] {
  const resolvedX = resolveCol(rows, xCol);
  const resolvedY = resolveCol(rows, yCol);
  if (resolvedX === xCol && resolvedY === yCol) return rows;
  return rows.map((r) => ({ ...r, [xCol]: r[resolvedX], [yCol]: r[resolvedY] }));
}

/** One trace per series, sharing one pair of axes -- an overlay chart. */
export function makeOverlayTraces(
  series: CompareSeries[],
  xCol: string,
  yCol: string,
  kind: ChartKind,
  yUnit: Unit,
): Data[] {
  return series.flatMap((s) => {
    const rows = remapCols(s.rows, xCol, yCol);
    return makePlotlyTraces(rows, xCol, yCol, kind, yUnit, s.color, s.label);
  });
}

export interface SmallMultiple {
  id: string;
  label: string;
  traces: Data[];
}

/** One independent trace set per series, all using the SAME `yUnit` -- a
 * caller renders each entry in its own small Plotly div. */
export function makeSmallMultipleTraces(
  series: CompareSeries[],
  xCol: string,
  yCol: string,
  kind: ChartKind,
  yUnit: Unit,
): SmallMultiple[] {
  return series.map((s) => {
    const rows = remapCols(s.rows, xCol, yCol);
    return {
      id: s.id,
      label: s.label,
      traces: makePlotlyTraces(rows, xCol, yCol, kind, yUnit, s.color, s.label),
    };
  });
}
