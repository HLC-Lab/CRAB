// SbatchMan campaign composer. PURE module (no DOM/fetch) so the unit
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
// config.json's `global_options.name` defaults to the group tag (placeholders included),
// so the run registry names each job instead of recording `unknown`.
//
// The embedded CRAB JSON keeps its `{var}` placeholders literal — SbatchMan's own
// substitution regex ignores `{` followed by whitespace, so JSON object braces are
// safe; only `{token}` (no spaces) is substituted.

import yaml from "js-yaml";
import type { CrabConfig } from "@/api/types";
import { fromConfig, validateDraft, type WrapperCatalog } from "@/lib/config";

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

/** `{identifier}` tokens, with the `$` of a `${HOME}` shell expansion captured. Object braces,
 * `{}`, `{ x }`, `{"a": 1}` and `x{3}` are not identifiers and never match. */
const TOKEN_RE = /(\$?)\{([A-Za-z_][A-Za-z0-9_]*)\}/g;

/** Names referenced as `{name}` in `texts`; the value is true when at least one reference is
 * not a `${name}` shell expansion (only those can be "not a defined variable"). */
function tokenNames(texts: string[]): Map<string, boolean> {
  const names = new Map<string, boolean>();
  for (const t of texts)
    for (const m of t.matchAll(TOKEN_RE)) names.set(m[2], (names.get(m[2]) ?? false) || !m[1]);
  return names;
}

