/**
 * Wrappers section pure helpers (src/lib/wrappers.ts): targets, tree, filter, drift, attention,
 * binary and time helpers.
 */
import { describe, expect, it } from "vitest";

import {
  attentionCount,
  binaryLabel,
  buildTree,
  canImport,
  drift,
  driftLines,
  filterTree,
  hasProblem,
  missingReason,
  modifiedPreview,
  needsAttention,
  relativeTime,
  shortSha,
  wrapperTargets,
} from "@/lib/wrappers";

import { clone, entry } from "./wrapperFixtures";

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

describe("binary helpers", () => {
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

describe("modifiedPreview", () => {
  const names = (n: number) => Array.from({ length: n }, (_, i) => `a/f${i}.py`);

  it("shows every file when there are fewer than the limit", () => {
    expect(modifiedPreview(["a.py", "b.py"])).toEqual({ shown: ["a.py", "b.py"], more: 0 });
    expect(modifiedPreview(names(10))).toEqual({ shown: names(10), more: 0 });
  });

  it("shows the first 10 and counts the rest", () => {
    expect(modifiedPreview(names(13))).toEqual({ shown: names(10), more: 3 });
    expect(modifiedPreview(names(5), 2)).toEqual({ shown: names(2), more: 3 });
  });

  it("is empty for no files", () => {
    expect(modifiedPreview([])).toEqual({ shown: [], more: 0 });
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
