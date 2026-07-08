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
  assignColors,
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

/** One removable chip per selected series, for the Compare workbench's
 * selection summary. The single place colors are assigned for a selection --
 * `selectedSeries` in ResultsCompareView.vue derives its own trace colors
 * from this same list, so a chip and its chart trace can never disagree. */
export interface CompareChip {
  id: string;
  label: string;
  color: string;
}

export function buildChips(selected: Map<string, SeriesMeta>): CompareChip[] {
  const ids = [...selected.keys()];
  const colors = assignColors(ids);
  return ids.map((id) => {
    const m = selected.get(id)!;
    return { id, label: `${m.jobBasename} / ${m.experiment} / ${m.app}`, color: colors[id] };
  });
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

/** One unit for `col`, computed across every selected series' values at once
 * (decision 11) -- never per series, so every card/overlay trace agrees. */
export function sharedUnit(series: CompareSeries[], col: string): Unit {
  const vals = series.flatMap((s) => {
    const resolved = resolveCol(s.rows, col);
    return s.rows.map((r) => r[resolved]).filter((v): v is number => typeof v === "number");
  });
  return unitForCol(col, vals);
}

function remapCol(rows: ResultRow[], col: string): ResultRow[] {
  const resolved = resolveCol(rows, col);
  if (resolved === col) return rows;
  return rows.map((r) => ({ ...r, [col]: r[resolved] }));
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
    const rows = remapCol(remapCol(s.rows, xCol), yCol);
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
    const rows = remapCol(remapCol(s.rows, xCol), yCol);
    return {
      id: s.id,
      label: s.label,
      traces: makePlotlyTraces(rows, xCol, yCol, kind, yUnit, s.color, s.label),
    };
  });
}
