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
import {
  binaryLine,
  descriptionNote,
  filterSections,
  firstWrapper,
  librarySections,
  summaryPath,
  summaryTitle,
  visibleFiles,
} from "@/lib/wrapperTree";
import {
  attentionCount,
  buildTree,
  drift,
  driftLines,
  filterTree,
  missingReason,
  needsAttention,
  relativeTime,
  shortSha,
  wrapperTargets,
} from "@/lib/wrappers";
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
  const wrappers = [
    entry("a/1.py", "receipt"),
    entry("a/2.py", "missing"),
    entry("a/3.py", "unknown"),
    entry("a/4.py", "error"),
    entry("a/5.py", "missing"),
    entry("a/6.py", "path"),
  ];

  it("splits the problem wrappers into broken, missing and lookup failed, in catalog order", () => {
    const out = needsAttention(wrappers);
    expect(out.broken.map((w) => w.relpath)).toEqual(["a/3.py"]);
    expect(out.missing.map((w) => w.relpath)).toEqual(["a/2.py", "a/5.py"]);
    expect(out.lookupFailed.map((w) => w.relpath)).toEqual(["a/4.py"]);
  });

  it("counts only broken and missing wrappers, not failed lookups", () => {
    expect(attentionCount(wrappers)).toBe(3);
    expect(attentionCount([entry("a/4.py", "error"), entry("a/6.py", "path")])).toBe(0);
    expect(attentionCount([])).toBe(0);
  });
});

describe("missingReason", () => {
  it("names the executable the lookup was for", () => {
    expect(missingReason(entry("a/1.py", "missing", { executable: "amg" }))).toBe(
      "no receipt, config entry or PATH match for amg",
    );
  });

  it("falls back to the benchmark id when there is no executable", () => {
    const noExe = entry("a/1.py", "missing", { executable: null, benchmark_id: "amg2" });
    expect(missingReason(noExe)).toBe("no receipt, config entry or PATH match for amg2");
  });

  it("says how to fix a wrapper that has no receipt id (Import binary is not possible)", () => {
    const fix = "no receipt id in this wrapper: set the app's binary in the experiment config";
    const withExe = entry("a/1.py", "missing", { executable: "ib_send_lat", benchmark_id: null });
    expect(missingReason(withExe)).toBe(
      `no receipt, config entry or PATH match for ib_send_lat; ${fix}`,
    );
    const neither = entry("a/1.py", "missing", { executable: null, benchmark_id: null });
    expect(missingReason(neither)).toBe(`no receipt, config entry or PATH match; ${fix}`);
  });
});

describe("driftLines", () => {
  it("is one line for most states", () => {
    expect(driftLines(clone())).toEqual(["up to date"]);
    expect(driftLines(clone({ modified: ["a.py"] }))).toEqual(["1 modified file"]);
    expect(driftLines(clone({ state: "behind", behind: 2 }))).toEqual(["2 commits behind"]);
  });

  it("lists the commits behind above the modified files when both apply", () => {
    expect(driftLines(clone({ state: "behind", behind: 2, modified: ["a", "b"] }))).toEqual([
      "2 commits behind",
      "2 modified files",
    ]);
    expect(driftLines(clone({ state: "behind", behind: null, modified: ["a"] }))).toEqual([
      "behind GitHub",
      "1 modified file",
    ]);
  });
});

describe("relativeTime", () => {
  it("says just now under a minute", () => {
    expect(relativeTime(0)).toBe("just now");
    expect(relativeTime(59_999)).toBe("just now");
  });

  it("counts minutes, then hours", () => {
    expect(relativeTime(60_000)).toBe("1 min ago");
    expect(relativeTime(5 * 60_000 + 10_000)).toBe("5 min ago");
    expect(relativeTime(60 * 60_000)).toBe("1 h ago");
    expect(relativeTime(3 * 3_600_000 + 59 * 60_000)).toBe("3 h ago");
  });

  it("treats a negative gap (clock skew) as just now", () => {
    expect(relativeTime(-5_000)).toBe("just now");
  });
});

