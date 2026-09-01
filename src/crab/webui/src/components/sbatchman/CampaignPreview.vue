<script setup lang="ts">
// Read-only preview of the composed SbatchMan jobs YAML (mirrors JsonPanel's
// layout), plus the destination picker and Write/Launch actions (plan 084
// S8). Presentational only — the campaign store (owned by SbatchmanView)
// holds the write/launch state and does the API calls.
import { ref } from "vue";
import type { SbatchmanLaunchResult, SbatchmanWriteResult } from "@/api/types";

const props = defineProps<{
  yaml: string;
  totalJobs: number;
  connectedClusters: string[];
  destination: string;
  busy: boolean;
  error: string | null;
  lastWrite: SbatchmanWriteResult | null;
  lastLaunch: SbatchmanLaunchResult | null;
}>();
const emit = defineEmits<{
  "update:destination": [string];
  write: [];
  launch: [];
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

    <div class="launch-section">
      <label
        >Cluster
        <select
          :value="destination"
          @change="emit('update:destination', ($event.target as HTMLSelectElement).value)"
        >
          <option value="" disabled>Choose a connected cluster…</option>
          <option v-for="c in connectedClusters" :key="c" :value="c">{{ c }}</option>
        </select>
      </label>

      <p v-if="error" class="banner err">{{ error }}</p>

      <div class="actions">
        <button class="btn" :disabled="busy || !destination" @click="emit('write')">
          {{ busy ? "Writing…" : "Write files" }}
        </button>
        <button
          class="btn"
          :disabled="busy || !lastWrite"
          title="Write the campaign first"
          @click="emit('launch')"
        >
          {{ busy ? "Running…" : "Run sbatchman launch" }}
        </button>
      </div>

      <p v-if="lastWrite" class="out">
        Written to <b>{{ lastWrite.remote_path }}</b>
        <span class="muted">(local copy: {{ lastWrite.local_path }})</span>
      </p>
      <div v-if="lastLaunch" class="out">
        <p :class="lastLaunch.ok ? 'ok' : 'err'">
          {{ lastLaunch.ok ? "sbatchman launch succeeded." : "sbatchman launch failed." }}
        </p>
        <pre v-if="lastLaunch.stdout">{{ lastLaunch.stdout }}</pre>
        <pre v-if="lastLaunch.stderr" class="stderr">{{ lastLaunch.stderr }}</pre>
      </div>
    </div>
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

.launch-section {
  padding: 0.75rem;
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.launch-section label {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  color: var(--text2);
  font-size: var(--t-sm);
}
select {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.5rem;
  font-family: var(--sans);
  font-size: var(--t-md);
}
.actions {
  display: flex;
  gap: 0.5rem;
}
.btn {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.8rem;
  cursor: pointer;
  font-family: var(--sans);
  font-size: var(--t-sm);
}
.btn:hover:not(:disabled) {
  border-color: var(--accent);
}
.btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.banner {
  padding: 0.4rem 0.6rem;
  border-radius: var(--r);
  font-size: var(--t-sm);
  margin: 0;
}
.banner.err {
  background: rgba(245, 101, 101, 0.12);
  color: var(--danger);
  border: 1px solid var(--danger);
}
.out {
  font-size: var(--t-sm);
  color: var(--text2);
}
.out b {
  color: var(--text);
}
.out .muted {
  color: var(--text3);
}
.out .ok {
  color: var(--ok);
  margin: 0 0 0.3rem;
}
.out .err {
  color: var(--danger);
  margin: 0 0 0.3rem;
}
.out pre {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 0.5rem;
  font-size: var(--t-xs);
  white-space: pre-wrap;
  margin: 0 0 0.4rem;
}
.out pre.stderr {
  color: var(--danger);
}
</style>
