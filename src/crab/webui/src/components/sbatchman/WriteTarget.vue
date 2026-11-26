<script setup lang="ts">
// The "Write to cluster" block in the SbatchMan page's sticky top bar: the ONE
// cluster choice of the page (it also feeds the wrapper picker), the path the
// file will land at, and Write files. When writing is not possible the reason
// sits next to the button: a problem count that opens the problem list, or a
// pointer to Remotes when no cluster is connected. On an umbrella install it also asks
// for the benchmark folder the file goes into (existing folders are suggested, a new name
// is allowed). When the cluster already has a file with this name it asks whether to
// replace it. Presentational only: the store owns the state and does the API call.
import { computed } from "vue";
import { RouterLink } from "vue-router";
import type { SbatchmanWriteResult } from "@/api/types";

// What the folder is, for the field's tooltip (the visible line under it is the short form).
const BENCHMARK_HINT =
  "A folder under benchmarks/ in the umbrella on the cluster. The jobs file is written there. Pick one or type a new name. Empty: the first folder.";

const props = defineProps<{
  connectedClusters: string[];
  destination: string;
  /** Where the file will land, from the backend. Empty until known. */
  stagingPath: string;
  busy: boolean;
  /** Number of validation problems; Write files is disabled while it is above zero. */
  problemCount: number;
  canWrite: boolean;
  /** The cluster is an umbrella install: the file goes into a benchmark folder. */
  isUmbrella: boolean;
  /** The folder as typed, and the one used when it is empty. */
  benchmark: string;
  defaultBenchmark: string;
  /** Existing benchmark folders, offered as suggestions. */
  benchmarks: string[];
  /** Why the folder blocks Write files, if it does. */
  benchmarkProblem: string | null;
  lastWrite: SbatchmanWriteResult | null;
  /** What to do after the write (nothing is launched); commands sit between backticks. */
  nextStep: string;
  /** The cluster already has a file with this name: ask before replacing it. */
  needsOverwrite: boolean;
}>();
const emit = defineEmits<{
  "update:destination": [string];
  "update:benchmark": [string];
  write: [];
  replace: [];
  "show-problems": [];
}>();

// The directory part is dimmed context, the file name is what changes with the campaign name.
const stagingDir = computed(() =>
  props.stagingPath.slice(0, props.stagingPath.lastIndexOf("/") + 1),
);
const stagingFile = computed(() => props.stagingPath.slice(props.stagingPath.lastIndexOf("/") + 1));

// The file name of the last write, for the "Written ..." line (the full path follows it).
const writtenFile = computed(() => {
  const path = props.lastWrite?.remote_path ?? "";
  return path.slice(path.lastIndexOf("/") + 1);
});
// The next-step text marks commands with backticks: odd parts are code.
const nextStepParts = computed(() =>
  props.nextStep.split("`").map((text, i) => ({ text, code: i % 2 === 1 })),
);
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

      <div v-if="isUmbrella" class="lbl folder">
        <label class="seclabel" for="benchmark-folder">Benchmark folder</label>
        <input
          id="benchmark-folder"
          list="benchmark-folders"
          autocomplete="off"
          spellcheck="false"
          :value="benchmark"
          :placeholder="defaultBenchmark || 'a folder name'"
          :title="BENCHMARK_HINT"
          aria-describedby="benchmark-folder-hint"
          :aria-invalid="benchmarkProblem ? 'true' : undefined"
          @input="emit('update:benchmark', ($event.target as HTMLInputElement).value)"
        />
        <datalist id="benchmark-folders">
          <option v-for="b in benchmarks" :key="b" :value="b" />
        </datalist>
        <small id="benchmark-folder-hint" class="hint" :title="BENCHMARK_HINT"
          >Where the jobs file goes, under benchmarks/. Empty: the first folder.</small
        >
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

    <p v-if="benchmarkProblem" class="out problem" role="alert">{{ benchmarkProblem }}</p>

    <div v-if="needsOverwrite" class="out replace" role="alert">
      <span>A file with this name already exists on the cluster. Replace it?</span>
      <button type="button" class="btn" :disabled="busy" @click="emit('replace')">
        Replace file
      </button>
    </div>

    <div v-if="lastWrite" class="out written" role="status">
      <p class="line">
        <b class="ok">Written {{ writtenFile }}</b> to the cluster:
        <span class="written-path" :title="lastWrite.remote_path">{{ lastWrite.remote_path }}</span>
      </p>
      <p v-if="nextStep" class="line">
        <template v-for="(part, i) in nextStepParts" :key="i"
          ><code v-if="part.code">{{ part.text }}</code
          ><template v-else>{{ part.text }}</template></template
        >
      </p>
      <p class="line copy" :title="lastWrite.local_path">
        A copy is kept on this laptop:
        <span class="written-path">{{ lastWrite.local_path }}</span>
      </p>
    </div>
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
  flex-wrap: wrap;
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
.folder input {
  width: 11rem;
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.5rem;
  font-family: var(--mono);
  font-size: var(--t-sm);
}
.folder input:focus {
  outline: none;
  border-color: var(--accent);
}
.folder .hint {
  max-width: 15rem;
  font-size: var(--t-xs);
  color: var(--text3);
}
.folder input[aria-invalid="true"] {
  border-color: var(--warn);
}
.out.problem {
  color: var(--warn);
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
.replace {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  color: var(--warn);
}
.replace .btn {
  padding: 0.3rem 0.8rem;
  font-size: var(--t-sm);
}
.written {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.written .line {
  margin: 0;
}
.written .ok {
  color: var(--ok, var(--text));
  font-weight: 700;
}
.written-path {
  color: var(--text);
  font-family: var(--mono);
  overflow-wrap: anywhere;
  word-break: break-all;
}
.written code {
  font-family: var(--mono);
  color: var(--text);
  background: var(--bg2);
  border-radius: var(--r);
  padding: 0.05rem 0.3rem;
  overflow-wrap: anywhere;
}
.written .copy {
  color: var(--text3);
}
.written .copy .written-path {
  color: var(--text3);
}
</style>
