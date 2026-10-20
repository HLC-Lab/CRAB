<script setup lang="ts">
// One folder of the Library tree (recursive): its heading, then its subfolders and files. `bare`
// skips the heading, for a section's root. Open and "+ N more" state belongs to the parent.
import { computed } from "vue";
import type { WrapperEntry } from "@/api/types";
import { visibleFiles } from "@/lib/wrapperTree";
import type { WrapperFolder } from "@/lib/wrappers";

const props = defineProps<{
  folder: WrapperFolder;
  depth: number;
  /** Tells the same folder name apart across sections ("L" local, "S" shipped). */
  prefix: string;
  /** The relpath of the selected wrapper. */
  current: string;
  /** A filter is typed: every folder is open and every file is listed. */
  filtering: boolean;
  open: Record<string, boolean>;
  more: Record<string, boolean>;
  bare?: boolean;
}>();
const emit = defineEmits<{
  select: [relpath: string];
  toggle: [key: string, open: boolean];
  more: [key: string];
}>();

const key = computed(() => `${props.prefix}:${props.folder.path}`);
const holdsCurrent = computed(() => props.current.startsWith(`${props.folder.path}/`));
const isOpen = computed(() => {
  if (props.bare || props.filtering) return true;
  if (key.value in props.open) return props.open[key.value];
  return props.depth === 0 || holdsCurrent.value;
});
const childDepth = computed(() => (props.bare ? props.depth : props.depth + 1));

// The selected file stays listed even when it sits past the first few.
const showAll = computed(
  () =>
    props.filtering ||
    !!props.more[key.value] ||
    props.folder.wrappers.some((w) => w.relpath === props.current),
);
const files = computed(() => visibleFiles(props.folder, showAll.value));

function fileName(w: WrapperEntry): string {
  return w.relpath.split("/").pop() ?? w.relpath;
}
function mark(w: WrapperEntry): { text: string; bad: boolean } | null {
  if (!w.loadable) return { text: "cannot load", bad: true };
  if (w.binary.status === "missing") return { text: "no binary", bad: false };
  return null;
}
</script>

<template>
  <div class="folder">
    <button
      v-if="!bare"
      type="button"
      class="head"
      :class="{ suite: depth === 0 }"
      :style="{ '--d': depth }"
      :aria-expanded="isOpen"
      @click="emit('toggle', key, !isOpen)"
    >
      {{ folder.name }}<span v-if="!isOpen" class="count">{{ folder.count }}</span>
    </button>
    <template v-if="isOpen">
      <WrapperTreeFolder
        v-for="f in folder.folders"
        :key="f.path"
        :folder="f"
        :depth="childDepth"
        :prefix="prefix"
        :current="current"
        :filtering="filtering"
        :open="open"
        :more="more"
        @select="emit('select', $event)"
        @toggle="(k, o) => emit('toggle', k, o)"
        @more="emit('more', $event)"
      />
      <button
        v-for="w in files.files"
        :key="w.relpath"
        type="button"
        class="leaf"
        :class="{ on: w.relpath === current }"
        :style="{ '--d': childDepth }"
        :aria-current="w.relpath === current ? 'true' : undefined"
        @click="emit('select', w.relpath)"
      >
        <span class="name">{{ fileName(w) }}</span>
        <span v-if="mark(w)" class="flag" :class="{ bad: mark(w)?.bad }">{{ mark(w)?.text }}</span>
      </button>
      <button
        v-if="files.more"
        type="button"
        class="more"
        :style="{ '--d': childDepth }"
        @click="emit('more', key)"
      >
        + {{ files.more }} more
      </button>
    </template>
  </div>
</template>

<style scoped>
button {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 0.5rem;
  width: 100%;
  background: none;
  border: 0;
  border-radius: 4px;
  color: var(--text);
  font-family: inherit;
  font-size: inherit;
  text-align: left;
  cursor: pointer;
  padding: 0.12rem 0.4rem 0.12rem calc(0.4rem + var(--d) * 0.7rem);
}
button:focus-visible {
  outline: 1px solid var(--accent);
}
.head {
  justify-content: flex-start;
  gap: 0.45rem;
  color: var(--text2);
}
.head.suite {
  margin-top: 0.9rem;
  color: var(--text);
  font-weight: 700;
}
.count {
  color: var(--text3);
  font-weight: 400;
}
.leaf:hover {
  background: var(--bg2);
}
.leaf.on {
  background: var(--bg3);
}
.name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.flag {
  color: var(--warn);
  font-size: 0.85em;
  white-space: nowrap;
}
.flag.bad {
  color: var(--danger);
}
.more {
  justify-content: flex-start;
  color: var(--text3);
}
.more:hover {
  color: var(--text);
}
</style>
