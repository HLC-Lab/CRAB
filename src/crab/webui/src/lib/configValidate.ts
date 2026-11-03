// Shape validation of the editor's draft model: the structural checks that make a
// config well-formed enough to submit, mirroring the engine's pre-submit checks.
// Pure (no Vue/IO). Only types are imported from config.ts, so there is no runtime
// cycle; config.ts re-exports the public names below.

import type { AllocationDraft, Draft, EnvRow, OptionsDraft, SbatchDraft } from "@/lib/config";
import {
  CORES_MODES,
  LAUNCHER_KINDS,
  PLACEHOLDER,
  hasAllocation,
  isNumeric as _numeric,
} from "@/lib/configShared";

// -- Shape validation --------------------------------------------------------
// Structural checks only (not engine semantics): the things that make a config
// well-formed enough to submit. Returns a list of human-readable issues.

const _posInt = (s: string) => /^[0-9]+$/.test(s.trim()) && parseInt(s, 10) > 0;
// A {name} placeholder, kept as text by toAllocation for SbatchMan to substitute.
const _token = (s: string) => PLACEHOLDER.test(s.trim());

/** Validate the tunable option fields. `where` prefixes messages (e.g. an experiment name). */
export function validateOptions(o: OptionsDraft, where = ""): string[] {
  const issues: string[] = [];
  const at = where ? `${where}: ` : "";
  if (o.minruns.trim() && !_posInt(o.minruns))
    issues.push(`${at}min runs must be a positive integer.`);
  if (o.maxruns.trim() && !_posInt(o.maxruns))
    issues.push(`${at}max runs must be a positive integer.`);
  if (
    o.minruns.trim() &&
    o.maxruns.trim() &&
    _posInt(o.minruns) &&
    _posInt(o.maxruns) &&
    parseInt(o.maxruns, 10) < parseInt(o.minruns, 10)
  )
    issues.push(`${at}max runs must be ≥ min runs.`);
  if (o.timeout.trim() && !_numeric(o.timeout))
    issues.push(`${at}timeout must be a number (seconds).`);
  if (o.alpha.trim() && !_numeric(o.alpha)) issues.push(`${at}alpha must be a number.`);
  if (o.beta.trim() && !_numeric(o.beta)) issues.push(`${at}beta must be a number.`);
  issues.push(...validateLauncher(o, at));
  return issues;
}

// Mirrors has_token in core/execution/tokens.py: a "{" anywhere means SbatchMan substitutes later.
function hasBrace(v: unknown): boolean {
  if (typeof v === "string") return v.includes("{");
  if (Array.isArray(v)) return v.some(hasBrace);
  if (v !== null && typeof v === "object") return Object.values(v).some(hasBrace);
  return false;
}

/** Mirrors check_launcher_options in core/execution/launch_checks.py. */
function validateLauncher(o: OptionsDraft, at: string): string[] {
  const issues: string[] = [];
  if (o.launcher.trim() && !hasBrace(o.launcher) && !LAUNCHER_KINDS.includes(o.launcher.trim()))
    issues.push(
      `${at}launcher '${o.launcher.trim()}' must be one of ${LAUNCHER_KINDS.join(", ")}.`,
    );
  const raw = o.launcherFlagsRaw;
  if (raw !== undefined && !hasBrace(raw)) {
    if (!Array.isArray(raw) || !raw.every((f) => typeof f === "string"))
      issues.push(`${at}launcher_flags must be a list of strings.`);
  }
  return issues;
}

/** Mirrors _check_shared_scope and the shared-with-partitions check in core/config_checks.py. */
function validateSharedScope(a: AllocationDraft, at: string): string[] {
  const issues: string[] = [];
  const cores = a.cores.trim();
  if (a.mode === "shared") {
    const set: [string, boolean][] = [
      ["split", a.by === "app" && !!a.split.trim() && !hasBrace(a.split)],
      ["stride", !!a.stride.trim() && !hasBrace(a.stride)],
      ["seed", !!a.seed.trim() && !hasBrace(a.seed)],
    ];
    for (const [key, present] of set) {
      if (present)
        issues.push(
          `${at}allocation: ${key} cannot be used with mode 'shared' (every app gets all the nodes)`,
        );
    }
  } else if (cores && !hasBrace(cores)) {
    issues.push(`${at}allocation: cores is only valid with mode 'shared'`);
    return issues;
  }
  if (cores && !hasBrace(cores) && !CORES_MODES.includes(cores))
    issues.push(`${at}allocation: cores '${cores}' is not one of: ${CORES_MODES.join(", ")}`);
  if (a.mode === "shared" && a.by === "groups" && a.partitions.some((p) => p.name.trim()))
    issues.push(
      `${at}allocation.mode 'shared' cannot be used with partitions ` +
        "(the top-level mode lays out the partition blocks); " +
        "set mode 'shared' inside a partition instead",
    );
  return issues;
}

