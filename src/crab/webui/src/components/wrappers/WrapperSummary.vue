<script setup lang="ts">
// The Library's right column: what the selected wrapper is, what it measures and how it runs on
// the shown target. The summary comes from `crab wrappers show`; Import binary reuses the form
// of the Status tab.
import { computed, ref, watch } from "vue";
import type { WrapperEntry, WrapperTarget, WrappersError } from "@/api/types";
import { binaryLine, descriptionNote, summaryPath, summaryTitle } from "@/lib/wrapperTree";
import { canImport, shortSha } from "@/lib/wrappers";
import { useWrappersStore } from "@/stores/wrappers";
import ImportBinaryForm from "./ImportBinaryForm.vue";

const props = defineProps<{
  target: WrapperTarget;
  relpath: string;
  /** The wrapper sits in the untracked local/wrappers folder. */
  local: boolean;
  /** The wrapper's row in the target's list (the Import form needs it), if it is listed. */
  entry: WrapperEntry | undefined;
}>();

const store = useWrappersStore();
const key = computed(() => `${props.target.id}:${props.relpath}`);
const summary = computed(() => store.summaries[key.value]);
const loading = computed(() => !!store.summaryLoading[key.value]);
const error = computed(() => store.summaryError[key.value]);

const showImport = ref(false);
watch(
  key,
  () => {
    showImport.value = false;
    void store.loadShow(props.target.id, props.relpath);
  },
  { immediate: true },
);

// "This laptop" reads oddly mid-sentence.
const where = computed(() => (props.target.local ? "this laptop" : props.target.label));
const line = computed(() => (summary.value ? binaryLine(summary.value, where.value) : null));
const note = computed(() => (summary.value ? descriptionNote(summary.value) : null));
const importable = computed(() => !!summary.value && canImport(summary.value) && !!props.entry);

function metricNote(m: { role: string | null; conv: boolean }): string {
  return [m.conv ? "convergence target" : "", m.role === "check" ? "check" : ""]
    .filter(Boolean)
    .join(", ");
}

function errorText(e: WrappersError): string {
  if (e.code === "crab_outdated") {
    return `Update crab on ${props.target.label} (crab update) to see this.`;
  }
  if (e.code === "not_found") {
    return `${summaryTitle(props.relpath)} is no longer on ${props.target.label}: it is not in its wrappers folders.`;
  }
  return e.detail ? `${e.message}\n${e.detail}` : e.message;
}
</script>

<template>
  <article class="sum">
    <p v-if="error" class="error">{{ errorText(error) }}</p>
    <p v-else-if="!summary && loading" class="quiet">Loading {{ summaryTitle(relpath) }}…</p>

    <template v-else-if="summary">
      <h2>{{ summaryTitle(relpath) }}</h2>
      <p class="path">
        <code>{{ summaryPath(summary, local) }}</code>
        <a
          v-if="summary.source.url"
          class="link"
          :href="summary.source.url"
          target="_blank"
          rel="noopener"
          >view on GitHub at {{ shortSha(summary.source.commit) }}</a
        >
        <span v-else class="quiet">{{ local ? "only in this checkout" : "not on GitHub" }}</span>
      </p>

      <div v-if="!summary.loadable" class="broken">
        <h3 class="bad">Cannot load</h3>
        <pre>{{ summary.error }}</pre>
      </div>

      <p v-if="summary.description.text" class="desc">{{ summary.description.text }}</p>
      <p v-if="note" class="note" :class="{ gap: !summary.description.text }">{{ note }}</p>

      <section>
        <h3>What it measures</h3>
        <table v-if="summary.metrics.length" class="metrics">
          <thead>
            <tr>
              <th>Metric</th>
              <th>Unit</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in summary.metrics" :key="m.name">
              <td>{{ m.name }}</td>
              <td class="quiet">{{ m.unit }}</td>
              <td class="quiet">{{ metricNote(m) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="quiet">This wrapper declares no metrics.</p>
      </section>

      <section>
        <h3>How it runs</h3>
        <dl class="kv">
          <dt>Executable</dt>
          <dd>
            <code v-if="summary.executable">{{ summary.executable }}</code>
            <span v-else class="quiet">not declared</span>
          </dd>
          <dt>Binary</dt>
          <dd>
            <span v-if="line" :class="line.tone">{{ line.text }}</span>
            <div v-if="importable && !showImport" class="act">
              <button type="button" class="btn" @click="showImport = true">Import binary</button>
            </div>
          </dd>
        </dl>
        <ImportBinaryForm
          v-if="showImport && entry"
          :target="target.id"
          :target-label="where"
          :wrapper="entry"
          @done="showImport = false"
          @cancel="showImport = false"
        />
      </section>
    </template>
  </article>
</template>

<style scoped>
.sum {
  --hair: color-mix(in srgb, var(--border) 60%, var(--bg));
  min-width: 0;
}
h2 {
  font-family: var(--mono);
  font-size: 1.5rem;
  line-height: 1.2;
  overflow-wrap: anywhere;
}
h3 {
  font-size: 1.02rem;
  margin-bottom: 0.5rem;
}
h3.bad {
  color: var(--danger);
}
.path {
  margin-top: 0.3rem;
  color: var(--text2);
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
}
code,
pre {
  font-family: var(--mono);
}
.desc {
  margin-top: 1.25rem;
  max-width: 46rem;
  white-space: pre-line;
}
.note {
  margin-top: 0.4rem;
  color: var(--text3);
}
.note.gap {
  margin-top: 1.25rem;
}
.broken {
  margin-top: 1.25rem;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--text2);
}
section {
  margin-top: 2rem;
}
.metrics {
  border-collapse: collapse;
  min-width: 26rem;
}
.metrics th,
.metrics td {
  text-align: left;
  padding: 0.4rem 2.5rem 0.4rem 0;
}
.metrics th {
  font-weight: normal;
  color: var(--text3);
}
.metrics tbody tr {
  border-top: 1px solid var(--hair);
}
.kv {
  display: grid;
  grid-template-columns: 9rem 1fr;
  gap: 0.4rem 1rem;
}
.kv dt {
  color: var(--text3);
}
.kv dd {
  min-width: 0;
  overflow-wrap: anywhere;
}
.warn {
  color: var(--warn);
}
.ok {
  color: var(--text);
}
.muted,
.quiet {
  color: var(--text3);
}
.error {
  color: var(--danger);
  white-space: pre-line;
}
.act {
  margin-top: 0.5rem;
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
}
.btn:hover {
  border-color: var(--border2);
}
.link {
  color: var(--text2);
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-color: var(--border2);
}
.link:hover {
  color: var(--text);
}
</style>
