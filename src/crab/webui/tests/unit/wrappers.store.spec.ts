/**
 * Wrappers store, per target, with the API client mocked (the I/O boundary).
 */
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: {
    wrappers: {
      list: vi.fn(),
      show: vi.fn(),
      status: vi.fn(),
      pull: vi.fn(),
      importBinary: vi.fn(),
    },
  },
  ApiError: class ApiError extends Error {
    code = "";
    detail?: string;
  },
}));

import { ApiError, api } from "@/api/client";
import type { PullResult, WrapperEntry, WrapperSummary, WrappersResult } from "@/api/types";
import { useWrappersStore } from "@/stores/wrappers";

import { clone, entry } from "./wrapperFixtures";

function list(...wrappers: WrapperEntry[]): WrappersResult {
  return { schema: 2, search_path: ["/x"], wrappers };
}

function apiError(code: string, message: string, detail?: string): Error {
  // The mock ApiError takes the message only (the real one takes the envelope).
  const e = new (ApiError as unknown as new (m: string) => Error)(message) as Error & {
    code: string;
    detail?: string;
  };
  e.code = code;
  e.detail = detail;
  return e;
}

describe("wrappers store, per target", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    for (const fn of Object.values(api.wrappers)) vi.mocked(fn).mockReset();
  });

  it("loads a target's list and keeps loading and errors per target", async () => {
    let seenLoading = false;
    const store = useWrappersStore();
    vi.mocked(api.wrappers.list).mockImplementationOnce(async () => {
      seenLoading = store.listLoading.local === true;
      return list(entry("a/1.py", "missing"));
    });
    await store.loadList("local");
    expect(api.wrappers.list).toHaveBeenCalledWith("local");
    expect(seenLoading).toBe(true);
    expect(store.listLoading.local).toBe(false);
    expect(store.lists.local.wrappers).toHaveLength(1);
    expect(store.listError.local).toBeUndefined();

    vi.mocked(api.wrappers.list).mockRejectedValueOnce(
      apiError("crab_outdated", "Update crab on leo.", "unknown command"),
    );
    await store.loadList("leo");
    expect(store.listError.leo).toEqual({
      code: "crab_outdated",
      message: "Update crab on leo.",
      detail: "unknown command",
    });
    expect(store.lists.leo).toBeUndefined();
    expect(store.lists.local.wrappers).toHaveLength(1);
  });

  it("keeps the old list when a reload fails, and clears the error on success", async () => {
    const store = useWrappersStore();
    vi.mocked(api.wrappers.list).mockResolvedValueOnce(list(entry("a/1.py", "missing")));
    await store.loadList("leo");
    vi.mocked(api.wrappers.list).mockRejectedValueOnce(
      apiError("connection_error", "Not connected."),
    );
    await store.loadList("leo");
    expect(store.listError.leo?.code).toBe("connection_error");
    expect(store.lists.leo.wrappers).toHaveLength(1);

    vi.mocked(api.wrappers.list).mockResolvedValueOnce(list());
    await store.loadList("leo");
    expect(store.listError.leo).toBeUndefined();
    expect(store.lists.leo.wrappers).toHaveLength(0);
  });

  it("stores a non-ApiError as code unexpected", async () => {
    vi.mocked(api.wrappers.status).mockRejectedValueOnce(new TypeError("boom"));
    const store = useWrappersStore();
    await store.loadStatus("local");
    expect(store.statusError.local?.code).toBe("unexpected");
  });

  it("loads the status and one wrapper's summary under their own keys", async () => {
    const summary = { relpath: "a/1.py", loadable: true } as WrapperSummary;
    vi.mocked(api.wrappers.status).mockResolvedValueOnce(clone());
    vi.mocked(api.wrappers.show).mockResolvedValueOnce(summary);
    const store = useWrappersStore();
    await store.loadStatus("leo");
    await store.loadShow("leo", "a/1.py");
    expect(api.wrappers.show).toHaveBeenCalledWith("leo", "a/1.py");
    expect(store.statuses.leo.state).toBe("up to date");
    expect(store.summaries["leo:a/1.py"]).toEqual(summary);
    expect(store.summaryLoading["leo:a/1.py"]).toBe(false);

    vi.mocked(api.wrappers.show).mockRejectedValueOnce(apiError("not_found", "No such wrapper."));
    await store.loadShow("leo", "a/1.py");
    expect(store.summaryError["leo:a/1.py"]?.code).toBe("not_found");
    expect(store.summaries["leo:a/1.py"]).toEqual(summary);
  });

  it("pulls, then reloads the status and the list", async () => {
    const result: PullResult = {
      schema: 2,
      dir: "/h/w",
      action: "updated",
      old: "aaa",
      new: "bbb",
    };
    vi.mocked(api.wrappers.status).mockResolvedValue(clone());
    vi.mocked(api.wrappers.list).mockResolvedValue(list());
    const store = useWrappersStore();
    let seenPulling = false;
    vi.mocked(api.wrappers.pull).mockImplementationOnce(async () => {
      seenPulling = store.pulling.leo === true;
      return result;
    });
    const ok = await store.pull("leo");
    expect(ok).toBe(true);
    expect(seenPulling).toBe(true);
    expect(store.pulling.leo).toBe(false);
    expect(store.lastPull.leo).toEqual(result);
    expect(api.wrappers.status).toHaveBeenCalledWith("leo");
    expect(api.wrappers.list).toHaveBeenCalledWith("leo");
  });

  it("keeps a refused pull's error and does not reload", async () => {
    vi.mocked(api.wrappers.pull).mockRejectedValueOnce(
      apiError("conflict", "Local edits block the pull.", "a.py"),
    );
    const store = useWrappersStore();
    const ok = await store.pull("local");
    expect(ok).toBe(false);
    expect(store.pullError.local).toEqual({
      code: "conflict",
      message: "Local edits block the pull.",
      detail: "a.py",
    });
    expect(store.pulling.local).toBe(false);
    expect(store.lastPull.local).toBeUndefined();
    expect(api.wrappers.status).not.toHaveBeenCalled();
    expect(api.wrappers.list).not.toHaveBeenCalled();
  });

  const body = { id: "b", binary: "/opt/b", pre_run: [], launcher: "" as const };

  it("imports a binary on a target, then reloads its list and a loaded summary", async () => {
    vi.mocked(api.wrappers.list).mockResolvedValue(list(entry("a/1.py", "receipt")));
    vi.mocked(api.wrappers.show).mockResolvedValue({ relpath: "a/1.py" } as WrapperSummary);
    vi.mocked(api.wrappers.importBinary).mockResolvedValue({ schema: 2, receipt: { id: "b" } });
    const store = useWrappersStore();
    await store.loadShow("leo", "a/1.py");
    vi.mocked(api.wrappers.show).mockClear();

    const ok = await store.importBinaryOn("leo", "a/1.py", body);
    expect(ok).toBe(true);
    expect(api.wrappers.importBinary).toHaveBeenCalledWith("leo", body);
    expect(store.lists.leo.wrappers[0].binary.status).toBe("receipt");
    expect(api.wrappers.show).toHaveBeenCalledWith("leo", "a/1.py");
    expect(store.importing["leo:a/1.py"]).toBe(false);
    expect(store.importError["leo:a/1.py"]).toBeUndefined();
  });

  it("does not load a summary nobody asked for after an import", async () => {
    vi.mocked(api.wrappers.list).mockResolvedValue(list());
    vi.mocked(api.wrappers.importBinary).mockResolvedValue({ schema: 2, receipt: {} });
    const store = useWrappersStore();
    await store.importBinaryOn("local", "a/1.py", body);
    expect(api.wrappers.show).not.toHaveBeenCalled();
  });

  it("keeps an import error on its wrapper and does not reload", async () => {
    vi.mocked(api.wrappers.importBinary).mockRejectedValueOnce(
      apiError("remote_command_error", "Import failed.", "binary /opt/b does not exist"),
    );
    const store = useWrappersStore();
    const ok = await store.importBinaryOn("leo", "a/1.py", body);
    expect(ok).toBe(false);
    expect(store.importError["leo:a/1.py"]).toContain("does not exist");
    expect(api.wrappers.list).not.toHaveBeenCalled();
  });
});
