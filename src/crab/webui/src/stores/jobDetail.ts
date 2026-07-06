import { defineStore } from "pinia";
import { ref } from "vue";
import { api, ApiError } from "@/api/client";
import type { JobDetail } from "@/api/types";

function msg(e: unknown): string {
  if (!(e instanceof ApiError)) return "Unexpected error";
  return e.detail ? `${e.message}\n${e.detail}` : e.message;
}

// Per-job detail view (plan 075): every `crab history` row for one exact
// submission, primary click target from a Jobs card. Separate from
// useReportStore (which covers the cross-time "every run of this use case"
// view) since the two fetch different backend routes and shapes, but a
// JobDetailView still uses useReportStore for the shared per-experiment
// logs/selection state that ExperimentCard.vue reads.
export const useJobDetailStore = defineStore("jobDetail", () => {
  const detail = ref<JobDetail | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);

  async function fetchDetail(recordId: string) {
    loading.value = true;
    error.value = null;
    try {
      detail.value = await api.jobs.experiments(recordId);
    } catch (e) {
      detail.value = null;
      error.value = msg(e);
    } finally {
      loading.value = false;
    }
  }

  return { detail, loading, error, fetchDetail };
});
