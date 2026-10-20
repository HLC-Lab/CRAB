<script setup lang="ts">
// One row per target: its crab-wrappers commit, how it compares with GitHub, its wrapper count,
// and a Pull button when pulling makes sense. A connected row can be selected (the list below the
// table shows that target's problems).
import { computed } from "vue";
import type { PullResult, WrapperTarget, WrappersError } from "@/api/types";
import ModifiedFiles from "@/components/wrappers/ModifiedFiles.vue";
import { attentionCount, drift, driftLines, relativeTime, shortSha } from "@/lib/wrappers";
import { useRemotesStore } from "@/stores/remotes";
import { useWrappersStore } from "@/stores/wrappers";

const props = defineProps<{
  targets: WrapperTarget[];
  selected: string;
  /** When the statuses were last loaded (ms since the epoch), null before the first load. */
  checkedAt: number | null;
  /** The current time in ms; the view ticks it so "5 min ago" stays right. */
  now: number;
}>();
const emit = defineEmits<{ select: [id: string]; check: []; connect: [name: string] }>();

const remotes = useRemotesStore();
const store = useWrappersStore();

const checking = computed(() => props.targets.some((t) => store.statusLoading[t.id]));
const laptopOnly = computed(() => props.targets.length === 1);

function authOf(name: string): string | undefined {
  return remotes.items.find((r) => r.name === name)?.auth;
}

function errorText(t: WrapperTarget, e: WrappersError): string {
  if (e.code === "crab_outdated") return `Update crab on ${t.label} (crab update) to see this.`;
  return e.message;
}

function subline(t: WrapperTarget): string {
  if (!t.connected) return "not connected";
  const dir = store.statuses[t.id]?.dir;
  if (!dir) return "";
  return t.local ? dir : `${dir} over ssh`;
}

function wrapperCount(t: WrapperTarget): number {
  return store.lists[t.id]?.wrappers.length ?? 0;
}

function attention(t: WrapperTarget): number {
  return attentionCount(store.lists[t.id]?.wrappers ?? []);
}

function pullNote(r: PullResult): string {
  if (r.action === "updated") return `Pulled ${shortSha(r.old)} to ${shortSha(r.new)}`;
  if (r.action === "cloned") return `Cloned at ${shortSha(r.new)}`;
  if (r.action === "up to date") return "Already up to date";
  return r.detail ?? "Nothing to pull";
}

function pullErrorText(t: WrapperTarget, e: WrappersError): string {
  if (e.code === "crab_outdated") return errorText(t, e);
  return e.detail ? `${e.message}\n${e.detail}` : e.message;
}

function hasNote(t: WrapperTarget): boolean {
  return !!(
    store.pulling[t.id] ||
    store.lastPull[t.id] ||
    store.pullError[t.id] ||
    (!t.connected && remotes.connectError[t.id])
  );
}

function select(t: WrapperTarget) {
  if (t.connected) emit("select", t.id);
}
</script>

