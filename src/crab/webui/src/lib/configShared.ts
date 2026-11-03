// Small pure helpers shared by the config mapping (lib/config.ts) and the
// flow diagram (lib/flow.ts). Kept free of Vue/IO. Types only are imported from
// config.ts, so there is no runtime cycle.

import type { AllocationDraft } from "@/lib/config";

/**
 * Whether the user has actually configured an allocation. There is no explicit
 * on/off toggle: the `allocation` key is emitted only when there is real content,
 * so an untouched editor leaves the config at the engine's default (linear, equal
 * split) rather than writing a redundant `{"mode":"linear"}`. A non-linear mode,
 * a split, or at least one named node group all count as content. (`stride`/`seed`
 * only apply to non-linear modes, so they are covered by the mode check.)
 */
export function hasAllocation(a: AllocationDraft): boolean {
  if (a.mode !== "linear") return true;
  if (a.cores.trim()) return true; // an invalid leftover, kept so validation can name it
  if (a.by === "groups") return a.partitions.some((p) => p.name.trim());
  return !!a.split.trim();
}

/** A non-negative decimal number written out as digits ("4", "2.5"). */
export const isNumeric = (s: string) => /^[0-9]+(\.[0-9]+)?$/.test(s.trim());
