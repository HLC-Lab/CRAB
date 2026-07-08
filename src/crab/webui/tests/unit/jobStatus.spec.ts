import { describe, expect, it } from "vitest";
import {
  failedExperimentNames,
  isFailureState,
  isTerminal,
  runFailureNote,
  stateClass,
} from "@/lib/jobStatus";

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

describe("runFailureNote (plan 081)", () => {
  it("formats N/M when some runs failed", () => {
    expect(runFailureNote({ total_runs: "10", failed_runs: "3" })).toBe("3/10 runs failed");
  });

  it("returns null when no runs failed", () => {
    expect(runFailureNote({ total_runs: "10", failed_runs: "0" })).toBeNull();
  });

  it("returns null for a pre-plan-081 metadata.csv (both fields empty)", () => {
    expect(runFailureNote({ total_runs: "", failed_runs: "" })).toBeNull();
  });

  it("returns null when the fields are entirely absent", () => {
    expect(runFailureNote({})).toBeNull();
  });

  it("returns null for non-numeric garbage rather than throwing", () => {
    expect(runFailureNote({ total_runs: "n/a", failed_runs: "n/a" })).toBeNull();
  });
});
