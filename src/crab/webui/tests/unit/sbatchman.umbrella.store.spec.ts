/**
 * The campaign store on an umbrella cluster: the target probe, the benchmark folder, the
 * derived `configs:` value, the config name suggestions and the debounced path preview.
 * Mocks the API client, the true I/O boundary.
 */
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: {
    sbatchman: {
      write: vi.fn(),
      stagingPath: vi.fn(),
      target: vi.fn(),
      campaigns: { create: vi.fn() },
    },
  },
  ApiError: class ApiError extends Error {},
}));

import { api } from "@/api/client";
import type { SbatchmanTarget } from "@/api/types";
import { emptyApp } from "@/lib/config";
import { STAGING_DEBOUNCE_MS, useSbatchmanStore } from "@/stores/sbatchman";

const targetMock = vi.mocked(api.sbatchman.target);
const stagingMock = vi.mocked(api.sbatchman.stagingPath);
const writeMock = vi.mocked(api.sbatchman.write);
const createMock = vi.mocked(api.sbatchman.campaigns.create);

const UMBRELLA: SbatchmanTarget = {
  kind: "umbrella",
  root: "/home/u/umbrella",
  configs_file: "/home/u/umbrella/.local/configs.leonardo.yaml",
  configs_names: ["crab_run_{nodes}", "crab_qe_{nodes}"],
  benchmarks: ["blink", "qe"],
};
const PLAIN: SbatchmanTarget = {
  kind: "plain",
  root: "",
  configs_file: "",
  configs_names: [],
  benchmarks: [],
};

/** Run the store's watchers, pending promises and the preview's debounce timer. */
async function settle(): Promise<void> {
  await vi.advanceTimersByTimeAsync(STAGING_DEBOUNCE_MS);
}

function makeWritable(store: ReturnType<typeof useSbatchmanStore>): void {
  const g = store.groups[0];
  g.tag = "pilot";
  g.configName = "crab_run";
  g.draft.numnodes = "2";
  const app = emptyApp();
  app.path = "blink/alltoall/alltoall_b.py";
  g.draft.experiments[0].apps.push(app);
}

async function umbrellaStore(target: SbatchmanTarget = UMBRELLA) {
  targetMock.mockResolvedValue(target);
  const store = useSbatchmanStore();
  store.destination = "leonardo";
  await settle();
  return store;
}

