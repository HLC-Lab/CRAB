<script setup lang="ts">
// Searchable overlay over the merged host+remote wrapper catalog. The merged
// list itself is computed by ExperimentPane (it's also needed there for each
// app row's "not on this cluster" warning badge), so this component only
// filters/groups/renders what it's given.
import { computed, ref } from "vue";
import { useCatalogStore } from "@/stores/catalog";
import type { Wrapper } from "@/api/types";

type WrapperOrigin = "host" | "remote" | "both";
type TaggedWrapper = Wrapper & { origin: WrapperOrigin };

const props = defineProps<{
  wrappers: TaggedWrapper[];
  sourceCluster: string;
  busy: boolean;
}>();
const emit = defineEmits<{
  choose: [string];
  close: [];
}>();

const catalog = useCatalogStore();

const wrapperQuery = ref("");
const collapsedSuites = ref<Set<string>>(new Set());
function toggleSuite(group: string): void {
  const next = new Set(collapsedSuites.value);
  if (next.has(group)) next.delete(group);
  else next.add(group);
  collapsedSuites.value = next;
}
// A suite stays open while searching, so a match is never hidden inside a
// collapsed suite the user forgot about.
function isSuiteOpen(group: string): boolean {
  return wrapperQuery.value.trim() !== "" || !collapsedSuites.value.has(group);
}

const filteredWrappers = computed(() => {
  const q = wrapperQuery.value.trim().toLowerCase();
  const list = props.wrappers;
  if (!q) return list;
  return list.filter((w) =>
    [w.relpath, w.bench_name, w.benchmark_id, w.group].some((s) => s?.toLowerCase().includes(q)),
  );
});
// Group by suite (top folder); within a suite, sort by the rest of the path so
// benchmark/version order is preserved.
const wrapperGroups = computed(() => {
  const by: Record<string, typeof filteredWrappers.value> = {};
  for (const w of filteredWrappers.value) (by[w.group || "other"] ??= []).push(w);
  for (const items of Object.values(by)) items.sort((a, b) => a.relpath.localeCompare(b.relpath));
  return Object.entries(by).sort(([a], [b]) => a.localeCompare(b));
});
// The path within its suite, e.g. "blink/v7/a2a.py" → "v7/a2a.py".
function subPath(relpath: string, group: string): string {
  return group && relpath.startsWith(group + "/") ? relpath.slice(group.length + 1) : relpath;
}
// Offer "+ Add" when the typed query isn't already an exact wrapper path.
const trimmedQuery = computed(() => wrapperQuery.value.trim());
const canAddFree = computed(
  () => !!trimmedQuery.value && !props.wrappers.some((w) => w.relpath === trimmedQuery.value),
);
</script>

<template>
  <div class="modal-bg" @click.self="emit('close')">
    <div class="modal card wrapper-modal">
      <header class="wm-head">
        <input
          v-model="wrapperQuery"
          class="search"
          placeholder="Search wrappers by name, path, or group…"
          autofocus
        />
        <span class="wm-src">{{ sourceCluster }}</span>
      </header>

      <p v-if="busy" class="wm-state">Loading wrappers…</p>

      <div v-else class="wrapper-list">
        <p v-if="catalog.localError" class="wm-hint err">Host wrappers: {{ catalog.localError }}</p>
        <p v-if="sourceCluster && catalog.error[sourceCluster]" class="wm-hint err">
          {{ sourceCluster }}: {{ catalog.error[sourceCluster] }}
          <button class="btn" @click="catalog.loadBenchmarks(sourceCluster, true)">Retry</button>
        </p>
        <p v-if="!sourceCluster && !catalog.localError" class="wm-hint">
          No cluster connected, showing wrappers on this host only.
        </p>
        <template v-for="[group, items] in wrapperGroups" :key="group">
          <button type="button" class="wg-head" @click="toggleSuite(group)">
            <svg
              class="chev"
              :class="{ open: isSuiteOpen(group) }"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <path d="M9 6l6 6-6 6" />
            </svg>
            <span class="wg-title">{{ group }}</span>
          </button>
          <button
            v-for="w in items"
            v-show="isSuiteOpen(group)"
            :key="w.relpath"
            class="wrap-row"
            :class="{ unloadable: !w.loadable }"
            :title="w.error || w.relpath"
            @click="emit('choose', w.relpath)"
          >
            <span class="wrap-main">
              <span class="wrap-name">{{ w.bench_name || w.file }}</span>
              <span class="wrap-path">{{ subPath(w.relpath, group) }}</span>
            </span>
            <span class="wrap-tags">
              <span class="tag origin" :class="w.origin">{{
                w.origin === "both" ? "host + remote" : w.origin
              }}</span>
              <span v-if="w.metadata.length" class="tag"
                >{{ w.metadata.length }} metric{{ w.metadata.length === 1 ? "" : "s" }}</span
              >
              <span
                v-if="!w.loadable"
                class="tag warn"
                title="Introspection failed. The path still works."
                >unloadable</span
              >
            </span>
          </button>
        </template>
        <p v-if="!wrapperGroups.length && !canAddFree" class="empty">
          Type a wrapper path to add it.
        </p>
      </div>

      <!-- Force a path that isn't in the catalog (e.g. not on the remote yet) -->
      <button v-if="canAddFree" class="wm-add" @click="emit('choose', trimmedQuery)">
        + Add "{{ trimmedQuery }}"
        <span class="wm-add-hint"
          >use this path even if it's not on {{ sourceCluster || "the cluster" }} yet</span
        >
      </button>
    </div>
  </div>
