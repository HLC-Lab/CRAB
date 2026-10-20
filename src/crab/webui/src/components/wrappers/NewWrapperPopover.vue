<script setup lang="ts">
// "New wrapper": wrappers are files written in an editor, so this shows the command that creates
// them, with a Copy button. Closes on Escape and on a click outside.
import { onMounted, onUnmounted, ref } from "vue";

const GUIDE_URL = "https://hlc-crab.readthedocs.io/en/latest/extending/wrappers/";
const COMMAND = "crab wrappers new myapp --local";

const root = ref<HTMLElement | null>(null);
const button = ref<HTMLButtonElement | null>(null);
const shown = ref(false);
const copied = ref(false);
let copiedTimer: ReturnType<typeof setTimeout> | undefined;

async function copy() {
  try {
    await navigator.clipboard.writeText(COMMAND);
  } catch {
    return; // No clipboard (insecure context, permission): the command stays selectable.
  }
  copied.value = true;
  clearTimeout(copiedTimer);
  copiedTimer = setTimeout(() => (copied.value = false), 2000);
}

function onKey(e: KeyboardEvent) {
  if (e.key !== "Escape" || !shown.value) return;
  shown.value = false;
  button.value?.focus();
}
function onClick(e: MouseEvent) {
  if (shown.value && root.value && !root.value.contains(e.target as Node)) shown.value = false;
}
onMounted(() => {
  document.addEventListener("keydown", onKey);
  document.addEventListener("click", onClick);
});
onUnmounted(() => {
  document.removeEventListener("keydown", onKey);
  document.removeEventListener("click", onClick);
  clearTimeout(copiedTimer);
});
</script>

<template>
  <div ref="root" class="wrap">
    <button
      ref="button"
      type="button"
      class="btn"
      :aria-expanded="shown"
      aria-haspopup="dialog"
      @click="shown = !shown"
    >
      New wrapper
    </button>
    <div v-if="shown" class="pop" role="dialog" aria-label="New wrapper">
      <p>Wrappers are written in your editor. Create the files with:</p>
      <div class="cmd">
        <code>{{ COMMAND }}</code>
        <button type="button" class="link" @click="copy">{{ copied ? "Copied" : "Copy" }}</button>
      </div>
      <p>
        <code>--local</code> puts it in <code>local/wrappers/myapp/</code>, only in this checkout.
        Leave it out to add it to your crab-wrappers clone instead, ready for a pull request.
      </p>
      <p class="guide">
        <a class="link" :href="GUIDE_URL" target="_blank" rel="noopener">Writing a wrapper</a>
        (guide)
      </p>
    </div>
  </div>
</template>

<style scoped>
.wrap {
  position: relative;
}
.btn {
  border: 1px solid var(--border);
  background: var(--bg2);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.3rem 0.8rem;
  cursor: pointer;
  font-family: inherit;
  font-size: inherit;
  white-space: nowrap;
}
.btn:hover {
  border-color: var(--border2);
}
.pop {
  position: absolute;
  right: 0;
  top: calc(100% + 0.5rem);
  width: min(30rem, 90vw);
  z-index: 5;
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 1rem 1.1rem;
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.45);
}
p {
  color: var(--text2);
}
.cmd {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  background: var(--bg);
  border-radius: 4px;
  padding: 0.5rem 0.7rem;
  margin: 0.5rem 0 0.8rem;
}
.cmd code {
  flex: 1;
  color: var(--text);
  user-select: all;
}
code {
  font-family: var(--mono);
}
.guide {
  margin-top: 0.8rem;
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
</style>
