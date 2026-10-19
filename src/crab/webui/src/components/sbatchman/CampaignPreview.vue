<script setup lang="ts">
// Read-only preview of the composed SbatchMan jobs YAML (mirrors JsonPanel's
// layout) and the list of problems that block writing. Writing itself lives in
// the page's top bar (WriteTarget). Hide/Show is in the pane header; the pane
// stays as a one-line strip when hidden, so it can be shown again. Presentational
// only: the campaign store owns the data.
import { nextTick, ref } from "vue";

const props = defineProps<{
  yaml: string;
  totalJobs: number;
  /** validateCampaign output; Write files stays disabled while it is non-empty. */
  issues: string[];
  open: boolean;
}>();
const emit = defineEmits<{
  "update:open": [boolean];
}>();

const copied = ref(false);
async function copyYaml() {
  try {
    await navigator.clipboard.writeText(props.yaml);
    copied.value = true;
    setTimeout(() => (copied.value = false), 1500);
  } catch {
    /* clipboard blocked; the YAML view is still available to copy by hand */
  }
}

const problemList = ref<HTMLElement | null>(null);
/** Open the pane if hidden and bring the problem list into view. */
async function revealProblems(): Promise<void> {
  emit("update:open", true);
  await nextTick();
  problemList.value?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  problemList.value?.focus({ preventScroll: true });
}
defineExpose({ revealProblems });
</script>

<template>
  <aside class="yamlpane" :class="{ closed: !open }">
    <header>
      <span>jobs.yaml · {{ totalJobs }} job{{ totalJobs === 1 ? "" : "s" }}</span>
      <span class="head-actions">
        <button v-if="open" class="link-btn" @click="copyYaml">
          {{ copied ? "Copied ✓" : "Copy" }}
        </button>
        <button class="link-btn" @click="emit('update:open', !open)">
          {{ open ? "Hide" : "Show" }}
        </button>
      </span>
    </header>

    <template v-if="open">
      <div v-if="issues.length" ref="problemList" class="issues" tabindex="-1">
        <p class="issues-title">
          {{ issues.length }} thing{{ issues.length === 1 ? "" : "s" }} to fix before writing
        </p>
        <ul>
          <li v-for="msg in issues" :key="msg">{{ msg }}</li>
        </ul>
      </div>
      <pre>{{ yaml }}</pre>
    </template>
  </aside>
</template>

<style scoped>
.link-btn {
  background: transparent;
  border: none;
  color: var(--text2);
  cursor: pointer;
  font-family: var(--sans);
  font-size: var(--t-sm);
  padding: 0;
}
.link-btn:hover {
  color: var(--text);
}

.yamlpane {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  width: 26rem;
  max-height: 40rem;
  overflow: auto;
  display: flex;
  flex-direction: column;
}
.yamlpane.closed {
  width: auto;
  min-width: 13rem;
}
.yamlpane header {
  position: sticky;
  top: 0;
  background: var(--bg2);
  color: var(--text2);
  padding: 0.4rem 0.75rem;
  font-size: var(--t-sm);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  z-index: 1;
}
.yamlpane.closed header {
  border-bottom: none;
}
.head-actions {
  display: flex;
  gap: 0.8rem;
}
.yamlpane pre {
  padding: 0.75rem;
  font-size: var(--t-sm);
  white-space: pre-wrap;
  color: var(--text2);
  margin: 0;
}

.issues {
  padding: 0.6rem 0.75rem;
  border-bottom: 1px solid var(--border);
  font-size: var(--t-sm);
  color: var(--warn);
  scroll-margin-top: 2.5rem;
}
.issues:focus {
  outline: none;
}
.issues-title {
  margin: 0 0 0.25rem;
  font-weight: 600;
}
.issues ul {
  margin: 0;
  padding-left: 1.1rem;
  color: var(--text2);
}
</style>
