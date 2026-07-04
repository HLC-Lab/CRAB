<script setup lang="ts">
// One app row inside ExperimentPane's Apps list: wrapper chip, node-group
// picker, collect-metrics toggle, remove, args, and start/end timing.
// `app` is the reactive AppDraft owned by the store's experiment array;
// mutating its fields here writes straight back (same pattern as
// AllocationEditor's `props.alloc` mutation).
import { computed } from "vue";
import type { AppDraft } from "@/lib/config";
import ConfirmButton from "@/components/ConfirmButton.vue";

const props = defineProps<{
  app: AppDraft;
  index: number;
  otherIndices: number[];
  groupNames: string[];
  colorForGroup: (name: string) => string;
  wrapperWarning: (relpath: string) => string;
}>();
const emit = defineEmits<{
  remove: [];
  "open-wrapper-picker": [];
}>();
// Local alias so the template can mutate fields (same pattern as
// AllocationEditor's `props.alloc` wrapping) without tripping
// vue/no-mutating-props.
const app = computed(() => props.app);
</script>

<template>
  <div class="app">
    <div class="app-row">
      <span class="idx">#{{ index }}</span>
      <button
        class="wrap-chip"
        :class="{ empty: !app.path }"
        title="Choose or change the wrapper"
        @click="emit('open-wrapper-picker')"
      >
        <span class="chip-text">{{ app.path || "Choose wrapper…" }}</span>
        <svg class="chip-ic" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M9 6l6 6-6 6" />
        </svg>
      </button>
      <span v-if="wrapperWarning(app.path)" class="wrap-warn" :title="wrapperWarning(app.path)"
        >&#9888;</span
      >
      <span v-if="groupNames.length" class="slicepick" title="Node group">
        <span
          class="sw"
          :style="{ background: app.partition ? colorForGroup(app.partition) : 'var(--text3)' }"
        />
        <select v-model="app.partition">
          <option value="">no group</option>
          <option v-for="g in groupNames" :key="g" :value="g">{{ g }}</option>
        </select>
      </span>
      <button
        type="button"
        class="toggle"
        role="switch"
        :aria-checked="app.collect"
        title="Parse and store this app's metrics"
        @click="app.collect = !app.collect"
      >
        <span class="swi" :class="{ off: !app.collect }" />
        collect metrics
      </button>
      <ConfirmButton
        v-slot="{ trigger }"
        :label="app.path || `app #${index}`"
        @confirm="emit('remove')"
      >
        <button type="button" class="icon-btn danger" title="Remove app" @click="trigger">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 7h14" />
            <path d="M9 7V5h6v2" />
            <path d="M7 7l1 13h8l1-13" />
          </svg>
        </button>
      </ConfirmButton>
    </div>
    <input v-model="app.args" class="full" placeholder="args" />
    <div class="timing">
      <label
        >Starts
        <select v-model="app.startKind">
          <option value="at_start">at start</option>
          <option value="delay">after a delay</option>
          <option value="after">after another app</option>
        </select>
      </label>
      <label v-if="app.startKind === 'delay'">Delay (s) <input v-model="app.startDelay" /></label>
      <label v-if="app.startKind === 'after'"
        >After
        <select v-model="app.startAfter">
          <option v-for="j in otherIndices" :key="j" :value="String(j)">#{{ j }}</option>
        </select>
      </label>
      <label
        >Ends
        <select v-model="app.endKind">
          <option value="complete">runs to completion</option>
          <option value="force">stops when the others finish</option>
          <option value="timed">stops after N seconds</option>
        </select>
      </label>
      <label v-if="app.endKind === 'timed'">Seconds <input v-model="app.endTimed" /></label>
    </div>
  </div>
</template>

<style scoped>
input,
textarea,
select {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.5rem;
  font-family: var(--mono);
  font-size: var(--t-md);
}
input:focus,
textarea:focus,
select:focus {
  outline: none;
  border-color: var(--accent);
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

.app {
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 0.9rem;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  background: var(--bg2);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
}
.app-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.idx {
  color: var(--text3);
  font-size: var(--t-sm);
  font-weight: 600;
}
.full {
  width: 100%;
}

/* Wrapper chip (opens the picker) */
.wrap-chip {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 0.4rem 0.6rem;
  cursor: pointer;
  color: var(--text);
  font-family: var(--mono);
  font-size: var(--t-md);
  text-align: left;
}
.wrap-chip:hover {
  border-color: var(--accent);
}
.wrap-chip.empty .chip-text {
  color: var(--text3);
}
.chip-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chip-ic {
  width: 15px;
  height: 15px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
  color: var(--text3);
  flex-shrink: 0;
}
.wrap-warn {
  color: var(--warn);
  font-size: 0.9rem;
  cursor: help;
}
.timing {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 0.85rem;
  align-items: end;
  padding-top: 0.5rem;
  border-top: 1px dashed var(--border);
}
.timing label {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  color: var(--text2);
  font-size: var(--t-sm);
}

/* Per-app slice picker — a native select dressed as a small pill with a colour dot */
.slicepick {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 0.25rem 0.5rem;
}
.slicepick .sw {
  width: 9px;
  height: 9px;
  border-radius: 3px;
  flex-shrink: 0;
}
.slicepick select {
  background: transparent;
  border: none;
  padding: 0;
  font-family: var(--sans);
  font-size: var(--t-sm);
  color: var(--text2);
}
.slicepick select:focus {
  outline: none;
}

/* Collect-metrics toggle switch */
.toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  background: transparent;
  border: none;
  cursor: pointer;
  font-family: var(--sans);
  font-size: var(--t-sm);
  color: var(--text2);
  padding: 0;
  white-space: nowrap;
}
.toggle .swi {
  width: 30px;
  height: 17px;
  border-radius: 999px;
  background: var(--accent);
  position: relative;
  flex-shrink: 0;
  transition: background 0.12s ease;
}
.toggle .swi.off {
  background: var(--bg3);
}
.toggle .swi::after {
  content: "";
  position: absolute;
  top: 2px;
  left: 15px;
  width: 13px;
  height: 13px;
  border-radius: 50%;
  background: #fff;
  transition: left 0.12s ease;
}
.toggle .swi.off::after {
  left: 2px;
  background: var(--text3);
}
</style>
