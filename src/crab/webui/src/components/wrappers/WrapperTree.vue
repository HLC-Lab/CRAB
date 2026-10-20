<script setup lang="ts">
// The Library's left column: a filter box and the target's wrappers as folders (the untracked
// local ones first). Which folders are open is kept here and starts from the selected wrapper.
import { computed, reactive, ref } from "vue";
import { filterSections, type LibrarySection } from "@/lib/wrapperTree";
import WrapperTreeFolder from "./WrapperTreeFolder.vue";

const props = defineProps<{ sections: LibrarySection[]; current: string }>();
const emit = defineEmits<{ select: [relpath: string] }>();

const query = ref("");
const open = reactive<Record<string, boolean>>({});
const more = reactive<Record<string, boolean>>({});

const filtering = computed(() => query.value.trim() !== "");
const shown = computed(() => filterSections(props.sections, query.value));
</script>

<template>
  <aside class="tree">
    <input
      v-model="query"
      type="search"
      placeholder="Filter wrappers"
      aria-label="Filter wrappers"
    />
    <p v-if="filtering && !shown.length" class="none">No wrapper matches "{{ query.trim() }}".</p>
    <section v-for="s in shown" :key="s.local ? 'local' : 'shipped'">
      <div v-if="s.label" class="suite">{{ s.label }}</div>
      <WrapperTreeFolder
        :folder="s.tree"
        bare
        :depth="s.label ? 1 : 0"
        :prefix="s.local ? 'L' : 'S'"
        :current="current"
        :filtering="filtering"
        :open="open"
        :more="more"
        @select="emit('select', $event)"
        @toggle="(key, value) => (open[key] = value)"
        @more="more[$event] = true"
      />
    </section>
  </aside>
</template>

<style scoped>
input {
  display: block;
  width: 100%;
  margin-bottom: 0.75rem;
  background: var(--bg1);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.4rem 0.6rem;
  font-family: inherit;
  font-size: inherit;
}
input:focus {
  outline: none;
  border-color: var(--accent);
}
.suite {
  margin-top: 0.9rem;
  padding-left: 0.4rem;
  font-weight: 700;
}
.none {
  color: var(--text3);
}
</style>
