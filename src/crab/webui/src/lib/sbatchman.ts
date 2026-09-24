// SbatchMan campaign composer (plan 084). PURE module (no DOM/fetch) so the unit
// suite can import it, mirroring lib/config.ts.
//
// It turns a CampaignSpec into a SbatchMan jobs YAML (`sbatchman launch -f`). Each
// group becomes one `jobs:` entry whose `preprocess` writes config.json into
// $SBATCHMAN_JOB_DIR and whose `command` runs the CRAB worker inside the allocation
// SbatchMan obtained. The cartesian expansion of `variables` and the `{var}`
// substitution are done by SbatchMan at launch time (core/launcher.py,
// core/variables.py); this module only PREVIEWS the product and emits the templates.
//
// ADR-027: this module does NOT write environment.json. `crab worker` falls back to
// the inherited process environment when no environment.json exists in the workdir --
// CRAB_ROOT/CRAB_SYSTEM/CRAB_PATH_WRAPPERS/SBATCHMAN_JOB_DIR must instead be exported
// by the partner's own SbatchMan preset (`sbatchman configure --env ...`, documented
// in docs/using/sbatchman-integration.md), not generated here. config.json still gets
// heredoc'd -- it is per-job sweep data, not a static preset value.
//
// The embedded CRAB JSON keeps its `{var}` placeholders literal — SbatchMan's own
// substitution regex ignores `{` followed by whitespace, so JSON object braces are
// safe; only `{token}` (no spaces) is substituted.

import yaml from "js-yaml";
import type { CrabConfig } from "@/api/types";
import { fromConfig, validateDraft } from "@/lib/config";

/** A sweep variable: a name and the list of values SbatchMan will expand. */
export interface SbatchmanVar {
  name: string;
  values: Array<string | number>;
}

/** One job group = one CRAB experiment template + its own tag/preset/variables. */
export interface SbatchmanGroup {
  /** Tag template, e.g. "g500_baseline_{scale}_{ef}_{nodes}". */
  tag: string;
  /** SbatchMan config (preset) name, may be templated, e.g. "{nodes}_nodes". */
  preset: string;
  /** Variables scoped to this group (merged over the campaign-global ones). */
  variables: SbatchmanVar[];
  /** The CRAB experiment JSON (from config.ts::toConfig); may hold `{var}`. */
  config: CrabConfig;
}

export interface SbatchmanCampaign {
  /** SbatchMan presets file, referenced by the jobs YAML `configs:` key. */
  configsPath: string;
  /** Remote CRAB checkout dir. Reference only (ADR-027): not written to any generated
   * file -- put it in the SbatchMan preset's `--env CRAB_ROOT=...` instead. */
  crabRoot: string;
  /** CRAB_SYSTEM. Reference only (ADR-027): put it in the SbatchMan preset's
   * `--env CRAB_SYSTEM=...` instead. */
  system: string;
  /** Extra environment entries. Reference only (ADR-027): put these in the SbatchMan
   * preset's `--env` list instead -- nothing here is written to a generated file. */
  env: Record<string, string>;
  /** Campaign-global variables (merged into every group). */
  variables: SbatchmanVar[];
  groups: SbatchmanGroup[];
}

const WORKDIR = "$SBATCHMAN_JOB_DIR";

// -- Variable analysis / preview --------------------------------------------

/** Group-scoped variables win over campaign-global ones of the same name. */
function effectiveVars(campaign: SbatchmanCampaign, group: SbatchmanGroup): SbatchmanVar[] {
  const byName = new Map<string, SbatchmanVar>();
  for (const v of campaign.variables) byName.set(v.name, v);
  for (const v of group.variables) byName.set(v.name, v);
  return [...byName.values()];
}

/** Names actually referenced (as `{name}`) across a group's templates + JSON. */
function usedNames(group: SbatchmanGroup): Set<string> {
  const names = new Set<string>();
  const texts = [group.tag, group.preset, JSON.stringify(group.config)];
  for (const t of texts) {
    for (const m of t.matchAll(/\{(\w+)\}/g)) names.add(m[1]);
  }
  return names;
}

