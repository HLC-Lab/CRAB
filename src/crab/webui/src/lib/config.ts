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

// -- Allocation (node-to-app mapping) ----------------------------------------
// The `allocation` object lives in global_options (and, later, per-experiment
// local_options). Two alternative strategies: by app (a positional `split`
// percentage array) or by named node groups (`partitions`, e.g. victim /
// aggressor, each with a `share`). Numeric fields here are emitted as NUMBERS
// (split[], share, stride, seed) — unlike numnodes/ppn which stay strings.

export type AllocMode = "linear" | "interleaved" | "random";
export type AllocBy = "app" | "groups";

export interface PartitionDraft {
  name: string;
  share: string; // "" = unset (equal split); emitted as a number when set
  rest: Record<string, unknown>; // inner mode/split etc., preserved untouched
}

export interface AllocationDraft {
  enabled: boolean; // when false, no `allocation` key is emitted (engine default)
  mode: AllocMode;
  by: AllocBy;
  split: string; // "60, 40" → [60, 40]; only meaningful when by === "app"
  stride: string; // interleaved only
  seed: string; // random only
  partitions: PartitionDraft[]; // only meaningful when by === "groups"
}

export function emptyAllocation(): AllocationDraft {
  return { enabled: false, mode: "linear", by: "app", split: "", stride: "", seed: "", partitions: [] };
}

export function emptyPartition(name = ""): PartitionDraft {
  return { name, share: "", rest: {} };
}

const RESERVED_PARTITION_KEYS = new Set(["share"]);

/** Build the `allocation` object, or undefined when disabled. */
export function toAllocation(a: AllocationDraft): Record<string, unknown> | undefined {
  if (!a.enabled) return undefined;
  const out: Record<string, unknown> = { mode: a.mode };
  if (a.mode === "interleaved" && a.stride.trim()) out.stride = Number(a.stride.trim());
  if (a.mode === "random" && a.seed.trim()) out.seed = Number(a.seed.trim());

  if (a.by === "groups") {
    const partitions: Record<string, unknown> = {};
    for (const p of a.partitions) {
      const key = p.name.trim();
      if (!key) continue;
      const entry: Record<string, unknown> = { ...p.rest };
      if (p.share.trim()) entry.share = Number(p.share.trim());
      partitions[key] = entry;
    }
    if (Object.keys(partitions).length) out.partitions = partitions;
  } else if (a.split.trim()) {
    out.split = a.split
      .split(",")
      .map((s) => Number(s.trim()))
      .filter((n) => !Number.isNaN(n));
  }
  return out;
}

/** Inverse: read an `allocation` object into a draft. */
export function fromAllocation(alloc: unknown): AllocationDraft {
  const a = emptyAllocation();
  if (!alloc || typeof alloc !== "object") return a;
  const o = alloc as Record<string, unknown>;
  a.enabled = true;
  const mode = String(o.mode ?? "linear");
  a.mode = mode === "interleaved" || mode === "random" ? mode : "linear";
  if (o.stride != null) a.stride = String(o.stride);
  if (o.seed != null) a.seed = String(o.seed);

  if (o.partitions && typeof o.partitions === "object") {
    a.by = "groups";
    a.partitions = Object.entries(o.partitions as Record<string, unknown>).map(([name, raw]) => {
      const p = (raw ?? {}) as Record<string, unknown>;
      const rest: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(p)) {
        if (!RESERVED_PARTITION_KEYS.has(k)) rest[k] = v;
      }
      return { name, share: p.share != null ? String(p.share) : "", rest };
    });
  } else if (Array.isArray(o.split)) {
    a.by = "app";
    a.split = (o.split as unknown[]).map((n) => String(n)).join(", ");
  }
  return a;
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
  allocation: AllocationDraft;
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
  return { name: "", numnodes: "", ppn: "1", allocation: emptyAllocation(), experiments: [] };
}

