// Pure mapping between the editor's draft model and the engine-shaped config
// JSON ({global_options, experiments}). Kept free of Vue/IO so it can be unit
// tested (tests/unit/config.roundtrip.spec.ts).
//
// Value encoding mirrors the hand-written examples: numeric *options* stay
// strings, collect is boolean. (More fields land in later increments.)

import type { AppConfig, CrabConfig, Experiment } from "@/api/types";
import { PLACEHOLDER, hasAllocation } from "@/lib/configShared";

/** config.json format version; matches CONFIG_SCHEMA_VERSION in core/engine.py. */
export const CONFIG_SCHEMA_VERSION = 2;

// start: when the app launches. end: when it stops (the victim/aggressor axis).
export type StartKind = "at_start" | "delay" | "after";
export type EndKind = "complete" | "force" | "timed";

export interface EnvRow {
  key: string;
  value: string;
}

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
  env: EnvRow[]; // per-app environment variables, emitted as the `env` object
  cpus: string; // core list such as "0-3,8"; empty means no pinning
  ppn: string; // ranks per node for this app; empty means the experiment's or the global value
  // Non-reserved app keys are injected as wrapper attributes by the engine
  // (documented). Preserve them untouched so load→save doesn't drop them.
  rest: Record<string, unknown>;
}

const RESERVED_APP_KEYS = new Set([
  "path",
  "args",
  "collect",
  "start",
  "end",
  "partition",
  "cpus",
  "ppn",
  "env",
]);

/** The rows of an app's `env` object; anything else is not an env the editor can show. */
function readEnv(value: unknown): EnvRow[] {
  if (value == null || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value as Record<string, unknown>).map(([key, v]) => ({
    key,
    value: typeof v === "string" ? v : String(v),
  }));
}

/** The `env` object for the rows with a name, or null when there is none to emit. */
function envObject(rows: EnvRow[]): Record<string, string> | null {
  const out: Record<string, string> = {};
  for (const r of rows) if (r.key.trim()) out[r.key.trim()] = r.value;
  return Object.keys(out).length ? out : null;
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
  if (s.startsWith("s"))
    return { startKind: "after", startDelay: "5", startAfter: s.slice(1) || "0" };
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

export type AllocMode = "linear" | "interleaved" | "random" | "shared";

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
  cores: string; // shared only; "" = key absent (the engine's default is disjoint)
  partitions: PartitionDraft[]; // only meaningful when by === "groups"
}

export function emptyAllocation(): AllocationDraft {
  return { mode: "linear", by: "app", split: "", stride: "", seed: "", cores: "", partitions: [] };
}

/** Deep copy, so mutating the clone never affects the source allocation. */
export function cloneAllocation(a: AllocationDraft): AllocationDraft {
  return {
    mode: a.mode,
    by: a.by,
    split: a.split,
    stride: a.stride,
    seed: a.seed,
    cores: a.cores,
    partitions: a.partitions.map((p) => ({ name: p.name, share: p.share, rest: { ...p.rest } })),
  };
}

/** Deep copy of an experiment, so mutating the clone never affects the source. */
export function cloneExperiment(e: ExperimentDraft): ExperimentDraft {
  return {
    name: e.name,
    description: e.description,
    overrideAlloc: e.overrideAlloc,
    allocation: cloneAllocation(e.allocation),
    options: { ...e.options },
    ppn: e.ppn,
    apps: e.apps.map((a) => ({ ...a, env: a.env.map((r) => ({ ...r })), rest: { ...a.rest } })),
  };
}

export function emptyPartition(name = ""): PartitionDraft {
  return { name, share: "", rest: {} };
}

const RESERVED_PARTITION_KEYS = new Set(["share"]);

// An exact `{name}` token (a SbatchMan sweep variable) is kept as a string so the
// campaign generator can place it; anything else goes through Number() as before.
/**
 * A config boolean as the engine reads it (core/config_checks.py parse_bool): JSON true/false,
 * or the text "true"/"false" in any case, which SbatchMan sweeps produce.
 */
function engineBool(v: unknown): boolean {
  if (typeof v === "string") return v.trim().toLowerCase() === "true";
  return v === true;
}

function numOrToken(raw: string): number | string {
  const s = raw.trim();
  return PLACEHOLDER.test(s) ? s : Number(s);
}

/**
 * Build the `allocation` object, or undefined when there is no content. Pass
 * `force` for a per-experiment override, where even a bare `{mode:"linear"}` is
 * meaningful (it replaces — rather than inherits — the global allocation).
 */
