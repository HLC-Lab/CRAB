<script setup lang="ts">
// Inline two-step delete confirm. No modal/overlay: the trigger (passed in via
// the default scoped slot) flips in place to "Delete <label>? [Cancel] [Delete]"
// on first activation; Delete emits `confirm`; Cancel or clicking away resets.
//
// Click-outside (a document `mousedown` listener), not a plain `focusout`
// handler, detects "away". Activating the trigger removes it from the DOM
// (the `v-if` below swaps it for the confirm UI), and a focused element being
// removed fires a synchronous blur/focusout on it. A naive focusout handler
// would read that as "focus left the widget" and instantly self-cancel,
// before the confirm UI is ever shown. `mousedown` isn't synthesized by DOM
// removal, so it only fires on a real click, sidestepping that race.
import { nextTick, onBeforeUnmount, ref } from "vue";

const props = defineProps<{ label?: string }>();
const emit = defineEmits<{ confirm: [] }>();

const root = ref<HTMLElement | null>(null);
const cancelBtn = ref<HTMLButtonElement | null>(null);
const confirming = ref(false);

function onOutside(e: MouseEvent) {
  if (root.value && !root.value.contains(e.target as Node)) reset();
}

function start() {
  confirming.value = true;
  document.addEventListener("mousedown", onOutside, true);
  // Move focus onto the confirm UI so keyboard users can Tab/Enter through it.
  nextTick(() => cancelBtn.value?.focus());
}
function reset() {
  confirming.value = false;
  document.removeEventListener("mousedown", onOutside, true);
}
function cancel() {
  reset();
}
function doConfirm() {
  reset();
  emit("confirm");
}
onBeforeUnmount(reset);
</script>

<template>
  <span ref="root" class="confirm-btn">
    <slot v-if="!confirming" :trigger="start" />
    <!-- .stop: a host row often has its own click handler (e.g. the rail's
         row-select); Cancel/Delete must never let that fire too. -->
    <span v-else class="confirm-inline" @click.stop>
      <span class="confirm-text">Delete{{ props.label ? ` ${props.label}` : "" }}?</span>
      <button ref="cancelBtn" type="button" class="confirm-cancel" @click="cancel">Cancel</button>
      <button type="button" class="confirm-delete" @click="doConfirm">Delete</button>
    </span>
  </span>
</template>

<style scoped>
/* min-width: 0 down the chain lets this shrink (and ellipsize the label)
   instead of overflowing a narrow host, e.g. the rail's experiment rows. */
.confirm-btn { display: inline-flex; align-items: center; min-width: 0; max-width: 100%; }
.confirm-inline {
  display: flex; align-items: center; gap: 0.4rem; min-width: 0; max-width: 100%;
  font-family: var(--sans); font-size: var(--t-sm); color: var(--text2);
}
.confirm-text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.confirm-cancel, .confirm-delete {
  font-family: var(--sans); font-size: var(--t-sm); border-radius: var(--r);
  padding: 0.15rem 0.55rem; cursor: pointer; background: var(--bg2); color: var(--text);
  border: 1px solid var(--border); flex-shrink: 0;
}
.confirm-cancel:hover { border-color: var(--border2); }
.confirm-delete { border-color: var(--danger); color: var(--danger); }
.confirm-delete:hover { background: var(--danger); color: var(--text); }
</style>
