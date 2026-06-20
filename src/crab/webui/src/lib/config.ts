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
  // Non-reserved app keys are injected as wrapper attributes by the engine
  // (documented). Preserve them untouched so load→save doesn't drop them.
  rest: Record<string, unknown>;
}

const RESERVED_APP_KEYS = new Set(["path", "args", "collect", "start", "end", "partition"]);

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
  mode: AllocMode;
  by: AllocBy;
  split: string; // "60, 40" → [60, 40]; only meaningful when by === "app"
  stride: string; // interleaved only
  seed: string; // random only
  partitions: PartitionDraft[]; // only meaningful when by === "groups"
}

export function emptyAllocation(): AllocationDraft {
  return { mode: "linear", by: "app", split: "", stride: "", seed: "", partitions: [] };
}

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
  if (a.by === "groups") return a.partitions.some((p) => p.name.trim());
  return !!a.split.trim();
}

export function emptyPartition(name = ""): PartitionDraft {
  return { name, share: "", rest: {} };
}

const RESERVED_PARTITION_KEYS = new Set(["share"]);

/**
 * Build the `allocation` object, or undefined when there is no content. Pass
 * `force` for a per-experiment override, where even a bare `{mode:"linear"}` is
 * meaningful (it replaces — rather than inherits — the global allocation).
 */
export function toAllocation(a: AllocationDraft, force = false): Record<string, unknown> | undefined {
  if (!force && !hasAllocation(a)) return undefined;
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
  // Per-experiment overrides (local_options). `overrideAlloc` gates the allocation:
  // when off, the experiment inherits the global allocation; when on, the editor's
  // allocation is force-emitted (even a bare {mode:"linear"}) so it replaces the global.
  overrideAlloc: boolean;
  allocation: AllocationDraft;
  options: OptionsDraft;
  apps: AppDraft[];
}

export function emptyExperiment(name = ""): ExperimentDraft {
  return { name, description: "", overrideAlloc: false, allocation: emptyAllocation(), options: emptyOptions(), apps: [] };
}

// -- Tunable options (convergence / output / advanced) -----------------------
// These global_options keys are also overridable per-experiment via local_options
// (a later increment), so the model is shared. Emission is emit-on-set: a blank
// field is omitted (engine default globally / inherit-from-global locally) — there
// is no full default written out. Booleans/enums use an empty string as "unset"
// (a tri-state) so we can distinguish "leave default" from an explicit value.

export type TriBool = "" | "true" | "false";

export interface OptionsDraft {
  minruns: string;
  maxruns: string;
  timeout: string;
  convergeall: TriBool;
  alpha: string;
  beta: string;
  outformat: "" | "csv" | "hdf";
  retainFiles: TriBool;
  tags: string;
  extrainfo: string;
  walltime: string;
  datapath: string;
}

export function emptyOptions(): OptionsDraft {
  return {
    minruns: "", maxruns: "", timeout: "", convergeall: "", alpha: "", beta: "",
    outformat: "", retainFiles: "", tags: "", extrainfo: "", walltime: "", datapath: "",
  };
}

/** Spread the set option fields onto a target options object (global or local). */
export function applyOptions(target: Record<string, unknown>, o: OptionsDraft): void {
  const s = (k: string, v: string) => {
    if (v.trim()) target[k] = v.trim();
  };
  // Numeric options are kept as strings, mirroring the hand-written examples.
  s("minruns", o.minruns);
  s("maxruns", o.maxruns);
  s("timeout", o.timeout);
  s("alpha", o.alpha);
  s("beta", o.beta);
  s("tags", o.tags);
  s("extrainfo", o.extrainfo);
  s("walltime", o.walltime);
  s("datapath", o.datapath);
  if (o.outformat) target.outformat = o.outformat;
  if (o.convergeall) target.convergeall = o.convergeall === "true";
  if (o.retainFiles) target.retain_files = o.retainFiles === "true";
}

