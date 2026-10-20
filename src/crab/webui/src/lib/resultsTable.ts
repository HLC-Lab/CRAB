// Pure sort/filter/column-visibility logic, ported from crab_dashboard.html's
// TableRenderer module. No DOM access — ResultsTable.vue wires these into a
// <table>, so this stays unit-testable without mounting a component.
import type { ResultRow } from "./resultsChart";

// Display-only cap, same as the legacy dashboard: a very large result tree
// shouldn't try to render tens of thousands of DOM rows at once.
export const MAX_DISPLAY_ROWS = 5000;

/** Columns pre-checked when a new set of columns first appears: run_id and
 * msg_size if present, plus up to two "avg*" columns; if that leaves fewer
 * than 3 selected, everything is shown instead. */
export function defaultVisibleColumns(cols: string[]): Set<string> {
  const visible = new Set<string>(["run_id", "msg_size"]);
  cols
    .filter((c) => /avg/i.test(c))
    .slice(0, 2)
    .forEach((c) => visible.add(c));
  if (visible.size < 3) cols.forEach((c) => visible.add(c));
  return visible;
}

export function filterRows(rows: ResultRow[], cols: string[], search: string): ResultRow[] {
  const q = search.trim().toLowerCase();
  if (!q) return rows;
  return rows.filter((r) =>
    cols.some((c) =>
      String(r[c] ?? "")
        .toLowerCase()
        .includes(q),
    ),
  );
}

export type SortDir = 1 | -1;

export function sortRows(rows: ResultRow[], col: string | null, dir: SortDir): ResultRow[] {
  if (!col) return rows;
  return [...rows].sort((a, b) => {
    const av = a[col];
    const bv = b[col];
    const cmp =
      typeof av === "number" && typeof bv === "number"
        ? av - bv
        : String(av ?? "").localeCompare(String(bv ?? ""));
    return cmp * dir;
  });
}
