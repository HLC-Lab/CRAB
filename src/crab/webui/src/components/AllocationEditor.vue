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

// Plain 3-way framing over the existing (by, split) model:
//   equal  → by:"app", no split (engine default equal split)
//   share  → by:"app", a split percentage list
//   groups → by:"groups", named node groups
const divide = computed<"equal" | "share" | "groups">({
  get: () => (a.value.by === "groups" ? "groups" : a.value.split.trim() ? "share" : "equal"),
  set: (v) => {
    if (v === "groups") {
      a.value.by = "groups";
    } else {
      a.value.by = "app";
      if (v === "equal") a.value.split = ""; // equal = no split, keeps the no-default rule
    }
  },
});

function addGroup() {
  a.value.partitions.push(emptyPartition());
}
function removeGroup(i: number) {
  a.value.partitions.splice(i, 1);
}
</script>

<template>
  <div class="alloc">
    <p class="lead">Controls how the job's nodes are divided among the apps. Leave it on equal to use the engine default.</p>

    <div class="body">
      <fieldset class="q">
        <legend>How should nodes be divided?</legend>
        <label class="radio"><input type="radio" value="equal" v-model="divide" /> equally among all apps <span class="def">default</span></label>
        <label class="radio"><input type="radio" value="share" v-model="divide" /> by share per app</label>
        <label class="radio"><input type="radio" value="groups" v-model="divide" /> into named groups</label>
      </fieldset>

      <label v-if="divide === 'share'" class="field">Split (% per app)
        <input v-model="a.split" placeholder="e.g. 60, 40. Blank means equal." />
      </label>

      <div v-else-if="divide === 'groups'" class="groups">
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

      <fieldset v-if="!hideMode" class="q">
        <legend>How are nodes laid out?</legend>
        <div class="moderow">
          <select v-model="a.mode">
            <option value="linear">linear</option>
            <option value="interleaved">interleaved</option>
            <option value="random">random</option>
          </select>
          <label v-if="a.mode === 'interleaved'" class="inline">stride <input v-model="a.stride" placeholder="1" /></label>
          <label v-if="a.mode === 'random'" class="inline">seed <input v-model="a.seed" placeholder="random if blank" /></label>
        </div>
        <p class="hint">linear: each group gets a contiguous block. interleaved: nodes dealt round-robin. random: node list shuffled, then split.</p>
      </fieldset>
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
.q { border: 1px solid var(--border); border-radius: var(--r); padding: 0.4rem 0.6rem 0.55rem;
  display: flex; flex-direction: column; gap: 0.15rem; }
.q legend { color: var(--text3); font-size: 0.72rem; padding: 0 0.3rem; }
.def { color: var(--text3); font-size: 0.66rem; }
.moderow { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; margin-top: 0.2rem; }
.inline { display: flex; align-items: center; gap: 0.35rem; color: var(--text2); font-size: 0.78rem; }
.inline input { width: 5rem; }
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