/** Inverse: read the tunable option fields out of a global/local options object. */
export function readOptions(src: Record<string, unknown>): OptionsDraft {
  const o = emptyOptions();
  const str = (v: unknown) => (v == null ? "" : String(v));
  const tri = (v: unknown): TriBool => (v == null ? "" : v ? "true" : "false");
  o.minruns = str(src.minruns);
  o.maxruns = str(src.maxruns);
  o.timeout = str(src.timeout);
  o.alpha = str(src.alpha);
  o.beta = str(src.beta);
  o.tags = str(src.tags);
  o.extrainfo = str(src.extrainfo);
  o.walltime = str(src.walltime);
  o.datapath = str(src.datapath);
  const fmt = str(src.outformat);
  o.outformat = fmt === "csv" || fmt === "hdf" ? fmt : "";
  o.convergeall = tri(src.convergeall);
  o.retainFiles = tri(src.retain_files);
  return o;
}

// -- sbatch directives -------------------------------------------------------
// Slurm `#SBATCH` lines. Two accepted shapes: a list of full directive strings
// (doc-preferred) or a legacy dict ({key: value|true}). All real examples use the
// dict form, so we preserve whichever was loaded. Internally we always hold full
// directive strings ("--time=00:20:00", "--exclusive") and re-emit in `form`.

export type SbatchForm = "list" | "dict";

export interface SbatchDraft {
  form: SbatchForm;
  lines: string[];
}

export function emptySbatch(): SbatchDraft {
  return { form: "list", lines: [] };
}

/** "--key=value" → ["key","value"]; "--key" → ["key", true] (bare flag). */
function parseDirective(line: string): [string, string | true] | null {
  const t = line.trim().replace(/^--/, "");
  if (!t) return null;
  const eq = t.indexOf("=");
  return eq === -1 ? [t, true] : [t.slice(0, eq), t.slice(eq + 1)];
}

export function toSbatch(s: SbatchDraft): string[] | Record<string, string | boolean> | undefined {
  const lines = s.lines.map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return undefined;
  if (s.form === "list") return lines.map((l) => (l.startsWith("--") ? l : `--${l}`));
  const dict: Record<string, string | boolean> = {};
  for (const line of lines) {
    const parsed = parseDirective(line);
    if (parsed) dict[parsed[0]] = parsed[1];
  }
  return dict;
}

export function fromSbatch(v: unknown): SbatchDraft {
  if (Array.isArray(v)) {
    return { form: "list", lines: v.map((x) => String(x)) };
  }
  if (v && typeof v === "object") {
    const lines: string[] = [];
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
      if (val === false) continue; // dict false ⇒ directive omitted (a no-op)
      lines.push(val === true ? `--${k}` : `--${k}=${val}`);
    }
    return { form: "dict", lines };
  }
  return emptySbatch();
}

export interface Draft {
  name: string;
  numnodes: string;
  ppn: string;
  allocation: AllocationDraft;
  options: OptionsDraft;
  sbatch: SbatchDraft;
  experiments: ExperimentDraft[];
}

export function emptyApp(): AppDraft {
  return {
    path: "", args: "", collect: true, partition: "",
    startKind: "at_start", startDelay: "5", startAfter: "0",
    endKind: "complete", endTimed: "60", rest: {},
  };
}

export function emptyDraft(): Draft {
  return { name: "", numnodes: "", ppn: "1", allocation: emptyAllocation(), options: emptyOptions(), sbatch: emptySbatch(), experiments: [] };
}