describe("shortSha", () => {
  it("keeps the first seven characters", () => {
    expect(shortSha("b173265b31616be54c4aee97d221b38294926e56")).toBe("b173265");
  });

  it("is empty without a commit", () => {
    expect(shortSha(null)).toBe("");
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

describe("library sections", () => {
  const local = "/h/CRAB/local/wrappers";
  const shipped = "/h/CRAB/wrappers";
  const searched = (relpath: string, folder: string) =>
    entry(relpath, "path", { folder, path: `${folder}/${relpath}` });

  it("splits the wrappers of the first search folder into a local section on top", () => {
    const sections = librarySections({
      schema: 2,
      search_path: [local, shipped],
      wrappers: [
        searched("blink/a.py", shipped),
        searched("mybench/mybench.py", local),
        searched("others/amg.py", shipped),
      ],
    });
    expect(sections.map((s) => [s.label, s.local, s.tree.count])).toEqual([
      ["local", true, 1],
      [null, false, 2],
    ]);
    expect(sections[0].tree.folders[0].wrappers[0].relpath).toBe("mybench/mybench.py");
    expect(sections[1].tree.folders.map((f) => f.name)).toEqual(["blink", "others"]);
  });

  it("leaves out a section with no wrappers", () => {
    const onlyShipped = librarySections({
      schema: 2,
      search_path: [local, shipped],
      wrappers: [searched("blink/a.py", shipped)],
    });
    expect(onlyShipped.map((s) => s.local)).toEqual([false]);
    const onlyLocal = librarySections({
      schema: 2,
      search_path: [local, shipped],
      wrappers: [searched("mybench/mybench.py", local)],
    });
    expect(onlyLocal.map((s) => s.local)).toEqual([true]);
    expect(librarySections({ schema: 2, search_path: [local], wrappers: [] })).toEqual([]);
  });

  it("names the first wrapper the tree lists: subfolders before files, local first", () => {
    const sections = librarySections({
      schema: 2,
      search_path: [local, shipped],
      wrappers: [
        searched("top.py", shipped),
        searched("others/amg.py", shipped),
        searched("blink/a.py", shipped),
      ],
    });
    expect(firstWrapper(sections)).toBe("blink/a.py");
    const withLocal = librarySections({
      schema: 2,
      search_path: [local, shipped],
      wrappers: [searched("blink/a.py", shipped), searched("mybench/m.py", local)],
    });
    expect(firstWrapper(withLocal)).toBe("mybench/m.py");
    expect(firstWrapper([])).toBe("");
  });

  it("filters every section and drops the ones left empty", () => {
    const sections = librarySections({
      schema: 2,
      search_path: [local, shipped],
      wrappers: [searched("blink/a.py", shipped), searched("mybench/mybench.py", local)],
    });
    const out = filterSections(sections, "MYB");
    expect(out.map((s) => [s.local, s.tree.count])).toEqual([[true, 1]]);
    expect(filterSections(sections, "")).toBe(sections);
  });
});

describe("summary text helpers", () => {
  function summary(extra: Partial<WrapperSummary> = {}): WrapperSummary {
    return {
      relpath: "blink/alltoall/alltoall_b.py",
      path: "/h/CRAB/wrappers/blink/alltoall/alltoall_b.py",
      folder: "/h/CRAB/wrappers",
      moved_from: null,
      loadable: true,
      error: null,
      benchmark_id: "blink",
      executable: "alltoall_b",
      keys: [],
      description: { text: "Blink is a suite.", source: "readme", path: "blink/README.md" },
      metrics: [],
      binary: { status: "receipt", path: "/opt/blink/alltoall_b" },
      source: { commit: "d380eb5ffff", url: "https://github.com/x/y" },
      ...extra,
    };
  }

  it("titles a wrapper by its file stem", () => {
    expect(summaryTitle("blink/alltoall/alltoall_b.py")).toBe("alltoall_b");
    expect(summaryTitle("top.py")).toBe("top");
  });

  it("shows the path under the search folder's name, local ones under local/", () => {
    const s = summary();
    expect(summaryPath(s, false)).toBe("wrappers/blink/alltoall/alltoall_b.py");
    const mine = summary({ relpath: "mybench/mybench.py", folder: "/h/CRAB/local/wrappers" });
    expect(summaryPath(mine, true)).toBe("local/wrappers/mybench/mybench.py");
  });

  it("says where a README description came from", () => {
    expect(descriptionNote(summary())).toBe(
      "From the first paragraph of blink/README.md: alltoall_b.py has no docstring.",
    );
  });

  it("adds no note to a docstring description", () => {
    const docstring = summary({
      description: { text: "t", source: "docstring", path: "blink/alltoall/alltoall_b.py" },
    });
    expect(descriptionNote(docstring)).toBeNull();
  });

  it("explains a missing description by its folder", () => {
    const none = { text: "", source: null, path: null } as WrapperSummary["description"];
    const nested = summary({ relpath: "others/amg.py", description: none });
    expect(descriptionNote(nested)).toBe(
      "No description: amg.py has no docstring and others/ has no README.",
    );
    const top = summary({ relpath: "top.py", description: none });
    expect(descriptionNote(top)).toBe(
      "No description: top.py has no docstring and the wrappers folder has no README.",
    );
  });

  it("words a found binary with where it was found and its path", () => {
    expect(binaryLine(summary(), "leo")).toEqual({
      tone: "ok",
      text: "found, receipt → /opt/blink/alltoall_b",
    });
    const config = summary({ binary: { status: "config", path: "/c/b" } });
    expect(binaryLine(config, "leo").text).toBe("found, set in config → /c/b");
    const onPath = summary({ binary: { status: "path", path: "/usr/bin/b" } });
    expect(binaryLine(onPath, "leo")).toEqual({ tone: "ok", text: "found on PATH → /usr/bin/b" });
  });

  it("words a missing binary with the target and the reason", () => {
    const missing = summary({
      executable: "amg",
      binary: { status: "missing", path: null },
    });
    expect(binaryLine(missing, "leonardo")).toEqual({
      tone: "warn",
      text: "missing on leonardo: no receipt, config entry or PATH match for amg",
    });
  });

  it("mutes a failed lookup and keeps its reason", () => {
    const failed = summary({
      binary: { status: "error", path: null, detail: "CRAB_SYSTEM is not set" },
    });
    expect(binaryLine(failed, "This laptop")).toEqual({
      tone: "muted",
      text: "could not look up the binary here (CRAB_SYSTEM is not set)",
    });
    const noDetail = summary({ binary: { status: "error", path: null } });
    expect(binaryLine(noDetail, "x").text).toBe("could not look up the binary here");
  });

  it("warns about a wrapper that cannot load, with its error's first line", () => {
    const broken = summary({
      loadable: false,
      error: "ImportError: no module\nTraceback...",
      binary: { status: "missing", path: null },
    });
    expect(binaryLine(broken, "leo")).toEqual({
      tone: "warn",
      text: "the wrapper does not load: ImportError: no module",
    });
  });

  it("never uses em or en dashes", () => {
    const lines = [
      binaryLine(summary(), "t"),
      binaryLine(summary({ binary: { status: "missing", path: null } }), "t"),
      binaryLine(summary({ binary: { status: "error", path: null, detail: "d" } }), "t"),
      binaryLine(summary({ loadable: false, error: "e" }), "t"),
    ];
    for (const l of lines) expect(l.text).not.toMatch(/[–—]/);
    const none = summary({ description: { text: "", source: null, path: null } });
    expect(descriptionNote(none)).not.toMatch(/[–—]/);
  });
});

describe("visibleFiles", () => {
  const files = Array.from({ length: 10 }, (_, i) => entry(`others/f${i}.py`, "path"));
  const folder = buildTree(files).folders[0];

  it("shows the first six files and counts the rest", () => {
    const out = visibleFiles(folder, false);
    expect(out.files.map((w) => w.relpath)).toEqual(files.slice(0, 6).map((w) => w.relpath));
    expect(out.more).toBe(4);
  });

  it("shows everything once expanded", () => {
    expect(visibleFiles(folder, true)).toEqual({ files: folder.wrappers, more: 0 });
  });

  it("has no more link for a short folder, and honours a custom limit", () => {
    const short = buildTree(files.slice(0, 3)).folders[0];
    expect(visibleFiles(short, false)).toEqual({ files: short.wrappers, more: 0 });
    expect(visibleFiles(folder, false, 8).more).toBe(2);
  });
});
