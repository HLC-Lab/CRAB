<script setup lang="ts">
// Campaign-level settings. The dashboard writes no environment (ADR-045): jobs
// take theirs from the SbatchMan config, so this card says where to find it and
// holds the `configs:` key that tells SbatchMan which configs file to read.
// (Campaign-wide sweep variables live in the Variables card, next to the group's own.)
import { useSbatchmanStore } from "@/stores/sbatchman";

const store = useSbatchmanStore();
</script>

<template>
  <div class="bar">
    <label class="configs">
      Configs file
      <input v-model="store.configsFile" placeholder="../../.local/configs.leonardo.yaml" />
      <small>
        Written as the configs: key, relative to this file. Leave it empty to write no key.
      </small>
    </label>
    <small class="note">
      Jobs take their environment from the SbatchMan config. Define it in your SbatchMan configs
      file (see the integration guide).
    </small>
  </div>
</template>

<style scoped>
.bar {
  display: flex;
  flex-direction: column;
  gap: var(--s-2, 0.5rem);
}
.configs {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  max-width: 38rem;
}
.note,
.configs small {
  display: block;
  max-width: 38rem;
  color: var(--text3);
  font-size: var(--t-xs);
}
</style>