/** Build the engine config from the draft, pruning empty optionals. */
export function toConfig(draft: Draft): CrabConfig {
  const global: Record<string, unknown> = {};
  if (draft.name.trim()) global.name = draft.name.trim();
  if (draft.numnodes.trim()) global.numnodes = draft.numnodes.trim();
  if (draft.ppn.trim()) global.ppn = draft.ppn.trim();
  const allocation = toAllocation(draft.allocation);
  if (allocation) global.allocation = allocation;
  applyOptions(global, draft.options);
  const sbatch = toSbatch(draft.sbatch);
  if (sbatch) global.sbatch_directives = sbatch;

  const experiments: CrabConfig["experiments"] = {};
  for (const exp of draft.experiments) {
    const key = exp.name.trim() || "experiment";
    const apps: Record<string, AppConfig> = {};
    exp.apps.forEach((a, i) => {
      const entry: AppConfig = {
        ...a.rest, // wrapper-attribute extras; reserved keys below take precedence
        path: a.path.trim(),
        args: a.args,
        collect: a.collect,
        start: startStr(a),
        end: endStr(a),
      };
      if (a.partition.trim()) entry.partition = a.partition.trim();
      apps[String(i)] = entry;
    });

    // local_options overrides: allocation (force-emitted when overriding) + scalars.
    const local: Record<string, unknown> = {};
    if (exp.overrideAlloc) {
      const a = toAllocation(exp.allocation, true);
      if (a) local.allocation = a;
    }
    applyOptions(local, exp.options);

    const out: CrabConfig["experiments"][string] = { apps };
    if (exp.description.trim()) out.description = exp.description.trim();
    if (Object.keys(local).length) out.local_options = local;
    experiments[key] = out;
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
  draft.ppn = str(g.ppn); // faithful: no default injection on import (emptyDraft seeds "1" for new configs)
  draft.allocation = fromAllocation(g.allocation);
  draft.options = readOptions(g);
  draft.sbatch = fromSbatch(g.sbatch_directives);
  draft.experiments = Object.entries(experiments ?? {}).map(([name, exp]) => {
    const e = exp as { description?: unknown; local_options?: unknown; apps?: Record<string, never> };
    const lo = (e.local_options ?? {}) as Record<string, unknown>;
    return {
      name,
      description: str(e.description),
      overrideAlloc: lo.allocation != null,
      allocation: fromAllocation(lo.allocation),
      options: readOptions(lo),
      apps: Object.values(e.apps ?? {}).map((a) => {
        const app = a as Record<string, unknown>;
        const rest: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(app)) {
          if (!RESERVED_APP_KEYS.has(k)) rest[k] = v;
        }
        return {
          path: str(app.path),
          args: str(app.args),
          collect: app.collect === true, // doc default is false
          partition: str(app.partition),
          rest,
          ...parseStart(str(app.start, "0")),
          ...parseEnd(str(app.end)),
        };
      }),
    };
  });
  return draft;
}

// -- Shape validation --------------------------------------------------------
// Structural checks only (not engine semantics): the things that make a config
// well-formed enough to submit. Returns a list of human-readable issues.

const _posInt = (s: string) => /^[0-9]+$/.test(s.trim()) && parseInt(s, 10) > 0;
const _numeric = (s: string) => /^[0-9]+(\.[0-9]+)?$/.test(s.trim());

/** Validate the tunable option fields. `where` prefixes messages (e.g. an experiment name). */
export function validateOptions(o: OptionsDraft, where = ""): string[] {
  const issues: string[] = [];
  const at = where ? `${where}: ` : "";
  if (o.minruns.trim() && !_posInt(o.minruns)) issues.push(`${at}min runs must be a positive integer.`);
  if (o.maxruns.trim() && !_posInt(o.maxruns)) issues.push(`${at}max runs must be a positive integer.`);
  if (o.minruns.trim() && o.maxruns.trim() && _posInt(o.minruns) && _posInt(o.maxruns) &&
      parseInt(o.maxruns, 10) < parseInt(o.minruns, 10))
    issues.push(`${at}max runs must be ≥ min runs.`);
  if (o.timeout.trim() && !_numeric(o.timeout)) issues.push(`${at}timeout must be a number (seconds).`);
  if (o.alpha.trim() && !_numeric(o.alpha)) issues.push(`${at}alpha must be a number.`);
  if (o.beta.trim() && !_numeric(o.beta)) issues.push(`${at}beta must be a number.`);
  return issues;
}

