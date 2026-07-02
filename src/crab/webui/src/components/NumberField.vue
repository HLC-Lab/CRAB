<script setup lang="ts">
// Numeric input with an up/down chevron stepper, ported from the approved
// alloc-v5 mockup's `.num`/`.sp` markup. Used wherever a small integer needs
// quick nudging (allocation node counts, stride, seed) without the browser's
// native spinner chrome (hidden globally in styles/tokens.css).
const props = defineProps<{
  modelValue: string;
  min?: number;
}>();
const emit = defineEmits<{
  (e: "update:modelValue", value: string): void;
}>();

function clamp(raw: string): string {
  if (raw === "" || props.min === undefined) return raw;
  const n = parseInt(raw, 10);
  if (Number.isNaN(n) || n >= props.min) return raw;
  return String(props.min);
}

function onInput(e: Event) {
  emit("update:modelValue", clamp((e.target as HTMLInputElement).value));
}

function step(delta: number) {
  const n = parseInt(props.modelValue || "0", 10) + delta;
  const value = props.min !== undefined && n < props.min ? props.min : n;
  emit("update:modelValue", String(value));
}
</script>

<template>
  <span class="num">
    <input type="number" :value="modelValue" :min="min" @input="onInput" />
    <span class="sp">
      <button type="button" aria-label="increase" @click="step(1)">
        <svg viewBox="0 0 10 10"><path d="M2 6l3-3 3 3" /></svg>
      </button>
      <button type="button" aria-label="decrease" @click="step(-1)">
        <svg viewBox="0 0 10 10"><path d="M2 4l3 3 3-3" /></svg>
      </button>
    </span>
  </span>
</template>

<style scoped>
.num {
  position: relative;
  display: inline-flex;
}
.num input {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.3rem 1.35rem 0.3rem 0.5rem;
  font-family: var(--mono);
  font-size: var(--t-md);
  width: 100%;
}
.num input:focus {
  outline: none;
  border-color: var(--accent);
}
.num .sp {
  position: absolute;
  right: 1px;
  top: 1px;
  bottom: 1px;
  width: 1.1rem;
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--border);
}
.num .sp button {
  flex: 1;
  border: none;
  background: transparent;
  color: var(--text3);
  cursor: pointer;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}
.num .sp button:hover {
  color: var(--accent);
  background: var(--sel-bg);
}
.num .sp button:last-child {
  border-top: 1px solid var(--border);
}
.num .sp svg {
  width: 9px;
  height: 9px;
  stroke: currentColor;
  stroke-width: 2.4;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
}
</style>
