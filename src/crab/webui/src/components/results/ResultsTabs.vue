<script setup lang="ts">
// Small in-page tab pair linking the Results picker and the Compare
// workbench (plan 077 S15) -- not a main-nav entry, Compare stays part of
// the Results section (decision 10).
import { RouterLink } from "vue-router";
</script>

<template>
  <div class="tabs">
    <RouterLink to="/results" class="tab-btn" active-class="active" exact-active-class="active">
      Jobs
    </RouterLink>
    <RouterLink to="/results/compare" class="tab-btn" active-class="active">Compare</RouterLink>
  </div>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: 2px;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 2px;
  width: fit-content;
  margin-bottom: 1rem;
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
</style>