/** Names actually referenced (as `{name}`) across a group's templates + JSON. */
function usedNames(group: SbatchmanGroup): Set<string> {
  return new Set(tokenNames([group.tag, group.preset, JSON.stringify(group.config)]).keys());
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

/** The combinations in SbatchMan's order, at most `limit` of them. Each step is cut to `limit`
 * because the first `limit` finished combinations only come from the first `limit` partial ones. */
function cartesian(vars: SbatchmanVar[], limit = Infinity): Array<Record<string, string>> {
  let combos: Array<Record<string, string>> = [{}];
  for (const v of vars) {
    const next: Array<Record<string, string>> = [];
    for (const combo of combos) {
      for (const val of v.values) {
        if (next.length >= limit) break;
        next.push({ ...combo, [v.name]: String(val) });
      }
      if (next.length >= limit) break;
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
// variable name.

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

/** The group's config with `global_options.name` set to the group tag when the
 * experiment sets no name of its own. Returns a copy; the group is never modified. */
function withRunName(group: SbatchmanGroup): CrabConfig {
  const name = group.config.global_options.name;
  const hasName = typeof name === "string" && name.trim() !== "";
  if (hasName || !group.tag.trim()) return group.config;
  return { ...group.config, global_options: { ...group.config.global_options, name: group.tag } };
}

/** config.json text, with numeric-position placeholders unquoted. */
function configJson(group: SbatchmanGroup): string {
  return JSON.stringify(markNumericTokens(withRunName(group)), null, 2).replace(
    SENTINEL_QUOTED,
    "{$1}",
  );
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
  return [`mkdir -p "${WORKDIR}"`, ...heredoc("config.json", configJson(group))];
}

/** Compose the full SbatchMan jobs YAML for a campaign. */
export function composeCampaignYaml(campaign: SbatchmanCampaign): string {
  const doc: Record<string, unknown> = {};
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

/** The catalog as validateDraft should see it for one group: a wrapper path written with a
 * {var} placeholder is resolved by SbatchMan at launch, so its first-combination form must not
 * be flagged (validateDraft only skips paths that still hold a placeholder). */
function catalogForGroup(
  group: SbatchmanGroup,
  combo: Record<string, string>,
  catalog: WrapperCatalog | undefined,
): WrapperCatalog | undefined {
  if (!catalog) return undefined;
  const wrappers = new Set(catalog.wrappers);
  for (const exp of Object.values(group.config.experiments ?? {}))
    for (const app of Object.values(exp.apps ?? {}))
      if (typeof app.path === "string" && app.path.includes("{"))
        wrappers.add(substitute(app.path, combo).trim());
  return { cluster: catalog.cluster, wrappers };
}

/** How many variable combinations of one group are checked (and expanded for the tag check). */
const COMBO_CAP = 200;

/** Keys the CRAB worker refuses in an app when SbatchMan provides the environment
 * (cli/orchestrator.py `_SBATCHMAN_LIMITS`); the text says what to do instead. */
const WORKER_REFUSED: Record<string, string> = {
  cpus: "the worker takes the cores from the SbatchMan allocation",
  bind: "the worker has no node topology to bind to",
};

/** The worker's "not set": None, "", [] or {}. */
function isEmptyValue(v: unknown): boolean {
  if (v == null || v === "") return true;
  if (Array.isArray(v)) return v.length === 0;
  return typeof v === "object" && Object.keys(v).length === 0;
}

/** One problem per app key the worker would refuse. The worker reads these keys off the app
 * dict (`bind` only ever lives among the editor's extra options) and ignores empty values. */
function workerRefusals(group: SbatchmanGroup, where: string): string[] {
  const out: string[] = [];
  for (const [expName, exp] of Object.entries(group.config.experiments ?? {}))
    for (const [appKey, app] of Object.entries(exp.apps ?? {}))
      for (const [key, reason] of Object.entries(WORKER_REFUSED)) {
        if (!isEmptyValue(app[key]))
          out.push(
            `${where}: experiment "${expName}", app ${appKey} sets ${key}: ${reason}; remove it.`,
          );
      }
  return out;
}

/** The problems of ONE variable combination: config.json with it substituted, then validateDraft. */
function comboIssues(
  group: SbatchmanGroup,
  template: string,
  combo: Record<string, string>,
  catalog: WrapperCatalog | undefined,
): string[] {
  let config: unknown;
  try {
    config = JSON.parse(substitute(template, combo));
  } catch {
    return ["a variable value makes config.json invalid (a text value in a numeric field?)."];
  }
  return validateDraft(fromConfig(config as CrabConfig), catalogForGroup(group, combo, catalog));
}

/** Problems of the first bad combination (up to COMBO_CAP), prefixed by the values that cause
 * them. Only the variables with several values are named; the first combination keeps its
 * plain messages. */
function groupExperimentIssues(
  campaign: SbatchmanCampaign,
  group: SbatchmanGroup,
  where: string,
  catalog: WrapperCatalog | undefined,
): string[] {
  const vars = usedVars(campaign, group);
  const template = configJson(group);
  const combos = cartesian(vars, COMBO_CAP);
  for (const [k, combo] of (combos.length ? combos : [{}]).entries()) {
    const found = comboIssues(group, template, combo, catalog);
    if (!found.length) continue;
    const values = vars.filter((v) => v.values.length > 1).map((v) => `${v.name}=${combo[v.name]}`);
    const prefix = k === 0 || !values.length ? "" : `with ${values.join(", ")}: `;
    return found.map((m) => `${where}: ${prefix}${m}`);
  }
  return [];
}

/** Everything that would make the generated YAML fail at `sbatchman launch` or give the
 * CRAB worker a bad config. Each group's experiment is checked the way the engine gets
 * it: config.json with each variable combination substituted (the first 200), then
 * validateDraft; only the first bad combination is reported. Also: keys the worker refuses,
 * empty tags, and (config, tag) pairs two groups share after expansion, which SbatchMan
 * skips without a message (ADR-045). With a catalog (the write target's wrappers) a wrapper
 * path the cluster lacks is a problem too. */
export function validateCampaign(campaign: SbatchmanCampaign, catalog?: WrapperCatalog): string[] {
  const issues = varIssues(campaign.variables, "Campaign variables");
  const firstGroupOfPair = new Map<string, number>();
  const reported = new Set<string>();
  campaign.groups.forEach((group, i) => {
    const where = group.tag.trim() ? `Group "${group.tag.trim()}"` : `Group #${i + 1}`;
    issues.push(...varIssues(group.variables, where));
    if (!group.preset.trim()) issues.push(`${where}: set the SbatchMan config it runs with.`);
    if (!group.tag.trim())
      issues.push(`Group #${i + 1} has no tag: give it one, for example run_{nodes}.`);
    issues.push(...workerRefusals(group, where));

    // Same (config, tag) in two groups after expansion: SbatchMan skips the later jobs.
    if (group.tag.trim() && group.preset.trim()) {
      const names = tokenNames([group.tag, group.preset]);
      const tagVars = effectiveVars(campaign, group).filter((v) => names.has(v.name));
      const pairs = new Set(
        cartesian(tagVars, COMBO_CAP).map((combo) =>
          JSON.stringify([substitute(group.preset, combo), substitute(group.tag, combo)]),
        ),
      );
      for (const pair of pairs) {
        const first = firstGroupOfPair.get(pair);
        if (first === undefined) firstGroupOfPair.set(pair, i);
        else if (!reported.has(`${first}:${i}`)) {
          reported.add(`${first}:${i}`);
          const [config, tag] = JSON.parse(pair) as [string, string];
          issues.push(
            `Groups #${first + 1} and #${i + 1} both run config "${config}" with tag "${tag}": SbatchMan skips the second one without a message. Give each group its own tag.`,
          );
        }
      }
    }

    const defined = new Set(effectiveVars(campaign, group).map((v) => v.name.trim()));
    const referenced = tokenNames([group.tag, group.preset, JSON.stringify(group.config)]);
    const unknown = [...referenced].filter(([n, plain]) => plain && !defined.has(n));
    for (const [n] of unknown) issues.push(`${where}: {${n}} is not a defined variable.`);
    if (unknown.length) return; // the experiment can't be checked with unresolved tokens

    issues.push(...groupExperimentIssues(campaign, group, where, catalog));
  });
  return issues;
}

/** The "SbatchMan preset" field's placeholder and hint: what name goes there and where it
 * comes from (docs/using/sbatchman-integration.md). */
export const PRESET_PLACEHOLDER = "crab_run";
export const PRESET_HINT =
  "The SbatchMan config this group runs with: its name in your SbatchMan configs file, for example crab_run_{nodes}. See docs/using/sbatchman-integration.md.";
