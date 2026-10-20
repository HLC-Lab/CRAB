// Pure helpers for the Wrappers section: targets, the Library tree, drift and labels for a
// target's `crab wrappers list --json` catalog. No fetch/DOM, so the unit suite imports them directly.

import type { CloneStatus, RemoteListItem, WrapperEntry, WrapperTarget } from "@/api/types";

/** What the binary helpers read: a list entry and a `wrappers show` summary both have it. */
export type BinaryInfo = Pick<WrapperEntry, "loadable" | "benchmark_id" | "executable" | "binary">;

const FOUND = new Set(["config", "receipt", "path"]);

const LABELS: Record<string, string> = {
  config: "set in config",
  receipt: "receipt",
  path: "found on PATH",
  missing: "missing",
  error: "lookup failed",
};

export function binaryLabel(w: BinaryInfo): string {
  if (!w.loadable) return "cannot load";
  return LABELS[w.binary.status] ?? w.binary.status;
}

/** An import writes a receipt, so it needs a loadable wrapper with a receipt id and no binary
 * found. A failed lookup ("error") is a bug in the wrapper: a receipt would not fix it, and it
 * could overwrite a receipt the app's other wrappers share. */
export function canImport(w: BinaryInfo): boolean {
  return w.loadable && !!w.benchmark_id && w.binary.status === "missing";
}

/** A wrapper needs attention: it cannot load, or no binary was found for it. */
export function hasProblem(w: WrapperEntry): boolean {
  return !w.loadable || !FOUND.has(w.binary.status);
}

// -- Targets ---------------------------------------------------------------------------

/** "local" is the laptop's reserved target id: the backend routes it to this machine. */
export const LOCAL_TARGET = "local";

/** This laptop first, then each cluster profile in the given order. A profile literally named
 * "local" is skipped: the name belongs to the laptop. */
export function wrapperTargets(
  remotes: Pick<RemoteListItem, "name" | "connected">[],
): WrapperTarget[] {
  const targets: WrapperTarget[] = [
    { id: LOCAL_TARGET, label: "This laptop", connected: true, local: true },
  ];
  for (const r of remotes) {
    if (r.name === LOCAL_TARGET) continue;
    targets.push({ id: r.name, label: r.name, connected: r.connected, local: false });
  }
  return targets;
}

// -- Library tree ------------------------------------------------------------------------

/** A folder of the Library tree. `count` and `problems` cover everything below it. */
export interface WrapperFolder {
  name: string;
  /** Folder path without a trailing slash, e.g. "blink/alltoall" ("" for the root). */
  path: string;
  folders: WrapperFolder[];
  /** The files directly in this folder, in catalog order. */
  wrappers: WrapperEntry[];
  count: number;
  problems: number;
}

/** Fills `count` and `problems` bottom-up and sorts the subfolders by name. */
function tally(node: WrapperFolder): WrapperFolder {
  node.folders.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  node.count = node.wrappers.length;
  node.problems = node.wrappers.filter(hasProblem).length;
  for (const f of node.folders) {
    tally(f);
    node.count += f.count;
    node.problems += f.problems;
  }
  return node;
}

function emptyFolder(name: string, path: string): WrapperFolder {
  return { name, path, folders: [], wrappers: [], count: 0, problems: 0 };
}

/** The catalog as nested folders (suite, group, ...). Top-level files sit in the root's
 * `wrappers`; subfolders are in alphabetical order. */
export function buildTree(wrappers: WrapperEntry[]): WrapperFolder {
  const root = emptyFolder("", "");
  for (const w of wrappers) {
    const parts = w.relpath.split("/");
    let node = root;
    for (const name of parts.slice(0, -1)) {
      const path = node.path ? `${node.path}/${name}` : name;
      let next = node.folders.find((f) => f.name === name);
      if (!next) {
        next = emptyFolder(name, path);
        node.folders.push(next);
      }
      node = next;
    }
    node.wrappers.push(w);
  }
  return tally(root);
}

function keep(node: WrapperFolder, match: (w: WrapperEntry) => boolean): WrapperFolder {
  const out = emptyFolder(node.name, node.path);
  out.wrappers = node.wrappers.filter(match);
  for (const f of node.folders) {
    const kept = keep(f, match);
    if (kept.count > 0) out.folders.push(kept);
  }
  return tally(out);
}

/** Only the wrappers whose relpath contains `query` (case-insensitive), with their ancestor
 * folders and recomputed counts. An empty query returns the tree itself. */
export function filterTree(tree: WrapperFolder, query: string): WrapperFolder {
  const q = query.trim().toLowerCase();
  if (!q) return tree;
  return keep(tree, (w) => w.relpath.toLowerCase().includes(q));
}

