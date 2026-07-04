<script setup lang="ts">
// GLOBAL · Run settings: convergence/output options plus Slurm directives,
// both bound directly to the draft's global options.
import { useAuthorStore } from "@/stores/author";
import OptionsFields from "@/components/OptionsFields.vue";
import SbatchEditor from "@/components/SbatchEditor.vue";

const store = useAuthorStore();
const d = store.draft;
</script>

<template>
  <h2 class="pane-title">Run settings</h2>
  <OptionsFields :options="d.options" />
  <h3 class="section-title">Slurm directives</h3>
  <SbatchEditor :sbatch="d.sbatch" />
</template>

<style scoped>
.pane-title {
  font-family: var(--sans);
  font-size: var(--t-lg);
  color: var(--text);
}
.section-title {
  font-family: var(--sans);
  font-size: var(--t-sm);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text3);
  margin-top: -0.3rem;
}
</style>
