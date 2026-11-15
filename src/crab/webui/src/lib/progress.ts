// Text for the progress line (what a slow step is doing, what it waits for, how long it has
// waited). Pure functions: the elapsed time is `now - started_at`, both in epoch seconds, as the
// backend sends them. No long dashes in any text (ADR-012).
import type { ProgressStage } from "@/api/types";

/** "12 s", "1 min 5 s", "2 min": whole seconds, never negative. */
export function elapsedText(startedAt: number, now: number): string {
  const total = Math.max(0, Math.floor(now - startedAt));
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return seconds === 0 ? `${minutes} min` : `${minutes} min ${seconds} s`;
}

/** "gives up after 30 s" (minutes for whole minutes), or "" when the step has no limit. */
export function limitText(timeoutS: number | null | undefined): string {
  if (timeoutS == null) return "";
  const limit = timeoutS >= 60 && timeoutS % 60 === 0 ? `${timeoutS / 60} min` : `${timeoutS} s`;
  return `gives up after ${limit}`;
}

/** "Connecting to the cluster: waiting for the login node ...". */
export function stageText(stage: Pick<ProgressStage, "label" | "waiting_for">): string {
  return `${stage.label}: waiting for ${stage.waiting_for}`;
}
