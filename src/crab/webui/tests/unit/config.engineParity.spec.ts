/**
 * Values the engine accepts that the editor must keep meaning-for-meaning. Expected values come
 * from the engine source cited in each describe, not from config.ts.
 */
import { describe, expect, it } from "vitest";

import { fromConfig, toConfig } from "@/lib/config";

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
