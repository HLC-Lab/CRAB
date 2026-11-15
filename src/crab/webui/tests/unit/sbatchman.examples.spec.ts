/**
 * The campaign generator against the jobs files shipped in examples/sbatchman.
 *
 * Each example is taken apart (its `configs:`, variables, jobs and the config.json its
 * heredoc writes), the config.json is loaded into the editor model with `fromConfig`, and
 * `composeCampaignYaml` writes it again. What the generator writes must match the example
 * in every key except comments: that is what "the dashboard writes this" means.
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import yaml from "js-yaml";
import { describe, expect, it } from "vitest";
import type { CrabConfig } from "@/api/types";
import { fromConfig, toConfig } from "@/lib/config";
import {
  composeCampaignYaml,
  validateCampaign,
  type SbatchmanCampaign,
  type SbatchmanVar,
} from "@/lib/sbatchman";

const examplesDir = join(dirname(fileURLToPath(import.meta.url)), "../../../../../examples/sbatchman");

interface ParsedJob {
  config: string;
  tag: string;
  command: string;
  variables?: Record<string, unknown[]>;
  preprocess: string;
}
interface ParsedDoc {
  configs?: string;
  variables?: Record<string, unknown[]>;
  jobs: ParsedJob[];
}

const files = readdirSync(examplesDir)
  .filter((f) => f.endsWith(".yaml") && !f.startsWith("configs."))
  .sort();

const vars = (v: Record<string, unknown[]> | undefined): SbatchmanVar[] =>
  Object.entries(v ?? {}).map(([name, values]) => ({
    name,
    values: values as Array<string | number>,
  }));

/** The opener line and the JSON body of the config.json heredoc in a preprocess script. */
function heredoc(preprocess: string): { opener: string; json: string } {
  const lines = preprocess.split("\n");
  const start = lines.findIndex((l) => l.startsWith('cat > "$SBATCHMAN_JOB_DIR/config.json"'));
  expect(start).toBeGreaterThanOrEqual(0);
  const end = lines.indexOf("JSON", start);
  expect(end).toBeGreaterThan(start);
  return { opener: lines[start], json: lines.slice(start + 1, end).join("\n") };
}

/** Replace `{name}` tokens the way SbatchMan does, with the first value of each variable. */
function expand(text: string, values: Record<string, string>): string {
  return text.replace(/\{(\w+)\}/g, (m, name) => (name in values ? values[name] : m));
}

/** Rebuild an example through the editor model and the generator. */
function regenerate(doc: ParsedDoc): ParsedDoc {
  const campaign: SbatchmanCampaign = {
    configsFile: doc.configs,
    variables: vars(doc.variables),
    groups: doc.jobs.map((job) => ({
      tag: job.tag,
      configName: job.config,
      variables: vars(job.variables),
      config: toConfig(fromConfig(JSON.parse(heredoc(job.preprocess).json) as CrabConfig)),
    })),
  };
  expect(validateCampaign(campaign)).toEqual([]);
  return yaml.load(composeCampaignYaml(campaign)) as ParsedDoc;
}

describe("examples/sbatchman", () => {
  it("has the six campaign files", () => {
    expect(files).toHaveLength(6);
  });

  for (const file of files) {
    describe(file, () => {
      const original = yaml.load(readFileSync(join(examplesDir, file), "utf8")) as ParsedDoc;
      const written = regenerate(original);

      it("keeps configs, variables and job keys", () => {
        expect(written.configs).toBe(original.configs);
        expect(written.variables).toEqual(original.variables);
        expect(written.jobs.map((j) => [j.config, j.tag, j.command, j.variables])).toEqual(
          original.jobs.map((j) => [j.config, j.tag, j.command, j.variables]),
        );
      });

      it("writes the same config.json for every job", () => {
        const first = Object.fromEntries(
          Object.entries(original.variables ?? {}).map(([k, v]) => [k, String(v[0])]),
        );
        original.jobs.forEach((job, i) => {
          const a = JSON.parse(expand(heredoc(job.preprocess).json, first));
          const b = JSON.parse(expand(heredoc(written.jobs[i].preprocess).json, first));
          expect(b).toEqual(a);
        });
      });

      it("quotes the heredoc only where the example does", () => {
        original.jobs.forEach((job, i) => {
          expect(heredoc(written.jobs[i].preprocess).opener).toBe(heredoc(job.preprocess).opener);
        });
      });
    });
  }
});
