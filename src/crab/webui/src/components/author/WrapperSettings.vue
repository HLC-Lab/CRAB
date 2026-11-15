<script setup lang="ts">
// Wrapper attributes of one app: free key and value rows under the app card, such as
// `input_file` and `pseudo_dir` for Quantum ESPRESSO. The rows are a view of `app.rest`
// (see lib/wrapperSettings.ts); a blank, reserved or repeated key stays out of `rest`.
import { computed, ref, watch } from "vue";
import type { AppDraft } from "@/lib/config";
import { restToRows, rowProblems, rowsToRest, type WrapperRow } from "@/lib/wrapperSettings";

const props = defineProps<{ app: AppDraft }>();
const app = computed(() => props.app);

const rows = ref<WrapperRow[]>(restToRows(props.app.rest));
const problems = computed(() => rowProblems(rows.value));

function sync() {
  const next = rowsToRest(rows.value, app.value.rest);
  if (JSON.stringify(next) !== JSON.stringify(app.value.rest)) app.value.rest = next;
}
// Rows are the source while the user types. When `rest` changes from outside (another
// config was opened) and no longer matches what the rows stand for, take it over.
watch(rows, sync, { deep: true });
watch(
  () => app.value.rest,
  (rest) => {
    if (JSON.stringify(rowsToRest(rows.value, rest)) !== JSON.stringify(rest)) {
      rows.value = restToRows(rest);
    }
  },
);

function add() {
  rows.value.push({ key: "", value: "" });
}
function remove(i: number) {
  rows.value.splice(i, 1);
}
</script>

<template>
  <div class="wrap-attrs">
    <div class="wa-head">
      <span class="wa-title" title="Keys the wrapper reads, such as input_file for Quantum ESPRESSO"
        >Wrapper attributes</span
      >
      <button type="button" class="wa-add" @click="add">+ Add attribute</button>
    </div>
    <div v-for="(row, i) in rows" :key="i" class="wa-block">
      <div class="wa-row">
        <input
          v-model="row.key"
          class="wa-key"
          placeholder="name"
          spellcheck="false"
          :aria-invalid="!!problems[i]"
        />
        <span class="wa-eq">=</span>
        <input v-model="row.value" class="wa-val" placeholder="value" spellcheck="false" />
        <button type="button" class="icon-btn danger" title="Remove attribute" @click="remove(i)">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 6l12 12" />
            <path d="M18 6L6 18" />
          </svg>
        </button>
      </div>
      <div v-if="problems[i]" class="wa-problem" role="alert">{{ problems[i] }}</div>
    </div>
  </div>
</template>

<style scoped>
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
input[aria-invalid="true"] {
  border-color: var(--warn);
}
.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid transparent;
  color: var(--text2);
  border-radius: var(--r);
  padding: 0.2rem;
  cursor: pointer;
}
.icon-btn:hover {
  border-color: var(--border);
  color: var(--text);
}
.icon-btn svg {
  width: 18px;
  height: 18px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.75;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.wrap-attrs {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding-top: 0.5rem;
  border-top: 1px dashed var(--border);
}
.wa-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--text2);
  font-size: var(--t-sm);
}
.wa-add {
  background: transparent;
  border: none;
  color: var(--accent);
  cursor: pointer;
  font-family: var(--sans);
  font-size: var(--t-sm);
  padding: 0;
}
.wa-block {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.wa-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.wa-key {
  width: 12rem;
}
.wa-val {
  flex: 1;
  min-width: 0;
}
.wa-eq {
  color: var(--text3);
  font-family: var(--mono);
}
.wa-problem {
  color: var(--warn);
  font-size: var(--t-sm);
}
</style>
