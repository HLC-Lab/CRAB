// Wrapper attributes in the Author editor. The engine injects every app key that is not one
// of its own (`RESERVED_APP_KEYS`) into the wrapper, for example Quantum ESPRESSO's
// `input_file` and `pseudo_dir`. No per-wrapper schema exists, so the editor shows them as
// free key and value rows and keeps them in `AppDraft.rest`.
import { RESERVED_APP_KEYS } from "@/lib/config";

export interface WrapperRow {
  key: string;
  value: string;
}

// `bind` is no app field of the editor, but CRAB refuses it in a campaign (ADR-045), so a
// row may not smuggle it in either.
const REFUSED_KEYS: ReadonlySet<string> = new Set([...RESERVED_APP_KEYS, "bind"]);

/** The text a row shows for a stored value: strings as they are, anything else as JSON. */
function shown(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function restToRows(rest: Record<string, unknown>): WrapperRow[] {
  return Object.entries(rest).map(([key, value]) => ({ key, value: shown(value) }));
}

/** One message per row: empty when the row is fine or still blank. */
export function rowProblems(rows: WrapperRow[]): string[] {
  const seen = new Set<string>();
  return rows.map((row) => {
    const key = row.key.trim();
    if (!key) return "";
    if (REFUSED_KEYS.has(key)) return `"${key}" is reserved: it has its own field`;
    if (seen.has(key)) return `"${key}" is set twice`;
    seen.add(key);
    return "";
  });
}

/**
 * The attributes a list of rows stands for. Rows without a key, with a reserved key or with
 * a repeated key are left out. A value that was not a string (a number, a list) stays as it
 * was while its row text is untouched; an edited row stores the text.
 */
export function rowsToRest(
  rows: WrapperRow[],
  previous: Record<string, unknown>,
): Record<string, unknown> {
  const problems = rowProblems(rows);
  const rest: Record<string, unknown> = {};
  rows.forEach((row, i) => {
    const key = row.key.trim();
    if (!key || problems[i]) return;
    const before = previous[key];
    rest[key] =
      key in previous && typeof before !== "string" && shown(before) === row.value
        ? before
        : row.value;
  });
  return rest;
}
