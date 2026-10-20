import { describe, expect, it } from "vitest";
import { jobBasenameFromDataDir, jobBasenameFromRelativePath, resultsKey } from "@/lib/jobKey";

describe("jobBasenameFromDataDir", () => {
  it("returns the last path segment", () => {
    expect(jobBasenameFromDataDir("/remote/data/leonardo/demo_job")).toBe("demo_job");
  });

  it("is empty for an empty path", () => {
    expect(jobBasenameFromDataDir("")).toBe("");
  });

  it("ignores a trailing slash", () => {
    expect(jobBasenameFromDataDir("/remote/data/demo_job/")).toBe("demo_job");
  });
});

describe("jobBasenameFromRelativePath", () => {
  it("returns the first real path segment, skipping a leading dot", () => {
    expect(jobBasenameFromRelativePath("./demo_job/experiment_1")).toBe("demo_job");
  });

  it("is empty for an empty path", () => {
    expect(jobBasenameFromRelativePath("")).toBe("");
  });

  it("is empty for just a dot", () => {
    expect(jobBasenameFromRelativePath(".")).toBe("");
  });
});

describe("resultsKey", () => {
  it("joins cluster/system/jobBasename", () => {
    expect(resultsKey("leonardo", "leonardo", "demo_job")).toBe("leonardo/leonardo/demo_job");
  });
});
