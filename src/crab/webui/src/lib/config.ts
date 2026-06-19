// Pure mapping between the editor's draft model and the engine-shaped config
// JSON ({global_options, experiments}). Kept free of Vue/IO so it can be unit
// tested (the Phase 3 round-trip check). See .crab-web-dev/07-phase3-authoring.md.
//
// Value encoding mirrors the hand-written examples: numeric *options* stay
// strings, collect is boolean. (More fields land in later increments.)

import type { AppConfig, CrabConfig } from "@/api/types";

// start: when the app launches. end: when it stops (the victim/aggressor axis).
export type StartKind = "at_start" | "delay" | "after";
export type EndKind = "complete" | "force" | "timed";

export interface AppDraft {
  path: string;
  args: string;
  collect: boolean;
  partition: string;
  startKind: StartKind;
  startDelay: string; // seconds, when startKind === "delay"
  startAfter: string; // app index N, when startKind === "after" → "sN"
  endKind: EndKind;
  endTimed: string; // seconds, when endKind === "timed"
}

function startStr(a: AppDraft): string {
  if (a.startKind === "delay") return a.startDelay.trim() || "0";
  if (a.startKind === "after") return "s" + (a.startAfter.trim() || "0");
  return "0";
}

function endStr(a: AppDraft): string {
  if (a.endKind === "force") return "f";
  if (a.endKind === "timed") return a.endTimed.trim() || "0";
  return "";
}

function parseStart(s: string): Pick<AppDraft, "startKind" | "startDelay" | "startAfter"> {
  if (s.startsWith("s")) return { startKind: "after", startDelay: "5", startAfter: s.slice(1) || "0" };
  if (s === "" || s === "0") return { startKind: "at_start", startDelay: "5", startAfter: "0" };
  return { startKind: "delay", startDelay: s, startAfter: "0" };
}

function parseEnd(s: string): Pick<AppDraft, "endKind" | "endTimed"> {
  if (s === "f") return { endKind: "force", endTimed: "60" };
  if (s === "") return { endKind: "complete", endTimed: "60" };
  return { endKind: "timed", endTimed: s };
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
  return {
    path: "", args: "", collect: true, partition: "",
    startKind: "at_start", startDelay: "5", startAfter: "0",
    endKind: "complete", endTimed: "60",
  };
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
        start: startStr(a),
        end: endStr(a),
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
        partition: str(app.partition),
        ...parseStart(str(app.start, "0")),
        ...parseEnd(str(app.end)),
      };
    }),
  }));
  return draft;
}

// -- Shape validation --------------------------------------------------------
// Structural checks only (not engine semantics): the things that make a config
// well-formed enough to submit. Returns a list of human-readable issues.

export function validateDraft(d: Draft): string[] {
  const issues: string[] = [];
  const posInt = (s: string) => /^[0-9]+$/.test(s.trim()) && parseInt(s, 10) > 0;
  const numeric = (s: string) => /^[0-9]+(\.[0-9]+)?$/.test(s.trim());

  if (!d.numnodes.trim()) issues.push("Number of nodes is required.");
  else if (!posInt(d.numnodes)) issues.push("Number of nodes must be a positive integer.");
  if (d.ppn.trim() && !posInt(d.ppn)) issues.push("Procs per node must be a positive integer.");
  if (!d.experiments.length) issues.push("Add at least one experiment.");

  const names = new Set<string>();
  d.experiments.forEach((e, ei) => {
    const nm = e.name.trim();
    const label = nm || `#${ei}`;
    if (!nm) issues.push(`Experiment ${label} needs a name.`);
    else if (names.has(nm)) issues.push(`Duplicate experiment name "${nm}".`);
    else names.add(nm);

    if (!e.apps.length) issues.push(`Experiment "${label}" has no apps.`);
    e.apps.forEach((a, ai) => {
      if (!a.path.trim()) issues.push(`${label} · app #${ai}: wrapper path is required.`);
      if (a.startKind === "delay" && !numeric(a.startDelay))
        issues.push(`${label} · app #${ai}: start delay must be a number.`);
      if (a.endKind === "timed" && !numeric(a.endTimed))
        issues.push(`${label} · app #${ai}: timed duration must be a number.`);
      if (a.startKind === "after") {
        const ref = parseInt(a.startAfter, 10);
        if (!(ref >= 0 && ref < e.apps.length && ref !== ai))
          issues.push(`${label} · app #${ai}: "after" must reference another app.`);
      }
    });
  });
  return issues;
}

// -- Timeline diagram --------------------------------------------------------
// A schematic (not-to-scale) layout of an experiment's apps on a shared time
// axis, so the editor can show parallel vs staggered vs sequential launches and
// victim/aggressor/timed ends at a glance.

export interface TimelineBar {
  name: string;
  leftPct: number;
  widthPct: number;
  kind: EndKind;
  openEnded: boolean; // a victim that runs to completion (unknown duration)
  startNote: string;
  endNote: string;
}

function _num(s: string): number {
  const n = parseFloat(s);
  return Number.isFinite(n) ? n : 0;
}

function _clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function _base(path: string): string {
  const p = path.trim().replace(/\/+$/, "");
  return p ? p.split("/").pop()!.replace(/\.py$/, "") : "";
}

export function timelineLayout(apps: AppDraft[]): TimelineBar[] {
  const BASE = 8;
  const GAP = 1;
  const SCALE = 0.5; // schematic units per second
  const starts: number[] = [];
  const ends: number[] = [];

  apps.forEach((a, i) => {
    let start = 0;
    if (a.startKind === "delay") start = _clamp(_num(a.startDelay) * SCALE, 0, 30);
    else if (a.startKind === "after") {
      const ref = parseInt(a.startAfter, 10);
      const refEnd = Number.isInteger(ref) && ref >= 0 && ref < i ? ends[ref] : 0;
      start = (refEnd ?? 0) + GAP;
    }
    const dur = a.endKind === "timed" ? _clamp(_num(a.endTimed) * SCALE, 2, 40) : BASE;
    starts[i] = start;
    ends[i] = start + dur;
  });

  // Aggressors (force) visually end with the latest victim.
  const victimEnds = apps.map((a, i) => (a.endKind === "complete" ? ends[i] : 0));
  const maxVictim = Math.max(0, ...victimEnds);
  apps.forEach((a, i) => {
    if (a.endKind === "force" && maxVictim > starts[i]) ends[i] = maxVictim;
  });

  const maxEnd = Math.max(10, ...ends);
  return apps.map((a, i) => ({
    name: _base(a.path) || `app ${i}`,
    leftPct: (starts[i] / maxEnd) * 100,
    widthPct: Math.max(4, ((ends[i] - starts[i]) / maxEnd) * 100),
    kind: a.endKind,
    openEnded: a.endKind === "complete",
    startNote:
      a.startKind === "delay"
        ? `+${a.startDelay || 0}s`
        : a.startKind === "after"
          ? `after #${a.startAfter}`
          : "",
    endNote:
      a.endKind === "force" ? "killed" : a.endKind === "timed" ? `${a.endTimed || 0}s` : "runs to end",
  }));
}