/** Only the effective variables a group actually references (what SbatchMan expands). */
function usedVars(campaign: SbatchmanCampaign, group: SbatchmanGroup): SbatchmanVar[] {
  const used = usedNames(group);
  return effectiveVars(campaign, group).filter((v) => used.has(v.name));
}

/** Number of jobs a group expands to = product of its used variables' value counts. */
export function groupJobCount(campaign: SbatchmanCampaign, group: SbatchmanGroup): number {
  const vars = usedVars(campaign, group);
  if (vars.length === 0) return 1; // no variables -> a single job
  return vars.reduce((n, v) => n * v.values.length, 1);
}

/** Total jobs the whole campaign expands to. */
export function campaignJobCount(campaign: SbatchmanCampaign): number {
  return campaign.groups.reduce((n, g) => n + groupJobCount(campaign, g), 0);
}

function cartesian(vars: SbatchmanVar[]): Array<Record<string, string>> {
  let combos: Array<Record<string, string>> = [{}];
  for (const v of vars) {
    const next: Array<Record<string, string>> = [];
    for (const combo of combos) {
      for (const val of v.values) next.push({ ...combo, [v.name]: String(val) });
    }
    combos = next;
  }
  return combos;
}

function substitute(template: string, vars: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? vars[name] : m));
}

/** Up to `limit` example expanded tags for a group, for the live preview. */
export function sampleTags(
  campaign: SbatchmanCampaign,
  group: SbatchmanGroup,
  limit = 8,
): string[] {
  return cartesian(usedVars(campaign, group))
    .slice(0, limit)
    .map((combo) => substitute(group.tag, combo));
}

// -- YAML emission ------------------------------------------------------------
//
// The document is built as a plain object and serialized by js-yaml, so any name or
// value the user types (blank, `:`, `#`, quotes, a leading `{`) comes out as valid,
// correctly quoted YAML. Hand-built strings produced invalid YAML for e.g. a blank
// variable name (plan 090 S11b).

/** Numbers, and strings that look like numbers, become YAML numbers (SbatchMan
 * substitutes them into numeric positions); everything else stays a string. */
function listItem(v: string | number): string | number {
  if (typeof v === "number") return v;
  return /^-?\d+(\.\d+)?$/.test(v) ? Number(v) : v;
}

function varsObject(vars: SbatchmanVar[]): Record<string, Array<string | number>> {
  const out: Record<string, Array<string | number>> = {};
  for (const v of vars) out[v.name] = v.values.map(listItem);
  return out;
}

// -- Numeric placeholders in config.json ---------------------------------------
//
// The engine uses allocation numerics raw (core/allocation/allocator.py compares
// `stride < 1`), so a `{var}` in those positions must be written UNQUOTED in the
// heredoc: `"stride": {stride}` becomes `"stride": 4` after SbatchMan substitutes it.
// Everywhere else a placeholder stays inside its JSON string.

const NUMERIC_ALLOC_KEYS = new Set(["stride", "seed", "share"]);
const TOKEN = /^\{(\w+)\}$/;
const SENTINEL = (name: string) => `@@crab-num:${name}@@`;
const SENTINEL_QUOTED = /"@@crab-num:(\w+)@@"/g;

function asSentinel(v: unknown): unknown {
  if (typeof v !== "string") return v;
  const m = TOKEN.exec(v);
  return m ? SENTINEL(m[1]) : v;
}

/** Copy of `node` where numeric-position tokens under any `allocation` are sentinels. */
function markNumericTokens(node: unknown, inAllocation = false): unknown {
  if (Array.isArray(node)) return node.map((x) => markNumericTokens(x, inAllocation));
  if (!node || typeof node !== "object") return node;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
    if (inAllocation && NUMERIC_ALLOC_KEYS.has(k)) out[k] = asSentinel(v);
    else if (inAllocation && k === "split" && Array.isArray(v)) out[k] = v.map(asSentinel);
    else out[k] = markNumericTokens(v, inAllocation || k === "allocation");
  }
  return out;
}

