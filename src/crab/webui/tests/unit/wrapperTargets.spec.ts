/**
 * Wrappers section data layer: the pure helpers (targets, tree, filter, drift, attention) and the
 * per-target store, with the API client mocked (the I/O boundary).
 */
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: {
    remotes: { wrappers: vi.fn(), importBinary: vi.fn() },
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
import type {
  CloneStatus,
  PullResult,
  WrapperEntry,
  WrapperSummary,
  WrappersResult,
} from "@/api/types";
import { buildTree, drift, filterTree, needsAttention, wrapperTargets } from "@/lib/wrappers";
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

function clone(extra: Partial<CloneStatus> = {}): CloneStatus {
  return {
    dir: "/h/CRAB/wrappers",
    clone: true,
    detail: null,
    commit: "b173265b31616be54c4aee97d221b38294926e56",
    subject: "blink: mirror upstream",
    branch: "main",
    modified: [],
    upstream: { url: "https://example.org/w.git", head: "abc", error: null },
    behind: 0,
    ahead: 0,
    state: "up to date",
    ...extra,
  };
}

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

describe("wrapperTargets", () => {
  it("puts This laptop first, then the remotes in order", () => {
    expect(
      wrapperTargets([
        { name: "leo", connected: true },
        { name: "lumi", connected: false },
      ]),
    ).toEqual([
      { id: "local", label: "This laptop", connected: true, local: true },
      { id: "leo", label: "leo", connected: true, local: false },
      { id: "lumi", label: "lumi", connected: false, local: false },
    ]);
  });

  it("skips a remote named like the reserved laptop target", () => {
    const ids = wrapperTargets([
      { name: "local", connected: true },
      { name: "leo", connected: false },
    ]).map((t) => t.id);
    expect(ids).toEqual(["local", "leo"]);
  });

  it("always has the laptop, even with no remotes", () => {
    expect(wrapperTargets([])).toHaveLength(1);
  });
});

describe("buildTree", () => {
  const wrappers = [
    entry("g500/g.py", "path"),
    entry("blink/alltoall/b.py", "missing"),
    entry("blink/alltoall/a.py", "receipt"),
    entry("blink/pingpong.py", "unknown"),
    entry("top.py", "config"),
  ];

  it("nests folders from the relpaths, with counts and problems", () => {
    const tree = buildTree(wrappers);
    expect(tree.count).toBe(5);
    expect(tree.problems).toBe(2);
    expect(tree.wrappers.map((w) => w.relpath)).toEqual(["top.py"]);
    expect(tree.folders.map((f) => f.name)).toEqual(["blink", "g500"]);

    const blink = tree.folders[0];
    expect(blink.path).toBe("blink");
    expect(blink.count).toBe(3);
    expect(blink.problems).toBe(2);
    expect(blink.wrappers.map((w) => w.relpath)).toEqual(["blink/pingpong.py"]);

    const alltoall = blink.folders[0];
    expect(alltoall.name).toBe("alltoall");
    expect(alltoall.path).toBe("blink/alltoall");
    expect(alltoall.count).toBe(2);
    expect(alltoall.problems).toBe(1);
    // Direct files keep the catalog order.
    expect(alltoall.wrappers.map((w) => w.relpath)).toEqual([
      "blink/alltoall/b.py",
      "blink/alltoall/a.py",
    ]);
    expect(alltoall.folders).toEqual([]);

    expect(tree.folders[1].count).toBe(1);
    expect(tree.folders[1].problems).toBe(0);
  });

  it("builds an empty tree from an empty catalog", () => {
    const tree = buildTree([]);
    expect(tree).toMatchObject({ folders: [], wrappers: [], count: 0, problems: 0 });
  });
});

describe("filterTree", () => {
  const tree = buildTree([
    entry("blink/alltoall/a.py", "receipt"),
    entry("blink/alltoall/b.py", "missing"),
    entry("blink/pingpong.py", "receipt"),
    entry("g500/g.py", "path"),
    entry("Top.py", "unknown"),
  ]);

  it("returns the tree unchanged for an empty query", () => {
    expect(filterTree(tree, "")).toBe(tree);
  });

  it("keeps matching wrappers and their ancestor folders, case-insensitively", () => {
    const out = filterTree(tree, "ALLTOALL/B");
    expect(out.count).toBe(1);
    expect(out.problems).toBe(1);
    expect(out.folders.map((f) => f.path)).toEqual(["blink"]);
    expect(out.folders[0].count).toBe(1);
    expect(out.folders[0].wrappers).toEqual([]);
    expect(out.folders[0].folders[0].wrappers.map((w) => w.relpath)).toEqual([
      "blink/alltoall/b.py",
    ]);
  });

  it("matches the folder part of a relpath and top-level files", () => {
    const out = filterTree(tree, "blink");
    expect(out.count).toBe(3);
    expect(out.folders.map((f) => f.name)).toEqual(["blink"]);
    expect(filterTree(tree, "top").wrappers.map((w) => w.relpath)).toEqual(["Top.py"]);
    expect(filterTree(tree, "top").folders).toEqual([]);
  });

  it("returns an empty tree when nothing matches", () => {
    expect(filterTree(tree, "zzz")).toMatchObject({ folders: [], wrappers: [], count: 0 });
  });
});

describe("drift", () => {
  it("offers a clone when the folder is missing", () => {
    expect(
      drift(clone({ clone: false, detail: "missing: `crab wrappers pull` clones it" })),
    ).toEqual({ tone: "warn", text: "not cloned yet", canPull: true, pullLabel: "Clone" });
  });

  it("does not offer a pull for a folder that is not a clone", () => {
    const d = drift(clone({ clone: false, detail: "not a git clone" }));
    expect(d).toMatchObject({ tone: "muted", text: "not a git clone", canPull: false });
  });

  it("flags modified files and blocks the pull in any state", () => {
    expect(drift(clone({ modified: ["a.py"], state: "behind", behind: 2 }))).toMatchObject({
      tone: "warn",
      text: "1 modified file",
      canPull: false,
    });
    expect(drift(clone({ modified: ["a.py", "b.py"] }))).toMatchObject({
      tone: "warn",
      text: "2 modified files",
      canPull: false,
    });
  });

  it("reports up to date", () => {
    expect(drift(clone())).toMatchObject({ tone: "ok", text: "up to date", canPull: false });
  });

  it("reports how many commits behind, and labels the pull", () => {
    expect(drift(clone({ state: "behind", behind: 1 }))).toEqual({
      tone: "warn",
      text: "1 commit behind",
      canPull: true,
      pullLabel: "Pull 1 commit",
    });
    expect(drift(clone({ state: "behind", behind: 3 }))).toEqual({
      tone: "warn",
      text: "3 commits behind",
      canPull: true,
      pullLabel: "Pull 3 commits",
    });
  });

  it("is behind without a count when the commits are not fetched yet", () => {
    expect(drift(clone({ state: "behind", behind: null }))).toEqual({
      tone: "warn",
      text: "behind GitHub",
      canPull: true,
      pullLabel: "Pull",
    });
  });

  it("reports ahead and diverged without a pull", () => {
    expect(drift(clone({ state: "ahead", ahead: 2 }))).toMatchObject({
      tone: "muted",
      text: "2 commits ahead of GitHub",
      canPull: false,
    });
    expect(drift(clone({ state: "diverged", ahead: 1, behind: 1 }))).toMatchObject({
      tone: "warn",
      text: "diverged from GitHub",
      canPull: false,
    });
  });

  it("reports an unreachable GitHub and the unknown state", () => {
    const unreachable = clone({
      state: "unknown",
      upstream: { url: null, head: null, error: "timeout" },
    });
    expect(drift(unreachable)).toMatchObject({
      tone: "muted",
      text: "GitHub not reachable",
      canPull: false,
    });
    expect(drift(clone({ state: "unknown", detail: "no commits yet" }))).toMatchObject({
      tone: "muted",
      text: "unknown",
      canPull: false,
    });
  });

  it("never uses em or en dashes", () => {
    const states: Partial<CloneStatus>[] = [
      { clone: false, detail: "missing: x" },
      { clone: false, detail: "not a git clone" },
      { modified: ["a"] },
      {},
      { state: "behind", behind: 2 },
      { state: "behind", behind: null },
      { state: "ahead", ahead: 1 },
      { state: "diverged" },
      { state: "unknown" },
    ];
    for (const s of states) {
      const d = drift(clone(s));
      expect(`${d.text} ${d.pullLabel}`).not.toMatch(/[–—]/);
    }
  });
});

describe("needsAttention", () => {
  it("splits the problem wrappers into missing and broken, keeping catalog order", () => {
    const out = needsAttention([
      entry("a/1.py", "receipt"),
      entry("a/2.py", "missing"),
      entry("a/3.py", "unknown"),
      entry("a/4.py", "error"),
      entry("a/5.py", "missing"),
      entry("a/6.py", "path"),
    ]);
    expect(out.missing.map((w) => w.relpath)).toEqual(["a/2.py", "a/5.py"]);
    expect(out.broken.map((w) => w.relpath)).toEqual(["a/3.py", "a/4.py"]);
  });
});

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
