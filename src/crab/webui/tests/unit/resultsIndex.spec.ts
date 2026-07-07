import { describe, expect, it } from "vitest";
import { describeStaleness, sortEntries } from "@/lib/resultsIndex";
import type { ResultsJobEntry } from "@/api/types";

function entry(overrides: Partial<ResultsJobEntry>): ResultsJobEntry {
  return {
    cluster: "leonardo",
    system: "leonardo",
    job_basename: "job1",
    connected: true,
    status: "COMPLETED",
    record_id: null,
    job_id: null,
    submitted_at: null,
    cached: false,
    cached_bytes: null,
    possibly_stale: true,
    ...overrides,
  };
}

describe("describeStaleness", () => {
  it("reports not fetched when nothing is cached", () => {
    expect(describeStaleness(entry({ cached: false }))).toEqual({
      label: "Not fetched yet",
      tone: "muted",
    });
  });

  it("reports possibly stale when cached but flagged stale", () => {
    expect(describeStaleness(entry({ cached: true, possibly_stale: true }))).toEqual({
      label: "Possibly stale",
      tone: "warn",
    });
  });

  it("reports up to date when cached and not stale", () => {
    expect(describeStaleness(entry({ cached: true, possibly_stale: false }))).toEqual({
      label: "Up to date",
      tone: "ok",
    });
  });
});

describe("sortEntries", () => {
  it("orders by submitted_at, most recent first", () => {
    const older = entry({ job_basename: "older", submitted_at: "2026-01-01T00:00:00Z" });
    const newer = entry({ job_basename: "newer", submitted_at: "2026-06-01T00:00:00Z" });
    expect(sortEntries([older, newer]).map((e) => e.job_basename)).toEqual(["newer", "older"]);
  });

  it("sorts entries with an unknown submitted_at last, in their original order", () => {
    const known = entry({ job_basename: "known", submitted_at: "2026-01-01T00:00:00Z" });
    const unknownA = entry({ job_basename: "unknownA", submitted_at: null });
    const unknownB = entry({ job_basename: "unknownB", submitted_at: null });
    expect(sortEntries([unknownA, known, unknownB]).map((e) => e.job_basename)).toEqual([
      "known",
      "unknownA",
      "unknownB",
    ]);
  });

  it("does not mutate the input array", () => {
    const list = [entry({ job_basename: "a", submitted_at: "2026-01-01T00:00:00Z" })];
    const sorted = sortEntries(list);
    expect(sorted).not.toBe(list);
  });
});