/** config.json text, with numeric-position placeholders unquoted. */
function configJson(config: CrabConfig): string {
  return JSON.stringify(markNumericTokens(config), null, 2).replace(SENTINEL_QUOTED, "{$1}");
}

/** The bash heredoc lines (at column 0) that write config.json. They become one
 * YAML block scalar, so the heredoc body and its `JSON` terminator reach bash at
 * column 0. ADR-027: environment.json is NOT written
 * here -- `crab worker` falls back to the inherited process environment when no
 * environment.json exists in the workdir. */
function preprocessLines(_campaign: SbatchmanCampaign, group: SbatchmanGroup): string[] {
  const heredoc = (relPath: string, json: string): string[] => [
    `cat > "${WORKDIR}/${relPath}" <<'JSON'`,
    ...json.split("\n"),
    "JSON",
  ];
  return [`mkdir -p "${WORKDIR}"`, ...heredoc("config.json", configJson(group.config))];
}

/** Compose the full SbatchMan jobs YAML for a campaign. */
export function composeCampaignYaml(campaign: SbatchmanCampaign): string {
  const doc: Record<string, unknown> = { configs: campaign.configsPath };
  if (campaign.variables.length) doc.variables = varsObject(campaign.variables);
  doc.jobs = campaign.groups.map((group) => {
    const job: Record<string, unknown> = { config: group.preset, tag: group.tag };
    if (group.variables.length) job.variables = varsObject(group.variables);
    // Trailing newline so the heredoc's `JSON` terminator line is newline-ended.
    job.preprocess = preprocessLines(campaign, group).join("\n") + "\n";
    job.command = `crab worker --workdir "${WORKDIR}"`;
    return job;
  });
  // lineWidth -1: never fold long lines (the preprocess script must stay verbatim).
  return yaml.dump(doc, { lineWidth: -1, noRefs: true });
}

// -- Validation ---------------------------------------------------------------

function varIssues(vars: SbatchmanVar[], where: string): string[] {
  const issues: string[] = [];
  const seen = new Set<string>();
  for (const v of vars) {
    const name = v.name.trim();
    if (!name) {
      issues.push(`${where}: a variable has no name.`);
      continue;
    }
    if (seen.has(name)) issues.push(`${where}: "${name}" is defined twice.`);
    seen.add(name);
    if (!v.values.length) issues.push(`${where}: variable "${name}" has no values.`);
  }
  return issues;
}

/** Everything that would make the generated YAML fail at `sbatchman launch` or give the
 * CRAB worker a bad config. Each group's experiment is checked the way the engine gets
 * it: config.json with the first variable combination substituted, then validateDraft. */
export function validateCampaign(campaign: SbatchmanCampaign): string[] {
  const issues = varIssues(campaign.variables, "Campaign variables");
  campaign.groups.forEach((group, i) => {
    const where = `Group "${group.tag.trim() || `#${i + 1}`}"`;
    issues.push(...varIssues(group.variables, where));
    if (!group.preset.trim()) issues.push(`${where}: set the SbatchMan config it runs with.`);

    const defined = new Set(effectiveVars(campaign, group).map((v) => v.name.trim()));
    const unknown = [...usedNames(group)].filter((n) => !defined.has(n));
    for (const n of unknown) issues.push(`${where}: {${n}} is not a defined variable.`);
    if (unknown.length) return; // the experiment can't be checked with unresolved tokens

    const combo = cartesian(usedVars(campaign, group))[0] ?? {};
    let config: unknown;
    try {
      config = JSON.parse(substitute(configJson(group.config), combo));
    } catch {
      issues.push(
        `${where}: a variable value makes config.json invalid (a text value in a numeric field?).`,
      );
      return;
    }
    for (const issue of validateDraft(fromConfig(config as CrabConfig)))
      issues.push(`${where}: ${issue}`);
  });
  return issues;
}
