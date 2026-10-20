/**
 * Wrappers library view helpers (src/lib/wrapperTree.ts): sections, summary text, visible files
 * and the summary link.
 */
import { describe, expect, it } from "vitest";

import type { WrapperSummary } from "@/api/types";
import {
  binaryLine,
  descriptionNote,
  filterSections,
  firstWrapper,
  librarySections,
  summaryPath,
  summaryTitle,
  visibleFiles,
  wrapperSummaryLink,
} from "@/lib/wrapperTree";
import { buildTree } from "@/lib/wrappers";

import { entry } from "./wrapperFixtures";

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

describe("wrapperSummaryLink", () => {
  const rel = "blink/alltoall/alltoall_b.py";

  it("opens the Library on the source cluster when its catalog has the wrapper", () => {
    expect(wrapperSummaryLink(rel, "leo", [rel, "other.py"])).toBe(
      "/wrappers?tab=library&target=leo&wrapper=blink%2Falltoall%2Falltoall_b.py",
    );
  });

  it("falls back to This laptop with no source cluster", () => {
    expect(wrapperSummaryLink(rel, "", [rel])).toBe(
      "/wrappers?tab=library&target=local&wrapper=blink%2Falltoall%2Falltoall_b.py",
    );
  });

  it("falls back to This laptop when the cluster's catalog lacks the wrapper", () => {
    expect(wrapperSummaryLink(rel, "leo", ["other.py"])).toContain("target=local&");
    expect(wrapperSummaryLink(rel, "leo", [])).toContain("target=local&");
  });

  it("encodes the target and the relpath", () => {
    expect(wrapperSummaryLink("a b/c&d.py", "my cl", ["a b/c&d.py"])).toBe(
      "/wrappers?tab=library&target=my+cl&wrapper=a+b%2Fc%26d.py",
    );
  });

  it("has no link for an empty path", () => {
    expect(wrapperSummaryLink("", "leo", [""])).toBeNull();
    expect(wrapperSummaryLink("   ", "", [])).toBeNull();
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