export function toAllocation(
  a: AllocationDraft,
  force = false,
): Record<string, unknown> | undefined {
  if (!force && !hasAllocation(a)) return undefined;
  const out: Record<string, unknown> = { mode: a.mode };
  if (a.mode === "interleaved" && a.stride.trim()) out.stride = numOrToken(a.stride);
  if (a.mode === "random" && a.seed.trim()) out.seed = numOrToken(a.seed);
  if (a.cores.trim()) out.cores = a.cores.trim();

  if (a.by === "groups") {
    const partitions: Record<string, unknown> = {};
    for (const p of a.partitions) {
      const key = p.name.trim();
      if (!key) continue;
      const entry: Record<string, unknown> = { ...p.rest };
      if (p.share.trim()) entry.share = numOrToken(p.share);
      partitions[key] = entry;
    }
    if (Object.keys(partitions).length) out.partitions = partitions;
  } else if (a.split.trim()) {
    out.split = a.split
      .split(",")
      .map(numOrToken)
      .filter((n) => typeof n === "string" || !Number.isNaN(n));
  }
  return out;
}

/** Inverse: read an `allocation` object into a draft. */
export function fromAllocation(alloc: unknown): AllocationDraft {
  const a = emptyAllocation();
  if (!alloc || typeof alloc !== "object") return a;
  const o = alloc as Record<string, unknown>;
  const mode = String(o.mode ?? "linear");
  a.mode = mode === "interleaved" || mode === "random" || mode === "shared" ? mode : "linear";
  if (o.stride != null) a.stride = String(o.stride);
  if (o.seed != null) a.seed = String(o.seed);
  if (o.cores != null) a.cores = String(o.cores);

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
  ppn: string; // local_options.ppn; empty means the global value
  apps: AppDraft[];
}

/** True when the experiment overrides at least one run setting (not the allocation). */
export function hasLocalOverrides(e: ExperimentDraft): boolean {
  return optionsAreSet(e.options) || e.ppn.trim() !== "";
}

export function emptyExperiment(name = ""): ExperimentDraft {
  return {
    name,
    description: "",
    overrideAlloc: false,
    allocation: emptyAllocation(),
    options: emptyOptions(),
    ppn: "",
    apps: [],
  };
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
  outformat: "" | "csv";
  retainFiles: TriBool;
  tags: string;
  extrainfo: string;
  walltime: string;
  datapath: string;
  // Execution launcher (ADR-033). Empty = no key (the preset's launcher applies).
  launcher: string;
  // launcher_flags REPLACES the preset's flags for the launcher kind. Unchecked = key absent,
  // checked with empty text = [] ("no extra flags"), checked with text = the whitespace tokens.
  replaceLauncherFlags: boolean;
  launcherFlags: string;
  // A launcher_flags value the text field cannot hold (not a list of strings, or a flag that
  // contains whitespace). Kept as loaded and re-emitted until the user edits the field.
  launcherFlagsRaw?: unknown;
}

export function emptyOptions(): OptionsDraft {
  return {
    minruns: "",
    maxruns: "",
    timeout: "",
    convergeall: "",
    alpha: "",
    beta: "",
    outformat: "",
    retainFiles: "",
    tags: "",
    extrainfo: "",
    walltime: "",
    datapath: "",
    launcher: "",
    replaceLauncherFlags: false,
    launcherFlags: "",
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
  s("launcher", o.launcher);
  if (o.launcherFlagsRaw !== undefined) target.launcher_flags = o.launcherFlagsRaw;
  else if (o.replaceLauncherFlags)
    target.launcher_flags = o.launcherFlags.split(/\s+/).filter(Boolean);
}

/** True when the options would emit at least one key (same rule as applyOptions). */
export function optionsAreSet(o: OptionsDraft): boolean {
  const out: Record<string, unknown> = {};
  applyOptions(out, o);
  return Object.keys(out).length > 0;
}

/** Inverse: read the tunable option fields out of a global/local options object. */
export function readOptions(src: Record<string, unknown>): OptionsDraft {
  const o = emptyOptions();
  const str = (v: unknown) => (v == null ? "" : String(v));
  const tri = (v: unknown): TriBool => (v == null ? "" : engineBool(v) ? "true" : "false");
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
  o.outformat = fmt === "csv" ? fmt : ""; // the engine accepts csv only
  o.convergeall = tri(src.convergeall);
  o.retainFiles = tri(src.retain_files);
  o.launcher = str(src.launcher);
  if ("launcher_flags" in src) {
    const flags = src.launcher_flags;
    o.replaceLauncherFlags = true;
    if (Array.isArray(flags) && flags.every((f) => typeof f === "string" && /^\S+$/.test(f)))
      o.launcherFlags = flags.join(" ");
    else o.launcherFlagsRaw = flags;
  }
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
  if (s.form === "list") return lines.map((l) => (l.startsWith("-") ? l : `--${l}`));
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
    path: "",
    args: "",
    collect: true,
    partition: "",
    startKind: "at_start",
    startDelay: "5",
    startAfter: "0",
    endKind: "complete",
    endTimed: "60",
    env: [],
    cpus: "",
    ppn: "",
    rest: {},
  };
}

