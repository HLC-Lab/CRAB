// Pure helpers for the Wrappers Library tab: the tree's sections, the summary's wording.
// No fetch/DOM, so the unit suite imports them directly.

import type { WrapperEntry, WrapperSummary, WrappersResult } from "@/api/types";
import {
  binaryLabel,
  buildTree,
  filterTree,
  missingReason,
  type WrapperFolder,
} from "@/lib/wrappers";

export interface LibrarySection {
  /** The heading above the section ("local"), null for the shipped wrappers. */
  label: string | null;
  local: boolean;
  tree: WrapperFolder;
}

/** The target's wrappers split by search folder: the untracked `local/wrappers` (the first
 * search folder) on top, everything else as one section. A section with no wrappers is left out. */
export function librarySections(list: WrappersResult): LibrarySection[] {
  const first = list.search_path[0];
  const mine: WrapperEntry[] = [];
  const rest: WrapperEntry[] = [];
  for (const w of list.wrappers) (w.folder === first ? mine : rest).push(w);
  const sections: LibrarySection[] = [];
  if (mine.length) sections.push({ label: "local", local: true, tree: buildTree(mine) });
  if (rest.length) sections.push({ label: null, local: false, tree: buildTree(rest) });
  return sections;
}

/** Applies the filter to every section and drops the ones it empties. */
export function filterSections(sections: LibrarySection[], query: string): LibrarySection[] {
  if (!query.trim()) return sections;
  return sections
    .map((s) => ({ ...s, tree: filterTree(s.tree, query) }))
    .filter((s) => s.tree.count > 0);
}

/** The first `limit` files of a folder, and how many more there are while it is not expanded. */
export function visibleFiles(
  folder: WrapperFolder,
  expanded: boolean,
  limit = 6,
): { files: WrapperEntry[]; more: number } {
  if (expanded || folder.wrappers.length <= limit) return { files: folder.wrappers, more: 0 };
  return { files: folder.wrappers.slice(0, limit), more: folder.wrappers.length - limit };
}

function fileOf(relpath: string): string {
  return relpath.split("/").pop() ?? relpath;
}

/** The summary's title: the file name without ".py". */
export function summaryTitle(relpath: string): string {
  return fileOf(relpath).replace(/\.py$/, "");
}

/** The wrapper's path as shown: under its search folder's name ("local/wrappers" for the
 * untracked one, so it reads as what it is). */
export function summaryPath(summary: WrapperSummary, local: boolean): string {
  const names = summary.folder.split("/").filter(Boolean);
  const shown = names.slice(local ? -2 : -1).join("/");
  return `${shown}/${summary.relpath}`;
}

/** The line under the description: where it came from, or why there is none. */
export function descriptionNote(summary: WrapperSummary): string | null {
  const file = fileOf(summary.relpath);
  const { source, path } = summary.description;
  if (source === "docstring") return null;
  if (source === "readme") return `From the first paragraph of ${path}: ${file} has no docstring.`;
  const dir = summary.relpath.includes("/")
    ? `${summary.relpath.split("/").slice(0, -1).join("/")}/`
    : "the wrappers folder";
  return `No description: ${file} has no docstring and ${dir} has no README.`;
}

export interface BinaryLine {
  tone: "ok" | "warn" | "muted";
  text: string;
}

/** The "Binary" row of the summary, in words, for the target that was asked. */
export function binaryLine(summary: WrapperSummary, targetLabel: string): BinaryLine {
  if (!summary.loadable) {
    const why = (summary.error ?? "unknown error").split("\n")[0];
    return { tone: "warn", text: `the wrapper does not load: ${why}` };
  }
  const { status, path, detail } = summary.binary;
  if (status === "missing") {
    return { tone: "warn", text: `missing on ${targetLabel}: ${missingReason(summary)}` };
  }
  if (status === "error") {
    const why = detail ? ` (${detail})` : "";
    return { tone: "muted", text: `could not look up the binary here${why}` };
  }
  const label = binaryLabel(summary);
  const found = label.startsWith("found") ? label : `found, ${label}`;
  return { tone: "ok", text: path ? `${found} → ${path}` : found };
}

/** The first wrapper in the order the tree lists them: subfolders first, then the files. */
export function firstWrapper(sections: LibrarySection[]): string {
  const walk = (folder: WrapperFolder): string | undefined => {
    for (const f of folder.folders) {
      const found = walk(f);
      if (found) return found;
    }
    return folder.wrappers[0]?.relpath;
  };
  for (const s of sections) {
    const found = walk(s.tree);
    if (found) return found;
  }
  return "";
}
