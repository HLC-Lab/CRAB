<script setup lang="ts">
// JSON view: current config (read-only, with Copy) plus load-from-file
// import, replacing what used to be two separate toolbar buttons and a modal.
import { ref } from "vue";
import { useAuthorStore } from "@/stores/author";

const emit = defineEmits<{
  // Fired after a file import actually changed the draft, so the parent can
  // reset pane selection (same as LibraryBar's `opened`).
  imported: [];
}>();

const store = useAuthorStore();

const copied = ref(false);
async function copyJson() {
  try {
    await navigator.clipboard.writeText(store.configJson);
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch {
    /* clipboard blocked; the JSON view is still available to copy by hand */
  }
}

const importFileInput = ref<HTMLInputElement | null>(null);

async function onImportFileChosen(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = ""; // reset so choosing the same file again still fires @change
  if (!file) return;
  const text = await file.text();
  if (store.importJson(text)) {
    emit("imported");
  }
}
</script>

<template>
  <aside class="jsonpane">
    <header>
      <span>config.json</span>
      <button class="link-btn" @click="copyJson">{{ copied ? "Copied ✓" : "Copy" }}</button>
    </header>
    <pre>{{ store.configJson }}</pre>
    <div class="jsonpane-import">
      <h3>Import JSON</h3>
      <p class="hint">Load a config file from your computer into the editor as a new draft.</p>
      <input
        ref="importFileInput"
        type="file"
        accept="application/json,.json"
        class="visually-hidden"
        @change="onImportFileChosen"
      />
      <button class="btn primary" @click="importFileInput?.click()">Load from file…</button>
    </div>
  </aside>
</template>

<style scoped>
.btn {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.8rem;
  cursor: pointer;
  font-family: var(--sans);
}
.btn:hover:not(:disabled) {
  border-color: var(--accent);
}
.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

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

.jsonpane {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  width: 26rem;
  max-height: 40rem;
  overflow: auto;
  display: flex;
  flex-direction: column;
}
.jsonpane header {
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
.jsonpane pre {
  padding: 0.75rem;
  font-size: var(--t-sm);
  white-space: pre-wrap;
  color: var(--text2);
  margin: 0;
}
.jsonpane-import {
  padding: 0.75rem;
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.jsonpane-import h3 {
  font-family: var(--sans);
  font-size: var(--t-sm);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text3);
}
.jsonpane-import .hint {
  color: var(--text3);
  font-size: var(--t-xs);
  margin: 0;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
