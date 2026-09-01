<script setup lang="ts">
// Read-only preview of the composed SbatchMan jobs YAML, mirroring JsonPanel's
// layout. Copy-only for now; writing/launching it is S7/S8.
import { ref } from "vue";

const props = defineProps<{
  yaml: string;
  totalJobs: number;
}>();

const copied = ref(false);
async function copyYaml() {
  try {
    await navigator.clipboard.writeText(props.yaml);
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch {
    /* clipboard blocked; the YAML view is still available to copy by hand */
  }
}
</script>

<template>
  <aside class="yamlpane">
    <header>
      <span>jobs.yaml · {{ totalJobs }} job{{ totalJobs === 1 ? "" : "s" }}</span>
      <button class="link-btn" @click="copyYaml">{{ copied ? "Copied ✓" : "Copy" }}</button>
    </header>
    <pre>{{ yaml }}</pre>
  </aside>
</template>

<style scoped>
.link-btn {
  background: transparent;
  border: none;
  color: var(--text2);
  cursor: pointer;
  font-family: var(--sans);
  font-size: var(--t-sm);
  padding: 0;
}
.link-btn:hover {
  color: var(--text);
}

.yamlpane {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  width: 26rem;
  max-height: 40rem;
  overflow: auto;
  display: flex;
  flex-direction: column;
}
.yamlpane header {
  position: sticky;
  top: 0;
  background: var(--bg2);
  color: var(--text2);
  padding: 0.4rem 0.75rem;
  font-size: var(--t-sm);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  z-index: 1;
}
.yamlpane pre {
  padding: 0.75rem;
  font-size: var(--t-sm);
  white-space: pre-wrap;
  color: var(--text2);
  margin: 0;
}
</style>
