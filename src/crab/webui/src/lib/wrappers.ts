// Pure helpers for the Wrappers page: grouping, counts and labels for the cluster's
// `crab wrappers list --json` catalog. No fetch/DOM, so the unit suite imports them directly.

import type { WrapperEntry } from "@/api/types";

export interface AppGroup {
  app: string;
  wrappers: WrapperEntry[];
}

/** Wrappers grouped by their top folder (the app), in the order the catalog lists them. */
export function groupByApp(wrappers: WrapperEntry[]): AppGroup[] {
  const groups = new Map<string, WrapperEntry[]>();
  for (const w of wrappers) {
    const app = w.relpath.includes("/") ? w.relpath.split("/")[0] : "(top level)";
    if (!groups.has(app)) groups.set(app, []);
    groups.get(app)!.push(w);
  }
  return [...groups.entries()].map(([app, list]) => ({ app, wrappers: list }));
}

const FOUND = new Set(["config", "receipt", "path"]);

/** found: a binary is known; missing: none found; broken: cannot load or the lookup failed. */
export function summarize(wrappers: WrapperEntry[]): {
  found: number;
  missing: number;
  broken: number;
} {
  let found = 0;
  let missing = 0;
  let broken = 0;
  for (const w of wrappers) {
    if (!w.loadable || w.binary.status === "error") broken += 1;
    else if (FOUND.has(w.binary.status)) found += 1;
    else missing += 1;
  }
  return { found, missing, broken };
}

const LABELS: Record<string, string> = {
  config: "set in config",
  receipt: "receipt",
  path: "found on PATH",
  missing: "missing",
  error: "lookup failed",
};

export function binaryLabel(w: WrapperEntry): string {
  if (!w.loadable) return "cannot load";
  return LABELS[w.binary.status] ?? w.binary.status;
}

/** An import writes a receipt, so it needs a loadable wrapper with a receipt id and no binary
 * found. A failed lookup ("error") is a bug in the wrapper: a receipt would not fix it, and it
 * could overwrite a receipt the app's other wrappers share. */
export function canImport(w: WrapperEntry): boolean {
  return w.loadable && !!w.benchmark_id && w.binary.status === "missing";
}

/** A wrapper needs attention: it cannot load, or no binary was found for it. */
export function hasProblem(w: WrapperEntry): boolean {
  return !w.loadable || !FOUND.has(w.binary.status);
}