/** The wrappers that need attention, split by what to do: look at the wrapper itself (broken:
 * cannot load) or import a binary (missing). `lookupFailed` (the binary lookup itself errored,
 * e.g. CRAB_SYSTEM unset) is informational: it is not counted as a problem. Catalog order. */
export function needsAttention(wrappers: WrapperEntry[]): {
  broken: WrapperEntry[];
  missing: WrapperEntry[];
  lookupFailed: WrapperEntry[];
} {
  const broken: WrapperEntry[] = [];
  const missing: WrapperEntry[] = [];
  const lookupFailed: WrapperEntry[] = [];
  for (const w of wrappers) {
    if (!w.loadable) broken.push(w);
    else if (w.binary.status === "missing") missing.push(w);
    else if (w.binary.status === "error") lookupFailed.push(w);
  }
  return { broken, missing, lookupFailed };
}

/** How many wrappers need attention: those that cannot load plus those with no binary. */
export function attentionCount(wrappers: WrapperEntry[]): number {
  const { broken, missing } = needsAttention(wrappers);
  return broken.length + missing.length;
}

/** Why a wrapper's binary is missing, naming what the lookup searched for. A wrapper with no
 * receipt id cannot take an imported binary, so the reason says where to set it instead. */
export function missingReason(w: BinaryInfo): string {
  const base = "no receipt, config entry or PATH match";
  const name = w.executable ?? w.benchmark_id;
  const reason = name ? `${base} for ${name}` : base;
  if (canImport(w)) return reason;
  return `${reason}; no receipt id in this wrapper: set the app's binary in the experiment config`;
}

/** The first seven characters of a commit id ("" when there is none). */
export function shortSha(sha: string | null): string {
  return sha ? sha.slice(0, 7) : "";
}

// -- Drift (a clone against GitHub) ------------------------------------------------------

export interface Drift {
  tone: "ok" | "warn" | "muted";
  text: string;
  canPull: boolean;
  /** The Pull button's label (only meaningful when `canPull`). */
  pullLabel: string;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** How a target's crab-wrappers clone compares with GitHub, in words for the Status tab. */
export function drift(status: CloneStatus): Drift {
  if (!status.clone) {
    if ((status.detail ?? "").startsWith("missing")) {
      return { tone: "warn", text: "not cloned yet", canPull: true, pullLabel: "Clone" };
    }
    return { tone: "muted", text: "not a git clone", canPull: false, pullLabel: "Pull" };
  }
  if (status.modified.length > 0) {
    return {
      tone: "warn",
      text: plural(status.modified.length, "modified file", "modified files"),
      canPull: false,
      pullLabel: "Pull",
    };
  }
  switch (status.state) {
    case "up to date":
      return { tone: "ok", text: "up to date", canPull: false, pullLabel: "Pull" };
    case "behind": {
      const n = status.behind;
      if (n == null)
        return { tone: "warn", text: "behind GitHub", canPull: true, pullLabel: "Pull" };
      return {
        tone: "warn",
        text: `${plural(n, "commit", "commits")} behind`,
        canPull: true,
        pullLabel: `Pull ${plural(n, "commit", "commits")}`,
      };
    }
    case "ahead":
      return {
        tone: "muted",
        text:
          status.ahead == null
            ? "ahead of GitHub"
            : `${plural(status.ahead, "commit", "commits")} ahead of GitHub`,
        canPull: false,
        pullLabel: "Pull",
      };
    case "diverged":
      return { tone: "warn", text: "diverged from GitHub", canPull: false, pullLabel: "Pull" };
    default:
      return {
        tone: "muted",
        text: status.upstream.error ? "GitHub not reachable" : "unknown",
        canPull: false,
        pullLabel: "Pull",
      };
  }
}

/** The GitHub column's lines: a clone that is behind and also has edits lists both. */
export function driftLines(status: CloneStatus): string[] {
  const main = drift(status).text;
  if (status.modified.length > 0 && status.state === "behind") {
    return [drift({ ...status, modified: [] }).text, main];
  }
  return [main];
}

/** The first `limit` modified files, and how many more there are. */
export function modifiedPreview(files: string[], limit = 10): { shown: string[]; more: number } {
  return { shown: files.slice(0, limit), more: Math.max(files.length - limit, 0) };
}

/** "just now", "5 min ago", "2 h ago" for a gap in milliseconds. */
export function relativeTime(elapsedMs: number): string {
  const minutes = Math.floor(Math.max(elapsedMs, 0) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  return `${Math.floor(minutes / 60)} h ago`;
}
