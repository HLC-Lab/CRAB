<script setup lang="ts">
// Shared dialog shell: backdrop plus a container with role="dialog" and
// aria-modal. Gives every dialog the same keyboard behaviour: focus moves in on
// open (to the [autofocus] element, else the first focusable), Tab and
// Shift+Tab stay inside, Escape and a click on the backdrop emit `close`, and
// focus returns to the opener when the dialog goes away.
//
// Fallthrough attributes (class) land on the backdrop, so a host keeps styling
// it with its own class. The host styles the container through `dialogClass`
// and :deep(), since the container is not the host's root element.
import { onBeforeUnmount, onMounted, ref } from "vue";
import { isEscape, nextFocusIndex } from "@/lib/focusTrap";

const props = defineProps<{
  /** Accessible name when the dialog has no visible title to point at. */
  label?: string;
  /** Id of the element that titles the dialog (alternative to `label`). */
  labelledby?: string;
  dialogClass?: string;
}>();
const emit = defineEmits<{ close: [] }>();

const dialogEl = ref<HTMLElement | null>(null);
let opener: HTMLElement | null = null;

const FOCUSABLE =
  'a[href], button, input, select, textarea, summary, [tabindex]:not([tabindex="-1"])';

function focusables(): HTMLElement[] {
  const root = dialogEl.value;
  if (!root) return [];
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) =>
      !el.hasAttribute("disabled") &&
      el.getAttribute("tabindex") !== "-1" &&
      // Skips v-show/display:none elements (e.g. rows of a collapsed suite).
      el.getClientRects().length > 0,
  );
}

function focusInitial(): void {
  const root = dialogEl.value;
  if (!root) return;
  // The autofocus attribute alone does nothing on an element inserted after
  // page load, so focus it by hand.
  const target = root.querySelector<HTMLElement>("[autofocus]") ?? focusables()[0] ?? root;
  target.focus();
}

onMounted(() => {
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  focusInitial();
});

onBeforeUnmount(() => {
  if (opener && opener.isConnected) opener.focus();
  opener = null;
});

function onKeydown(e: KeyboardEvent): void {
  if (isEscape(e.key)) {
    e.preventDefault();
    e.stopPropagation();
    emit("close");
    return;
  }
  if (e.key !== "Tab") return;
  const list = focusables();
  const current = list.indexOf(document.activeElement as HTMLElement);
  const next = nextFocusIndex(current, list.length, e.shiftKey);
  e.preventDefault();
  // Nothing focusable: keep focus on the dialog container itself.
  if (next === -1) dialogEl.value?.focus();
  else list[next].focus();
}
</script>

<template>
  <div @click.self="emit('close')">
    <div
      ref="dialogEl"
      role="dialog"
      aria-modal="true"
      :aria-label="props.labelledby ? undefined : props.label"
      :aria-labelledby="props.labelledby"
      tabindex="-1"
      :class="props.dialogClass"
      @keydown="onKeydown"
    >
      <slot />
    </div>
  </div>
</template>
