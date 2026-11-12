// Wording of the SbatchMan editor (ADR-045): the thing a group runs with is a SbatchMan
// config, never a CRAB preset, and problems are called "problems". Reads the sources as
// text, so a user-visible string that says "preset" fails here.
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { PRESET_HINT, PRESET_PLACEHOLDER, validateCampaign } from "@/lib/sbatchman";

const src = join(dirname(fileURLToPath(import.meta.url)), "../../src");
const componentDir = join(src, "components/sbatchman");

/** The text of a .vue file's <template> block. */
function template(path: string): string {
  const text = readFileSync(path, "utf8");
  const start = text.indexOf("<template>");
  const end = text.lastIndexOf("</template>");
  return text.slice(start, end);
}

const vueFiles = [
  ...readdirSync(componentDir)
    .filter((f) => f.endsWith(".vue"))
    .map((f) => join(componentDir, f)),
  join(src, "views/SbatchmanView.vue"),
];

describe("SbatchMan editor copy", () => {
  it("has the files to check", () => {
    expect(vueFiles.length).toBeGreaterThan(5);
  });

  for (const file of vueFiles) {
    it(`${file.slice(src.length + 1)}: no template text says preset`, () => {
      // Code is not text: drop {{ expressions }} and bound attributes (:preset="group.preset"); the
      // placeholder and hint constants are checked as strings below.
      const text = template(file)
        .replace(/\{\{[\s\S]*?\}\}/g, "")
        .replace(/\s(?:[:@]|v-)[\w.:-]*="[^"]*"/g, "");
      expect(text).not.toMatch(/preset/i);
    });
  }

  it("lib exports and validation messages do not say preset", () => {
    const config = {
      global_options: {},
      experiments: {
        e: { apps: { "0": { path: "x/y.py", args: "", collect: true, start: "0", end: "" } } },
      },
    };
    const broken = {
      variables: [{ name: "v", values: [1, 1] }],
      groups: [
        { tag: "", preset: "", variables: [], config },
        { tag: "t", preset: "", variables: [], config },
        { tag: "t", preset: "", variables: [], config },
      ],
    };
    const messages = validateCampaign(broken);
    expect(messages.length).toBeGreaterThan(0);
    for (const text of [PRESET_HINT, PRESET_PLACEHOLDER, ...messages]) {
      expect(text).not.toMatch(/preset/i);
    }
  });

  it("the hint names the integration guide path", () => {
    expect(PRESET_HINT).toContain("docs/using/sbatchman-integration.md");
    expect(PRESET_HINT).toContain("SbatchMan config");
  });

  it("the campaign note and the group label say config", () => {
    expect(template(join(componentDir, "GroupBasics.vue"))).toContain("SbatchMan config");
    expect(template(join(componentDir, "CampaignBar.vue"))).toContain("SbatchMan config");
  });

  it("CampaignPreview counts problems, not things", () => {
    const text = template(join(componentDir, "CampaignPreview.vue"));
    expect(text).toContain("problem");
    expect(text).not.toMatch(/things? to fix|thing\{\{/);
  });

  it("OptionsFields drops the preset wording in the campaign editor", () => {
    const text = readFileSync(join(src, "components/OptionsFields.vue"), "utf8");
    expect(text).toContain("isSbatchmanMode()");
    expect(text).toContain('noPreset ? "(default)" : "(preset default)"');
    // Every "preset" left in the template sits behind the flag.
    expect(template(join(src, "components/OptionsFields.vue"))).not.toMatch(/preset/i);
  });
});
