<script setup lang="ts">
// Editor for the tunable option fields (convergence / output / advanced). Mutates
// the passed OptionsDraft in place. Reused for global_options and, later, for an
// experiment's local_options overrides. Blank/("default") fields are not emitted.
import { computed } from "vue";
import type { OptionsDraft } from "@/lib/config";

type Group = "convergence" | "output" | "advanced";
const props = defineProps<{
  options: OptionsDraft;
  // Label for the "unset" choice on tri-state selects: "default" globally,
  // "inherit" when overriding per-experiment.
  unsetLabel?: string;
  // Which field groups to render. Omit for all three (used by the overrides card);
  // the rail panes pass a subset so "Convergence" and "Output & advanced" are separate.
  groups?: Group[];
}>();
const o = computed(() => props.options);
const unset = computed(() => props.unsetLabel ?? "default");
const show = (g: Group) => !props.groups || props.groups.includes(g);
// A single-group pane is titled by its <h2>, so drop the redundant section <h4>.
const single = computed(() => props.groups?.length === 1);
</script>

<template>
  <div class="opts">
    <section v-if="show('convergence')">
      <h4 v-if="!single">Convergence</h4>
      <div class="grid">
        <label>Min runs <span class="def">default 10</span><input v-model="o.minruns" /></label>
        <label>Max runs <span class="def">default 20</span><input v-model="o.maxruns" /></label>
        <label>Timeout (s) <span class="def">default 1200</span><input v-model="o.timeout" /></label>
        <label>Converge all
          <select v-model="o.convergeall">
            <option value="">({{ unset }})</option>
            <option value="true">yes, every metric</option>
            <option value="false">no, only flagged metrics</option>
          </select>
        </label>
        <label>Alpha <span class="def">default 0.05</span><input v-model="o.alpha" />
          <small>Confidence-interval significance level.</small>
        </label>
        <label>Beta <span class="def">default 0.05</span><input v-model="o.beta" />
          <small>CI width must fall below beta times the mean.</small>
        </label>
      </div>
    </section>

    <section v-if="show('output')">
      <h4 v-if="!single">Output</h4>
      <div class="grid">
        <label>Format
          <select v-model="o.outformat">
            <option value="">({{ unset }})</option>
            <option value="csv">csv</option>
            <option value="hdf">hdf</option>
          </select>
        </label>
        <label>Retain files
          <select v-model="o.retainFiles">
            <option value="">({{ unset }})</option>
            <option value="true">yes</option>
            <option value="false">no, delete scratch files</option>
          </select>
        </label>
        <label class="wide">Tags <input v-model="o.tags" placeholder="none" /></label>
      </div>
    </section>

    <section v-if="show('advanced')">
      <h4 v-if="!single">Advanced</h4>
      <div class="grid">
        <label>Extra info <span class="def">default job</span><input v-model="o.extrainfo" /></label>
        <label>Walltime <span class="def">default 00:10:00</span><input v-model="o.walltime" /></label>
        <label class="wide">Data path <span class="def">default &lt;CRAB_ROOT&gt;/data</span><input v-model="o.datapath" /></label>
      </div>
    </section>
  </div>
</template>

<style scoped>
.opts { display: flex; flex-direction: column; gap: 0.8rem; }
h4 { font-family: var(--sans); font-size: var(--t-sm); text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--text3); margin-bottom: 0.4rem; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; }
label { display: flex; flex-direction: column; gap: 0.2rem; color: var(--text2); font-size: var(--t-sm); }
label.wide { grid-column: 1 / -1; }
small { color: var(--text3); font-size: var(--t-xs); line-height: 1.3; }
/* Engine default surfaced as a muted label hint (never as a value inside the
   input, which would read as a real, committed value). */
.def { color: var(--text3); font-family: var(--mono); font-size: var(--t-xs); }
input, select {
  background: var(--bg2); border: 1px solid var(--border); color: var(--text);
  border-radius: var(--r); padding: 0.35rem 0.5rem; font-family: var(--mono); font-size: var(--t-md);
}
input:focus, select:focus { outline: none; border-color: var(--accent); }
</style>