<template>
  <div class="status">
    <table class="targets">
      <thead>
        <tr>
          <th>Target</th>
          <th>crab-wrappers</th>
          <th>GitHub</th>
          <th>Wrappers</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <template v-for="t in targets" :key="t.id">
          <tr
            :class="{ sel: t.connected && selected === t.id, pick: t.connected }"
            :tabindex="t.connected ? 0 : undefined"
            @click="select(t)"
            @keydown.enter="select(t)"
          >
            <td class="nowrap">
              <span class="tname">{{ t.label }}</span>
              <span v-if="t.connected && selected === t.id" class="arrow"> ←</span>
              <span class="sub">{{ subline(t) }}</span>
            </td>

            <template v-if="!t.connected">
              <td colspan="3" class="muted">Connect it to see its wrappers.</td>
              <td class="act">
                <router-link
                  v-if="authOf(t.id) === 'password'"
                  class="btn"
                  to="/remotes"
                  @click.stop
                  >Connect in Remotes</router-link
                >
                <button
                  v-else
                  class="btn"
                  :disabled="remotes.busy[t.id]"
                  @click.stop="emit('connect', t.id)"
                >
                  {{ remotes.busy[t.id] ? "Connecting…" : "Connect" }}
                </button>
              </td>
            </template>

            <template v-else-if="!store.statuses[t.id]">
              <td colspan="3" :class="store.statusError[t.id] ? 'error' : 'muted'">
                {{ store.statusError[t.id] ? errorText(t, store.statusError[t.id]!) : "Loading…" }}
              </td>
              <td></td>
            </template>

            <template v-else>
              <td>
                <code class="sha">{{ shortSha(store.statuses[t.id].commit) }}</code>
                {{ store.statuses[t.id].subject }}
              </td>
              <td class="nowrap">
                <div
                  v-for="line in driftLines(store.statuses[t.id])"
                  :key="line"
                  :class="`tone-${drift(store.statuses[t.id]).tone}`"
                >
                  {{ line }}
                </div>
                <ModifiedFiles :files="store.statuses[t.id].modified" />
              </td>
              <td class="nowrap">
                <template v-if="store.lists[t.id]">
                  {{ wrapperCount(t)
                  }}<span v-if="attention(t)" class="warn"
                    >, {{ attention(t) }}
                    {{ attention(t) === 1 ? "needs" : "need" }} attention</span
                  >
                </template>
                <span v-else-if="store.listError[t.id]" class="error">{{
                  errorText(t, store.listError[t.id]!)
                }}</span>
                <span v-else class="muted">Loading…</span>
              </td>
              <td class="act">
                <button
                  v-if="drift(store.statuses[t.id]).canPull"
                  class="btn warn-btn"
                  :disabled="store.pulling[t.id]"
                  @click.stop="store.pull(t.id)"
                >
                  {{ store.pulling[t.id] ? "Pulling…" : drift(store.statuses[t.id]).pullLabel }}
                </button>
                <template
                  v-else-if="
                    store.statuses[t.id].modified.length && store.statuses[t.id].state === 'behind'
                  "
                >
                  <button class="btn" disabled>Pull</button>
                  <span class="pullnote">commit, stash or move the edits first</span>
                </template>
              </td>
            </template>
          </tr>

          <tr v-if="hasNote(t)" class="extra">
            <td colspan="5">
              <span v-if="store.pulling[t.id]" class="muted">Pulling on {{ t.label }}…</span>
              <span v-else-if="store.pullError[t.id]" class="error">{{
                pullErrorText(t, store.pullError[t.id]!)
              }}</span>
              <span v-else-if="store.lastPull[t.id]" class="muted">{{
                pullNote(store.lastPull[t.id])
              }}</span>
              <span v-else-if="remotes.connectError[t.id]" class="error">{{
                remotes.connectError[t.id]
              }}</span>
            </td>
          </tr>
        </template>
      </tbody>
    </table>

    <p class="foot">
      <template v-if="checkedAt === null">Checking GitHub…</template>
      <template v-else>
        GitHub checked {{ relativeTime(now - checkedAt) }} ·
        <button class="link" :disabled="checking" @click="emit('check')">
          {{ checking ? "Checking…" : "Check again" }}
        </button>
      </template>
      <template v-if="laptopOnly">
        · <router-link class="link" to="/remotes">Connect a cluster</router-link> to see its
        wrappers here.
      </template>
    </p>
  </div>
</template>

<style scoped>
.status {
  --hair: color-mix(in srgb, var(--border) 60%, var(--bg));
}
table {
  border-collapse: collapse;
  width: 100%;
}
th,
td {
  text-align: left;
  vertical-align: top;
  padding: 0.7rem 1rem 0.7rem 0;
}
th {
  font-weight: normal;
  color: var(--text3);
}
th:last-child,
td.act {
  text-align: right;
  padding-right: 0;
}
tbody tr {
  border-top: 1px solid var(--hair);
}
tbody tr:last-child {
  border-bottom: 1px solid var(--hair);
}
tbody tr.extra {
  border-top: 0;
}
tbody tr.extra td {
  padding-top: 0;
  padding-bottom: 0.7rem;
}
tbody tr.pick {
  cursor: pointer;
}
tbody tr.sel {
  background: linear-gradient(90deg, var(--sel-bg), transparent 70%);
}
tr.sel + tr.extra {
  background: linear-gradient(90deg, var(--sel-bg), transparent 70%);
}
.tname {
  font-weight: 700;
}
.arrow {
  color: var(--text3);
}
.sub {
  display: block;
  color: var(--text3);
  font-size: 0.92em;
  white-space: nowrap;
}
.sha {
  font-family: var(--mono);
  color: var(--text);
  margin-right: 0.35rem;
}
td {
  color: var(--text2);
}
td.nowrap {
  white-space: nowrap;
}
.muted {
  color: var(--text2);
}
.tone-ok {
  color: var(--text2);
}
.tone-warn,
.warn {
  color: var(--warn);
}
.tone-muted,
.pullnote {
  color: var(--text3);
}
.error {
  color: var(--danger);
  white-space: pre-line;
}
.pullnote {
  display: block;
  font-size: 0.9em;
  margin-top: 0.25rem;
  white-space: nowrap;
}
.btn {
  display: inline-block;
  border: 1px solid var(--border);
  background: var(--bg2);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.3rem 0.8rem;
  cursor: pointer;
  font-family: var(--mono);
  font-size: inherit;
  white-space: nowrap;
}
.btn:hover:not(:disabled) {
  border-color: var(--border2);
  text-decoration: none;
}
.btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.btn.warn-btn {
  background: var(--warn);
  border-color: var(--warn);
  color: #1a1a1a;
}
.foot {
  margin-top: 0.5rem;
  color: var(--text3);
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
.link:hover:not(:disabled) {
  color: var(--text);
}
.link:disabled {
  cursor: default;
}
</style>
