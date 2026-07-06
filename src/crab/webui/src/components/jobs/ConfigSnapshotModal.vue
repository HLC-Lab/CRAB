<script setup lang="ts">
// Read-only view of the config a job was submitted with (item 6, plan 075).
// A job only carries a copy (config_snapshot), not a live link back to a
// library entry (ad-hoc submits have no entry at all) - so this shows the
// exact snapshot, not an editable/live config.
import { ref } from "vue";

const props = defineProps<{ configName: string; config: unknown }>();
const emit = defineEmits<{ close: [] }>();

const json = JSON.stringify(props.config, null, 2);

const copied = ref(false);
async function copyJson() {
  try {
    await navigator.clipboard.writeText(json);
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch {
    /* clipboard blocked; the JSON is still visible to copy by hand */
  }
}
</script>

<template>
  <div class="config-snapshot-bg" @click.self="emit('close')">
    <div class="modal">
      <div class="modal-head">
        <h2>{{ configName }}</h2>
        <button class="btn" @click="emit('close')">Close</button>
      </div>
      <p class="hint">
        The exact config this job was submitted with. Read-only - editing it here would not change
        anything.
      </p>
      <pre class="json">{{ json }}</pre>
      <div class="modal-actions">
        <button class="btn" @click="copyJson">{{ copied ? "Copied" : "Copy" }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.config-snapshot-bg {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 60;
}
.modal {
  width: min(48rem, 92vw);
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  padding: 1.25rem;
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
}
.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}
.modal-head h2 {
  font-family: var(--sans);
  font-size: var(--t-lg);
  word-break: break-word;
}
.hint {
  color: var(--text2);
  font-size: var(--t-md);
  margin: 0.3rem 0 0.75rem;
}
.json {
  flex: 1;
  overflow: auto;
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 0.75rem;
  font-family: var(--mono);
  font-size: var(--t-sm);
  white-space: pre-wrap;
  word-break: break-word;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 0.75rem;
}
.btn {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.8rem;
  cursor: pointer;
  font-family: var(--sans);
}
.btn:hover {
  border-color: var(--accent);
}
</style>
