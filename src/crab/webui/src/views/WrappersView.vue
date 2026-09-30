<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import type { ReceiptImport, WrapperEntry } from "@/api/types";
import { binaryLabel, canImport, groupByApp, hasProblem, summarize } from "@/lib/wrappers";
import { useRemotesStore } from "@/stores/remotes";
import { useWrappersStore } from "@/stores/wrappers";

const remotes = useRemotesStore();
const store = useWrappersStore();

const cluster = ref("");
const onlyMissing = ref(false);
// The wrapper whose "Import binary" form is open, and that form's fields.
const openRelpath = ref("");
const form = reactive({ binary: "", preRun: "", launcher: "" as ReceiptImport["launcher"] });

const connected = computed(() => remotes.items.filter((r) => r.connected).map((r) => r.name));
const result = computed(() => (cluster.value ? store.catalog[cluster.value] : undefined));
const counts = computed(() => summarize(result.value?.wrappers ?? []));
const groups = computed(() => {
  const all = result.value?.wrappers ?? [];
  return groupByApp(onlyMissing.value ? all.filter(hasProblem) : all);
});

function key(w: WrapperEntry): string {
  return `${cluster.value}:${w.relpath}`;
}

function openImport(w: WrapperEntry) {
  openRelpath.value = w.relpath;
  form.binary = "";
  form.preRun = "";
  form.launcher = "";
}

async function submitImport(w: WrapperEntry) {
  const ok = await store.importBinary(cluster.value, w.relpath, {
    id: w.benchmark_id ?? "",
    binary: form.binary.trim(),
    pre_run: form.preRun
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean),
    launcher: form.launcher,
  });
  if (ok) openRelpath.value = "";
}

watch(cluster, (name) => {
  openRelpath.value = "";
  if (name && !store.catalog[name]) store.load(name);
});

onMounted(async () => {
  await remotes.refresh();
  if (!cluster.value && connected.value.length) cluster.value = connected.value[0];
});
</script>

<template>
  <section class="wrappers">
    <header class="head">
      <h1>Wrappers</h1>
      <div class="actions">
        <select v-if="connected.length" v-model="cluster" aria-label="Cluster">
          <option v-for="name in connected" :key="name" :value="name">{{ name }}</option>
        </select>
        <button
          class="btn"
          :disabled="!cluster || store.loading[cluster]"
          @click="store.load(cluster)"
        >
          ↻ Refresh
        </button>
      </div>
    </header>

    <p v-if="!connected.length" class="notice">
      Connect a cluster in <router-link to="/remotes">Remotes</router-link> to see its wrappers and
      whether each one can find its application.
    </p>

    <template v-else-if="cluster">
      <p v-if="store.error[cluster]" class="banner err">
        {{ store.error[cluster] }}
        <span class="hint"
          >If this cluster's CRAB is older than the dashboard, run crab update there.</span
        >
      </p>
      <p v-else-if="store.loading[cluster] && !result" class="notice">
        Loading wrappers from {{ cluster }}…
      </p>

      <template v-if="result">
        <div class="summary">
          <span class="chip ok">{{ counts.found }} ready</span>
          <span class="chip warn">{{ counts.missing }} missing a binary</span>
          <span v-if="counts.broken" class="chip err">{{ counts.broken }} with errors</span>
          <label class="toggle"
            ><input v-model="onlyMissing" type="checkbox" /> Only show problems</label
          >
        </div>
        <p class="search">
          Searched in order: <code v-for="dir in result.search_path" :key="dir">{{ dir }}</code>
        </p>

        <div v-for="group in groups" :key="group.app" class="card">
          <h2>{{ group.app }}</h2>
          <ul class="list">
            <li v-for="w in group.wrappers" :key="w.relpath" class="item">
              <div class="row">
                <code class="rel">{{ w.relpath }}</code>
                <span class="status" :class="w.loadable ? w.binary.status : 'unknown'">{{
                  binaryLabel(w)
                }}</span>
                <code v-if="w.binary.path" class="bin">{{ w.binary.path }}</code>
                <span v-else-if="!w.loadable" class="bin err-text">{{ w.error }}</span>
                <span v-else-if="w.binary.detail" class="bin err-text">{{ w.binary.detail }}</span>
                <span v-else class="bin"></span>
                <button
                  v-if="canImport(w) && openRelpath !== w.relpath"
                  class="btn small"
                  @click="openImport(w)"
                >
                  Import binary
                </button>
              </div>

              <form
                v-if="openRelpath === w.relpath"
                class="import"
                @submit.prevent="submitImport(w)"
              >
                <p class="hint">
                  Saves a receipt named <code>{{ w.benchmark_id }}</code> on {{ cluster }}, so every
                  run of this wrapper uses that binary.
                </p>
                <div class="grid">
                  <label
                    >Binary path on {{ cluster }}
                    <input
                      v-model="form.binary"
                      required
                      :placeholder="`/path/to/${w.executable ?? 'program'}`"
                  /></label>
                  <label
                    >Launcher
                    <select v-model="form.launcher">
                      <option value="">cluster default</option>
                      <option value="srun">srun</option>
                      <option value="mpirun">mpirun</option>
                    </select>
                  </label>
                </div>
                <label
                  >Commands to run first (optional, one per line)
                  <textarea v-model="form.preRun" rows="2" placeholder="module load openmpi" />
                </label>
                <p v-if="store.importError[key(w)]" class="banner err small">
                  {{ store.importError[key(w)] }}
                </p>
                <div class="form-actions">
                  <button type="button" class="btn" @click="openRelpath = ''">Cancel</button>
                  <button
                    type="submit"
                    class="btn primary"
                    :disabled="store.importing[key(w)] || !form.binary.trim()"
                  >
                    {{ store.importing[key(w)] ? "Saving…" : "Save receipt" }}
                  </button>
                </div>
              </form>
            </li>
          </ul>
        </div>
      </template>
    </template>
  </section>
