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

import { api, ApiError } from "@/api/client";
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
  app.path = "blink/alltoall/alltoall_b.py";
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

describe("write result", () => {
  const writeMock = vi.mocked(api.sbatchman.write);
  const result = { local_path: "/data/20260101T000000Z-pilot.yaml", remote_path: "/c/pilot.yaml" };

  function conflict(): Error {
    return Object.assign(new ApiError("A campaign file named pilot.yaml already exists."), {
      code: "conflict",
      status: 409,
    });
  }

  function writableStore() {
    const store = useSbatchmanStore();
    makeWritable(store);
    store.destination = "leonardo";
    return store;
  }

  beforeEach(() => {
    writeMock.mockReset();
    stagingMock.mockResolvedValue({ path: "~/CRAB/.web_staging/campaign.yaml" });
  });

  it("is kept after a successful write", async () => {
    writeMock.mockResolvedValue(result);
    const store = writableStore();

    expect(await store.write()).toBe(true);
    await settle();

    expect(store.lastWrite).toEqual(result);
    expect(store.needsOverwrite).toBe(false);
  });

  it("is cleared when the campaign YAML changes afterwards", async () => {
    writeMock.mockResolvedValue(result);
    const store = writableStore();
    await store.write();
    await settle();

    store.groups[0].draft.numnodes = "4";
    await settle();

    expect(store.lastWrite).toBeNull();
  });

  it("is cleared when the campaign is renamed", async () => {
    writeMock.mockResolvedValue(result);
    const store = writableStore();
    await store.write();
    await settle();

    store.name = "another name";
    await settle();

    expect(store.lastWrite).toBeNull();
  });

  it("is sent with the overwrite flag off by default", async () => {
    writeMock.mockResolvedValue(result);
    const store = writableStore();
    await store.write();

    expect(writeMock).toHaveBeenCalledWith("leonardo", store.yaml, store.name, false);
  });

  it("turns a 409 into a replace question instead of an error", async () => {
    writeMock.mockRejectedValueOnce(conflict());
    const store = writableStore();

    expect(await store.write()).toBe(false);

    expect(store.needsOverwrite).toBe(true);
    expect(store.error).toBeNull();
    expect(store.lastWrite).toBeNull();
  });

  it("re-sends the same write with overwrite on when the user agrees", async () => {
    writeMock.mockRejectedValueOnce(conflict());
    writeMock.mockResolvedValueOnce(result);
    const store = writableStore();
    await store.write();

    expect(await store.write({ overwrite: true })).toBe(true);

    expect(writeMock).toHaveBeenLastCalledWith("leonardo", store.yaml, store.name, true);
    expect(store.needsOverwrite).toBe(false);
    expect(store.lastWrite).toEqual(result);
  });

  it("drops the replace question when the YAML changes", async () => {
    writeMock.mockRejectedValueOnce(conflict());
    const store = writableStore();
    await store.write();

    store.groups[0].draft.numnodes = "4";
    await settle();

    expect(store.needsOverwrite).toBe(false);
  });

  /** A write that stays in flight until the returned function settles it. */
  type Outcome = { ok: true } | { ok: false; err: Error };
  function pendingWrite(): (o: Outcome) => void {
    let finish: (o: Outcome) => void = () => {};
    writeMock.mockImplementationOnce(
      () =>
        new Promise((resolve, reject) => {
          finish = (o) => (o.ok ? resolve(result) : reject(o.err));
        }),
    );
    return (o) => finish(o);
  }

  it("discards a success that arrives after the campaign was edited", async () => {
    const store = writableStore();
    const finish = pendingWrite();
    const done = store.write();
    store.groups[0].draft.numnodes = "4";
    await settle();
    finish({ ok: true });

    expect(await done).toBe(false);

    expect(store.lastWrite).toBeNull();
    expect(store.busy).toBe(false);
  });

  it("discards a 409 that arrives after the campaign was renamed", async () => {
    const store = writableStore();
    const finish = pendingWrite();
    const done = store.write();
    store.name = "another name";
    await settle();
    finish({ ok: false, err: conflict() });
    await done;

    expect(store.needsOverwrite).toBe(false);
    expect(store.error).toBeNull();
  });

  it("discards an error that arrives after the cluster was switched", async () => {
    const store = writableStore();
    const finish = pendingWrite();
    const done = store.write();
    store.destination = "lumi";
    await settle();
    finish({ ok: false, err: new Error("boom") });
    await done;

    expect(store.error).toBeNull();
    expect(store.lastWrite).toBeNull();
  });

  it("drops the replace question when the cluster is switched", async () => {
    writeMock.mockRejectedValueOnce(conflict());
    const store = writableStore();
    await store.write();
    expect(store.needsOverwrite).toBe(true);

    store.destination = "lumi";
    await settle();

    expect(store.needsOverwrite).toBe(false);
  });

  it("does not replace for a campaign other than the one the 409 belonged to", async () => {
    writeMock.mockRejectedValueOnce(conflict());
    writeMock.mockResolvedValueOnce(result);
    const store = writableStore();
    await store.write();

    // Edited, and Replace pressed before the watcher in the store has run.
    store.groups[0].draft.numnodes = "4";
    await store.write({ overwrite: true });

    expect(writeMock).toHaveBeenLastCalledWith("leonardo", store.yaml, store.name, false);
  });

  it("still shows any other error as before", async () => {
    writeMock.mockRejectedValueOnce(
      Object.assign(new ApiError("The campaign file has no `jobs` section."), {
        code: "input_error",
        status: 422,
      }),
    );
    const store = writableStore();

    expect(await store.write()).toBe(false);

    expect(store.error).toBe("The campaign file has no `jobs` section.");
    expect(store.needsOverwrite).toBe(false);
  });
});
