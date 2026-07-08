// Shared Slurm/experiment status classification, used by both the flat Jobs
// list and the per-use-case experiment report so the two views agree on what
// counts as terminal/ok/danger/etc.

// Never re-polled once reached (mirrors api/jobs.py's _TERMINAL_STATES).
export const TERMINAL_STATES = new Set([
  "COMPLETED",
  "FAILED",
  "CANCELLED",
  "TIMEOUT",
  "OUT_OF_MEMORY",
  "NODE_FAIL",
  "PREEMPTED",
  "BOOT_FAIL",
  "DEADLINE",
  "REVOKED",
]);

export function isTerminal(state: string): boolean {
  return TERMINAL_STATES.has(state);
}

export function stateClass(state: string): string {
  if (state === "COMPLETED") return "ok";
  if (["FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL", "DEADLINE"].includes(state))
    return "danger";
  if (state === "CANCELLED" || state === "REVOKED") return "muted";
  if (state === "UNKNOWN") return "warn";
  return "active"; // RUNNING, PENDING, and anything else Slurm reports live
}

// A terminal state worth offering "Rerun" for — the same set stateClass
// colors "danger". Deliberately excludes CANCELLED/REVOKED: those were
// stopped on purpose, not a failure to recover from.
export function isFailureState(state: string): boolean {
  return ["FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL", "DEADLINE"].includes(
    state,
  );
}

// The experiment names worth offering a one-click "Rerun failed" for (plan
// 076's quick action) — shared by the per-job detail view and the grouped
// use-case history view so the two don't drift.
export function failedExperimentNames(
  experiments: { status: string; experiment_name: string }[],
): string[] {
  return experiments.filter((e) => isFailureState(e.status)).map((e) => e.experiment_name);
}

// Plan 081: an experiment's overall status latches to FAILED on its first
// bad internal run and never resets, even though every OTHER run in that
// same min/maxruns loop that succeeded still has its data collected. This
// tells "3 of 10 runs failed, here's data from the other 7" apart from
// "everything failed" -- null when there's nothing to show (a
// `metadata.csv` written before plan 081, never migrated by design, reports
// both fields as "", and `Number("")` is 0) or when nothing actually
// failed. Loosely typed so it works against both `ReportExperiment` and the
// results view's `ExperimentRunStatus`, which share this shape.
export function runFailureNote(experiment: {
  total_runs?: string;
  failed_runs?: string;
}): string | null {
  const failed = Number(experiment.failed_runs);
  const total = Number(experiment.total_runs);
  if (!Number.isFinite(failed) || !Number.isFinite(total) || failed <= 0 || total <= 0) {
    return null;
  }
  return `${failed}/${total} runs failed`;
}
