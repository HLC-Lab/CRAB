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
import { emptyDraft, emptyExperiment } from "@/lib/config";
import { type CampaignSpec, type GroupState, useSbatchmanStore } from "@/stores/sbatchman";

const listMock = vi.mocked(api.sbatchman.campaigns.list);
const getMock = vi.mocked(api.sbatchman.campaigns.get);
const createMock = vi.mocked(api.sbatchman.campaigns.create);
const updateMock = vi.mocked(api.sbatchman.campaigns.update);
const duplicateMock = vi.mocked(api.sbatchman.campaigns.duplicate);
const removeMock = vi.mocked(api.sbatchman.campaigns.remove);

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
