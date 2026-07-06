import { describe, expect, it } from "vitest";
import { failedExperimentNames, isFailureState, isTerminal, stateClass } from "@/lib/jobStatus";

describe("isFailureState", () => {
  it("is true for danger-classed states", () => {
    for (const s of ["FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL", "DEADLINE"]) {
      expect(isFailureState(s)).toBe(true);
      expect(stateClass(s)).toBe("danger");
    }
  });

  it("is false for a deliberate stop, a success, or a live state", () => {
    expect(isFailureState("CANCELLED")).toBe(false);
    expect(isFailureState("REVOKED")).toBe(false);
    expect(isFailureState("COMPLETED")).toBe(false);
    expect(isFailureState("RUNNING")).toBe(false);
  });
});

describe("isTerminal / stateClass sanity", () => {
  it("every failure state is terminal", () => {
    expect(isFailureState("FAILED") && isTerminal("FAILED")).toBe(true);
  });
});

describe("failedExperimentNames (plan 076 quick rerun)", () => {
  it("returns only the names of failing experiments", () => {
    const names = failedExperimentNames([
      { status: "FAILED", experiment_name: "01_baseline" },
      { status: "COMPLETED", experiment_name: "02_variant" },
      { status: "TIMEOUT", experiment_name: "03_stress" },
    ]);
    expect(names).toEqual(["01_baseline", "03_stress"]);
  });

  it("returns an empty list when nothing failed", () => {
    expect(
      failedExperimentNames([{ status: "COMPLETED", experiment_name: "01_baseline" }]),
    ).toEqual([]);
  });
});
