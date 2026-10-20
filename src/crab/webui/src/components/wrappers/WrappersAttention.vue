<script setup lang="ts">
// The wrappers of one target that need attention: cannot load (red), binary missing (yellow,
// with the Import binary form), and the grey, uncounted "lookup failed" line.
import { computed, ref, watch } from "vue";
import type { WrapperEntry, WrapperTarget, WrappersError } from "@/api/types";
import { canImport, missingReason, needsAttention } from "@/lib/wrappers";
import ImportBinaryForm from "./ImportBinaryForm.vue";

const props = defineProps<{
  target: WrapperTarget;
  /** The target's loaded list, if any. */
  wrappers: WrapperEntry[] | undefined;
  loading: boolean;
  error: WrappersError | undefined;
}>();

const groups = computed(() => needsAttention(props.wrappers ?? []));
const total = computed(() => groups.value.broken.length + groups.value.missing.length);

// One Import binary form open at a time; the lookup-failed list is folded away.
const openRelpath = ref("");
const showLookup = ref(false);
watch(
  () => props.target.id,
  () => {
    openRelpath.value = "";
    showLookup.value = false;
  },
);

// The distinct reasons the lookups gave (e.g. "CRAB_SYSTEM is not set"), when they gave any.
const lookupReasons = computed(() => {
  const details = groups.value.lookupFailed
    .map((w) => w.binary.detail)
    .filter((d): d is string => !!d);
  return [...new Set(details)].join(", ");
});
const lookupLead = computed(() => {
  const n = groups.value.lookupFailed.length;
  const reason = lookupReasons.value ? ` (${lookupReasons.value})` : "";
  const where = props.target.local ? "here" : `on ${props.target.label}`;
  const who =
    n === 1
      ? "1 wrapper could not look up its binary"
      : `${n} wrappers could not look up their binary`;
  const tail = props.target.local
    ? "That is normal on a laptop and not counted as a problem."
    : "That is not counted as a problem.";
  return `${who} ${where}${reason}. ${tail}`;
});

function errorText(e: WrappersError): string {
  if (e.code === "crab_outdated") {
    return `Update crab on ${props.target.label} (crab update) to see this.`;
  }
  return e.message;
}

/** The first line of a wrapper's load error: the full text is in the Library. */
function firstLine(text: string | null): string {
  return (text ?? "").split("\n")[0];
}
</script>

<template>
  <section class="attn">
    <p v-if="error && !wrappers" class="error">{{ errorText(error) }}</p>
    <p v-else-if="loading && !wrappers" class="quiet">Loading wrappers from {{ target.label }}…</p>

    <template v-else-if="wrappers">
      <h2 v-if="total">Needs attention on {{ target.label }}</h2>
      <template v-else>
        <h2>Nothing needs attention on {{ target.label }}</h2>
        <p class="plain">Every wrapper loads and finds its binary.</p>
      </template>

      <div v-if="groups.broken.length" class="group">
        <h3 class="bad">Cannot load</h3>
        <div v-for="w in groups.broken" :key="w.relpath" class="row">
          <code class="rel">{{ w.relpath }}</code>
          <span class="why ellip" :title="w.error ?? ''">{{ firstLine(w.error) }}</span>
          <router-link
            class="link"
            :to="{
              path: '/wrappers',
              query: { tab: 'library', target: target.id, wrapper: w.relpath },
            }"
            >Open in Library</router-link
          >
        </div>
      </div>

      <div v-if="groups.missing.length" class="group">
        <h3 class="warn">Binary missing</h3>
        <template v-for="w in groups.missing" :key="w.relpath">
          <div class="row">
            <code class="rel">{{ w.relpath }}</code>
            <span class="why">{{ missingReason(w) }}</span>
            <button
              v-if="canImport(w) && openRelpath !== w.relpath"
              class="btn"
              @click="openRelpath = w.relpath"
            >
              Import binary
            </button>
          </div>
          <ImportBinaryForm
            v-if="openRelpath === w.relpath"
            :target="target.id"
            :target-label="target.label"
            :wrapper="w"
            @done="openRelpath = ''"
            @cancel="openRelpath = ''"
          />
        </template>
      </div>

      <div v-if="groups.lookupFailed.length" class="lookup">
        <p>
          {{ lookupLead }}
          <button class="link" @click="showLookup = !showLookup">
            {{ showLookup ? "Hide them" : "Show them" }}
          </button>
        </p>
        <ul v-if="showLookup" class="lookup-list">
          <li v-for="w in groups.lookupFailed" :key="w.relpath">
            <code>{{ w.relpath }}</code>
            <span v-if="w.binary.detail"> · {{ w.binary.detail }}</span>
          </li>
        </ul>
      </div>
    </template>
  </section>
</template>

<style scoped>
.attn {
  --hair: color-mix(in srgb, var(--border) 60%, var(--bg));
  margin-top: 2.5rem;
}
h2 {
  font-family: var(--sans);
  font-size: var(--t-lg);
  margin-bottom: 0.75rem;
}
.plain {
  color: var(--text2);
}
.quiet {
  color: var(--text3);
}
.error {
  color: var(--danger);
  white-space: pre-line;
}
.group {
  margin-top: 1.25rem;
}
h3 {
  font-size: 1em;
  font-weight: 700;
  margin-bottom: 0.25rem;
}
h3.warn {
  color: var(--warn);
}
h3.bad {
  color: var(--danger);
}
.row {
  display: grid;
  grid-template-columns: minmax(14rem, 22rem) 1fr auto;
  gap: 1.5rem;
  align-items: center;
  padding: 0.55rem 0;
  border-top: 1px solid var(--hair);
}
.rel {
  font-family: var(--mono);
}
.why {
  color: var(--text2);
}
.ellip {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.lookup {
  margin-top: 1.75rem;
  color: var(--text3);
}
.lookup-list {
  list-style: none;
  margin-top: 0.5rem;
}
.btn {
  border: 1px solid var(--border);
  background: var(--bg2);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.3rem 0.8rem;
  cursor: pointer;
  font-family: var(--mono);
  white-space: nowrap;
}
.btn:hover {
  border-color: var(--border2);
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
  font-size: inherit;
}
.link:hover {
  color: var(--text);
}
@media (max-width: 760px) {
  .row {
    grid-template-columns: 1fr;
    gap: 0.25rem;
  }
}
</style>
