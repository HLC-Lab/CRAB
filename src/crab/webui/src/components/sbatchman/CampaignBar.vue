<script setup lang="ts">
// Campaign-level settings. The dashboard writes no environment (ADR-045): jobs
// take theirs from the SbatchMan config, so this card says where to find it and
// holds the `configs:` key that tells SbatchMan which configs file to read.
// (Campaign-wide sweep variables live in the Variables card, next to the group's own.)
import { computed } from "vue";
import { useSbatchmanStore } from "@/stores/sbatchman";

const store = useSbatchmanStore();
// On an umbrella install an empty field writes the umbrella's own configs file: show it.
const derived = computed(() => (store.configsFile.trim() ? "" : store.configsRef));
</script>

<template>
  <div class="bar">
    <label class="configs">
      Configs file
      <input
        v-model="store.configsFile"
        :placeholder="derived || '../../.local/configs.leonardo.yaml'"
      />
      <small v-if="derived">
        Written as the configs: key, relative to this file. Left empty, it uses this cluster's own
        configs file: {{ derived }}.
      </small>
      <small v-else>
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
  color: var(--text2);
  font-size: var(--t-md);
}
input {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.5rem;
  font-family: var(--mono);
  font-size: var(--t-md);
}
input:focus {
  outline: none;
  border-color: var(--accent);
}
.note,
.configs small {
  display: block;
  max-width: 38rem;
  color: var(--text3);
  font-size: var(--t-xs);
}
</style>
