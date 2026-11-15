import { defineStore } from "pinia";
import { ref } from "vue";
import { api } from "@/api/client";
import type { ProgressResponse } from "@/api/types";

const POLL_MS = 1000; // ADR-019: one poll a second, no stream

// Polls `GET /api/remotes/{name}/progress` while a slow call is in flight. The key is a cluster
// name, or `local` for this laptop. Several callers may watch one key (a connect and an install);
// polling stops when the last one stops.
export const useProgressStore = defineStore("progress", () => {
  const current = ref<Record<string, ProgressResponse>>({});
  const watchers: Record<string, { count: number; timer: ReturnType<typeof setInterval> }> = {};
  const inFlight = new Set<string>();

  async function poll(cluster: string) {
    if (inFlight.has(cluster)) return;
    inFlight.add(cluster);
    try {
      const res = await api.remotes.progress(cluster);
      // An answer that lands after the last stop must not bring the line back.
      if (watchers[cluster]) current.value[cluster] = res;
    } catch {
      // A failed poll keeps the last answer; the call being watched reports its own error.
    } finally {
      inFlight.delete(cluster);
    }
  }

  function watch(cluster: string) {
    const existing = watchers[cluster];
    if (existing) {
      existing.count += 1;
      return;
    }
    watchers[cluster] = { count: 1, timer: setInterval(() => void poll(cluster), POLL_MS) };
    void poll(cluster);
  }

  function stop(cluster: string) {
    const w = watchers[cluster];
    if (!w) return;
    w.count -= 1;
    if (w.count > 0) return;
    clearInterval(w.timer);
    delete watchers[cluster];
    delete current.value[cluster];
  }

  return { current, watch, stop };
});
