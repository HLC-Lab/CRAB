<script setup lang="ts">
// Campaign-level settings: the SbatchMan configs.yaml reference, the remote
// CRAB checkout (environment.json's CRAB_ROOT), CRAB_SYSTEM, extra
// environment entries, and the campaign-wide sweep variables (a group's own
// variables of the same name override these — see lib/sbatchman.ts).
import { computed } from "vue";
import type { EnvPair } from "@/stores/sbatchman";
import type { SbatchmanVar } from "@/lib/sbatchman";
import VariablesEditor from "@/components/sbatchman/VariablesEditor.vue";

const props = defineProps<{
  configsPath: string;
  crabRoot: string;
  system: string;
  env: EnvPair[];
  variables: SbatchmanVar[];
}>();
const emit = defineEmits<{
  "update:configsPath": [string];
  "update:crabRoot": [string];
  "update:system": [string];
}>();
const env = computed(() => props.env);

function addEnv() {
  env.value.push({ key: "", value: "" });
}
function removeEnv(i: number) {
  env.value.splice(i, 1);
}
</script>

<template>
  <h2 class="pane-title">Campaign</h2>
  <div class="grid">
    <label class="wide"
      >SbatchMan configs.yaml
      <input
        :value="configsPath"
        placeholder="path on the cluster"
        @input="emit('update:configsPath', ($event.target as HTMLInputElement).value)"
      />
      <small>Referenced by the generated jobs YAML's `configs:` key.</small>
    </label>
    <label
      >Remote CRAB checkout
      <input
        :value="crabRoot"
        placeholder="e.g. /home/user/CRAB"
        @input="emit('update:crabRoot', ($event.target as HTMLInputElement).value)"
      />
      <small>Becomes environment.json's CRAB_ROOT.</small>
    </label>
    <label
      >CRAB_SYSTEM
      <input
        :value="system"
        placeholder="cluster preset name"
        @input="emit('update:system', ($event.target as HTMLInputElement).value)"
      />
      <small>Recorded in environment.json.</small>
    </label>
  </div>

  <div class="env-section">
    <div class="seclabel">Environment</div>
    <div v-for="(p, i) in env" :key="i" class="env-row">
      <input v-model="p.key" placeholder="KEY" />
      <input v-model="p.value" placeholder="value" />
      <button type="button" class="icon-btn danger" title="Remove entry" @click="removeEnv(i)">
        ×
      </button>
    </div>
    <button type="button" class="btn" @click="addEnv">+ Add entry</button>
  </div>

  <div class="vars-section">
    <div class="seclabel">Campaign variables</div>
    <p class="hint">Shared by every group unless a group defines its own with the same name.</p>
    <VariablesEditor :variables="variables" />
  </div>
</template>

<style scoped>
.pane-title {
  font-family: var(--sans);
  font-size: var(--t-lg);
  color: var(--text);
}
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.9rem;
  max-width: 38rem;
}
.grid label {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  color: var(--text2);
  font-size: var(--t-md);
}
.grid label.wide {
  grid-column: 1 / -1;
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
small {
  color: var(--text3);
  font-size: var(--t-xs);
}

.env-section,
.vars-section {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  max-width: 38rem;
}
.seclabel {
  font-family: var(--sans);
  font-size: var(--t-xs);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text3);
  font-weight: 700;
}
.hint {
  color: var(--text3);
  font-size: var(--t-sm);
  margin: 0;
}
.env-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
.env-row input {
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
