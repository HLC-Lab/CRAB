<script setup lang="ts">
// The "Write to cluster" block in the SbatchMan page's sticky top bar: the ONE
// cluster choice of the page (it also feeds the wrapper picker), the path the
// file will land at, and Write files. When writing is not possible the reason
// sits next to the button: a problem count that opens the problem list, or a
// pointer to Remotes when no cluster is connected. Presentational only: the
// store owns the state and does the API call.
import { computed } from "vue";
import { RouterLink } from "vue-router";
import type { SbatchmanWriteResult } from "@/api/types";

const props = defineProps<{
  connectedClusters: string[];
  destination: string;
  /** Where the file will land, from the backend. Empty until known. */
  stagingPath: string;
  busy: boolean;
  /** Number of validation problems; Write files is disabled while it is above zero. */
  problemCount: number;
  canWrite: boolean;
  lastWrite: SbatchmanWriteResult | null;
}>();
const emit = defineEmits<{
  "update:destination": [string];
  write: [];
  "show-problems": [];
}>();

// The directory part is dimmed context, the file name is what changes with the campaign name.
const stagingDir = computed(() =>
  props.stagingPath.slice(0, props.stagingPath.lastIndexOf("/") + 1),
);
const stagingFile = computed(() => props.stagingPath.slice(props.stagingPath.lastIndexOf("/") + 1));
</script>

<template>
  <section class="write-target" aria-label="Write to cluster">
    <div class="main">
      <div class="lbl">
        <span class="seclabel">Write to cluster</span>
        <div class="row">
          <template v-if="connectedClusters.length">
            <select
              aria-label="Cluster"
              :value="destination"
              @change="emit('update:destination', ($event.target as HTMLSelectElement).value)"
            >
              <option v-for="c in connectedClusters" :key="c" :value="c">{{ c }}</option>
            </select>
            <span v-if="stagingPath" class="staging" :title="stagingPath"
              >&rarr; <span class="dir">{{ stagingDir }}</span
              ><b>{{ stagingFile }}</b></span
            >
          </template>
          <span v-else class="none"
            >No cluster connected.
            <RouterLink to="/remotes">Connect a cluster in Remotes</RouterLink></span
          >
        </div>
      </div>

      <div class="go">
        <button
          v-if="problemCount"
          type="button"
          class="problems"
          title="Show what to fix"
          @click="emit('show-problems')"
        >
          {{ problemCount }} problem{{ problemCount === 1 ? "" : "s" }}
        </button>
        <button type="button" class="btn primary" :disabled="!canWrite" @click="emit('write')">
          {{ busy ? "Writing…" : "Write files" }}
        </button>
      </div>
    </div>

    <p v-if="lastWrite" class="out" role="status">
      Written to <b :title="lastWrite.remote_path">{{ lastWrite.remote_path }}</b>
      <span class="muted" :title="lastWrite.local_path"
        >(local copy: {{ lastWrite.local_path }})</span
      >
    </p>
  </section>
</template>

<style scoped>
.write-target {
  margin-left: auto;
  min-width: 0;
  max-width: 100%;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  border: 1px solid var(--accent);
  border-radius: var(--r2);
  background: var(--bg1);
  padding: 0.45rem 0.45rem 0.45rem 0.9rem;
  box-shadow: 0 0 0 3px var(--accent-glow);
}
.main {
  display: flex;
  align-items: center;
  gap: 0.8rem;
}
.lbl {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  min-width: 0;
}
.seclabel {
  font-family: var(--sans);
  font-size: var(--t-xs);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text2);
  font-weight: 700;
}
.row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
}
select {
  min-width: 9.5rem;
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.5rem;
  font-family: var(--sans);
  font-size: var(--t-md);
}
select:focus {
  outline: none;
  border-color: var(--accent);
}
.staging {
  display: flex;
  gap: 0.3rem;
  min-width: 0;
  max-width: 22rem;
  font-family: var(--mono);
  font-size: var(--t-sm);
  color: var(--text2);
  white-space: nowrap;
}
/* A long directory gives way, the file name (what the campaign name changes) stays whole. */
.staging .dir {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.staging b {
  flex-shrink: 0;
  color: var(--text);
  font-weight: 600;
}
.none {
  color: var(--text2);
  font-size: var(--t-sm);
  padding: 0.4rem 0;
}
.none a {
  color: var(--accent);
}
.go {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex-shrink: 0;
}
.problems {
  background: transparent;
  border: none;
  color: var(--warn);
  font-family: var(--sans);
  font-size: var(--t-sm);
  font-weight: 600;
  text-decoration: underline;
  cursor: pointer;
  padding: 0;
}
.btn {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.5rem 1.1rem;
  cursor: pointer;
  font-family: var(--sans);
  font-size: var(--t-md);
  font-weight: 700;
  white-space: nowrap;
}
.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.out {
  margin: 0;
  max-width: 34rem;
  font-size: var(--t-sm);
  color: var(--text2);
  overflow-wrap: anywhere;
}
.out b {
  color: var(--text);
  font-family: var(--mono);
}
.out .muted {
  color: var(--text3);
  font-family: var(--mono);
}
</style>
