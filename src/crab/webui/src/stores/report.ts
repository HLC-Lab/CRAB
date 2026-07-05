import { defineStore } from "pinia";
import { ref } from "vue";
import { api, ApiError } from "@/api/client";
import type { ExperimentLogs, UseCaseReport } from "@/api/types";

function msg(e: unknown): string {
  if (!(e instanceof ApiError)) return "Unexpected error";
  return e.detail ? `${e.message}\n${e.detail}` : e.message;
}

// Keys the per-experiment logs maps by "<record_id>/<experiment_name>" — a
// use case can span several job records, so the pair is what's unique.
function experimentKey(recordId: string, experimentName: string): string {
  return `${recordId}/${experimentName}`;
}

export const useReportStore = defineStore("report", () => {
  const report = ref<UseCaseReport | null>(null);
  const loading = ref(false);
  const error = ref<string | null>(null);

  async function fetchReport(configName: string) {
    loading.value = true;
    error.value = null;
    try {
      report.value = await api.jobs.report(configName);
    } catch (e) {
      report.value = null;
      error.value = msg(e);
    } finally {
      loading.value = false;
    }
  }

  // The one experiment whose logs panel is open at a time (mirrors the jobs
  // store's single-open-log convention).
  const openExperimentKey = ref<string | null>(null);
  const experimentLogs = ref<Record<string, ExperimentLogs>>({});
  const experimentLogsBusy = ref<Record<string, boolean>>({});
  const experimentLogsError = ref<Record<string, string>>({});

  async function toggleExperimentLogs(recordId: string, experimentName: string) {
    const key = experimentKey(recordId, experimentName);
    if (openExperimentKey.value === key) {
      openExperimentKey.value = null;
      return;
    }
    openExperimentKey.value = key;
    if (experimentLogs.value[key]) return; // already loaded, just re-open

    experimentLogsBusy.value[key] = true;
    delete experimentLogsError.value[key];
    try {
      experimentLogs.value[key] = await api.jobs.experimentLogs(recordId, experimentName);
    } catch (e) {
      experimentLogsError.value[key] = msg(e);
    } finally {
      experimentLogsBusy.value[key] = false;
    }
  }

  return {
    report,
    loading,
    error,
    fetchReport,
    openExperimentKey,
    experimentLogs,
    experimentLogsBusy,
    experimentLogsError,
    toggleExperimentLogs,
    experimentKey,
  };
});
