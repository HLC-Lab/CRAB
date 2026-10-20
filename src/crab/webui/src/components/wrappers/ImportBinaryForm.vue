<script setup lang="ts">
// Saves a receipt (a binary path for a benchmark id) on a target. Shared by the Status tab's
// needs-attention list and the Library summary.
import { computed, reactive } from "vue";
import type { ReceiptImport, WrapperEntry } from "@/api/types";
import { useWrappersStore } from "@/stores/wrappers";

const props = defineProps<{
  target: string;
  targetLabel: string;
  wrapper: WrapperEntry;
}>();
const emit = defineEmits<{ done: []; cancel: [] }>();

const store = useWrappersStore();
const form = reactive({ binary: "", preRun: "", launcher: "" as ReceiptImport["launcher"] });

const key = computed(() => `${props.target}:${props.wrapper.relpath}`);
const fileName = computed(() => props.wrapper.relpath.split("/").pop() ?? props.wrapper.relpath);

async function submit() {
  const ok = await store.importBinaryOn(props.target, props.wrapper.relpath, {
    id: props.wrapper.benchmark_id ?? "",
    binary: form.binary.trim(),
    pre_run: form.preRun
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean),
    launcher: form.launcher,
  });
  if (ok) emit("done");
}
</script>

<template>
  <form class="import" @submit.prevent="submit">
    <p class="lead">
      Saves a receipt named <code>{{ wrapper.benchmark_id }}</code> on {{ targetLabel }}, so every
      run of {{ fileName }} there uses this binary.
    </p>
    <div class="grid">
      <label
        >Binary path on {{ targetLabel }}
        <input
          v-model="form.binary"
          required
          :placeholder="`/path/to/${wrapper.executable ?? 'program'}`"
        />
      </label>
      <label
        >Launcher
        <select v-model="form.launcher">
          <option value="">cluster default</option>
          <option value="srun">srun</option>
          <option value="mpirun">mpirun</option>
        </select>
      </label>
      <label
        >Commands to run first (optional, one per line)
        <textarea v-model="form.preRun" rows="2" />
      </label>
    </div>
    <p v-if="store.importError[key]" class="error">{{ store.importError[key] }}</p>
    <div class="actions">
      <button type="button" class="link" @click="emit('cancel')">Cancel</button>
      <button
        type="submit"
        class="btn primary"
        :disabled="store.importing[key] || !form.binary.trim()"
      >
        {{ store.importing[key] ? "Saving…" : "Save receipt" }}
      </button>
    </div>
  </form>
</template>

<style scoped>
.import {
  margin: 0.25rem 0 1rem;
  padding: 1rem 1.1rem;
  background: var(--bg1);
  border-radius: var(--r);
}
.lead {
  color: var(--text2);
  margin-bottom: 0.75rem;
}
.grid {
  display: grid;
  grid-template-columns: 2fr 1fr 2fr;
  gap: 0.9rem;
  align-items: start;
}
label {
  display: block;
  color: var(--text2);
}
input,
select,
textarea {
  display: block;
  width: 100%;
  margin-top: 0.25rem;
  background: var(--bg);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.3rem 0.5rem;
  font-family: var(--mono);
  font-size: inherit;
}
input:focus,
select:focus,
textarea:focus {
  outline: none;
  border-color: var(--accent);
}
.error {
  margin-top: 0.75rem;
  color: var(--danger);
  white-space: pre-line;
}
.actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 1rem;
  margin-top: 0.9rem;
}
.btn {
  border: 1px solid var(--border);
  background: var(--bg2);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.3rem 0.8rem;
  cursor: pointer;
  font-family: var(--mono);
}
.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.link {
  background: none;
  border: 0;
  padding: 0;
  color: var(--text2);
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-color: var(--border2);
  cursor: pointer;
  font-family: inherit;
  font-size: inherit;
}
.link:hover {
  color: var(--text);
}
@media (max-width: 760px) {
  .grid {
    grid-template-columns: 1fr;
  }
}
</style>
