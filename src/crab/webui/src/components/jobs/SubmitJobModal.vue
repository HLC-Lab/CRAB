<script setup lang="ts">
// Shared submit flow used from both AuthorView (submits the open config
// inline) and JobsView (picks a saved config first). ADR-010: preset is
// chosen here, at submit time, never stored in the config itself.
import { computed, onMounted, ref, watch } from "vue";
import { api } from "@/api/client";
import type { CrabConfig, LibraryEntry } from "@/api/types";
import { useJobsStore } from "@/stores/jobs";
import { useRemotesStore } from "@/stores/remotes";

const props = defineProps<{
  initialConfig?: { config: CrabConfig; name: string } | null;
}>();
const emit = defineEmits<{ close: []; submitted: [] }>();

const remotes = useRemotesStore();
const jobs = useJobsStore();

const selectedRemote = ref("");
const selectedPreset = ref("");
const selectedConfigId = ref("");
const libraryEntries = ref<LibraryEntry[]>([]);
const loadingLibrary = ref(false);
const libraryError = ref<string | null>(null);

const connectedRemotes = computed(() => remotes.items.filter((r) => r.connected));
const knownPresets = computed(() => remotes.info[selectedRemote.value]?.presets ?? []);

// Default the preset to the remote's own stored default, or its first known preset.
watch(selectedRemote, (name) => {
  const profile = remotes.items.find((r) => r.name === name);
  selectedPreset.value = profile?.preset || knownPresets.value[0]?.name || "";
});

onMounted(async () => {
  await remotes.refresh();
  if (connectedRemotes.value.length === 1) {
    selectedRemote.value = connectedRemotes.value[0].name;
  }
  if (!props.initialConfig) {
    loadingLibrary.value = true;
    try {
      libraryEntries.value = await api.experiments.list();
    } catch {
      libraryError.value = "Could not load saved configs.";
    } finally {
      loadingLibrary.value = false;
    }
  }
});

const canSubmit = computed(
  () =>
    !!selectedRemote.value &&
    !!selectedPreset.value.trim() &&
    (!!props.initialConfig || !!selectedConfigId.value) &&
    !jobs.submitBusy,
);

async function doSubmit() {
  const base = { profile_name: selectedRemote.value, preset: selectedPreset.value.trim() };
  const body = props.initialConfig
    ? { ...base, config: props.initialConfig.config, name: props.initialConfig.name }
    : { ...base, config_id: selectedConfigId.value };
  const label =
    props.initialConfig?.name ??
    libraryEntries.value.find((e) => e.id === selectedConfigId.value)?.name;
  const accepted = await jobs.submit(body, label);
  if (accepted) emit("submitted");
}
</script>

<template>
  <div class="modal-bg" @click.self="emit('close')">
    <div class="modal card">
      <h2>Submit job</h2>

      <p v-if="!connectedRemotes.length" class="hint err">
        No connected clusters. Connect one from the Remotes view first.
      </p>

      <template v-else>
        <label class="field">
          <span>Remote</span>
          <select v-model="selectedRemote">
            <option value="" disabled>Choose a remote…</option>
            <option v-for="r in connectedRemotes" :key="r.name" :value="r.name">
              {{ r.name }}
            </option>
          </select>
        </label>

        <label v-if="!initialConfig" class="field">
          <span>Config</span>
          <select v-model="selectedConfigId" :disabled="loadingLibrary">
            <option value="" disabled>
              {{ loadingLibrary ? "Loading…" : "Choose a config…" }}
            </option>
            <option v-for="e in libraryEntries" :key="e.id" :value="e.id">{{ e.name }}</option>
          </select>
        </label>
        <p v-if="initialConfig" class="hint">
          Submitting the open config: {{ initialConfig.name }}
        </p>
        <p v-if="libraryError" class="hint err">{{ libraryError }}</p>

        <label class="field">
          <span>Preset</span>
          <select v-if="knownPresets.length" v-model="selectedPreset">
            <option v-for="p in knownPresets" :key="p.name" :value="p.name">{{ p.name }}</option>
          </select>
          <input v-else v-model="selectedPreset" placeholder="cluster preset" />
        </label>

        <p v-if="jobs.submitError" class="hint err">{{ jobs.submitError }}</p>
      </template>

      <div class="modal-actions">
        <button class="btn" @click="emit('close')">Cancel</button>
        <button class="btn primary" :disabled="!canSubmit" @click="doSubmit">
          {{ jobs.submitBusy ? "Submitting…" : "Submit" }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-bg {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 60;
}
.modal {
  width: min(28rem, 92vw);
  padding: 1.25rem;
}
.card {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
}
h2 {
  font-family: var(--sans);
  font-size: var(--t-lg);
  margin-bottom: 0.6rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  margin-bottom: 0.6rem;
  font-family: var(--sans);
  font-size: var(--t-sm);
  color: var(--text2);
}
select,
input {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.5rem;
  font-family: var(--mono);
  font-size: var(--t-md);
}
select:focus,
input:focus {
  outline: none;
  border-color: var(--accent);
}
.hint {
  color: var(--text3);
  font-size: var(--t-sm);
  margin-bottom: 0.6rem;
}
.hint.err {
  color: var(--danger);
  white-space: pre-wrap;
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
.btn:hover:not(:disabled) {
  border-color: var(--accent);
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.btn.primary:hover:not(:disabled) {
  filter: brightness(1.1);
}
</style>
