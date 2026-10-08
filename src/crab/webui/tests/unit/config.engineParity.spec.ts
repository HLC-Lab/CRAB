/**
 * Values the engine accepts that the editor must keep meaning-for-meaning. Expected values come
 * from the engine source cited in each describe, not from config.ts.
 */
import { describe, expect, it } from "vitest";

import {
  fromAllocation,
  fromConfig,
  fromSbatch,
  toConfig,
  validateAllocation,
  validateSbatch,
} from "@/lib/config";

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- engine JSON is untyped here
type AnyObj = Record<string, any>;

const oneApp = (app: AnyObj, global: AnyObj = {}): AnyObj => ({
  global_options: { numnodes: "4", ...global },
  experiments: { ex1: { apps: { 0: { path: "a.py", ...app } } } },
});

describe("text booleans (core/config_checks.py parse_bool: 'true'/'false' in any case)", () => {
  it("keeps convergeall and retain_files false when they arrive as text", () => {
    const round = toConfig(fromConfig(oneApp({}, { convergeall: "false", retain_files: "False" })));
    expect(round.global_options.convergeall).toBe(false);
    expect(round.global_options.retain_files).toBe(false);
  });

  it("keeps text true as true", () => {
    const round = toConfig(fromConfig(oneApp({}, { convergeall: "TRUE", retain_files: "true" })));
    expect(round.global_options.convergeall).toBe(true);
    expect(round.global_options.retain_files).toBe(true);
  });
});

describe("text collect (core/experiment/runner.py reads collect with parse_bool)", () => {
  it("keeps an app measured when collect arrives as text", () => {
    const round = toConfig(fromConfig(oneApp({ collect: "True" })));
    expect(round.experiments.ex1.apps[0].collect).toBe(true);
  });
});

describe("placeholder tokens in a legacy split (SbatchMan substitutes {name} before the engine)", () => {
  it("keeps tokens as group shares, in order, next to numbers", () => {
    const config = oneApp({}, { allocation: { mode: "linear", split: ["{a}", 50] } });
    config.experiments.ex1.apps[1] = { path: "b.py" };
    const round = toConfig(fromConfig(config));
    expect(round.global_options.allocation.partitions).toEqual({
      group_1: { share: "{a}" },
      group_2: { share: 50 },
    });
  });
});

describe("placeholder tokens pass validation where they are saved (toAllocation keeps them)", () => {
  it("accepts {name} tokens in group shares", () => {
    const a = fromAllocation({
      mode: "linear",
      partitions: { v: { share: "{s}" }, x: { share: "{t}" } },
    });
    expect(validateAllocation(a).issues).toEqual([]);
  });

  it("accepts {name} tokens in a by-app split, stride and seed", () => {
    const split = fromAllocation({ mode: "linear", split: ["{a}", 50] });
    split.by = "app";
    split.split = "{a}, 50";
    const stride = fromAllocation({ mode: "interleaved", stride: "{k}" });
    const seed = fromAllocation({ mode: "random", seed: "{r}" });
    expect([split, stride, seed].flatMap((a) => validateAllocation(a).issues)).toEqual([]);
  });

  it("still rejects text that is neither a number nor a token", () => {
    const a = fromAllocation({ mode: "linear", partitions: { v: { share: "half" } } });
    expect(validateAllocation(a).issues).toEqual(['node group "v": share must be a number.']);
  });
});

describe("sbatch warnings follow the engine's directive merge (core/engine.py protected_defaults)", () => {
  const warn = (lines: string[]) => validateSbatch(fromSbatch(lines));

  it("warns that -n is ignored, like --nodes and -N", () => {
    expect(warn(["-n 4"])).toEqual([
      'Slurm directive "-n" is computed by CRAB from nodes/ppn and will be ignored.',
    ]);
    expect(warn(["--nodes=2", "-N 2"])).toHaveLength(2);
  });

  it("warns on the short log flags -o and -e like --output and --error", () => {
    expect(warn(["-o out.log", "-e err.log"])).toEqual([
      'Slurm directive "-o" overrides CRAB\'s log redirection (allowed, but be aware).',
      'Slurm directive "-e" overrides CRAB\'s log redirection (allowed, but be aware).',
    ]);
  });

  it("leaves directives the engine passes through alone", () => {
    expect(warn(["--exclusive", "-J myjob", "--time=00:10:00"])).toEqual([]);
  });
});
