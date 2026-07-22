/**
 * SbatchMan campaign composer (plan 084).
 *
 * The load-bearing property: the emitted `preprocess` is a YAML block scalar
 * whose heredoc bodies must reach bash at column 0 after YAML dedents the block.
 * We verify that with a REAL YAML parser (js-yaml), then simulate SbatchMan's
 * `{var}` substitution and confirm the embedded config.json is still valid JSON
 * (object braces survive; only `{token}` placeholders are replaced).
 */
import yaml from "js-yaml";
import { describe, expect, it } from "vitest";
import {
  campaignJobCount,
  composeCampaignYaml,
  groupJobCount,
  sampleTags,
  type SbatchmanCampaign,
} from "@/lib/sbatchman";

function campaign(): SbatchmanCampaign {
  return {
    configsPath: "graph500/configs.yaml",
    crabRoot: "/leonardo/home/user/CRAB",
    system: "leonardo",
    env: {},
    variables: [
      { name: "scale", values: [20, 24] },
      { name: "ef", values: [8, 16] },
    ],
    groups: [
      {
        tag: "g500_baseline_{scale}_{ef}_{nodes}",
        preset: "{nodes}_nodes",
        variables: [{ name: "nodes", values: [8, 16, 32] }],
        config: {
          global_options: { numnodes: "{nodes}" },
          experiments: {
            "g500_baseline_{scale}_{ef}_{nodes}": {
              apps: {
                "0": {
                  path: "graph500/g500.py",
                  args: "-scale {scale} -ef {ef}",
                  collect: true,
                  start: "0",
                  end: "",
                },
              },
            },
          },
        },
      },
    ],
  };
}

/** Replace `{name}` tokens the way SbatchMan does (object braces are untouched). */
function substituteAll(text: string, vars: Record<string, string>): string {
  return text.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? vars[name] : m));
}

/** Pull the body of a `cat > ".../<file>" <<'JSON' ... JSON` heredoc out of a
 * (already YAML-dedented) preprocess script. */
function heredocBody(preprocess: string, file: string): string {
  const lines = preprocess.split("\n");
  const start = lines.findIndex((l) => l.startsWith(`cat > "$SBATCHMAN_JOB_DIR/${file}" <<'JSON'`));
  expect(start).toBeGreaterThanOrEqual(0);
  const body: string[] = [];
  for (let i = start + 1; i < lines.length; i++) {
    if (lines[i] === "JSON") return body.join("\n");
    body.push(lines[i]);
  }
  throw new Error("unterminated heredoc");
}

describe("job-count preview", () => {
  it("is the product of used variables' value counts", () => {
    const c = campaign();
    expect(groupJobCount(c, c.groups[0])).toBe(2 * 2 * 3);
    expect(campaignJobCount(c)).toBe(12);
  });

  it("ignores variables the group does not reference", () => {
    const c = campaign();
    c.variables.push({ name: "unused", values: [1, 2, 3, 4, 5] });
    expect(groupJobCount(c, c.groups[0])).toBe(12); // unchanged
  });

  it("is 1 for a group with no variables", () => {
    const c = campaign();
    c.variables = [];
    c.groups[0].variables = [];
    c.groups[0].tag = "static_tag";
    expect(groupJobCount(c, c.groups[0])).toBe(1);
  });
});

describe("sampleTags", () => {
  it("substitutes the tag template over the cartesian product", () => {
    const c = campaign();
    const tags = sampleTags(c, c.groups[0], 100);
    expect(tags.length).toBe(12);
    expect(tags[0]).toBe("g500_baseline_20_8_8");
    expect(tags).toContain("g500_baseline_24_16_32");
    expect(tags.every((t) => !t.includes("{"))).toBe(true);
  });

  it("respects the limit", () => {
    const c = campaign();
    expect(sampleTags(c, c.groups[0], 3).length).toBe(3);
  });
});

describe("composeCampaignYaml", () => {
  it("emits YAML that parses to the expected structure", () => {
    const doc = yaml.load(composeCampaignYaml(campaign())) as any;
    expect(doc.configs).toBe("graph500/configs.yaml");
    expect(doc.variables.scale).toEqual([20, 24]);
    expect(doc.variables.ef).toEqual([8, 16]);
    expect(doc.jobs).toHaveLength(1);
    expect(doc.jobs[0].config).toBe("{nodes}_nodes");
    expect(doc.jobs[0].tag).toBe("g500_baseline_{scale}_{ef}_{nodes}");
    expect(doc.jobs[0].variables.nodes).toEqual([8, 16, 32]);
    expect(doc.jobs[0].command).toBe('crab worker --workdir "$SBATCHMAN_JOB_DIR"');
  });

  it("dedents the heredoc bodies to column 0 for bash", () => {
    const doc = yaml.load(composeCampaignYaml(campaign())) as any;
    const pre: string = doc.jobs[0].preprocess;
    // After YAML strips the block indent, these must start at column 0.
    expect(pre).toMatch(/^mkdir -p "\$SBATCHMAN_JOB_DIR"$/m);
    expect(pre).toMatch(/^cat > "\$SBATCHMAN_JOB_DIR\/config.json" <<'JSON'$/m);
    expect(pre).toMatch(/^JSON$/m);
  });

  it("keeps {var} placeholders literal, and the config survives substitution", () => {
    const doc = yaml.load(composeCampaignYaml(campaign())) as any;
    const pre: string = doc.jobs[0].preprocess;

    // Placeholders are literal in the emitted (unsubstituted) config.json body.
    const rawConfig = heredocBody(pre, "config.json");
    expect(rawConfig).toContain("-scale {scale} -ef {ef}");
    expect(rawConfig).toContain("g500_baseline_{scale}_{ef}_{nodes}");

    // Simulate SbatchMan expanding one combination, then bash running the heredoc.
    const vars = { scale: "20", ef: "8", nodes: "8" };
    const expanded = substituteAll(pre, vars);
    const cfg = JSON.parse(heredocBody(expanded, "config.json"));
    expect(cfg.experiments["g500_baseline_20_8_8"]).toBeDefined();
    expect(cfg.experiments["g500_baseline_20_8_8"].apps["0"].args).toBe("-scale 20 -ef 8");
    expect(cfg.global_options.numnodes).toBe("8");

    // environment.json carries a concrete CRAB_ROOT (no __CWD__).
    const env = JSON.parse(heredocBody(expanded, "environment.json"));
    expect(env.CRAB_ROOT).toBe("/leonardo/home/user/CRAB");
    expect(env.CRAB_SYSTEM).toBe("leonardo");
  });
});
