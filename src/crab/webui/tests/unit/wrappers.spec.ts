/**
 * Wrappers page (plan 091 S26): pure helpers in lib/wrappers.ts, and the store's load and
 * import flow with the API client mocked (the I/O boundary).
 */
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: { remotes: { wrappers: vi.fn(), importBinary: vi.fn() } },
  ApiError: class ApiError extends Error {},
}));

import { ApiError, api } from "@/api/client";
import type { WrapperEntry } from "@/api/types";
import { binaryLabel, canImport, groupByApp, hasProblem, summarize } from "@/lib/wrappers";
import { useWrappersStore } from "@/stores/wrappers";

function entry(relpath: string, status: string, extra: Partial<WrapperEntry> = {}): WrapperEntry {
  return {
    relpath,
    path: `/h/CRAB/wrappers/${relpath}`,
    folder: "/h/CRAB/wrappers",
    loadable: status !== "unknown",
    error: status === "unknown" ? "ImportError: x" : null,
    benchmark_id: "b",
    executable: "b",
    keys: [],
    metrics: ["t"],
    binary: { status, path: status === "missing" || status === "unknown" ? null : "/bin/b" },
    ...extra,
  } as WrapperEntry;
}

describe("lib/wrappers", () => {
  it("groups wrappers by their app folder, keeping order", () => {
    const groups = groupByApp([
      entry("blink/a.py", "receipt"),
      entry("g500/g.py", "path"),
      entry("blink/b.py", "missing"),
    ]);
    expect(groups.map((g) => [g.app, g.wrappers.map((w) => w.relpath)])).toEqual([
      ["blink", ["blink/a.py", "blink/b.py"]],
      ["g500", ["g500/g.py"]],
    ]);
  });

  it("counts found, missing and broken wrappers", () => {
    const all = [
      entry("a/1.py", "receipt"),
      entry("a/2.py", "path"),
      entry("a/3.py", "missing"),
      entry("a/4.py", "error"),
      entry("a/5.py", "unknown"),
    ];
    expect(summarize(all)).toEqual({ found: 2, missing: 1, broken: 2 });
  });

  it("flags wrappers that cannot load or have no binary", () => {
    expect(
      ["receipt", "path", "config", "missing", "error", "unknown"].map((s) =>
        hasProblem(entry("a/1.py", s)),
      ),
    ).toEqual([false, false, false, true, true, true]);
  });

  it("labels binary sources in plain words", () => {
    expect(binaryLabel(entry("a/1.py", "path"))).toBe("found on PATH");
    expect(binaryLabel(entry("a/1.py", "receipt"))).toBe("receipt");
    expect(binaryLabel(entry("a/1.py", "missing"))).toBe("missing");
    expect(binaryLabel(entry("a/1.py", "unknown"))).toBe("cannot load");
  });

  it("offers an import only for loadable wrappers with a receipt id and no binary found", () => {
    expect(canImport(entry("a/1.py", "missing"))).toBe(true);
    // A failed lookup is a bug in the wrapper; a receipt would not fix it and might overwrite
    // one that other wrappers of the same app share.
    expect(canImport(entry("a/1.py", "error"))).toBe(false);
    expect(canImport(entry("a/1.py", "receipt"))).toBe(false);
    expect(canImport(entry("a/1.py", "missing", { benchmark_id: null }))).toBe(false);
    expect(canImport(entry("a/1.py", "unknown"))).toBe(false);
  });
});

describe("wrappers store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.mocked(api.remotes.wrappers).mockReset();
    vi.mocked(api.remotes.importBinary).mockReset();
  });

  it("loads a cluster's catalog and keeps load errors per cluster", async () => {
    vi.mocked(api.remotes.wrappers).mockResolvedValueOnce({
      schema: 1,
      search_path: ["/x"],
      wrappers: [entry("a/1.py", "missing")],
    });
    const store = useWrappersStore();
    await store.load("leo");
    expect(store.catalog.leo.wrappers).toHaveLength(1);

    vi.mocked(api.remotes.wrappers).mockRejectedValueOnce(
      new ApiError("unknown command 'wrappers'"),
    );
    await store.load("old");
    expect(store.error.old).toContain("wrappers");
  });

  it("imports a binary, then reloads the catalog", async () => {
    vi.mocked(api.remotes.wrappers)
      .mockResolvedValueOnce({ schema: 1, search_path: [], wrappers: [entry("a/1.py", "missing")] })
      .mockResolvedValueOnce({
        schema: 1,
        search_path: [],
        wrappers: [entry("a/1.py", "receipt")],
      });
    vi.mocked(api.remotes.importBinary).mockResolvedValue({ schema: 1, receipt: { id: "b" } });
    const store = useWrappersStore();
    await store.load("leo");

    const ok = await store.importBinary("leo", "a/1.py", {
      id: "b",
      binary: "/opt/b",
      pre_run: [],
      launcher: "",
    });
    expect(ok).toBe(true);
    expect(api.remotes.importBinary).toHaveBeenCalledWith("leo", {
      id: "b",
      binary: "/opt/b",
      pre_run: [],
      launcher: "",
    });
    expect(store.catalog.leo.wrappers[0].binary.status).toBe("receipt");
  });

  it("keeps an import error on its wrapper and does not reload", async () => {
    vi.mocked(api.remotes.wrappers).mockResolvedValue({ schema: 1, search_path: [], wrappers: [] });
    const failure = new ApiError("`crab receipts set` failed on the cluster (exit 2).");
    (failure as ApiError & { detail?: string }).detail = "crab: binary /opt/b does not exist";
    vi.mocked(api.remotes.importBinary).mockRejectedValue(failure);
    const store = useWrappersStore();
    await store.load("leo");
    const ok = await store.importBinary("leo", "a/1.py", {
      id: "b",
      binary: "/opt/b",
      pre_run: [],
      launcher: "",
    });
    expect(ok).toBe(false);
    expect(store.importError["leo:a/1.py"]).toContain("does not exist");
    expect(api.remotes.wrappers).toHaveBeenCalledTimes(1);
  });
});
