import { describe, expect, it } from "vitest";
import { presetNotes } from "@/lib/presetFit";
import type { CrabConfig } from "@/api/types";

const SLURM = { name: "cluster", description: "", scheduler: "slurm", launcher: "srun" } as const;
const LOCAL = {
  name: "ws",
  description: "",
  scheduler: "local",
  launcher: "mpirun",
  hosts: ["ws1", "ws2"],
} as const;

function config(
  globalOptions: Record<string, unknown> = {},
  localOptions: Record<string, unknown> = {},
): CrabConfig {
  return {
    global_options: globalOptions,
    experiments: { exp1: { local_options: localOptions, apps: { "0": { path: "a.py" } } } },
  };
}

describe("presetNotes", () => {
  it("has no note for a Slurm preset running srun", () => {
    expect(presetNotes(config({ numnodes: 4, ppn: 8 }, { launcher: "srun" }), SLURM)).toEqual([]);
  });

  it("has no note for a local preset that fits", () => {
    expect(presetNotes(config({ numnodes: 2, ppn: 4 }), LOCAL)).toEqual([]);
  });

  it("refuses the srun launcher on a local preset", () => {
    expect(presetNotes(config({ launcher: "srun" }), LOCAL)).toEqual([
      { level: "error", text: "experiment exp1: the srun launcher needs a Slurm preset" },
    ]);
  });

  it("reads an experiment-scoped launcher override", () => {
    const cfg: CrabConfig = {
      global_options: {},
      experiments: {
        a: { apps: { "0": { path: "x" } } },
        b: { local_options: { launcher: "srun" }, apps: { "0": { path: "x" } } },
      },
    };
    expect(presetNotes(cfg, LOCAL)).toEqual([
      { level: "error", text: "experiment b: the srun launcher needs a Slurm preset" },
    ]);
  });

  it("refuses the direct launcher with more than one process", () => {
    const text =
      "experiment exp1: the direct launcher runs a single process; use ppn 1 and numnodes 1, " +
      "or the mpirun launcher";
    expect(presetNotes(config({ ppn: 4 }, { launcher: "direct" }), LOCAL)).toEqual([
      { level: "error", text },
    ]);
    expect(presetNotes(config({ numnodes: 2 }, { launcher: "direct" }), LOCAL)).toEqual([
      { level: "error", text },
    ]);
  });

  it("uses the preset's launcher when the config names none", () => {
    const direct = { ...LOCAL, launcher: "direct" } as const;
    expect(presetNotes(config({ ppn: 2 }), direct)).toHaveLength(1);
    expect(presetNotes(config({ ppn: 1, numnodes: 1 }), direct)).toEqual([]);
  });

  it("refuses more nodes than a local preset has hosts", () => {
    expect(presetNotes(config({ numnodes: 3 }), LOCAL)).toEqual([
      { level: "error", text: "experiment exp1: numnodes 3 is more than the preset's 2 host(s)" },
    ]);
  });

  it("joins the problems of one experiment like the engine", () => {
    const notes = presetNotes(config({ numnodes: 3 }, { launcher: "srun" }), LOCAL);
    expect(notes).toEqual([
      {
        level: "error",
        text:
          "experiment exp1: the srun launcher needs a Slurm preset; " +
          "numnodes 3 is more than the preset's 2 host(s)",
      },
    ]);
  });

  it("warns once about sbatch_directives on a local preset", () => {
    const cfg = config({ sbatch_directives: ["--time=1:00:00"] });
    cfg.experiments.exp2 = { apps: { "0": { path: "x" } } };
    expect(presetNotes(cfg, LOCAL)).toEqual([
      { level: "warn", text: "sbatch_directives are ignored by the local scheduler" },
    ]);
    expect(presetNotes(config({ sbatch_directives: [] }), LOCAL)).toEqual([]);
    expect(presetNotes(config({ sbatch_directives: ["--time=1:00:00"] }), SLURM)).toEqual([]);
  });

  it("skips a check whose value is a {token}", () => {
    expect(
      presetNotes(config({ numnodes: "{n}", ppn: "{p}" }, { launcher: "direct" }), LOCAL),
    ).toEqual([]);
    expect(presetNotes(config({ numnodes: "{n}" }), LOCAL)).toEqual([]);
    expect(presetNotes(config({}, { launcher: "{l}" }), LOCAL)).toEqual([]);
  });

  it("returns nothing for a preset with an error", () => {
    const broken = { name: "bad", description: "", error: "no such file" };
    expect(presetNotes(config({ numnodes: 9, launcher: "srun" }), broken)).toEqual([]);
  });

  it("returns nothing for an old-cluster preset without the fields", () => {
    const old = { name: "old", description: "" };
    expect(presetNotes(config({ numnodes: 9, launcher: "srun" }), old)).toEqual([]);
  });

  it("copes with a config that has no experiments", () => {
    const bare = { global_options: {}, experiments: {} };
    expect(presetNotes(bare, LOCAL)).toEqual([]);
  });
});
