<script setup lang="ts">
// Wrappers page: Status (every target's crab-wrappers clone and what needs attention) and
// Library. The tab lives in the route query (?tab=library) so links can open it.
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import WrappersAttention from "@/components/wrappers/WrappersAttention.vue";
import WrappersCatalogList from "@/components/wrappers/WrappersCatalogList.vue";
import WrappersStatus from "@/components/wrappers/WrappersStatus.vue";
import { attentionCount, LOCAL_TARGET, wrapperTargets } from "@/lib/wrappers";
import { useRemotesStore } from "@/stores/remotes";
import { useWrappersStore } from "@/stores/wrappers";

const route = useRoute();
const router = useRouter();
const remotes = useRemotesStore();
const store = useWrappersStore();

const tab = computed(() => (route.query.tab === "library" ? "library" : "status"));
function openTab(name: "status" | "library") {
  const query = { ...route.query };
  if (name === "status") delete query.tab;
  else query.tab = name;
  router.replace({ query });
}

const ready = ref(false);
const targets = computed(() => wrapperTargets(remotes.items));
const connectedTargets = computed(() => targets.value.filter((t) => t.connected));

const selected = ref(LOCAL_TARGET);
// Once the owner picks a row, the default (first target with problems) stops moving it.
let picked = false;
function pick(id: string) {
  picked = true;
  selected.value = id;
}
const selectedTarget = computed(
  () => targets.value.find((t) => t.id === selected.value) ?? targets.value[0],
);

function attentionOn(id: string): number {
  return attentionCount(store.lists[id]?.wrappers ?? []);
}
const totalAttention = computed(() =>
  connectedTargets.value.reduce((sum, t) => sum + attentionOn(t.id), 0),
);

const checkedAt = ref<number | null>(null);
const now = ref(Date.now());
let ticker: ReturnType<typeof setInterval> | undefined;

function loadTarget(id: string): Promise<unknown> {
  return Promise.all([store.loadStatus(id), store.loadList(id)]);
}

async function checkAll() {
  await Promise.all(connectedTargets.value.map((t) => store.loadStatus(t.id)));
  checkedAt.value = Date.now();
  now.value = Date.now();
}

async function connect(name: string) {
  await remotes.connect(name);
  if (remotes.items.find((r) => r.name === name)?.connected) await loadTarget(name);
}

onMounted(async () => {
  ticker = setInterval(() => (now.value = Date.now()), 30_000);
  await remotes.refresh();
  ready.value = true;
  await Promise.all(connectedTargets.value.map((t) => loadTarget(t.id)));
  checkedAt.value = Date.now();
  now.value = Date.now();
  if (!picked) {
    selected.value = connectedTargets.value.find((t) => attentionOn(t.id) > 0)?.id ?? LOCAL_TARGET;
  }
});
onUnmounted(() => clearInterval(ticker));
</script>

<template>
  <section class="wrappers">
    <header class="head">
      <h1>Wrappers</h1>
      <div class="tabs" role="tablist">
        <button
          role="tab"
          :aria-selected="tab === 'status'"
          :class="{ on: tab === 'status' }"
          @click="openTab('status')"
        >
          Status<span v-if="totalAttention" class="count">{{ totalAttention }}</span>
        </button>
        <button
          role="tab"
          :aria-selected="tab === 'library'"
          :class="{ on: tab === 'library' }"
          @click="openTab('library')"
        >
          Library
        </button>
      </div>
    </header>

    <template v-if="ready">
      <template v-if="tab === 'status'">
        <WrappersStatus
          :targets="targets"
          :selected="selected"
          :checked-at="checkedAt"
          :now="now"
          @select="pick"
          @check="checkAll"
          @connect="connect"
        />
        <WrappersAttention
          v-if="selectedTarget"
          :target="selectedTarget"
          :wrappers="store.lists[selectedTarget.id]?.wrappers"
          :loading="!!store.listLoading[selectedTarget.id]"
          :error="store.listError[selectedTarget.id]"
        />
      </template>
      <WrappersCatalogList v-else />
    </template>
  </section>
</template>

<style scoped>
.wrappers {
  padding: 1.75rem 1.5rem 4rem;
  max-width: 78rem;
  margin: 0 auto;
}
.head {
  display: flex;
  align-items: baseline;
  gap: 2rem;
  margin-bottom: 1.5rem;
}
h1 {
  font-family: var(--sans);
  font-size: var(--t-xl);
}
.tabs {
  display: flex;
  gap: 1.25rem;
}
.tabs button {
  background: none;
  border: 0;
  border-bottom: 2px solid transparent;
  color: var(--text2);
  padding: 0.2rem 0;
  font-family: inherit;
  font-size: inherit;
  cursor: pointer;
}
.tabs button:hover {
  color: var(--text);
}
.tabs button.on {
  color: var(--text);
  border-bottom-color: var(--accent);
}
.count {
  color: var(--warn);
  margin-left: 0.35rem;
}
</style>
