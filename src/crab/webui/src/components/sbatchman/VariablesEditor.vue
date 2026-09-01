<script setup lang="ts">
// A name + comma-separated-values row editor for SbatchMan sweep variables.
// Mutates the passed array in place (same pattern as AllocationEditor's
// `alloc` prop). Values are kept as plain strings — composeCampaignYaml
// already emits a numeric-looking string as a bare YAML number, so no
// string/number split is needed here.
import { computed } from "vue";
import type { SbatchmanVar } from "@/lib/sbatchman";

const props = defineProps<{
  variables: SbatchmanVar[];
}>();
const vars = computed(() => props.variables);

function valuesText(v: SbatchmanVar): string {
  return v.values.join(", ");
}
function setValues(v: SbatchmanVar, text: string): void {
  v.values = text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}
function addVar() {
  vars.value.push({ name: "", values: [] });
}
function removeVar(i: number) {
  vars.value.splice(i, 1);
}
</script>

<template>
  <div class="vars">
    <div v-for="(v, i) in variables" :key="i" class="var-row">
      <input
        class="name"
        :value="v.name"
        placeholder="name"
        @input="v.name = ($event.target as HTMLInputElement).value"
      />
      <input
        class="values"
        :value="valuesText(v)"
        placeholder="values, comma separated"
        @change="setValues(v, ($event.target as HTMLInputElement).value)"
      />
      <button type="button" class="icon-btn danger" title="Remove variable" @click="removeVar(i)">
        ×
      </button>
    </div>
    <button type="button" class="btn" @click="addVar">+ Add variable</button>
  </div>
</template>

<style scoped>
.vars {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.var-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
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
.name {
  flex: 0 0 9rem;
}
.values {
  flex: 1;
  min-width: 0;
}
.btn {
  align-self: flex-start;
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
.icon-btn {
  background: transparent;
  border: 1px solid transparent;
  color: var(--text3);
  border-radius: var(--r);
  padding: 0.2rem 0.5rem;
  cursor: pointer;
  font-family: var(--sans);
  line-height: 1;
}
.icon-btn.danger:hover {
  color: var(--danger);
  border-color: var(--danger);
}
</style>
