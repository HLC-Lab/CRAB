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
// On the global pane, "unset" and "the engine's real default" are the same
// runtime outcome, so the blank option's label states the default directly
// (no separate value would ever need to distinguish itself from it, so the
// explicit option matching the default is hidden entirely - see the template
// below). On a per-experiment override (unsetLabel="inherit"), "inherit" is a
// genuinely distinct third state, so all three options stay.
const isGlobal = computed(() => unset.value === "default");
// A single-group pane is titled by its <h2>, so drop the redundant section <h4>.
const single = computed(() => props.groups?.length === 1);
</script>

<template>
  <div class="opts">
    <section v-if="show('convergence')">
      <h4 v-if="!single">Convergence</h4>
      <div class="grid">
        <label>Min runs <input v-model="o.minruns" placeholder="10" /></label>
        <label>Max runs <input v-model="o.maxruns" placeholder="20" /></label>
        <label>Timeout (s) <input v-model="o.timeout" placeholder="1200" /></label>
        <label>Converge all
          <select v-model="o.convergeall">
            <option value="">{{ isGlobal ? "no, only flagged metrics" : "(inherit)" }}</option>
            <option value="true">yes, every metric</option>
            <option v-if="!isGlobal" value="false">no, only flagged metrics</option>
          </select>
        </label>
        <label>Alpha <input v-model="o.alpha" placeholder="0.05" />
          <small>Confidence-interval significance level.</small>
        </label>
        <label>Beta <input v-model="o.beta" placeholder="0.05" />
          <small>CI width must fall below beta times the mean.</small>
        </label>
      </div>
    </section>

    <section v-if="show('output')">
      <h4 v-if="!single">Output</h4>
      <div class="grid">
        <label>Format
          <select v-model="o.outformat">
            <option value="">{{ isGlobal ? "csv" : "(inherit)" }}</option>
            <option v-if="!isGlobal" value="csv">csv</option>
            <option value="hdf">hdf</option>
          </select>
        </label>
        <label>Retain files
          <select v-model="o.retainFiles">
            <option value="">{{ isGlobal ? "yes" : "(inherit)" }}</option>
            <option value="false">no, delete scratch files</option>
            <option v-if="!isGlobal" value="true">yes</option>
          </select>
        </label>
        <label class="wide">Tags <input v-model="o.tags" placeholder="none" /></label>
      </div>
    </section>

    <section v-if="show('advanced')">
      <h4 v-if="!single">Advanced</h4>
      <div class="grid">
        <label>Extra info <input v-model="o.extrainfo" placeholder="job" /></label>
        <label>Walltime <input v-model="o.walltime" placeholder="00:10:00" /></label>
        <label class="wide">Data path <input v-model="o.datapath" placeholder="<CRAB_ROOT>/data" /></label>
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
input, select {
  background: var(--bg2); border: 1px solid var(--border); color: var(--text);
  border-radius: var(--r); padding: 0.35rem 0.5rem; font-family: var(--mono); font-size: var(--t-md);
}
input::placeholder { color: var(--text3); opacity: 0.75; }
input:focus, select:focus { outline: none; border-color: var(--accent); }
</style>
