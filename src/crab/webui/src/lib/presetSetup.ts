// What the Remotes card says about a cluster's presets: which ones still need a value, and why
// the list may be empty. Reads the optional keys of `crab info --json`; an older cluster omits
// them, so every helper falls back to "nothing to say". Pure: no fetch, no DOM.
import type { CrabInfo } from "@/api/types";

export interface PresetsHint {
  kind: "warn" | "err";
  text: string;
}

/** "needs setup: --account" for a preset with unset values, null when it is ready to use. */
export function needsLabel(preset: { needs?: string[] }): string | null {
  const needs = preset.needs ?? [];
  return needs.length ? `needs setup: ${needs.join(", ")}` : null;
}

/** A loader error, or the no-shared-presets hint; null when there is nothing to flag. */
export function presetsHint(info: CrabInfo): PresetsHint | null {
  if (info.presets_error) return { kind: "err", text: info.presets_error };
  const sources = info.presets_sources;
  if (sources && !sources.some((s) => s.exists)) {
    return {
      kind: "warn",
      text: "No shared presets on this cluster: run `crab update` there to fetch crab-wrappers.",
    };
  }
  return null;
}