/** Validate an allocation and return its defined node-group names (for per-app checks). */
export function validateAllocation(
  a: AllocationDraft,
  where = "",
): { issues: string[]; groups: Set<string> } {
  const issues: string[] = [];
  const groups = new Set<string>();
  const at = where ? `${where}: ` : "";
  if (!hasAllocation(a)) return { issues, groups };
  issues.push(...validateSharedScope(a, at));
  if (a.mode === "interleaved" && a.stride.trim() && !_posInt(a.stride) && !_token(a.stride))
    issues.push(`${at}allocation stride must be a positive integer.`);
  if (a.mode === "random" && a.seed.trim() && !/^[0-9]+$/.test(a.seed.trim()) && !_token(a.seed))
    issues.push(`${at}allocation seed must be an integer.`);
  if (a.by === "app" && a.split.trim()) {
    const parts = a.split.split(",").map((s) => s.trim());
    if (!parts.every((s) => _numeric(s) || _token(s)))
      issues.push(`${at}allocation split must be a comma-separated list of numbers.`);
  }
  if (a.by === "groups") {
    const named = a.partitions.filter((p) => p.name.trim());
    if (!named.length && a.mode !== "shared")
      issues.push(`${at}add at least one node group, or allocate by app instead.`);
    named.forEach((p) => {
      if (groups.has(p.name.trim())) issues.push(`${at}duplicate node group "${p.name.trim()}".`);
      else groups.add(p.name.trim());
      if (p.share.trim() && !_numeric(p.share) && !_token(p.share))
        issues.push(`${at}node group "${p.name.trim()}": share must be a number.`);
    });
    const shared = named.filter((p) => p.share.trim());
    if (shared.length && shared.length !== named.length)
      issues.push(`${at}set a share on every node group, or on none (for an equal split).`);
    else if (shared.length && shared.every((p) => _numeric(p.share))) {
      const sum = shared.reduce((t, p) => t + Number(p.share), 0);
      if (Math.abs(sum - 100) > 0.01)
        issues.push(`${at}node-group shares should sum to 100 (currently ${sum}).`);
    }
  }
  return { issues, groups };
}

// Slurm directive keys CRAB writes itself, with what to say instead
// (core/execution/launch_checks.py MANAGED_DIRECTIVES). The engine refuses them.
const TASKS_TAIL = "CRAB sets the task count from numnodes and ppn";
const MANAGED_SBATCH: Record<string, string> = {
  nodes: "use numnodes",
  N: "use numnodes",
  "ntasks-per-node": "use ppn",
  ntasks: TASKS_TAIL,
  n: TASKS_TAIL,
};
const LOG_SBATCH = new Set(["output", "error", "o", "e"]);

// CRAB_* launch variables the engine no longer reads, with their replacement
// (core/execution/settings.py DROPPED_KEYS).
const DROPPED_ENV_KEYS: Record<string, string> = {
  CRAB_WL_MANAGER: "launcher",
  CRAB_SCHEDULER: "scheduler",
  CRAB_MPIRUN: "launchers.mpirun.command",
  CRAB_MPIRUN_ADDITIONAL_FLAGS: "launchers.mpirun.flags",
  CRAB_MPIRUN_MAP_BY_NODE_FLAG: "launchers.mpirun.flags",
  CRAB_MPIRUN_HOSTNAMES_FLAG: "nothing: delete it (mpirun finds the job's nodes itself)",
  CRAB_PINNING_FLAGS: "launchers.srun.flags",
};

/** A directive's key as the engine's merge reads it: leading dashes off, up to "=" or a space. */
function engineDirectiveKey(line: string): string {
  const clean = line.trim().replace(/^-+/, "");
  return clean.includes("=") ? clean.split("=")[0] : clean.split(/\s+/)[0];
}

export function validateSbatch(s: SbatchDraft): string[] {
  const issues: string[] = [];
  for (const line of s.lines.map((l) => l.trim()).filter(Boolean)) {
    const key = engineDirectiveKey(line);
    if (!key) continue;
    const flag = `${key.length === 1 ? "-" : "--"}${key}`;
    if (key in MANAGED_SBATCH)
      issues.push(
        `sbatch_directives: '${line}' sets '${key}', which CRAB manages itself: ${MANAGED_SBATCH[key]}`,
      );
    else if (LOG_SBATCH.has(key))
      issues.push(
        `Slurm directive "${flag}" overrides CRAB's log redirection (allowed, but be aware).`,
      );
  }
  return issues;
}

