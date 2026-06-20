<script setup lang="ts">
// Reusable editor for an `allocation` object. Mutates the passed AllocationDraft
// in place (it is reactive). Used for global_options.allocation now and, in a
// later increment, per-experiment local_options.allocation.
import { computed } from "vue";
import { type AllocationDraft, emptyPartition } from "@/lib/config";

const props = defineProps<{
  alloc: AllocationDraft;
  // When the caller owns the mode selector (e.g. the per-experiment override,
  // which adds an "inherit" option), hide this component's own mode <select>.
  hideMode?: boolean;
}>();
const a = computed(() => props.alloc);

function addGroup() {
  a.value.partitions.push(emptyPartition());
}
function removeGroup(i: number) {
  a.value.partitions.splice(i, 1);
}
</script>

<template>
  <div class="alloc">
    <p class="lead">Leave this on <code>linear</code> with no split to use the engine default (an equal split). Set a mode, split, or node groups only if you need them.</p>

    <div class="body">
      <label v-if="!hideMode" class="field">Layout mode
        <select v-model="a.mode">
          <option value="linear">linear (contiguous blocks)</option>
          <option value="interleaved">interleaved (round-robin)</option>
          <option value="random">random (shuffled)</option>
        </select>
      </label>
      <label v-if="a.mode === 'interleaved'" class="field">Stride
        <input v-model="a.stride" placeholder="1" />
      </label>
      <label v-if="a.mode === 'random'" class="field">Seed (optional)
        <input v-model="a.seed" placeholder="non-deterministic if blank" />
      </label>

      <fieldset class="by">
        <legend>Distribute nodes</legend>
        <label class="radio"><input type="radio" value="app" v-model="a.by" /> by app (split)</label>
        <label class="radio"><input type="radio" value="groups" v-model="a.by" /> by node groups</label>
      </fieldset>

      <label v-if="a.by === 'app'" class="field">Split (% per app)
        <input v-model="a.split" placeholder="e.g. 60, 40. Blank means equal." />
      </label>

      <div v-else class="groups">
        <div class="ghead">
          <span>Node groups</span>
          <button class="icon-btn" title="Add node group" @click="addGroup">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14" /><path d="M5 12h14" /></svg>
          </button>
        </div>
        <p class="hint">Named groups (for example <code>victim</code> and <code>aggressor</code>) that an app can target. Set a share on every group, or on none for an equal split.</p>
        <div v-for="(p, i) in a.partitions" :key="i" class="grow-row">
          <input v-model="p.name" class="gname" placeholder="group name" />
          <div class="share">
            <input v-model="p.share" class="gshare" placeholder="auto" />
            <span class="pct">%</span>
          </div>
          <span v-if="Object.keys(p.rest).length" class="inner" title="Has inner mode/split (preserved)">⋯</span>
          <button class="icon-btn danger" title="Remove group" @click="removeGroup(i)">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M5 7h14" /><path d="M9 7V5h6v2" /><path d="M7 7l1 13h8l1-13" />
            </svg>
          </button>
        </div>
        <p v-if="!a.partitions.length" class="empty">No groups yet.</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.alloc { display: flex; flex-direction: column; gap: 0.6rem; }
.lead { color: var(--text3); font-size: 0.72rem; line-height: 1.35; }
.lead code { font-family: var(--mono); color: var(--text2); }
.radio { display: flex; align-items: center; gap: 0.4rem; color: var(--text2);
  font-size: 0.8rem; cursor: pointer; }
.radio input { accent-color: var(--accent); }
.body { display: flex; flex-direction: column; gap: 0.6rem; padding-left: 0.2rem; }
.field { display: flex; flex-direction: column; gap: 0.2rem; color: var(--text2); font-size: 0.78rem; }
.by { border: 1px solid var(--border); border-radius: var(--r); padding: 0.4rem 0.6rem;
  display: flex; gap: 1rem; flex-wrap: wrap; }
.by legend { color: var(--text3); font-size: 0.72rem; padding: 0 0.3rem; }
.ghead { display: flex; align-items: center; justify-content: space-between; color: var(--text2);
  font-size: 0.78rem; }
.hint { color: var(--text3); font-size: 0.72rem; }
.hint code { font-family: var(--mono); color: var(--text2); }
.grow-row { display: flex; align-items: center; gap: 0.4rem; }
.gname { flex: 1; }
.share { display: flex; align-items: center; gap: 0.2rem; }
.gshare { width: 3.5rem; text-align: right; }
.pct { color: var(--text3); font-size: 0.75rem; }
.inner { color: var(--text3); cursor: help; }
.empty { color: var(--text3); font-size: 0.78rem; }
.icon-btn {
  display: inline-flex; align-items: center; justify-content: center;
  background: transparent; border: 1px solid transparent; color: var(--text2);
  border-radius: var(--r); padding: 0.2rem; cursor: pointer;
}
.icon-btn:hover { border-color: var(--border); color: var(--text); }
.icon-btn.danger:hover { border-color: var(--danger); color: var(--danger); }
.icon-btn svg {
  width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.75;
  stroke-linecap: round; stroke-linejoin: round;
}
input, select {
  background: var(--bg2); border: 1px solid var(--border); color: var(--text);
  border-radius: var(--r); padding: 0.35rem 0.5rem; font-family: var(--mono); font-size: 0.82rem;
}
input:focus, select:focus { outline: none; border-color: var(--accent); }
</style>