/** Validate an allocation and return its defined node-group names (for per-app checks). */
export function validateAllocation(a: AllocationDraft, where = ""): { issues: string[]; groups: Set<string> } {
  const issues: string[] = [];
  const groups = new Set<string>();
  const at = where ? `${where}: ` : "";
  if (!hasAllocation(a)) return { issues, groups };
  if (a.mode === "interleaved" && a.stride.trim() && !_posInt(a.stride))
    issues.push(`${at}allocation stride must be a positive integer.`);
  if (a.mode === "random" && a.seed.trim() && !/^[0-9]+$/.test(a.seed.trim()))
    issues.push(`${at}allocation seed must be an integer.`);
  if (a.by === "app" && a.split.trim()) {
    const parts = a.split.split(",").map((s) => s.trim());
    if (!parts.every((s) => _numeric(s)))
      issues.push(`${at}allocation split must be a comma-separated list of numbers.`);
  }
  if (a.by === "groups") {
    const named = a.partitions.filter((p) => p.name.trim());
    if (!named.length) issues.push(`${at}add at least one node group, or allocate by app instead.`);
    named.forEach((p) => {
      if (groups.has(p.name.trim())) issues.push(`${at}duplicate node group "${p.name.trim()}".`);
      else groups.add(p.name.trim());
      if (p.share.trim() && !_numeric(p.share)) issues.push(`${at}node group "${p.name.trim()}": share must be a number.`);
    });
    const shared = named.filter((p) => p.share.trim());
    if (shared.length && shared.length !== named.length)
      issues.push(`${at}set a share on every node group, or on none (for an equal split).`);
    else if (shared.length && shared.every((p) => _numeric(p.share))) {
      const sum = shared.reduce((t, p) => t + Number(p.share), 0);
      if (Math.abs(sum - 100) > 0.01) issues.push(`${at}node-group shares should sum to 100 (currently ${sum}).`);
    }
  }
  return { issues, groups };
}

// CRAB computes these from numnodes/ppn and ignores user attempts to set them.
const PROTECTED_SBATCH = new Set(["nodes", "ntasks-per-node", "N", "ntasks"]);

export function validateSbatch(s: SbatchDraft): string[] {
  const issues: string[] = [];
  for (const line of s.lines.map((l) => l.trim()).filter(Boolean)) {
    const parsed = parseDirective(line);
    if (!parsed) continue;
    const key = parsed[0];
    if (PROTECTED_SBATCH.has(key))
      issues.push(`Slurm directive "--${key}" is computed by CRAB from nodes/ppn and will be ignored.`);
    else if (key === "output" || key === "error")
      issues.push(`Slurm directive "--${key}" overrides CRAB's log redirection (allowed, but be aware).`);
  }
  return issues;
}

