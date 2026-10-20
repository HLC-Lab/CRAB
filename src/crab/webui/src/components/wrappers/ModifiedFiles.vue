<script setup lang="ts">
// The modified files of a crab-wrappers clone, collapsed behind a quiet "Show files" toggle.
import { computed, ref } from "vue";
import { modifiedPreview } from "@/lib/wrappers";

const props = defineProps<{ files: string[] }>();

const open = ref(false);
const preview = computed(() => modifiedPreview(props.files));
</script>

<template>
  <div v-if="files.length" class="modified">
    <button class="link" :aria-expanded="open" @click.stop="open = !open">
      {{ open ? "Hide files" : "Show files" }}
    </button>
    <ul v-if="open" class="files">
      <li v-for="f in preview.shown" :key="f">{{ f }}</li>
      <li v-if="preview.more" class="more">and {{ preview.more }} more</li>
    </ul>
  </div>
</template>

<style scoped>
.modified {
  margin-top: 0.15rem;
}
.link {
  background: none;
  border: 0;
  padding: 0;
  color: var(--text2);
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-color: var(--border2);
  cursor: pointer;
  font-family: inherit;
  font-size: 0.92em;
}
.link:hover {
  color: var(--text);
}
.files {
  list-style: none;
  margin: 0.3rem 0 0;
  padding: 0;
  font-family: var(--mono);
  font-size: 0.9em;
  color: var(--text3);
}
.files .more {
  font-family: inherit;
}
</style>
