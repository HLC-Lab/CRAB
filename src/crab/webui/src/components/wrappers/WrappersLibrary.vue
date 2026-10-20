<script setup lang="ts">
// The Library tab: one target's wrappers as a folder tree on the left, the selected wrapper's
// summary on the right. The selection is the route's `wrapper`, so a link can open one.
import { computed, watch } from "vue";
import type { WrapperTarget, WrappersError } from "@/api/types";
import { firstWrapper, librarySections } from "@/lib/wrapperTree";
import { useWrappersStore } from "@/stores/wrappers";
import WrapperSummary from "./WrapperSummary.vue";
import WrapperTree from "./WrapperTree.vue";

const props = defineProps<{
  target: WrapperTarget;
  /** The route's wrapper (a relpath), "" for none: the first one is shown then. */
  wrapper: string;
}>();
const emit = defineEmits<{ select: [relpath: string] }>();

const store = useWrappersStore();
const list = computed(() => store.lists[props.target.id]);
const error = computed(() => store.listError[props.target.id]);

watch(
  () => props.target.id,
  (id) => {
    if (!store.lists[id] && !store.listLoading[id]) void store.loadList(id);
  },
  { immediate: true },
);

const sections = computed(() => (list.value ? librarySections(list.value) : []));
const current = computed(() => props.wrapper || firstWrapper(sections.value));
const entry = computed(() => list.value?.wrappers.find((w) => w.relpath === current.value));
const local = computed(() => !!entry.value && entry.value.folder === list.value?.search_path[0]);

function errorText(e: WrappersError): string {
  if (e.code === "crab_outdated") {
    return `Update crab on ${props.target.label} (crab update) to see this.`;
  }
  return e.message;
}
</script>

<template>
  <p v-if="error && !list" class="error">{{ errorText(error) }}</p>
  <p v-else-if="!list" class="quiet">Loading wrappers from {{ target.label }}…</p>
  <p v-else-if="!list.wrappers.length" class="quiet">
    No wrappers found on {{ target.label }}. The Status tab can clone or pull them.
  </p>
  <div v-else class="lib">
    <WrapperTree
      :key="target.id"
      :sections="sections"
      :current="current"
      @select="emit('select', $event)"
    />
    <WrapperSummary
      v-if="current"
      :target="target"
      :relpath="current"
      :local="local"
      :entry="entry"
    />
  </div>
</template>

<style scoped>
.lib {
  display: grid;
  grid-template-columns: 17rem minmax(0, 1fr);
  gap: 2.5rem;
}
.quiet {
  color: var(--text3);
}
.error {
  color: var(--danger);
  white-space: pre-line;
}
@media (max-width: 760px) {
  .lib {
    grid-template-columns: 1fr;
  }
}
</style>