export function validateDraft(d: Draft): string[] {
  const issues: string[] = [];
  const numeric = _numeric;

  if (!d.numnodes.trim()) issues.push("Number of nodes is required.");
  else if (!_posInt(d.numnodes)) issues.push("Number of nodes must be a positive integer.");
  if (d.ppn.trim() && !_posInt(d.ppn)) issues.push("Procs per node must be a positive integer.");
  if (!d.experiments.length) issues.push("Add at least one experiment.");
  issues.push(...validateOptions(d.options));
  issues.push(...validateSbatch(d.sbatch));

  const global = validateAllocation(d.allocation);
  issues.push(...global.issues);

  const names = new Set<string>();
  d.experiments.forEach((e, ei) => {
    const nm = e.name.trim();
    const label = nm || `#${ei}`;
    if (!nm) issues.push(`Experiment ${label} needs a name.`);
    else if (names.has(nm)) issues.push(`Duplicate experiment name "${nm}".`);
    else names.add(nm);

    // Per-experiment overrides. The merged allocation REPLACES the global one, so
    // the node groups an app may reference are the local ones when overridden.
    issues.push(...validateOptions(e.options, `Experiment "${label}"`));
    // When overriding, the local allocation REPLACES the global one entirely
    // (force-emitted, even bare linear ⇒ no groups), so use its groups, not the global's.
    let groups = global.groups;
    if (e.overrideAlloc) {
      const local = validateAllocation(e.allocation, `Experiment "${label}"`);
      issues.push(...local.issues);
      groups = local.groups;
    }

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
      // A partition reference is invalid whenever it names no defined node group —
      // including when none exist (e.g. after switching to "by app" or disabling allocation).
      if (a.partition.trim() && !groups.has(a.partition.trim()))
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
  group?: string; // node-group name, when the effective allocation is partitioned
  nodes?: number; // approximate node count for this app (sizing aid, not engine placement)
}

function _wrapperName(path: string): string {
  // Full relative path (so suite/benchmark is visible), minus the .py extension.
  return path.trim().replace(/\.py$/, "");
}

/**
 * Approximate per-app node allocation, for the flow-diagram badges. Returns a
 * value only when allocation is actually configured (named groups, or a split):
 * we never guess the engine's default placement. `share% × numnodes` for groups
 * (equal split when shares are unset), `split% × numnodes` for by-app. Indexed
 * by app position. Best-effort sizing aid — NOT authoritative placement.
 */
export function allocationSummary(
  apps: AppDraft[],
  alloc: AllocationDraft,
  numnodes: string,
): { group?: string; nodes?: number }[] {
  const out = apps.map(() => ({}) as { group?: string; nodes?: number });
  if (!apps.length || !hasAllocation(alloc)) return out;
  const n = parseInt(numnodes.trim(), 10);
  const total = Number.isFinite(n) && n > 0 ? n : 0;

  if (alloc.by === "groups") {
    const named = alloc.partitions.filter((p) => p.name.trim());
    const allShared = named.length > 0 && named.every((p) => p.share.trim() && _numeric(p.share));
    const groupNodes: Record<string, number> = {};
    if (total) {
      for (const p of named) {
        groupNodes[p.name.trim()] = allShared
          ? Math.round((Number(p.share) / 100) * total)
          : Math.floor(total / named.length);
      }
    }
    apps.forEach((a, i) => {
      const g = a.partition.trim();
      out[i] = { group: g || undefined, nodes: g && g in groupNodes ? groupNodes[g] : undefined };
    });
  } else if (alloc.split.trim() && total) {
    const split = alloc.split.split(",").map((s) => Number(s.trim()));
    if (split.length === apps.length && split.every((x) => !Number.isNaN(x))) {
      apps.forEach((_, i) => (out[i] = { nodes: Math.round((split[i] / 100) * total) }));
    }
  }
  return out;
}

/**
 * Apps grouped into sequential stages (each stage = one column of parallel apps).
 * Pass the effective allocation + numnodes to annotate nodes with allocation badges.
 */
export function flowLayout(apps: AppDraft[], alloc?: AllocationDraft, numnodes = ""): FlowNode[][] {
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

  const badges = alloc ? allocationSummary(apps, alloc, numnodes) : [];
  const columns: FlowNode[][] = [];
  apps.forEach((a, i) => {
    const node: FlowNode = {
      index: i,
      name: _wrapperName(a.path) || `app ${i}`,
      role: a.collect ? "victim" : "aggressor",
      endKind: a.endKind,
      note: a.startKind === "delay" ? `+${a.startDelay || 0}s` : "",
      group: badges[i]?.group,
      nodes: badges[i]?.nodes,
    };
    (columns[level[i]] ??= []).push(node);
  });
  return columns.filter(Boolean);
}
