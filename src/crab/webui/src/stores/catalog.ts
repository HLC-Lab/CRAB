import { defineStore } from "pinia";
import { ref } from "vue";
import { api, ApiError } from "@/api/client";
import type { BenchmarksResult, NodesResult } from "@/api/types";

// Cluster-side catalog (wrappers + nodes) fetched on demand and cached per
// cluster, so the Author pickers don't re-hit SSH on every open. Keyed by the
// remote's name.

function msg(e: unknown): string {
  return e instanceof ApiError ? e.message : "Unexpected error";
}

export const useCatalogStore = defineStore("catalog", () => {
  const benchmarks = ref<Record<string, BenchmarksResult>>({});
  const nodes = ref<Record<string, NodesResult>>({});
  const busy = ref<Record<string, boolean>>({});
  const error = ref<Record<string, string>>({});

  // The host machine's own wrappers/ (the checkout running crab web), a
  // single catalog, not keyed per cluster, since there's exactly one host.
  const localBenchmarks = ref<BenchmarksResult | null>(null);
  const localBusy = ref(false);
  const localError = ref("");

  async function loadLocalBenchmarks(force = false) {
    if (!force && localBenchmarks.value) return;
    localBusy.value = true;
    localError.value = "";
    try {
      localBenchmarks.value = await api.local.benchmarks();
    } catch (e) {
      localError.value = msg(e);
    } finally {
      localBusy.value = false;
    }
  }

  async function loadBenchmarks(cluster: string, force = false) {
    if (!cluster || (!force && benchmarks.value[cluster])) return;
    busy.value[cluster] = true;
    delete error.value[cluster];
    try {
      benchmarks.value[cluster] = await api.remotes.benchmarks(cluster);
    } catch (e) {
      error.value[cluster] = msg(e);
    } finally {
      busy.value[cluster] = false;
    }
  }

  async function loadNodes(cluster: string, force = false) {
    if (!cluster || (!force && nodes.value[cluster])) return;
    busy.value[cluster] = true;
    delete error.value[cluster];
    try {
      nodes.value[cluster] = await api.remotes.nodes(cluster);
    } catch (e) {
      error.value[cluster] = msg(e);
    } finally {
      busy.value[cluster] = false;
    }
  }

  // Drop a cluster's cache (e.g. on disconnect or manual refresh).
  function forget(cluster: string) {
    delete benchmarks.value[cluster];
    delete nodes.value[cluster];
    delete error.value[cluster];
  }

  return {
    benchmarks,
    nodes,
    busy,
    error,
    loadBenchmarks,
    loadNodes,
    forget,
    localBenchmarks,
    localBusy,
    localError,
    loadLocalBenchmarks,
  };
});