export function emptyDraft(): Draft {
  return {
    name: "",
    numnodes: "",
    ppn: "1",
    allocation: emptyAllocation(),
    options: emptyOptions(),
    sbatch: emptySbatch(),
    experiments: [],
  };
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
      if (a.cpus.trim()) entry.cpus = a.cpus.trim();
      if (a.ppn.trim()) entry.ppn = a.ppn.trim();
      const env = envObject(a.env);
      if (env) entry.env = env;
      apps[String(i)] = entry;
    });

    // local_options overrides: allocation (force-emitted when overriding) + scalars.
    const local: Record<string, unknown> = {};
    if (exp.overrideAlloc) {
      const a = toAllocation(exp.allocation, true);
      if (a) local.allocation = a;
    }
    applyOptions(local, exp.options);
    if (exp.ppn.trim()) local.ppn = exp.ppn.trim();

    const out: CrabConfig["experiments"][string] = { apps };
    if (exp.description.trim()) out.description = exp.description.trim();
    if (Object.keys(local).length) out.local_options = local;
    experiments[key] = out;
  }
  // Always emitted (unlike options): it names the format, so the engine can refuse a newer one.
  return { schema_version: CONFIG_SCHEMA_VERSION, global_options: global, experiments };
}

/**
 * Normalize legacy `split`-form allocations to the `partitions` form the editor
 * uses. For the global allocation and each experiment's `local_options.allocation`
 * that carries an array `split` and no `partitions`, the split becomes named
 * groups `group_1..group_N` (share = the split value, omitted for an even split,
 * i.e. share == 100/N) and the governed apps are tagged `partition` by order
 * (app index i → `group_{i+1}`), unless an app already carries a partition. The
 * global allocation governs experiments that have no local allocation override.
 * Deep-clones; the input is not mutated.
 */
export function normalizeSplitToPartitions(config: CrabConfig): CrabConfig {
  const clone = JSON.parse(JSON.stringify(config ?? {})) as CrabConfig;
  const g = (clone.global_options ?? {}) as Record<string, unknown>;
  const experiments = (clone.experiments ?? {}) as Record<string, Experiment>;

  // A split array → a partitions object keyed group_1..group_N (+ the keys, in
  // order). Evenness is ALL-OR-NOTHING: shares are omitted only when the WHOLE
  // split is the equal division (100/N each); otherwise every group gets an
  // explicit share. A mix of shared/unshared partitions is rejected by the
  // engine ("either all partitions specify 'share' or none"), so never emit one.
  const toGroups = (split: unknown[]): { partitions: Record<string, unknown>; keys: string[] } => {
    const n = split.length || 1;
    const nums = split.map((raw) => numOrToken(String(raw)));
    const even =
      100 % n === 0 &&
      nums.every(
        (v) => typeof v === "number" && Number.isFinite(v) && Math.abs(v - 100 / n) < 1e-9,
      );
    const partitions: Record<string, unknown> = {};
    const keys: string[] = [];
    nums.forEach((v, i) => {
      const key = `group_${i + 1}`;
      keys.push(key);
      partitions[key] = even ? {} : { share: v };
    });
    return { partitions, keys };
  };

  // The engine gives split shares to chain heads only: an app with start "sN" runs on app N's
  // nodes (ADR-032), so it takes no group and the next head takes the next one.
  const tagApps = (apps: Record<string, AppConfig> | undefined, keys: string[]): void => {
    if (!apps) return;
    let head = 0;
    Object.values(apps).forEach((app) => {
      if (!app || typeof app !== "object" || String(app.start ?? "").startsWith("s")) return;
      if ((app.partition == null || app.partition === "") && head < keys.length) {
        app.partition = keys[head];
      }
      head += 1;
    });
  };

  // Global allocation: convert + tag apps of experiments with no local override.
  const ga = g.allocation as Record<string, unknown> | undefined;
  if (ga && ga.mode !== "shared" && Array.isArray(ga.split) && ga.partitions == null) {
    const { partitions, keys } = toGroups(ga.split);
    delete ga.split;
    ga.partitions = partitions;
    for (const e of Object.values(experiments)) {
      const lo = (e?.local_options ?? {}) as Record<string, unknown>;
      if (lo.allocation == null) tagApps(e?.apps, keys);
    }
  }

  // Per-experiment overrides: convert + tag that experiment's apps.
  for (const e of Object.values(experiments)) {
    const lo = (e?.local_options ?? {}) as Record<string, unknown>;
    const la = lo.allocation as Record<string, unknown> | undefined;
    if (la && la.mode !== "shared" && Array.isArray(la.split) && la.partitions == null) {
      const { partitions, keys } = toGroups(la.split);
      delete la.split;
      la.partitions = partitions;
      tagApps(e?.apps, keys);
    }
  }

  return clone;
}

