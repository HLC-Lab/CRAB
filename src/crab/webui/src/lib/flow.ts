// The experiment flow diagram and the per-app allocation summary behind its
// badges. Pure (no Vue/IO). Types only are imported from config.ts, so there is no
// runtime cycle; config.ts re-exports these names for its callers.

import type { AllocationDraft, AppDraft } from "@/lib/config";
import { hasAllocation, isNumeric as _numeric } from "@/lib/configShared";
import { sliceColor, sliceName } from "@/lib/slices";

// -- Flow diagram ------------------------------------------------------------
// An abstract (not-to-scale) view of an experiment's apps: columns are
// sequential stages (an "after app N" app sits one stage right of N), apps in
// the same column run in parallel. End-behavior (victim/aggressor/timed) is
// not shown here; it's already surfaced by each app's own "Ends" dropdown in
// the editor. `measured` reflects `collect`. Duration is intentionally not
// depicted.

export interface FlowNode {
  index: number;
  name: string;
  measured: boolean; // the `collect` flag: metrics are parsed and stored
  note: string; // small qualifier, e.g. "+5s"
  group?: string; // node-group name, when the effective allocation is partitioned
  color?: string; // the group's slice color, when grouped (see lib/slices.ts)
  nodes?: number; // approximate node count for this app (sizing aid, not engine placement)
}

/** A flow node plus the apps that depend on it (their `start` is "s<index>"). */
export interface FlowTree extends FlowNode {
  children: FlowTree[];
}

function _wrapperName(path: string): string {
  // Full relative path (so suite/benchmark is visible), minus the .py extension.
  return path.trim().replace(/\.py$/, "");
}

/**
 * Approximate per-app node allocation, for the flow-diagram badges. Returns a
 * value only when allocation is actually configured (named groups, or a split):
 * we never guess the engine's default placement. `share% × numnodes` for groups
 * (equal split when shares are unset), `split% × numnodes` for by-app. Indexed
 * by app position. Best-effort sizing aid — NOT authoritative placement.
 */
export function allocationSummary(
  apps: AppDraft[],
  alloc: AllocationDraft,
  numnodes: string,
): { group?: string; color?: string; nodes?: number }[] {
  const out = apps.map(() => ({}) as { group?: string; color?: string; nodes?: number });
  if (!apps.length || !hasAllocation(alloc)) return out;
  const n = parseInt(numnodes.trim(), 10);
  const total = Number.isFinite(n) && n > 0 ? n : 0;

  if (alloc.by === "groups") {
    // Keyed by the resolved display name (own name, else the positional "group
    // N" fallback), matching how AuthorView's namedSlices/dropdown resolve group
    // identity — otherwise an unnamed slice's apps would show correctly in the
    // editor/dropdown but lose their color/node-count badge here.
    const indexed = alloc.partitions.map((p, i) => ({ p, i, name: sliceName(p.name, i) }));
    const allShared =
      indexed.length > 0 && indexed.every(({ p }) => p.share.trim() && _numeric(p.share));
    const groupNodes: Record<string, number> = {};
    const groupColor: Record<string, string> = {};
    for (const { p, i, name } of indexed) {
      groupColor[name] = sliceColor(i);
      if (total) {
        groupNodes[name] = allShared
          ? Math.round((Number(p.share) / 100) * total)
          : Math.floor(total / indexed.length);
      }
    }
    apps.forEach((a, i) => {
      const g = a.partition.trim();
      out[i] = {
        group: g || undefined,
        nodes: g && g in groupNodes ? groupNodes[g] : undefined,
        color: g && g in groupColor ? groupColor[g] : undefined,
      };
    });
  } else if (alloc.split.trim() && total) {
    const split = alloc.split.split(",").map((s) => Number(s.trim()));
    if (split.length === apps.length && split.every((x) => !Number.isNaN(x))) {
      apps.forEach((_, i) => (out[i] = { nodes: Math.round((split[i] / 100) * total) }));
    }
  }
  return out;
}

/**
 * Build the dependency forest of an experiment's apps. An app whose `start` is
 * "s<index>" (startKind "after") becomes a child of that app, drawn directly off
 * it; apps that start at run start or after a delay are roots (they run together).
 * Cycles and forward/invalid references degrade gracefully to roots. Pass the
 * effective allocation + numnodes to annotate nodes with allocation badges.
 */
export function flowForest(apps: AppDraft[], alloc?: AllocationDraft, numnodes = ""): FlowTree[] {
  const badges = alloc ? allocationSummary(apps, alloc, numnodes) : [];
  const nodes: FlowTree[] = apps.map((a, i) => ({
    index: i,
    name: _wrapperName(a.path) || `app ${i}`,
    measured: a.collect,
    note: a.startKind === "delay" ? `+${a.startDelay || 0}s` : "",
    group: badges[i]?.group,
    color: badges[i]?.color,
    nodes: badges[i]?.nodes,
    children: [],
  }));

  // The app each one starts after, if any and valid.
  const parentOf = apps.map((a, i) => {
    if (a.startKind !== "after") return -1;
    const ref = parseInt(a.startAfter, 10);
    return Number.isInteger(ref) && ref >= 0 && ref < apps.length && ref !== i ? ref : -1;
  });
  // Drop a parent link that would close a cycle, so rendering can't recurse forever.
  const wouldCycle = (i: number): boolean => {
    const seen = new Set<number>([i]);
    for (let p = parentOf[i]; p >= 0; p = parentOf[p]) {
      if (seen.has(p)) return true;
      seen.add(p);
    }
    return false;
  };
  const roots: FlowTree[] = [];
  apps.forEach((_, i) => {
    const p = parentOf[i];
    if (p >= 0 && !wouldCycle(i)) nodes[p].children.push(nodes[i]);
    else roots.push(nodes[i]);
  });
  return roots;
}
