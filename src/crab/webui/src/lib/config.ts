// Pure mapping between the editor's draft model and the engine-shaped config
// JSON ({global_options, experiments}). Kept free of Vue/IO so it can be unit
// tested (the Phase 3 round-trip check). See .crab-web-dev/07-phase3-authoring.md.
//
// Value encoding mirrors the hand-written examples: numeric *options* stay
// strings, collect is boolean. (More fields land in later increments.)

import type { AppConfig, CrabConfig } from "@/api/types";

export interface AppDraft {
  path: string;
  args: string;
  collect: boolean;
  start: string;
  end: string;
  partition: string;
}

export interface ExperimentDraft {
  name: string;
  description: string;
  apps: AppDraft[];
}

export interface Draft {
  name: string;
  numnodes: string;
  ppn: string;
  experiments: ExperimentDraft[];
}

export function emptyApp(): AppDraft {
  return { path: "", args: "", collect: true, start: "0", end: "", partition: "" };
}

export function emptyDraft(): Draft {
  return { name: "", numnodes: "", ppn: "1", experiments: [] };
}

/** Build the engine config from the draft, pruning empty optionals. */
export function toConfig(draft: Draft): CrabConfig {
  const global: Record<string, unknown> = {};
  if (draft.name.trim()) global.name = draft.name.trim();
  if (draft.numnodes.trim()) global.numnodes = draft.numnodes.trim();
  if (draft.ppn.trim()) global.ppn = draft.ppn.trim();

  const experiments: CrabConfig["experiments"] = {};
  for (const exp of draft.experiments) {
    const key = exp.name.trim() || "experiment";
    const apps: Record<string, AppConfig> = {};
    exp.apps.forEach((a, i) => {
      const entry: AppConfig = {
        path: a.path.trim(),
        args: a.args,
        collect: a.collect,
        start: a.start,
        end: a.end,
      };
      if (a.partition.trim()) entry.partition = a.partition.trim();
      apps[String(i)] = entry;
    });
    experiments[key] = exp.description.trim()
      ? { description: exp.description.trim(), apps }
      : { apps };
  }
  return { global_options: global, experiments };
}

/** Inverse: load an engine config (or legacy `applications` form) into a draft. */
export function fromConfig(config: CrabConfig): Draft {
  const g = (config?.global_options ?? {}) as Record<string, unknown>;
  const str = (v: unknown, fallback = "") => (v == null ? fallback : String(v));

  // Legacy single-experiment form: a top-level `applications` block.
  let experiments = config?.experiments;
  const legacy = (config as unknown as { applications?: unknown })?.applications;
  if (!experiments && legacy) {
    experiments = { default_ex: { apps: legacy as never } };
  }

  const draft = emptyDraft();
  draft.name = str(g.name);
  draft.numnodes = str(g.numnodes);
  draft.ppn = str(g.ppn, "1");
  draft.experiments = Object.entries(experiments ?? {}).map(([name, exp]) => ({
    name,
    description: str((exp as { description?: unknown }).description),
    apps: Object.values((exp as { apps?: Record<string, never> }).apps ?? {}).map((a) => {
      const app = a as Record<string, unknown>;
      return {
        path: str(app.path),
        args: str(app.args),
        collect: app.collect !== false,
        start: str(app.start, "0"),
        end: str(app.end),
        partition: str(app.partition),
      };
    }),
  }));
  return draft;
}
