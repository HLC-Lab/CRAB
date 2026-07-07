<script setup lang="ts">
// Small in-page tab pair linking the Results picker and the Compare
// workbench (plan 077 S15) -- not a main-nav entry, Compare stays part of
// the Results section (decision 10). Also carries the one manual "Refresh"
// action for the shared cross-cluster index (plan 079): both pages call
// `loadIndex()` on mount, which is now a no-op once already loaded this
// session (see stores/results.ts), so this button is the only way to force
// a reload without leaving the page.
import { RouterLink } from "vue-router";
import { useResultsStore } from "@/stores/results";

const results = useResultsStore();
</script>

<template>
  <div class="tabs-row">
    <div class="tabs">
      <RouterLink to="/results" class="tab-btn" active-class="active" exact-active-class="active">
        Jobs
      </RouterLink>
      <RouterLink to="/results/compare" class="tab-btn" active-class="active">Compare</RouterLink>
    </div>
    <button
      type="button"
      class="refresh-btn"
      :disabled="results.indexBusy"
      @click="results.loadIndex(true)"
    >
      {{ results.indexBusy ? "Refreshing…" : "Refresh" }}
    </button>
  </div>
</template>

<style scoped>
.tabs-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1rem;
}
.tabs {
  display: flex;
  gap: 2px;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 2px;
  width: fit-content;
}
.tab-btn {
  display: inline-block;
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 0.3rem 0.9rem;
  border-radius: 4px;
  font-family: var(--sans);
  font-size: var(--t-sm);
  color: var(--text2);
  text-decoration: none;
}
.tab-btn.active {
  background: var(--bg1);
  color: var(--accent);
}
.refresh-btn {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.3rem 0.8rem;
  cursor: pointer;
  font-family: var(--sans);
  font-size: var(--t-sm);
}
.refresh-btn:hover {
  border-color: var(--accent);
}
.refresh-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