beforeEach(() => {
  setActivePinia(createPinia());
  targetMock.mockReset();
  stagingMock.mockReset();
  writeMock.mockReset();
  createMock.mockReset();
  stagingMock.mockResolvedValue({ path: "/x/campaign.yaml" });
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("target", () => {
  it("is asked for when a cluster is chosen and forgotten when it is cleared", async () => {
    const store = await umbrellaStore();
    expect(targetMock).toHaveBeenCalledWith("leonardo");
    expect(store.target).toEqual(UMBRELLA);

    store.destination = "";
    await settle();
    expect(store.target).toBeNull();
  });

  it("is not asked for while no cluster is chosen", async () => {
    useSbatchmanStore();
    await settle();
    expect(targetMock).not.toHaveBeenCalled();
  });

  it("leaves plain behavior when the call fails, with no error banner", async () => {
    targetMock.mockRejectedValue(new Error("boom"));
    const store = useSbatchmanStore();
    makeWritable(store);
    store.destination = "leonardo";
    await settle();

    expect(store.target).toBeNull();
    expect(store.error).toBeNull();
    expect(store.configNames).toEqual([]);
    expect(store.writeBlock).toBeNull();
    writeMock.mockResolvedValue({ local_path: "/l", remote_path: "/r", new_folder: false });
    await store.write();
    expect(writeMock).toHaveBeenCalledWith("leonardo", store.yaml, store.name, false, undefined);
  });

  it("ignores a slow answer for a cluster that is no longer chosen", async () => {
    let resolveOld: (t: SbatchmanTarget) => void = () => {};
    targetMock.mockReturnValueOnce(new Promise((r) => (resolveOld = r)));
    targetMock.mockResolvedValueOnce(PLAIN);
    const store = useSbatchmanStore();
    store.destination = "leonardo";
    await settle();
    store.destination = "lumi";
    await settle();
    resolveOld(UMBRELLA);
    await settle();

    expect(store.target).toEqual(PLAIN);
  });
});

describe("config names and the configs: value", () => {
  it("suggests the names of the cluster's configs file", async () => {
    const store = await umbrellaStore();
    expect(store.configNames).toEqual(["crab_run_{nodes}", "crab_qe_{nodes}"]);
  });

  it("writes ../../.local/<configs file> in an umbrella when the field is empty", async () => {
    const store = await umbrellaStore();
    expect(store.configsRef).toBe("../../.local/configs.leonardo.yaml");
    expect(store.yaml.startsWith("configs: ../../.local/configs.leonardo.yaml\n")).toBe(true);
  });

  it("prefers what the user typed", async () => {
    const store = await umbrellaStore();
    store.configsFile = " mine.yaml ";
    expect(store.configsRef).toBe("mine.yaml");
    expect(store.yaml.startsWith("configs: mine.yaml\n")).toBe(true);
  });

  it("writes no key on a plain cluster with an empty field, and does not touch the field", async () => {
    const store = await umbrellaStore(PLAIN);
    expect(store.configsRef).toBe("");
    expect(store.yaml).not.toMatch(/^configs:/m);
    expect(store.configNames).toEqual([]);
    const umbrella = await umbrellaStore();
    expect(umbrella.configsFile).toBe("");
  });
});

describe("benchmark folder", () => {
  it("defaults to the first existing folder without becoming part of the saved spec", async () => {
    createMock.mockResolvedValue({ id: "c", name: "c", updated_at: "", spec: {} });
    const store = await umbrellaStore();
    expect(store.benchmark).toBe("");
    expect(store.effectiveBenchmark).toBe("blink");
    expect(store.isDirty).toBe(false);
  });

  it("takes what the user typed, trimmed", async () => {
    const store = await umbrellaStore();
    store.benchmark = "  qe ";
    expect(store.effectiveBenchmark).toBe("qe");
  });

  it("is none on a plain cluster, whatever was typed", async () => {
    const store = await umbrellaStore(PLAIN);
    store.benchmark = "qe";
    expect(store.effectiveBenchmark).toBe("");
  });

  it("is part of the dirty state once typed", async () => {
    const store = await umbrellaStore();
    store.benchmark = "qe";
    expect(store.isDirty).toBe(true);
  });

  it("blocks Write files with a reason when an umbrella has no folder yet", async () => {
    const store = await umbrellaStore({ ...UMBRELLA, benchmarks: [] });
    makeWritable(store);
    expect(store.writeBlock).toEqual({
      kind: "benchmark",
      message: expect.stringMatching(/folder/i),
    });
    expect(store.canWrite).toBe(false);
    store.benchmark = "new-one";
    expect(store.writeBlock).toBeNull();
  });

  it.each(["..", "a/b", "-x", ".hidden", "a b", "x".repeat(61)])(
    "blocks Write files for the folder name %j",
    async (name) => {
      const store = await umbrellaStore();
      makeWritable(store);
      store.benchmark = name;
      expect(store.writeBlock).toEqual({ kind: "benchmark", message: expect.any(String) });
    },
  );

  it("is sent with the write, and a new folder says what to do next", async () => {
    writeMock.mockResolvedValue({ local_path: "/l", remote_path: "/r", new_folder: true });
    const store = await umbrellaStore();
    makeWritable(store);
    store.benchmark = "fresh";
    expect(store.newFolderNotice).toBeNull();

    await store.write();

    expect(writeMock).toHaveBeenCalledWith("leonardo", store.yaml, store.name, false, "fresh");
    expect(store.newFolderNotice).toBe(
      "New folder: run ./install.sh in /home/u/umbrella to create its SbatchMan project and check the file.",
    );
  });

  it("asks for the target again after a write that made a folder", async () => {
    writeMock.mockResolvedValue({ local_path: "/l", remote_path: "/r", new_folder: true });
    const store = await umbrellaStore();
    makeWritable(store);
    store.benchmark = "fresh";
    targetMock.mockClear();
    targetMock.mockResolvedValue({ ...UMBRELLA, benchmarks: ["blink", "fresh", "qe"] });

    await store.write();
    await settle();

    expect(targetMock).toHaveBeenCalledTimes(1);
    expect(store.target?.benchmarks).toContain("fresh");
  });

  it("has no notice for a write into an existing folder", async () => {
    writeMock.mockResolvedValue({ local_path: "/l", remote_path: "/r", new_folder: false });
    const store = await umbrellaStore();
    makeWritable(store);
    await store.write();
    expect(store.newFolderNotice).toBeNull();
    expect(writeMock).toHaveBeenCalledWith("leonardo", store.yaml, store.name, false, "blink");
  });
});

describe("path preview", () => {
  it("waits for typing to pause: one call for a burst of edits", async () => {
    const store = await umbrellaStore(PLAIN);
    stagingMock.mockClear();

    for (const n of ["a", "ab", "abc"]) {
      store.name = n;
      await vi.advanceTimersByTimeAsync(STAGING_DEBOUNCE_MS - 100);
    }
    expect(stagingMock).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(STAGING_DEBOUNCE_MS);

    expect(stagingMock).toHaveBeenCalledTimes(1);
    expect(stagingMock).toHaveBeenCalledWith("leonardo", "abc", undefined);
  });

  it("never has two calls in flight; the last edit gets the last call", async () => {
    const store = await umbrellaStore(PLAIN);
    stagingMock.mockClear();
    let inFlight = 0;
    let most = 0;
    const resolvers: Array<() => void> = [];
    stagingMock.mockImplementation(async (_c, name) => {
      inFlight++;
      most = Math.max(most, inFlight);
      await new Promise<void>((r) => resolvers.push(r));
      inFlight--;
      return { path: `/x/${name}.yaml` };
    });

    store.name = "first";
    await settle();
    store.name = "second";
    await settle();
    expect(stagingMock).toHaveBeenCalledTimes(1);
    resolvers.shift()!();
    await settle();
    resolvers.shift()!();
    await settle();

    expect(most).toBe(1);
    expect(stagingMock).toHaveBeenCalledTimes(2);
    expect(store.stagingPath).toBe("/x/second.yaml");
  });

  it("sends undefined, not an empty string, when there is no folder", async () => {
    const store = await umbrellaStore(PLAIN);
    store.benchmark = "";
    store.name = "n";
    await settle();
    expect(stagingMock).toHaveBeenLastCalledWith("leonardo", "n", undefined);
  });

  it("sends the folder in an umbrella and follows it when it changes", async () => {
    const store = await umbrellaStore();
    expect(stagingMock).toHaveBeenLastCalledWith("leonardo", "campaign", "blink");
    store.benchmark = "qe";
    await settle();
    expect(stagingMock).toHaveBeenLastCalledWith("leonardo", "campaign", "qe");
  });

  it("is empty and not asked for while the folder name is invalid", async () => {
    const store = await umbrellaStore();
    stagingMock.mockClear();
    store.benchmark = "a/b";
    await settle();
    expect(stagingMock).not.toHaveBeenCalled();
    expect(store.stagingPath).toBe("");
  });

  it("clears at once when the cluster is cleared", async () => {
    const store = await umbrellaStore(PLAIN);
    expect(store.stagingPath).toBe("/x/campaign.yaml");
    store.destination = "";
    await vi.advanceTimersByTimeAsync(0);
    expect(store.stagingPath).toBe("");
  });
});
