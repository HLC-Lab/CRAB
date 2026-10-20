<script setup lang="ts">
// Left rail: navigator over global sections + experiments. Selection itself
// (`view`) is owned by AuthorView (shared with the main pane router); this
// component only renders the nav and asks for selection/list changes.
import { computed } from "vue";
import { useAuthorStore } from "@/stores/author";
import { hasAllocation } from "@/lib/config";

type GlobalId = "job" | "alloc" | "run";

const props = defineProps<{
  view: { kind: "global" | "exp"; id: GlobalId | number };
}>();
const emit = defineEmits<{
  "select-global": [GlobalId];
  "select-exp": [number];
  "add-experiment": [];
  "duplicate-experiment": [number];
  "request-remove-experiment": [number];
}>();

const store = useAuthorStore();
const d = store.draft;

// Active dots on the global nav items.
const CONVERGE_KEYS = ["minruns", "maxruns", "timeout", "convergeall", "alpha", "beta"] as const;
const OUTPUT_KEYS = [
  "outformat",
  "retainFiles",
  "tags",
  "extrainfo",
  "walltime",
  "datapath",
] as const;
const allocActive = computed(() => hasAllocation(d.allocation));
const runActive = computed(
  () =>
    CONVERGE_KEYS.some((k) => d.options[k] !== "") ||
    OUTPUT_KEYS.some((k) => d.options[k] !== "") ||
    d.sbatch.lines.some((l) => l.trim()),
);
</script>

<template>
  <aside class="rail">
    <div class="zone">
      <div class="zlabel">Setup</div>
      <ul>
        <li
          :class="{ on: props.view.kind === 'global' && props.view.id === 'job' }"
          @click="emit('select-global', 'job')"
        >
          <span>Basics</span>
        </li>
        <li
          :class="{ on: props.view.kind === 'global' && props.view.id === 'alloc' }"
          @click="emit('select-global', 'alloc')"
        >
          <span>Node allocation</span><span v-if="allocActive" class="dot" />
        </li>
        <li
          :class="{ on: props.view.kind === 'global' && props.view.id === 'run' }"
          @click="emit('select-global', 'run')"
        >
          <span>Run settings</span><span v-if="runActive" class="dot" />
        </li>
      </ul>
    </div>

    <div class="zone">
      <div class="zlabel">
        <span>Experiments</span>
        <button class="add" title="Add experiment" @click="emit('add-experiment')">+</button>
      </div>
      <ul>
        <li
          v-for="(exp, i) in d.experiments"
          :key="i"
          :class="{ on: props.view.kind === 'exp' && props.view.id === i }"
          @click="emit('select-exp', i)"
        >
          <span class="exp-name">{{ exp.name || "untitled" }}</span>
          <span class="exp-meta"
            >{{ exp.apps.length }} app{{ exp.apps.length === 1 ? "" : "s" }}</span
          >
          <span class="exp-actions">
            <button
              type="button"
              class="icon-btn"
              title="Duplicate experiment"
              @click.stop="emit('duplicate-experiment', i)"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <rect x="9" y="9" width="11" height="11" rx="2" />
                <path d="M5 15V5a2 2 0 0 1 2-2h10" />
              </svg>
            </button>
            <button
              type="button"
              class="icon-btn danger"
              title="Remove experiment"
              @click.stop="emit('request-remove-experiment', i)"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M5 7h14" />
                <path d="M9 7V5h6v2" />
                <path d="M7 7l1 13h8l1-13" />
              </svg>
            </button>
          </span>
        </li>
        <li v-if="!d.experiments.length" class="empty nav-empty">No experiments yet.</li>
      </ul>
    </div>
  </aside>
</template>

<style scoped>
.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid transparent;
  color: var(--text2);
  border-radius: var(--r);
  padding: 0.2rem;
  cursor: pointer;
}
.icon-btn:hover {
  border-color: var(--border);
  color: var(--text);
}
.icon-btn svg {
  width: 18px;
  height: 18px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.75;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.rail {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
  padding: 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

/* Rail navigator: two labelled zones (Setup / Experiments) */
.zone {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.zlabel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-family: var(--sans);
  color: var(--text3);
  font-size: var(--t-sm);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 700;
  padding: 0 0.2rem;
}
.zone .add {
  background: transparent;
  border: none;
  color: var(--text3);
  cursor: pointer;
  font-size: 1rem;
  line-height: 1;
  padding: 0;
}
.zone .add:hover {
  color: var(--text);
}
.zone ul {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.zone li {
  position: relative;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.5rem;
  padding: 0.4rem 4.6rem 0.4rem 0.55rem;
  border: 1px solid transparent;
  border-radius: var(--r);
  cursor: pointer;
  color: var(--text2);
  font-size: var(--t-md);
}
.zone li:hover {
  background: var(--bg2);
  color: var(--text);
}
.zone li.on {
  background: var(--bg2);
  border-color: var(--accent);
  color: var(--text);
}
.zone .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  flex-shrink: 0;
}
.exp-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.exp-meta {
  color: var(--text3);
  font-size: var(--t-sm);
  flex-shrink: 0;
}
.nav-empty {
  cursor: default;
  color: var(--text3);
}
.nav-empty:hover {
  background: transparent;
}
/* Hover-actions: duplicate/delete replace the "N apps" meta text on hover.
   Both are absolutely positioned in the same spot (not flex siblings of
   .exp-name), so neither ever reserves layout width while hidden -- .exp-name
   gets the row's full available width at all times, and only truncates when
   the text genuinely doesn't fit next to the reserved right-hand gutter
   (see .zone li's padding-right above). */
.exp-meta,
.exp-actions {
  position: absolute;
  right: 0.55rem;
  top: 50%;
  transform: translateY(-50%);
}
.exp-meta {
  transition: opacity 0.1s ease;
}
.exp-actions {
  display: inline-flex;
  align-items: center;
  gap: 0.15rem;
  opacity: 0;
}
.zone li:hover .exp-actions {
  opacity: 1;
}
.zone li:hover .exp-meta {
  opacity: 0;
}

.empty {
  color: var(--text3);
  font-size: var(--t-md);
}
</style>