/** Mirror of placement.parse_cpus: the problem with a core list, or "" when it parses. */
function cpusProblem(text: string): string {
  const cores: number[] = [];
  for (const raw of text.split(",")) {
    const item = raw.trim();
    const span = /^([0-9]+)-([0-9]+)$/.exec(item);
    if (/^[0-9]+$/.test(item)) cores.push(parseInt(item, 10));
    else if (span && parseInt(span[1], 10) <= parseInt(span[2], 10))
      for (let c = parseInt(span[1], 10); c <= parseInt(span[2], 10); c++) cores.push(c);
    else
      return `cpus '${text}' has the invalid item '${item}'; use N or A-B (A <= B), comma-separated`;
  }
  if (new Set(cores).size !== cores.length) return `cpus '${text}' lists a core more than once`;
  return "";
}

/** Mirror of placement.parse_app_env, plus the editor's own row checks. */
function envProblems(rows: EnvRow[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const r of rows) {
    const key = r.key.trim();
    if (!key) {
      if (r.value.trim()) out.push("env: a value needs a variable name");
    } else if (key in DROPPED_ENV_KEYS) {
      out.push(`env: ${key} is no longer read; use ${DROPPED_ENV_KEYS[key]}`);
    } else if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
      out.push(`env: key '${key}' is not a valid shell variable name`);
    } else if (seen.has(key)) {
      out.push(`env: '${key}' is listed twice`);
    }
    if (key) seen.add(key);
  }
  return out;
}

/** The target cluster's wrapper catalog: relpaths as the wrappers list holds them. */
export interface WrapperCatalog {
  wrappers: ReadonlySet<string>;
  cluster: string;
}

/**
 * With a catalog, an app path outside it is a problem. A path the catalog cannot judge is never
 * flagged: a {name} placeholder (resolved at run time) or an absolute path. An EMPTY catalog is
 * treated as unknown (the listing may have failed or the clone may be missing), so no check runs
 * rather than flagging every app.
 */
export function validateDraft(d: Draft, catalog?: WrapperCatalog): string[] {
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

    issues.push(...validateOptions(e.options, `Experiment "${label}"`));
    if (e.ppn.trim() && !_posInt(e.ppn))
      issues.push(`Experiment "${label}": processes per node must be a positive integer.`);
    // An override REPLACES the global allocation entirely (force-emitted, even bare linear ⇒ no
    // groups), so an app may only reference the local groups.
    let groups = global.groups;
    if (e.overrideAlloc) {
      const local = validateAllocation(e.allocation, `Experiment "${label}"`);
      issues.push(...local.issues);
      groups = local.groups;
    }

    if (!e.apps.length) issues.push(`Experiment "${label}" has no apps.`);
    e.apps.forEach((a, ai) => {
      const wrapperPath = a.path.trim();
      if (!wrapperPath) issues.push(`${label} · app #${ai}: wrapper path is required.`);
      else if (
        catalog &&
        catalog.wrappers.size > 0 &&
        !wrapperPath.includes("{") &&
        !wrapperPath.startsWith("/") &&
        !catalog.wrappers.has(wrapperPath)
      )
        issues.push(`${label} · app #${ai}: wrapper ${wrapperPath} is not on ${catalog.cluster}.`);
      if (a.startKind === "delay" && !numeric(a.startDelay))
        issues.push(`${label} · app #${ai}: start delay must be a number.`);
      if (a.endKind === "timed" && !numeric(a.endTimed))
        issues.push(`${label} · app #${ai}: timed duration must be a number.`);
      if (a.startKind === "after") {
        const ref = parseInt(a.startAfter, 10);
        if (!(ref >= 0 && ref < e.apps.length && ref !== ai))
          issues.push(`${label} · app #${ai}: "after" must reference another app.`);
      }
      if (a.ppn.trim() && !_posInt(a.ppn))
        issues.push(`${label} · app #${ai}: processes per node must be a positive integer.`);
      // The engine skips its placement checks for an app that holds a {name} placeholder.
      const hasToken = a.cpus.includes("{") || a.env.some((r) => (r.key + r.value).includes("{"));
      if (!hasToken) {
        if (a.cpus.trim()) {
          const problem = cpusProblem(a.cpus.trim());
          if (problem) issues.push(`${label} · app #${ai}: ${problem}`);
        }
        for (const problem of envProblems(a.env)) issues.push(`${label} · app #${ai}: ${problem}`);
      }
      // A partition reference is invalid whenever it names no defined node group —
      // including when none exist (e.g. after switching to "by app" or disabling allocation).
      if (a.partition.trim() && !groups.has(a.partition.trim()))
        issues.push(
          `${label} · app #${ai}: partition "${a.partition.trim()}" is not a defined node group.`,
        );
    });
  });
  return issues;
}
