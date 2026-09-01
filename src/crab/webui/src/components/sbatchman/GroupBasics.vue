<script setup lang="ts">
// A campaign group's SbatchMan-specific fields (tag template, preset
// reference, node count/ppn) plus its own scoped variables. Deliberately NOT
// BasicsPane: those fields accept a raw `{var}` placeholder (e.g. numnodes
// "{nodes}"), which BasicsPane's shared draft.numnodes field also holds, but
// there is no validateDraft-style check run against it here — SbatchMan
// expands `{var}` at launch time, long after this editor is done with it.
import { computed } from "vue";
import type { Draft } from "@/lib/config";
import type { SbatchmanVar } from "@/lib/sbatchman";
import VariablesEditor from "@/components/sbatchman/VariablesEditor.vue";

const props = defineProps<{
  tag: string;
  preset: string;
  variables: SbatchmanVar[];
  draft: Draft;
  samples: string[];
}>();
const emit = defineEmits<{
  "update:tag": [string];
  "update:preset": [string];
}>();

const tagModel = computed({
  get: () => props.tag,
  set: (v: string) => emit("update:tag", v),
});
const presetModel = computed({
  get: () => props.preset,
  set: (v: string) => emit("update:preset", v),
});
const d = computed(() => props.draft);
</script>

<template>
  <h2 class="pane-title">Group basics</h2>
  <div class="grid">
    <label class="wide"
      >Tag <input v-model="tagModel" placeholder="e.g. g500_baseline_{scale}_{nodes}" />
      <small>Identifies this group's jobs to SbatchMan. Can use {var} placeholders.</small>
    </label>
    <label class="wide"
      >SbatchMan preset <input v-model="presetModel" placeholder="e.g. {nodes}_nodes" />
      <small>A config name from the referenced SbatchMan configs.yaml.</small>
    </label>
    <label
      >Nodes <input v-model="d.numnodes" placeholder="e.g. {nodes}" />
      <small>Total nodes to allocate. Can use a {var} placeholder.</small>
    </label>
    <label
      >Processes per node <input v-model="d.ppn" />
      <small>Tasks launched per node (ppn).</small>
    </label>
  </div>

  <div class="vars-section">
    <div class="seclabel">Group variables</div>
    <p class="hint">Scoped to this group; a name here overrides a campaign-wide one.</p>
    <VariablesEditor :variables="variables" />
  </div>

  <p v-if="samples.length" class="samples">
    Sample tags: <span v-for="(s, i) in samples" :key="i" class="chip">{{ s }}</span>
  </p>
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

.samples {
  color: var(--text2);
  font-size: var(--t-sm);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
}
.chip {
  font-family: var(--mono);
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.05rem 0.5rem;
  color: var(--text);
}
</style>
