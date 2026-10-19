/**
 * The write block's state on the sbatchman store: the one shared cluster
 * (destination), the staging path the backend computes for it, and why Write
 * files is disabled. Mocks the API client, the true I/O boundary.
 */
import { createPinia, setActivePinia } from "pinia";
import { nextTick } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: {
    sbatchman: {
      write: vi.fn(),
      stagingPath: vi.fn(),
      campaigns: {},
    },
  },
  ApiError: class ApiError extends Error {},
}));

import { api } from "@/api/client";
import { emptyApp } from "@/lib/config";
import { useSbatchmanStore } from "@/stores/sbatchman";

const stagingMock = vi.mocked(api.sbatchman.stagingPath);

/** Let the store's watcher run and its pending promises settle. */
async function settle(): Promise<void> {
  await nextTick();
  await Promise.resolve();
  await Promise.resolve();
}

function makeWritable(store: ReturnType<typeof useSbatchmanStore>): void {
  const g = store.groups[0];
  g.tag = "pilot";
  g.preset = "crab_run";
  g.draft.numnodes = "2";
  const app = emptyApp();
  app.path = "blink/alltoall_b.py";
  g.draft.experiments[0].apps.push(app);
}

beforeEach(() => {
  setActivePinia(createPinia());
  stagingMock.mockReset();
});

describe("staging path", () => {
  it("asks no one while no cluster is chosen", async () => {
    const store = useSbatchmanStore();
    store.name = "Pilot A2A";
    await settle();

    expect(stagingMock).not.toHaveBeenCalled();
    expect(store.stagingPath).toBe("");
  });

  it("is fetched for the chosen cluster and the campaign name", async () => {
    stagingMock.mockResolvedValue({ path: "~/CRAB/.web_staging/pilot-a2a.yaml" });
    const store = useSbatchmanStore();
    store.name = "Pilot A2A";
    store.destination = "leonardo";
    await settle();

    expect(stagingMock).toHaveBeenCalledWith("leonardo", "Pilot A2A");
    expect(store.stagingPath).toBe("~/CRAB/.web_staging/pilot-a2a.yaml");
  });

  it("is fetched again when the name changes", async () => {
    stagingMock.mockResolvedValueOnce({ path: "~/CRAB/.web_staging/a.yaml" });
    stagingMock.mockResolvedValueOnce({ path: "~/CRAB/.web_staging/b.yaml" });
    const store = useSbatchmanStore();
    store.destination = "leonardo";
    store.name = "a";
    await settle();
    store.name = "b";
    await settle();

    expect(stagingMock).toHaveBeenLastCalledWith("leonardo", "b");
    expect(store.stagingPath).toBe("~/CRAB/.web_staging/b.yaml");
  });

  it("is cleared when the cluster is cleared", async () => {
    stagingMock.mockResolvedValue({ path: "~/CRAB/.web_staging/campaign.yaml" });
    const store = useSbatchmanStore();
    store.destination = "leonardo";
    await settle();
    store.destination = "";
    await settle();

    expect(store.stagingPath).toBe("");
  });

  it("ignores a slow answer for a cluster that is no longer chosen", async () => {
    let resolveOld: (v: { path: string }) => void = () => {};
    stagingMock.mockReturnValueOnce(new Promise((r) => (resolveOld = r)));
    stagingMock.mockResolvedValueOnce({ path: "/lumi/campaign.yaml" });
    const store = useSbatchmanStore();
    store.destination = "leonardo";
    await settle();
    store.destination = "lumi";
    await settle();
    resolveOld({ path: "/leonardo/campaign.yaml" });
    await settle();

    expect(store.stagingPath).toBe("/lumi/campaign.yaml");
  });

  it("is empty, not an error banner, when the lookup fails", async () => {
    stagingMock.mockRejectedValue(new Error("boom"));
    const store = useSbatchmanStore();
    store.destination = "leonardo";
    await settle();

    expect(store.stagingPath).toBe("");
    expect(store.error).toBeNull();
  });
});

describe("shared cluster choice", () => {
  it("syncDestination picks the first connected cluster when none is chosen", () => {
    const store = useSbatchmanStore();
    store.syncDestination(["leonardo", "lumi"]);
    expect(store.destination).toBe("leonardo");
  });

  it("syncDestination keeps a choice that is still connected", () => {
    const store = useSbatchmanStore();
    store.destination = "lumi";
    store.syncDestination(["leonardo", "lumi"]);
    expect(store.destination).toBe("lumi");
  });

  it("syncDestination moves off a cluster that disconnected", () => {
    const store = useSbatchmanStore();
    store.destination = "lumi";
    store.syncDestination(["leonardo"]);
    expect(store.destination).toBe("leonardo");
  });

  it("syncDestination clears the choice when nothing is connected", () => {
    const store = useSbatchmanStore();
    store.destination = "lumi";
    store.syncDestination([]);
    expect(store.destination).toBe("");
  });
});

describe("write block", () => {
  it("blocks on the problem count while the campaign has issues", () => {
    const store = useSbatchmanStore();
    store.destination = "leonardo";

    expect(store.issues.length).toBeGreaterThan(0);
    expect(store.writeBlock).toEqual({ kind: "issues", count: store.issues.length });
    expect(store.canWrite).toBe(false);
  });

  it("blocks on the missing cluster once the campaign is clean", () => {
    const store = useSbatchmanStore();
    makeWritable(store);

    expect(store.issues).toEqual([]);
    expect(store.writeBlock).toEqual({ kind: "no-cluster" });
    expect(store.canWrite).toBe(false);
  });

  it("does not block when there is a cluster and no problem", () => {
    const store = useSbatchmanStore();
    makeWritable(store);
    store.destination = "leonardo";

    expect(store.writeBlock).toBeNull();
    expect(store.canWrite).toBe(true);
  });

  it("cannot write while busy", () => {
    const store = useSbatchmanStore();
    makeWritable(store);
    store.destination = "leonardo";
    store.busy = true;

    expect(store.writeBlock).toBeNull();
    expect(store.canWrite).toBe(false);
  });
});