/** Build the engine config from the draft, pruning empty optionals. */
export function toConfig(draft: Draft): CrabConfig {
  const global: Record<string, unknown> = {};
  if (draft.name.trim()) global.name = draft.name.trim();
  if (draft.numnodes.trim()) global.numnodes = draft.numnodes.trim();
  if (draft.ppn.trim()) global.ppn = draft.ppn.trim();
  const allocation = toAllocation(draft.allocation);
  if (allocation) global.allocation = allocation;

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
  draft.allocation = fromAllocation(g.allocation);
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

  // Allocation (global). Returns the defined node-group names for the per-app check.
  const groups = new Set<string>();
  const alloc = d.allocation;
  if (alloc.enabled) {
    if (alloc.mode === "interleaved" && alloc.stride.trim() && !posInt(alloc.stride))
      issues.push("Allocation stride must be a positive integer.");
    if (alloc.mode === "random" && alloc.seed.trim() && !/^[0-9]+$/.test(alloc.seed.trim()))
      issues.push("Allocation seed must be an integer.");
    if (alloc.by === "app" && alloc.split.trim()) {
      const parts = alloc.split.split(",").map((s) => s.trim());
      if (!parts.every((s) => numeric(s)))
        issues.push("Allocation split must be a comma-separated list of numbers.");
    }
    if (alloc.by === "groups") {
      const named = alloc.partitions.filter((p) => p.name.trim());
      if (!named.length) issues.push("Add at least one node group, or allocate by app instead.");
      named.forEach((p) => {
        if (groups.has(p.name.trim())) issues.push(`Duplicate node group "${p.name.trim()}".`);
        else groups.add(p.name.trim());
        if (p.share.trim() && !numeric(p.share)) issues.push(`Node group "${p.name.trim()}": share must be a number.`);
      });
      const shared = named.filter((p) => p.share.trim());
      if (shared.length && shared.length !== named.length)
        issues.push("Set a share on every node group, or on none (for an equal split).");
      else if (shared.length && shared.every((p) => numeric(p.share))) {
        const sum = shared.reduce((t, p) => t + Number(p.share), 0);
        if (Math.abs(sum - 100) > 0.01) issues.push(`Node-group shares should sum to 100 (currently ${sum}).`);
      }
    }
  }

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
      if (groups.size && a.partition.trim() && !groups.has(a.partition.trim()))
        issues.push(`${label} · app #${ai}: partition "${a.partition.trim()}" is not a defined node group.`);
    });
  });
  return issues;
}

// -- Flow diagram ------------------------------------------------------------
// An abstract (not-to-scale) view of an experiment's apps: columns are
// sequential stages (an "after app N" app sits one stage right of N), apps in
// the same column run in parallel, and colour shows victim (measured) vs
// aggressor (the `collect` flag, #1). Duration is intentionally not depicted.

export interface FlowNode {
  index: number;
  name: string;
  role: "victim" | "aggressor";
  endKind: EndKind;
  note: string; // small qualifier, e.g. "+5s"
}

function _base(path: string): string {
  const p = path.trim().replace(/\/+$/, "");
  return p ? p.split("/").pop()!.replace(/\.py$/, "") : "";
}

/** Apps grouped into sequential stages (each stage = one column of parallel apps). */
export function flowLayout(apps: AppDraft[]): FlowNode[][] {
  const level: number[] = [];
  apps.forEach((a, i) => {
    if (a.startKind === "after") {
      const ref = parseInt(a.startAfter, 10);
      const refLevel = Number.isInteger(ref) && ref >= 0 && ref < i ? (level[ref] ?? 0) : 0;
      level[i] = refLevel + 1;
    } else {
      level[i] = 0;
    }
  });

  const columns: FlowNode[][] = [];
  apps.forEach((a, i) => {
    const node: FlowNode = {
      index: i,
      name: _base(a.path) || `app ${i}`,
      role: a.collect ? "victim" : "aggressor",
      endKind: a.endKind,
      note: a.startKind === "delay" ? `+${a.startDelay || 0}s` : "",
    };
    (columns[level[i]] ??= []).push(node);
  });
  return columns.filter(Boolean);
}
