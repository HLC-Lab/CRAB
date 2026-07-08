// Pure helpers over the Results picker's cross-cluster index (plan 077
// decision 12): a legible staleness indicator and a stable sort order, no
// instructional blurb needed once real per-job metadata is on screen.
import type { ResultsJobEntry } from "@/api/types";

export type StalenessTone = "muted" | "warn" | "ok";

export interface Staleness {
  label: string;
  tone: StalenessTone;
}

export function describeStaleness(entry: ResultsJobEntry): Staleness {
  if (!entry.cached) return { label: "Not fetched yet", tone: "muted" };
  if (entry.possibly_stale) return { label: "Possibly stale", tone: "warn" };
  return { label: "Up to date", tone: "ok" };
}

/** Most recently submitted first; entries with no known `submitted_at` sort last. */
export function sortEntries(entries: ResultsJobEntry[]): ResultsJobEntry[] {
  return [...entries].sort((a, b) => {
    if (!a.submitted_at && !b.submitted_at) return 0;
    if (!a.submitted_at) return 1;
    if (!b.submitted_at) return -1;
    return b.submitted_at.localeCompare(a.submitted_at);
  });
}

export interface EntryFilters {
  search: string;
  clusters: Set<string>;
  staleness: Set<string>;
}

/** Search matches job_basename (substring, case-insensitive); `clusters`/
 * `staleness` are AND'd together, each empty meaning "no filter" for that
 * dimension -- same "no chip selected shows everything" convention Jobs'
 * own filter chips already use. `staleness` values are the same labels
 * `describeStaleness` produces, so the two functions share one vocabulary. */
export function filterEntries(
  entries: ResultsJobEntry[],
  filters: EntryFilters,
): ResultsJobEntry[] {
  const search = filters.search.trim().toLowerCase();
  return entries.filter((entry) => {
    if (search && !entry.job_basename.toLowerCase().includes(search)) return false;
    if (filters.clusters.size && !filters.clusters.has(entry.cluster)) return false;
    if (filters.staleness.size && !filters.staleness.has(describeStaleness(entry).label)) {
      return false;
    }
    return true;
  });
}
