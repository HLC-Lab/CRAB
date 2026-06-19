<script setup lang="ts">
// Editor for global_options.sbatch_directives. One directive per line
// ("--time=00:20:00", "--exclusive"); the form toggle picks the emitted shape
// (doc-preferred list vs legacy dict). Mutates the passed SbatchDraft in place.
import { computed } from "vue";
import type { SbatchDraft } from "@/lib/config";

const props = defineProps<{ sbatch: SbatchDraft }>();
const s = computed(() => props.sbatch);

const text = computed({
  get: () => s.value.lines.join("\n"),
  set: (v: string) => {
    s.value.lines = v.split("\n");
  },
});
</script>

<template>
  <div class="sbatch">
    <p class="hint">One <code>#SBATCH</code> directive per line, e.g. <code>--time=00:20:00</code> or <code>--exclusive</code>. <code>--nodes</code> / <code>--ntasks-per-node</code> are set by CRAB and ignored.</p>
    <textarea v-model="text" rows="4" spellcheck="false" placeholder="--time=00:20:00&#10;--exclusive" />
    <fieldset class="form">
      <legend>Write as</legend>
      <label class="radio"><input type="radio" value="list" v-model="s.form" /> list</label>
      <label class="radio"><input type="radio" value="dict" v-model="s.form" /> key=value (legacy)</label>
    </fieldset>
  </div>
</template>

<style scoped>
.sbatch { display: flex; flex-direction: column; gap: 0.5rem; }
.hint { color: var(--text3); font-size: 0.72rem; line-height: 1.35; }
.hint code { font-family: var(--mono); color: var(--text2); }
textarea {
  background: var(--bg2); border: 1px solid var(--border); color: var(--text);
  border-radius: var(--r); padding: 0.4rem 0.5rem; font-family: var(--mono); font-size: 0.8rem;
  width: 100%; resize: vertical;
}
textarea:focus { outline: none; border-color: var(--accent); }
.form { border: 1px solid var(--border); border-radius: var(--r); padding: 0.4rem 0.6rem;
  display: flex; gap: 1rem; }
.form legend { color: var(--text3); font-size: 0.72rem; padding: 0 0.3rem; }
.radio { display: flex; align-items: center; gap: 0.35rem; color: var(--text2); font-size: 0.78rem; cursor: pointer; }
.radio input { accent-color: var(--accent); }
</style>