</template>

<style scoped>
.btn {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.8rem;
  cursor: pointer;
  font-family: var(--sans);
}
.btn:hover:not(:disabled) {
  border-color: var(--accent);
}

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

.modal-bg {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}
.modal {
  width: min(40rem, 92vw);
  padding: 1.25rem;
}
.card {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
}
.search {
  width: 100%;
  margin-bottom: 0.6rem;
  font-size: var(--t-md);
  padding: 0.5rem 0.6rem;
}
.empty {
  color: var(--text3);
  font-size: var(--t-md);
}

/* shared tag pill (wrapper picker rows) */
.tag {
  font-size: var(--t-xs);
  color: var(--text3);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0 0.4rem;
}
.tag.warn {
  color: var(--warn);
  border-color: var(--warn);
}
.tag.origin.host {
  color: var(--text2);
  border-color: var(--border2);
}
.tag.origin.remote {
  color: var(--accent);
  border-color: var(--accent);
}
.tag.origin.both {
  color: var(--ok);
  border-color: var(--ok);
}

.chev {
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
  color: var(--text3);
  transition: transform 0.12s ease;
}
.chev.open {
  transform: rotate(90deg);
  color: var(--accent);
}

/* Wrapper picker overlay */
.wrapper-modal {
  width: min(44rem, 94vw);
  padding: 0.75rem;
  display: flex;
  flex-direction: column;
}
.wm-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.6rem;
}
.wm-head .search {
  flex: 1;
  margin-bottom: 0;
}
.wm-src {
  font-size: var(--t-sm);
  color: var(--text3);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.1rem 0.5rem;
  white-space: nowrap;
}
.wm-state {
  color: var(--text2);
  font-size: var(--t-md);
  padding: 1rem 0.5rem;
}
.wm-hint {
  color: var(--text3);
  font-size: var(--t-sm);
  padding: 0.3rem 0.2rem;
}
.wm-hint.err {
  color: var(--danger);
}
.wrapper-list {
  max-height: 26rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
/* Prominent suite headers (wrappers/<suite>/…) */
.wg-head {
  position: sticky;
  top: 0;
  background: var(--bg1);
  color: var(--accent);
  font-family: var(--sans);
  font-size: var(--t-lg);
  font-weight: 700;
  letter-spacing: 0.01em;
  padding: 0.7rem 0.55rem 0.3rem;
  border: none;
  border-bottom: 1px solid var(--border);
  margin-bottom: 0.15rem;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 0.45rem;
  cursor: pointer;
  text-align: left;
}
.wg-head:not(:first-child) {
  margin-top: 0.5rem;
}
.wg-head:hover {
  background: var(--bg2);
}
.wg-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wrap-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  width: 100%;
  text-align: left;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--r);
  padding: 0.4rem 0.55rem;
  cursor: pointer;
  color: var(--text);
}
.wrap-row:hover {
  background: var(--bg2);
  border-color: var(--accent);
}
.wrap-row.unloadable {
  opacity: 0.7;
}
.wrap-main {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  min-width: 0;
}
.wrap-name {
  font-size: var(--t-md);
}
.wrap-path {
  font-size: var(--t-sm);
  color: var(--text3);
  font-family: var(--mono);
}
.wrap-tags {
  display: flex;
  gap: 0.25rem;
  flex-shrink: 0;
}
.wm-add {
  margin-top: 0.5rem;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  align-items: flex-start;
  background: var(--bg2);
  border: 1px dashed var(--accent);
  border-radius: var(--r);
  padding: 0.5rem 0.6rem;
  cursor: pointer;
  color: var(--accent);
  font-family: var(--mono);
  font-size: var(--t-md);
  text-align: left;
}
.wm-add:hover {
  background: var(--accent-glow);
}
.wm-add-hint {
  color: var(--text3);
  font-size: var(--t-sm);
}
</style>
