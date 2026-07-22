// SbatchMan campaign composer (plan 084). PURE module (no DOM/fetch) so the unit
// suite can import it, mirroring lib/config.ts.
//
// It turns a CampaignSpec into a SbatchMan jobs YAML (`sbatchman launch -f`). Each
// group becomes one `jobs:` entry whose `preprocess` writes environment.json +
// config.json into $SBATCHMAN_JOB_DIR and whose `command` runs the CRAB worker
// inside the allocation SbatchMan obtained. The cartesian expansion of `variables`
// and the `{var}` substitution are done by SbatchMan at launch time (core/launcher.py,
// core/variables.py); this module only PREVIEWS the product and emits the templates.
//
// The embedded CRAB JSON keeps its `{var}` placeholders literal — SbatchMan's own
// substitution regex ignores `{` followed by whitespace, so JSON object braces are
// safe; only `{token}` (no spaces) is substituted.

import type { CrabConfig } from "@/api/types";

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
  /** Remote CRAB checkout dir -> environment.json CRAB_ROOT (resolved, no __CWD__). */
  crabRoot: string;
  /** CRAB_SYSTEM recorded in environment.json. */
  system: string;
  /** Extra environment entries merged into environment.json (optional). */
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

/** Double-quoted YAML scalar (needed for values starting with `{`, which YAML
 * would otherwise read as a flow mapping). */
function yamlStr(s: string): string {
  return `"${s.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

/** Single-quoted YAML scalar: preserves `$` and `"` literally for the command. */
function yamlSingle(s: string): string {
  return `'${s.replace(/'/g, "''")}'`;
}

/** A flow-list value: numbers (and numeric strings) bare, other strings quoted. */
function yamlListItem(v: string | number): string {
  if (typeof v === "number") return String(v);
  return /^-?\d+(\.\d+)?$/.test(v) ? v : yamlStr(v);
}

function yamlList(values: Array<string | number>): string {
  return `[${values.map(yamlListItem).join(", ")}]`;
}

/** The bash heredoc lines (at column 0) that write the two JSON files. The caller
 * indents the whole block; YAML dedents it back so the heredoc bodies and their
 * `JSON` terminators land at column 0 for bash. */
function preprocessLines(campaign: SbatchmanCampaign, group: SbatchmanGroup): string[] {
  const environment = {
    CRAB_ROOT: campaign.crabRoot,
    CRAB_SYSTEM: campaign.system,
    ...campaign.env,
  };
  const heredoc = (relPath: string, json: string): string[] => [
    `cat > "${WORKDIR}/${relPath}" <<'JSON'`,
    ...json.split("\n"),
    "JSON",
  ];
  return [
    `mkdir -p "${WORKDIR}"`,
    ...heredoc("environment.json", JSON.stringify(environment, null, 2)),
    ...heredoc("config.json", JSON.stringify(group.config, null, 2)),
  ];
}

/** Compose the full SbatchMan jobs YAML for a campaign. */
export function composeCampaignYaml(campaign: SbatchmanCampaign): string {
  const lines: string[] = [];
  lines.push(`configs: ${yamlStr(campaign.configsPath)}`);

  if (campaign.variables.length) {
    lines.push("variables:");
    for (const v of campaign.variables) lines.push(`  ${v.name}: ${yamlList(v.values)}`);
  }

  lines.push("jobs:");
  for (const group of campaign.groups) {
    lines.push(`  - config: ${yamlStr(group.preset)}`);
    lines.push(`    tag: ${yamlStr(group.tag)}`);
    if (group.variables.length) {
      lines.push("    variables:");
      for (const v of group.variables) lines.push(`      ${v.name}: ${yamlList(v.values)}`);
    }
    lines.push("    preprocess: |");
    // 6-space block indent; YAML strips it so heredoc bodies reach bash at col 0.
    for (const line of preprocessLines(campaign, group)) lines.push(`      ${line}`);
    lines.push(`    command: ${yamlSingle(`crab worker --workdir "${WORKDIR}"`)}`);
  }

  return lines.join("\n") + "\n";
}
