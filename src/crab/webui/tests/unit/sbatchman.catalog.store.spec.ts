/**
 * The campaign preview checks wrapper paths against the write target cluster's catalog.
 * Mocks the API client, the true I/O boundary; the catalog store is the real one.
 */
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: {
    sbatchman: { write: vi.fn(), stagingPath: vi.fn(), campaigns: {} },
    remotes: { benchmarks: vi.fn() },
  },
  ApiError: class ApiError extends Error {},
}));

import { api } from "@/api/client";
import type { BenchmarksResult } from "@/api/types";
import { emptyApp } from "@/lib/config";
import { useSbatchmanStore } from "@/stores/sbatchman";

const benchmarksMock = vi.mocked(api.remotes.benchmarks);
const stagingMock = vi.mocked(api.sbatchman.stagingPath);

async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) await nextTick();
}

function catalogOf(...relpaths: string[]): BenchmarksResult {
  return { wrappers: relpaths.map((relpath) => ({ relpath })) } as unknown as BenchmarksResult;
}

function makeWritable(store: ReturnType<typeof useSbatchmanStore>, path: string): void {
  const g = store.groups[0];
  g.tag = "pilot";
  g.preset = "crab_run";
  g.draft.numnodes = "2";
  const app = emptyApp();
  app.path = path;
  g.draft.experiments[0].apps.push(app);
}

const wrapperIssues = (store: ReturnType<typeof useSbatchmanStore>) =>
  store.issues.filter((i) => i.includes("is not on"));

beforeEach(() => {
  setActivePinia(createPinia());
  benchmarksMock.mockReset();
  stagingMock.mockReset();
  stagingMock.mockResolvedValue({ path: "~/x.yaml" });
});

describe("wrapper check in the campaign preview", () => {
  it("lists a wrapper path the target cluster lacks and blocks Write files", async () => {
    benchmarksMock.mockResolvedValue(catalogOf("blink/alltoall/alltoall_b.py"));
    const store = useSbatchmanStore();
    makeWritable(store, "blink/a2a_b.py");
    store.destination = "leonardo";
    await settle();

    expect(benchmarksMock).toHaveBeenCalledWith("leonardo");
    const hits = wrapperIssues(store);
    expect(hits).toHaveLength(1);
    expect(hits[0]).toContain("blink/a2a_b.py");
    expect(hits[0]).toContain("leonardo");
    expect(store.canWrite).toBe(false);
  });

  it("lists nothing when the cluster has the wrapper", async () => {
    benchmarksMock.mockResolvedValue(catalogOf("blink/alltoall/alltoall_b.py"));
    const store = useSbatchmanStore();
    makeWritable(store, "blink/alltoall/alltoall_b.py");
    store.destination = "leonardo";
    await settle();

    expect(wrapperIssues(store)).toEqual([]);
    expect(store.canWrite).toBe(true);
  });

  it("loads nothing while no cluster is chosen", async () => {
    const store = useSbatchmanStore();
    makeWritable(store, "blink/a2a_b.py");
    await settle();

    expect(benchmarksMock).not.toHaveBeenCalled();
    expect(wrapperIssues(store)).toEqual([]);
  });

  it("loads nothing while no group has an app path", async () => {
    const store = useSbatchmanStore();
    store.destination = "leonardo";
    await settle();

    expect(benchmarksMock).not.toHaveBeenCalled();
  });

  it("re-checks against the new cluster when the target changes", async () => {
    benchmarksMock.mockImplementation(async (cluster: string) =>
      cluster === "leonardo" ? catalogOf("blink/a2a_b.py") : catalogOf("other/x.py"),
    );
    const store = useSbatchmanStore();
    makeWritable(store, "blink/a2a_b.py");
    store.destination = "leonardo";
    await settle();
    expect(wrapperIssues(store)).toEqual([]);

    store.destination = "marenostrum";
    await settle();
    expect(benchmarksMock).toHaveBeenCalledWith("marenostrum");
    expect(wrapperIssues(store)[0]).toContain("marenostrum");
  });
});
