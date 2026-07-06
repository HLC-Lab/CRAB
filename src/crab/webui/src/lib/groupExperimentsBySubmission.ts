import type { ReportExperiment } from "@/api/types";

// Groups a flat use-case history (GET /api/jobs/report/{config_name}) into one
// entry per submission (plan 076), instead of one undifferentiated list.
// Mirrors web/api/jobs.py's `_job_basename` fallback: rows with a known
// `record_id` group by it; rows with none (a manual `crab run` never
// submitted through this dashboard) group by their data_dir's basename —
// keep this in sync with that server-side function if it ever changes.
export interface SubmissionGroup {
  key: string;
  recordId: string | null;
  cluster: string;
  system: string;
  submittedAt: string;
  experiments: ReportExperiment[];
}

function jobBasename(relativePath: string): string {
  const parts = relativePath.split("/").filter((p) => p && p !== ".");
  return parts[0] ?? "";
}

export function groupExperimentsBySubmission(experiments: ReportExperiment[]): SubmissionGroup[] {
  const groups: SubmissionGroup[] = [];
  const indexByKey = new Map<string, number>();

  for (const e of experiments) {
    const key = e.record_id ?? `${e.cluster}:${jobBasename(e.relative_path)}`;
    const idx = indexByKey.get(key);
    if (idx === undefined) {
      indexByKey.set(key, groups.length);
      groups.push({
        key,
        recordId: e.record_id ?? null,
        cluster: e.cluster,
        system: e.system,
        submittedAt: e.submitted_at ?? e.timestamp,
        experiments: [e],
      });
    } else {
      groups[idx].experiments.push(e);
    }
  }

  return groups.sort((a, b) => (a.submittedAt < b.submittedAt ? 1 : -1));
}
