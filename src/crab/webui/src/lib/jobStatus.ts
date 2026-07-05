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
