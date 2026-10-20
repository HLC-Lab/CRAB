<script setup lang="ts">
// GLOBAL · Basics: job name/size fields plus an informational (read-only)
// reference of the source cluster's Slurm partitions, to help size `Nodes`.
// `sourceCluster` is shared with ExperimentPane (which owns picking it) so
// it lives in AuthorView; this pane only reads it.
import { computed, ref } from "vue";
import { useAuthorStore } from "@/stores/author";
import { useCatalogStore } from "@/stores/catalog";

const props = defineProps<{
  sourceCluster: string;
}>();

const store = useAuthorStore();
const catalog = useCatalogStore();
const d = store.draft;

const showNodes = ref(false);
const nodesInfo = computed(() => catalog.nodes[props.sourceCluster]);
function toggleNodes() {
  showNodes.value = !showNodes.value;
  if (showNodes.value && props.sourceCluster) catalog.loadNodes(props.sourceCluster);
}
</script>

<template>
  <h2 class="pane-title">Basics</h2>
  <div class="job-grid">
    <label class="wide"
      >Name <input v-model="d.name" />
      <small>Names the run and prefixes the output folder.</small>
    </label>
    <label
      >Nodes <input v-model="d.numnodes" />
      <small>Total nodes to allocate.</small>
    </label>
    <label
      >Processes per node <input v-model="d.ppn" />
      <small>Tasks launched per node (ppn).</small>
    </label>
  </div>

  <!-- Informational cluster-node reference for sizing `Nodes` -->
  <div v-if="sourceCluster" class="nodes-ref">
    <button class="link-btn" @click="toggleNodes">
      {{ showNodes ? "▾" : "▸" }} cluster nodes · {{ sourceCluster }}
    </button>
    <div v-if="showNodes" class="nodes-body">
      <p v-if="catalog.busy[sourceCluster]" class="hint">Loading…</p>
      <p v-else-if="catalog.error[sourceCluster]" class="hint err">
        {{ catalog.error[sourceCluster] }}
      </p>
      <template v-else-if="nodesInfo">
        <p v-if="!nodesInfo.available" class="hint">
          {{ nodesInfo.note || "sinfo unavailable here" }}
        </p>
        <template v-else>
          <p class="hint">
            {{ nodesInfo.nodes.length }} nodes · {{ nodesInfo.partitions.length }} partition{{
              nodesInfo.partitions.length === 1 ? "" : "s"
            }}
          </p>
          <ul class="part-list">
            <li v-for="p in nodesInfo.partitions" :key="p.name">
              <span class="pname">{{ p.name }}</span>
              <span class="pmeta"
                >{{ p.nodes ?? "?" }}<span v-if="p.avail"> · {{ p.avail }}</span></span
              >
            </li>
          </ul>
          <p class="hint muted">
            Slurm partitions on the cluster, not the same as allocation node groups.
          </p>
        </template>
      </template>
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

.pane-title {
  font-family: var(--sans);
  font-size: var(--t-lg);
  color: var(--text);
}
.job-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.9rem;
  max-width: 38rem;
}
.job-grid label.wide {
  grid-column: 1 / -1;
}
.job-grid label {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  color: var(--text2);
  font-size: var(--t-md);
}
.job-grid small {
  color: var(--text3);
  font-size: var(--t-xs);
  line-height: 1.3;
}

/* Cluster-node reference (informational) */
.nodes-ref {
  margin-top: -0.2rem;
}
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
.nodes-body {
  margin-top: 0.4rem;
  max-width: 30rem;
}
.nodes-body .hint {
  color: var(--text3);
  font-size: var(--t-sm);
  margin-bottom: 0.2rem;
}
.nodes-body .hint.err {
  color: var(--danger);
}
.nodes-body .hint.muted {
  color: var(--text3);
  font-style: italic;
}
.part-list {
  list-style: none;
  max-height: 12rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  margin: 0.2rem 0;
}
.part-list li {
  display: flex;
  justify-content: space-between;
  gap: 0.5rem;
  font-size: var(--t-sm);
  color: var(--text2);
  padding: 0.12rem 0.2rem;
}
.pname {
  font-family: var(--mono);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pmeta {
  color: var(--text3);
  white-space: nowrap;
}
</style>
