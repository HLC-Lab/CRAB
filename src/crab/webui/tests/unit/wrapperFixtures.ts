/**
 * Fixture builders shared by the wrappers unit specs (not a spec itself: vitest only collects
 * `*.spec.ts`).
 */
import type { CloneStatus, WrapperEntry } from "@/api/types";

export function entry(
  relpath: string,
  status: string,
  extra: Partial<WrapperEntry> = {},
): WrapperEntry {
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

export function clone(extra: Partial<CloneStatus> = {}): CloneStatus {
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