</template>

<style scoped>
.wrappers {
  padding: 1.5rem 2rem;
  max-width: 72rem;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
}
h1 {
  font-family: var(--sans);
  font-size: 1.6rem;
}
h2 {
  font-family: var(--sans);
  font-size: var(--t-lg);
  margin-bottom: 0.5rem;
}
.actions {
  display: flex;
  gap: 0.5rem;
}
.btn {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.8rem;
  cursor: pointer;
  font-family: var(--mono);
}
.btn.small {
  padding: 0.2rem 0.6rem;
  font-size: var(--t-sm);
}
.btn:hover:not(:disabled) {
  border-color: var(--accent);
}
.btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.card {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 1rem;
  margin-bottom: 0.75rem;
}
.notice {
  color: var(--text2);
}
.banner {
  white-space: pre-line;
  padding: 0.5rem 0.75rem;
  border-radius: var(--r);
  margin-bottom: 0.75rem;
}
.banner.err {
  background: rgba(245, 101, 101, 0.12);
  color: var(--danger);
  border: 1px solid var(--danger);
}
.banner.small {
  font-size: var(--t-sm);
  margin-top: 0.5rem;
}
.hint {
  display: block;
  color: var(--text2);
  font-size: var(--t-sm);
  margin-top: 0.25rem;
}
.summary {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}
.chip {
  font-family: var(--mono);
  font-size: var(--t-sm);
  padding: 0.15rem 0.55rem;
  border-radius: 999px;
  border: 1px solid var(--border);
}
.chip.ok {
  color: var(--ok);
  border-color: var(--ok);
}
.chip.warn {
  color: var(--warn);
  border-color: var(--warn);
}
.chip.err {
  color: var(--danger);
  border-color: var(--danger);
}
.toggle {
  margin-left: auto;
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 0.35rem;
  color: var(--text2);
  font-size: var(--t-sm);
}
.search {
  color: var(--text3);
  font-size: var(--t-sm);
  margin-bottom: 1rem;
}
.search code {
  margin-left: 0.5rem;
  color: var(--text2);
}
.list {
  list-style: none;
}
.item + .item {
  border-top: 1px solid var(--border);
}
.row {
  display: grid;
  grid-template-columns: minmax(14rem, 18rem) 8rem 1fr auto;
  align-items: center;
  gap: 0.75rem;
  padding: 0.45rem 0;
}
.rel,
.bin {
  font-family: var(--mono);
  font-size: var(--t-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bin {
  color: var(--text2);
}
.err-text {
  color: var(--danger);
}
.status {
  font-size: var(--t-sm);
  font-family: var(--mono);
}
.status.config,
.status.receipt,
.status.path {
  color: var(--ok);
}
.status.missing {
  color: var(--warn);
}
.status.error,
.status.unknown {
  color: var(--danger);
}
.import {
  background: var(--bg2);
  border-radius: var(--r);
  padding: 0.75rem;
  margin: 0.25rem 0 0.75rem;
}
.import .grid {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 0.75rem;
  margin: 0.5rem 0;
}
label {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  color: var(--text2);
  font-size: 0.8rem;
}
input,
select,
textarea {
  background: var(--bg1);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.5rem;
  font-family: var(--mono);
}
.toggle input {
  padding: 0;
}
.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 0.75rem;
}
</style>
