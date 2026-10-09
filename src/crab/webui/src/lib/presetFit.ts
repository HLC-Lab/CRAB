// What a chosen preset cannot run in a config. Mirrors `_check_preset_fit` in
// src/crab/core/config_checks.py (the cluster's pre-submit check is authoritative); a `{token}`
// value never matches a check, as in the engine. Pure: no fetch, no DOM.
import type { CrabConfig, CrabInfo } from "@/api/types";

export type PresetInfo = CrabInfo["presets"][number];
export interface PresetNote {
  level: "error" | "warn";
  text: string;
}

/** An integer option, or null when it is missing or a `{var}` token (engine `_count`). */
function count(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? Math.trunc(value) : null;
  if (typeof value === "string" && /^\s*[+-]?\d+\s*$/.test(value)) return parseInt(value, 10);
  return null;
}

function isEmpty(value: unknown): boolean {
  if (Array.isArray(value)) return value.length === 0;
  return !value;
}

export function presetNotes(config: CrabConfig, preset: PresetInfo): PresetNote[] {
  // An old cluster (no scheduler field) or a preset that failed to load: nothing to compare.
  if (preset.error || !preset.scheduler) return [];
  const local = preset.scheduler === "local";
  const globalOptions = config.global_options ?? {};
  const numnodes = count(globalOptions.numnodes);
  const ppn = count("ppn" in globalOptions ? globalOptions.ppn : 1);
  const notes: PresetNote[] = [];

  for (const [name, exp] of Object.entries(config.experiments ?? {})) {
    const opts = { ...globalOptions, ...(exp.local_options ?? {}) };
    const launcher = "launcher" in opts ? opts.launcher : preset.launcher;
    const problems: string[] = [];
    if (launcher === "srun" && local) problems.push("the srun launcher needs a Slurm preset");
    if (launcher === "direct" && ((numnodes ?? 1) > 1 || (ppn ?? 1) > 1))
      problems.push(
        "the direct launcher runs a single process; use ppn 1 and numnodes 1, " +
          "or the mpirun launcher",
      );
    if (local && preset.hosts && numnodes !== null && numnodes > preset.hosts.length)
      problems.push(
        `numnodes ${numnodes} is more than the preset's ${preset.hosts.length} host(s)`,
      );
    if (problems.length)
      notes.push({ level: "error", text: `experiment ${name}: ${problems.join("; ")}` });
  }

  if (local && !isEmpty(globalOptions.sbatch_directives))
    notes.push({ level: "warn", text: "sbatch_directives are ignored by the local scheduler" });
  return notes;
}
