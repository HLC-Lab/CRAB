import { describe, expect, it } from "vitest";
import { describeStaleness, filterEntries, sortEntries } from "@/lib/resultsIndex";
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

describe("filterEntries", () => {
  const NO_FILTER = { search: "", clusters: new Set<string>(), staleness: new Set<string>() };
  const entries = [
    entry({ job_basename: "msgsize-sweep", cluster: "leonardo", cached: false }),
    entry({
      job_basename: "scaling-study",
      cluster: "leonardo",
      cached: true,
      possibly_stale: true,
    }),
    entry({ job_basename: "coscheduling", cluster: "m100", cached: true, possibly_stale: false }),
  ];

  it("returns everything when no filter is set", () => {
    expect(filterEntries(entries, NO_FILTER)).toEqual(entries);
  });

  it("matches job_basename by substring, case-insensitively", () => {
    const result = filterEntries(entries, { ...NO_FILTER, search: "SWEEP" });
    expect(result.map((e) => e.job_basename)).toEqual(["msgsize-sweep"]);
  });

  it("filters by cluster", () => {
    const result = filterEntries(entries, { ...NO_FILTER, clusters: new Set(["m100"]) });
    expect(result.map((e) => e.job_basename)).toEqual(["coscheduling"]);
  });

  it("filters by staleness label", () => {
    const result = filterEntries(entries, {
      ...NO_FILTER,
      staleness: new Set(["Not fetched yet"]),
    });
    expect(result.map((e) => e.job_basename)).toEqual(["msgsize-sweep"]);
  });

  it("composes all three filters with AND, not OR", () => {
    const result = filterEntries(entries, {
      search: "s", // matches msgsize-sweep and scaling-study
      clusters: new Set(["leonardo"]), // excludes coscheduling anyway
      staleness: new Set(["Possibly stale"]), // only scaling-study
    });
    expect(result.map((e) => e.job_basename)).toEqual(["scaling-study"]);
  });
});
