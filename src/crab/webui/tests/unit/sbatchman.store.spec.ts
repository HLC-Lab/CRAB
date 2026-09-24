/**
 * Campaign library actions on the sbatchman store (plan 086): load/open/save
 * (create + update)/duplicate/remove/isDirty. Mocks the API client, the true
 * I/O boundary, mirroring results.store.spec.ts's pattern. Write/YAML-preview
 * behavior (plan 084) is covered by lib/sbatchman.spec.ts and is untouched here.
 */
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: {
    sbatchman: {
      write: vi.fn(),
      campaigns: {
        list: vi.fn(),
        get: vi.fn(),
        create: vi.fn(),
        update: vi.fn(),
        duplicate: vi.fn(),
        remove: vi.fn(),
      },
    },
  },
  ApiError: class ApiError extends Error {
    detail?: string;
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  },
}));

import { ApiError, api } from "@/api/client";
import type { CampaignEntry } from "@/api/types";
import { emptyApp, emptyDraft, emptyExperiment } from "@/lib/config";
import { type CampaignSpec, type GroupState, useSbatchmanStore } from "@/stores/sbatchman";

const listMock = vi.mocked(api.sbatchman.campaigns.list);
const getMock = vi.mocked(api.sbatchman.campaigns.get);
const createMock = vi.mocked(api.sbatchman.campaigns.create);
const updateMock = vi.mocked(api.sbatchman.campaigns.update);
const duplicateMock = vi.mocked(api.sbatchman.campaigns.duplicate);
const removeMock = vi.mocked(api.sbatchman.campaigns.remove);
const writeMock = vi.mocked(api.sbatchman.write);

function group(tag: string): GroupState {
  const draft = emptyDraft();
  draft.experiments.push(emptyExperiment("run"));
  return { tag, preset: "", variables: [], draft };
}

