import { defineStore } from "pinia";
import { ref } from "vue";
import { api, ApiError } from "@/api/client";
import type { ResultsData } from "@/api/types";

// Same cadence as jobs.ts's SUBMISSION_POLL_INTERVAL_MS: a genuine poll, not
// a "wait N seconds then reveal" timer.
const FETCH_POLL_INTERVAL_MS = 1_000;

function msg(e: unknown): string {
  if (!(e instanceof ApiError)) return "Unexpected error";
  return e.detail ? `${e.message}\n${e.detail}` : e.message;
}

export const useResultsStore = defineStore("results", () => {
  // Loaded/cached-state per job id. `notFetched[id]` distinguishes "we asked
  // and there's genuinely nothing cached yet" from "we haven't asked" or
  // "asking failed" — only the first should show a plain "Fetch results"
  // prompt instead of an error.
  const results = ref<Record<string, ResultsData>>({});
  const notFetched = ref<Record<string, boolean>>({});
  const loadBusy = ref<Record<string, boolean>>({});
  const loadError = ref<Record<string, string>>({});

  const fetchBusy = ref<Record<string, boolean>>({});
  const fetchError = ref<Record<string, string>>({});

  const cacheSize = ref<number | null>(null);
  const cacheSizeBusy = ref(false);
  const clearBusy = ref(false);
  const clearError = ref<string | null>(null);

  async function loadResults(jobId: string) {
    loadBusy.value[jobId] = true;
    delete loadError.value[jobId];
    try {
      results.value[jobId] = await api.jobs.results.get(jobId);
      delete notFetched.value[jobId];
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        notFetched.value[jobId] = true;
      } else {
        loadError.value[jobId] = msg(e);
      }
    } finally {
      loadBusy.value[jobId] = false;
    }
  }

  async function refreshCacheSize() {
    cacheSizeBusy.value = true;
    try {
      cacheSize.value = (await api.jobs.results.cacheSize()).total_bytes;
    } finally {
      cacheSizeBusy.value = false;
    }
  }

  // Polls one in-flight fetch until it resolves — a plain setInterval per
  // fetch (cleared on resolution), same pattern as jobs.ts's pollSubmission,
  // since several jobs' fetches can be in flight independently.
  function pollFetch(jobId: string, fetchId: string) {
    const timer = setInterval(async () => {
      let status;
      try {
        status = await api.jobs.results.fetchStatus(jobId, fetchId);
      } catch (e) {
        clearInterval(timer);
        fetchBusy.value[jobId] = false;
        fetchError.value[jobId] = msg(e);
        return;
      }
      if (status.status === "pending") return;
      clearInterval(timer);
      fetchBusy.value[jobId] = false;
      if (status.status === "done") {
        await loadResults(jobId);
        await refreshCacheSize();
        return;
      }
      fetchError.value[jobId] = status.detail
        ? `${status.message}\n${status.detail}`
        : (status.message ?? "Fetch failed.");
    }, FETCH_POLL_INTERVAL_MS);
  }

  async function fetchResults(jobId: string) {
    fetchBusy.value[jobId] = true;
    delete fetchError.value[jobId];
    try {
      const accepted = await api.jobs.results.fetch(jobId);
      pollFetch(jobId, accepted.fetch_id);
    } catch (e) {
      fetchBusy.value[jobId] = false;
      fetchError.value[jobId] = msg(e);
    }
  }

  async function clearCache() {
    clearBusy.value = true;
    clearError.value = null;
    try {
      await api.jobs.results.clearCache();
      results.value = {};
      notFetched.value = {};
      await refreshCacheSize();
    } catch (e) {
      clearError.value = msg(e);
    } finally {
      clearBusy.value = false;
    }
  }

  return {
    results,
    notFetched,
    loadBusy,
    loadError,
    fetchBusy,
    fetchError,
    cacheSize,
    cacheSizeBusy,
    clearBusy,
    clearError,
    loadResults,
    fetchResults,
    refreshCacheSize,
    clearCache,
  };
});
