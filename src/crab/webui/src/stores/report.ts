import { defineStore } from "pinia";
import { computed, ref } from "vue";
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

  // Per-experiment rerun selection ("Rerun selected" only makes sense within
  // one job submission at a time, since that's the only thing that carries a
  // config_snapshot to resubmit — the UI disables the action across a mixed
  // selection rather than guessing which snapshot to use).
  const selected = ref<Set<string>>(new Set());

  function toggleSelected(recordId: string, experimentName: string) {
    const key = experimentKey(recordId, experimentName);
    if (selected.value.has(key)) selected.value.delete(key);
    else selected.value.add(key);
  }
  function clearSelected() {
    selected.value.clear();
  }
  const selectedRecordIds = computed(
    () => new Set([...selected.value].map((key) => key.split("/")[0])),
  );

  // Explicit selection mode (plan 076): checkboxes are anonymous controls
  // until you understand what they do, so they only render once a user has
  // deliberately opted into "select experiments to rerun" rather than always
  // being visible.
  const selectionMode = ref(false);
  function toggleSelectionMode() {
    if (selectionMode.value) exitSelectionMode();
    else selectionMode.value = true;
  }
  function exitSelectionMode() {
    selectionMode.value = false;
    clearSelected();
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
    selected,
    toggleSelected,
    clearSelected,
    selectedRecordIds,
    selectionMode,
    toggleSelectionMode,
    exitSelectionMode,
  };
});