function entry(overrides: Partial<CampaignEntry> = {}): CampaignEntry {
  const spec: CampaignSpec = {
    configsPath: "",
    crabRoot: "",
    system: "leonardo",
    env: [],
    variables: [{ name: "nodes", values: ["4", "8"] }],
    groups: [group("baseline-{nodes}")],
  };
  return {
    id: "a2a-baseline",
    name: "a2a baseline",
    updated_at: "2026-09-07T00:00:00+00:00",
    spec: spec as unknown as Record<string, unknown>,
    ...overrides,
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  listMock.mockReset();
  getMock.mockReset();
  createMock.mockReset();
  updateMock.mockReset();
  duplicateMock.mockReset();
  removeMock.mockReset();
  writeMock.mockReset();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("sbatchman store: campaign library", () => {
  it("loadLibrary populates library from the API", async () => {
    listMock.mockResolvedValueOnce([entry()]);
    const store = useSbatchmanStore();

    await store.loadLibrary();

    expect(store.library).toEqual([entry()]);
  });

  it("open loads a saved campaign into the editor and clears entryId's dirtiness", async () => {
    getMock.mockResolvedValueOnce(entry());
    const store = useSbatchmanStore();

    await store.open("a2a-baseline");

    expect(store.entryId).toBe("a2a-baseline");
    expect(store.name).toBe("a2a baseline");
    expect(store.system).toBe("leonardo");
    expect(store.variables).toEqual([{ name: "nodes", values: ["4", "8"] }]);
    expect(store.groups).toHaveLength(1);
    expect(store.groups[0].tag).toBe("baseline-{nodes}");
    expect(store.isDirty).toBe(false);
  });

  it("save creates a new entry when entryId is null", async () => {
    createMock.mockResolvedValueOnce(entry({ id: "new-id", name: "My Campaign" }));
    const store = useSbatchmanStore();
    store.name = "My Campaign";
    listMock.mockResolvedValueOnce([entry({ id: "new-id", name: "My Campaign" })]);

    const ok = await store.save();

    expect(ok).toBe(true);
    expect(createMock).toHaveBeenCalledWith("My Campaign", expect.any(Object));
    expect(updateMock).not.toHaveBeenCalled();
    expect(store.entryId).toBe("new-id");
    expect(store.isDirty).toBe(false);
    expect(store.notice).toContain("Saved");
  });

  it("save updates the existing entry once one is open", async () => {
    getMock.mockResolvedValueOnce(entry());
    const store = useSbatchmanStore();
    await store.open("a2a-baseline");

    updateMock.mockResolvedValueOnce(entry());
    listMock.mockResolvedValueOnce([entry()]);
    await store.save();

    expect(updateMock).toHaveBeenCalledWith("a2a-baseline", "a2a baseline", expect.any(Object));
    expect(createMock).not.toHaveBeenCalled();
  });

  it("save refuses an unnamed campaign", async () => {
    const store = useSbatchmanStore();
    store.name = "   ";

    const ok = await store.save();

    expect(ok).toBe(false);
    expect(store.error).toMatch(/name/i);
    expect(createMock).not.toHaveBeenCalled();
  });

  it("duplicateCampaign duplicates then opens the copy", async () => {
    duplicateMock.mockResolvedValueOnce(entry({ id: "a2a-baseline-2", name: "a2a baseline copy" }));
    listMock.mockResolvedValueOnce([entry(), entry({ id: "a2a-baseline-2" })]);
    getMock.mockResolvedValueOnce(entry({ id: "a2a-baseline-2", name: "a2a baseline copy" }));
    const store = useSbatchmanStore();

    await store.duplicateCampaign("a2a-baseline");

    expect(store.entryId).toBe("a2a-baseline-2");
    expect(store.notice).toContain("Duplicated");
  });

  it("removeCampaign resets to a new campaign if the open one was removed", async () => {
    getMock.mockResolvedValueOnce(entry());
    const store = useSbatchmanStore();
    await store.open("a2a-baseline");

    removeMock.mockResolvedValueOnce(undefined);
    listMock.mockResolvedValueOnce([]);
    await store.removeCampaign("a2a-baseline");

    expect(store.entryId).toBeNull();
    expect(store.name).toBe("campaign");
  });

  it("isDirty flips true after an edit and false again after a matching save", async () => {
    getMock.mockResolvedValueOnce(entry());
    const store = useSbatchmanStore();
    await store.open("a2a-baseline");
    expect(store.isDirty).toBe(false);

    store.system = "lumi";
    expect(store.isDirty).toBe(true);

    updateMock.mockResolvedValueOnce(entry({ spec: { ...entry().spec, system: "lumi" } }));
    listMock.mockResolvedValueOnce([]);
    await store.save();
    expect(store.isDirty).toBe(false);
  });

  it("isDirty flips true after a rename alone, even with the spec untouched", async () => {
    // Regression: the name lives outside `spec`, so the dirty-check must not
    // only diff `spec` — a rename with nothing else changed is still unsaved.
    getMock.mockResolvedValueOnce(entry());
    const store = useSbatchmanStore();
    await store.open("a2a-baseline");
    expect(store.isDirty).toBe(false);

    store.name = "a2a baseline (renamed)";
    expect(store.isDirty).toBe(true);
  });

  it("surfaces the backend error message on a failed save", async () => {
    createMock.mockRejectedValueOnce(new ApiError("Disk full.", 500));
    const store = useSbatchmanStore();
    store.name = "Whatever";

    const ok = await store.save();

    expect(ok).toBe(false);
    expect(store.error).toBe("Disk full.");
  });
});

describe("sbatchman store: write is gated on validation (plan 090 S11d)", () => {
  function makeValid(store: ReturnType<typeof useSbatchmanStore>): void {
    const g = store.groups[0];
    g.tag = "run";
    g.preset = "cpu";
    g.draft.numnodes = "2";
    const app = emptyApp();
    app.path = "blink/a2a_b.py";
    g.draft.experiments[0].apps.push(app);
  }

  it("a fresh campaign reports issues and write() refuses without calling the API", async () => {
    const store = useSbatchmanStore();
    store.destination = "cluster-a";

    expect(store.issues.length).toBeGreaterThan(0);
    const ok = await store.write();

    expect(ok).toBe(false);
    expect(writeMock).not.toHaveBeenCalled();
    expect(store.error).toMatch(/issue/);
  });

  it("a valid campaign writes", async () => {
    writeMock.mockResolvedValueOnce({ path: "/remote/x.yaml" } as never);
    const store = useSbatchmanStore();
    store.destination = "cluster-a";
    makeValid(store);

    expect(store.issues).toEqual([]);
    expect(await store.write()).toBe(true);
    expect(writeMock).toHaveBeenCalledOnce();
  });
});

describe("sbatchman store: older saved specs and stale write results (plan 090 S11g)", () => {
  it("open() fills in fields an older saved spec does not have", async () => {
    // A spec saved before env/variables/draft options existed.
    getMock.mockResolvedValueOnce({
      id: "old",
      name: "old campaign",
      updated_at: "2026-09-01T00:00:00+00:00",
      spec: {
        configsPath: "",
        groups: [{ tag: "t", draft: { numnodes: "2", experiments: [{ name: "run" }] } }],
      },
    });
    const store = useSbatchmanStore();

    await store.open("old");

    expect(store.error).toBeNull();
    expect(store.env).toEqual([{ key: "", value: "" }]);
    expect(store.variables).toEqual([]);
    const g = store.groups[0];
    expect(g.preset).toBe("");
    expect(g.variables).toEqual([]);
    expect(g.draft.options).toEqual(emptyDraft().options);
    expect(g.draft.experiments[0].apps).toEqual([]);
    expect(typeof store.yaml).toBe("string");
  });

  it("a group saved with no experiment gets one, so the editor has something to show", async () => {
    getMock.mockResolvedValueOnce(entry({ spec: { groups: [{ tag: "t" }] } }));
    const store = useSbatchmanStore();

    await store.open("a2a-baseline");

    expect(store.error).toBeNull();
    expect(store.groups[0].tag).toBe("t");
    expect(store.groups[0].draft.experiments).toHaveLength(1);
  });

  it("newCampaign and open clear the last write result", async () => {
    writeMock.mockResolvedValue({ remote_path: "/r/x.yaml", local_path: "/l/x.yaml" } as never);
    const store = useSbatchmanStore();
    store.lastWrite = { remote_path: "/r/x.yaml", local_path: "/l/x.yaml" } as never;
    store.newCampaign();
    expect(store.lastWrite).toBeNull();

    store.lastWrite = { remote_path: "/r/x.yaml", local_path: "/l/x.yaml" } as never;
    getMock.mockResolvedValueOnce(entry());
    await store.open("a2a-baseline");
    expect(store.lastWrite).toBeNull();
  });
});
