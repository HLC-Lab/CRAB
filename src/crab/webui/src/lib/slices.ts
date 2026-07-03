// Shared allocation-slice presentation logic: palette, name-fallback, and
// even-share math. AllocationEditor.vue (the editor), AuthorView.vue (the
// placement summary, per-app slice picker, flow diagram), and config.ts (the
// flow-diagram color badge) all need to agree on these, since previously each
// had its own copy, which could silently drift.

export const SLICE_COLORS = ["#6ea8fe", "#ff8c78", "#7ec699", "#b69cff", "#e0b352", "#56c2c2"];
const DEFAULT_SLICE_NAMES = ["victim", "aggressor"];

export function sliceColor(i: number): string {
  return SLICE_COLORS[i % SLICE_COLORS.length];
}

/** The positional fallback name for a slice that has no name yet. */
export function slicePlaceholder(i: number): string {
  return DEFAULT_SLICE_NAMES[i] ?? `group ${i + 1}`;
}

/** A slice's display name: its own name if set, else the positional fallback. */
export function sliceName(name: string, i: number): string {
  return name.trim() || slicePlaceholder(i);
}

/** N shares that sum to exactly 100, as equal as integer rounding allows. */
export function equalShares(n: number): number[] {
  const base = Math.floor(100 / n);
  const arr = Array(n).fill(base);
  const rem = 100 - base * n;
  for (let i = 0; i < rem; i++) arr[i]++;
  return arr;
}
