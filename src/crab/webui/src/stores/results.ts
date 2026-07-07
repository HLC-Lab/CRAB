import { defineStore } from "pinia";
import { ref } from "vue";
import { api, ApiError } from "@/api/client";
import { resultsKey } from "@/lib/jobKey";
import type { ResultsData } from "@/api/types";

// Same cadence as jobs.ts's SUBMISSION_POLL_INTERVAL_MS: a genuine poll, not
// a "wait N seconds then reveal" timer.
const FETCH_POLL_INTERVAL_MS = 1_000;

function msg(e: unknown): string {
  if (!(e instanceof ApiError)) return "Unexpected error";
  return e.detail ? `${e.message}\n${e.detail}` : e.message;
}

export const useResultsStore = defineStore("results", () => {
  // Loaded/cached-state per (cluster, system, jobBasename) -- plan 077 moves
  // job identity off the local registry, so a record id can't key this map
  // (a CLI-only job has none). `notFetched[key]` distinguishes "we asked and
  // there's genuinely nothing cached yet" from "we haven't asked" or "asking
  // failed" — only the first should show a plain "Fetch results" prompt
  // instead of an error.
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

  async function loadResults(cluster: string, system: string, jobBasename: string) {
    const key = resultsKey(cluster, system, jobBasename);
    loadBusy.value[key] = true;
    delete loadError.value[key];
    try {
      results.value[key] = await api.results.get(cluster, system, jobBasename);
      delete notFetched.value[key];
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        notFetched.value[key] = true;
      } else {
        loadError.value[key] = msg(e);
      }
    } finally {
      loadBusy.value[key] = false;
    }
  }

  async function refreshCacheSize() {
    cacheSizeBusy.value = true;
    try {
      cacheSize.value = (await api.results.cacheSize()).total_bytes;
    } finally {
      cacheSizeBusy.value = false;
    }
  }

  // Polls one in-flight fetch until it resolves — a plain setInterval per
  // fetch (cleared on resolution), same pattern as jobs.ts's pollSubmission,
  // since several jobs' fetches can be in flight independently.
  function pollFetch(cluster: string, system: string, jobBasename: string, fetchId: string) {
    const key = resultsKey(cluster, system, jobBasename);
    const timer = setInterval(async () => {
      let status;
      try {
        status = await api.results.fetchStatus(cluster, system, jobBasename, fetchId);
      } catch (e) {
        clearInterval(timer);
        fetchBusy.value[key] = false;
        fetchError.value[key] = msg(e);
        return;
      }
      if (status.status === "pending") return;
      clearInterval(timer);
      fetchBusy.value[key] = false;
      if (status.status === "done") {
        await loadResults(cluster, system, jobBasename);
        await refreshCacheSize();
        return;
      }
      fetchError.value[key] = status.detail
        ? `${status.message}\n${status.detail}`
        : (status.message ?? "Fetch failed.");
    }, FETCH_POLL_INTERVAL_MS);
  }

  async function fetchResults(cluster: string, system: string, jobBasename: string) {
    const key = resultsKey(cluster, system, jobBasename);
    fetchBusy.value[key] = true;
    delete fetchError.value[key];
    try {
      const accepted = await api.results.fetch(cluster, system, jobBasename);
      pollFetch(cluster, system, jobBasename, accepted.fetch_id);
    } catch (e) {
      fetchBusy.value[key] = false;
      fetchError.value[key] = msg(e);
    }
  }

  async function clearCache() {
    clearBusy.value = true;
    clearError.value = null;
    try {
      await api.results.clearCache();
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
