<script setup lang="ts">
// Editor for the tunable option fields (convergence / output / advanced). Mutates
// the passed OptionsDraft in place. Reused for global_options and, later, for an
// experiment's local_options overrides. Blank/("default") fields are not emitted.
import { computed } from "vue";
import type { OptionsDraft } from "@/lib/config";

const props = defineProps<{
  options: OptionsDraft;
  // Label for the "unset" choice on tri-state selects: "default" globally,
  // "inherit" when overriding per-experiment.
  unsetLabel?: string;
}>();
const o = computed(() => props.options);
const unset = computed(() => props.unsetLabel ?? "default");
</script>

<template>
  <div class="opts">
    <section>
      <h4>Convergence</h4>
      <div class="grid">
        <label>Min runs <input v-model="o.minruns" placeholder="10" /></label>
        <label>Max runs <input v-model="o.maxruns" placeholder="20" /></label>
        <label>Timeout (s) <input v-model="o.timeout" placeholder="1200.0" /></label>
        <label>Converge all
          <select v-model="o.convergeall">
            <option value="">({{ unset }})</option>
            <option value="true">yes — every metric</option>
            <option value="false">no — flagged metrics</option>
          </select>
        </label>
        <label>Alpha <input v-model="o.alpha" placeholder="0.05" /></label>
        <label>Beta <input v-model="o.beta" placeholder="0.05" /></label>
      </div>
    </section>

    <section>
      <h4>Output</h4>
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
            <option value="false">no — delete scratch</option>
          </select>
        </label>
        <label class="wide">Tags <input v-model="o.tags" placeholder="free-form label" /></label>
      </div>
    </section>

    <section>
      <h4>Advanced</h4>
      <div class="grid">
        <label>Extra info <input v-model="o.extrainfo" placeholder="job-name token" /></label>
        <label>Walltime <input v-model="o.walltime" placeholder="00:10:00" /></label>
        <label class="wide">Data path <input v-model="o.datapath" placeholder="defaults to &lt;CRAB_ROOT&gt;/data" /></label>
      </div>
    </section>
  </div>
</template>

<style scoped>
.opts { display: flex; flex-direction: column; gap: 0.8rem; }
h4 { font-family: var(--sans); font-size: 0.75rem; text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--text3); margin-bottom: 0.4rem; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; }
label { display: flex; flex-direction: column; gap: 0.2rem; color: var(--text2); font-size: 0.75rem; }
label.wide { grid-column: 1 / -1; }
input, select {
  background: var(--bg2); border: 1px solid var(--border); color: var(--text);
  border-radius: var(--r); padding: 0.35rem 0.5rem; font-family: var(--mono); font-size: 0.8rem;
}
input:focus, select:focus { outline: none; border-color: var(--accent); }
</style>