/** Inverse: load an engine config (or legacy `applications` form) into a draft. */
export function fromConfig(config: CrabConfig): Draft {
  config = normalizeSplitToPartitions(config);
  const g = (config?.global_options ?? {}) as Record<string, unknown>;
  const str = (v: unknown, fallback = "") => (v == null ? fallback : String(v));

  // Legacy single-experiment form: a top-level `applications` block. The engine
  // (engine.py:119-132) accepts two shapes: an object that already holds
  // `apps` (+ maybe `local_options`), used as-is; or a flat `{0:{...},1:{...}}`
  // map, wrapped under `apps`. Either way it becomes the experiment `default_ex`.
  let experiments = config?.experiments;
  const legacy = (config as unknown as { applications?: unknown })?.applications;
  if (!experiments && legacy && typeof legacy === "object") {
    const l = legacy as Record<string, unknown>;
    experiments =
      "apps" in l ? { default_ex: l as never } : { default_ex: { apps: legacy as never } };
  }

  const draft = emptyDraft();
  draft.name = str(g.name);
  draft.numnodes = str(g.numnodes);
  draft.ppn = str(g.ppn); // faithful: no default injection on import (emptyDraft seeds "1" for new configs)
  draft.allocation = fromAllocation(g.allocation);
  draft.options = readOptions(g);
  draft.sbatch = fromSbatch(g.sbatch_directives);
  draft.experiments = Object.entries(experiments ?? {}).map(([name, exp]) => {
    const e = exp as {
      description?: unknown;
      local_options?: unknown;
      apps?: Record<string, never>;
    };
    const lo = (e.local_options ?? {}) as Record<string, unknown>;
    return {
      name,
      description: str(e.description),
      overrideAlloc: lo.allocation != null,
      allocation: fromAllocation(lo.allocation),
      options: readOptions(lo),
      ppn: str(lo.ppn),
      apps: Object.values(e.apps ?? {}).map((a) => {
        const app = a as Record<string, unknown>;
        const rest: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(app)) {
          if (!RESERVED_APP_KEYS.has(k)) rest[k] = v;
        }
        return {
          path: str(app.path),
          args: str(app.args),
          collect: engineBool(app.collect), // doc default is false
          partition: str(app.partition),
          env: readEnv(app.env),
          cpus: str(app.cpus),
          ppn: str(app.ppn),
          rest,
          ...parseStart(str(app.start, "0")),
          ...parseEnd(str(app.end)),
        };
      }),
    };
  });
  return draft;
}

// -- Flow diagram ------------------------------------------------------------
// Lives in lib/flow.ts; re-exported so callers keep importing from here.
export { allocationSummary, flowForest } from "@/lib/flow";
export type { FlowNode, FlowTree } from "@/lib/flow";
export { CORES_MODES, LAUNCHER_KINDS, hasAllocation } from "@/lib/configShared";

// -- Shape validation --------------------------------------------------------
// Lives in lib/configValidate.ts; re-exported so callers keep importing from here.
export {
  validateAllocation,
  validateDraft,
  validateOptions,
  validateSbatch,
} from "@/lib/configValidate";
export type { WrapperCatalog } from "@/lib/configValidate";
