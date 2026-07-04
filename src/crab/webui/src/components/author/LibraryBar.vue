<script setup lang="ts">
import { computed, ref } from "vue";
import { useAuthorStore } from "@/stores/author";
import ConfirmModal from "@/components/ConfirmModal.vue";

const props = defineProps<{
  showJson: boolean;
}>();
const emit = defineEmits<{
  "update:showJson": [boolean];
  // Fired after the draft actually changed underneath the parent (a fresh
  // blank draft, or a loaded library entry), so it can reset pane selection.
  new: [];
  opened: [];
}>();

const store = useAuthorStore();
const d = store.draft;

function toggleJson(): void {
  emit("update:showJson", !props.showJson);
}

// Browse overlay (searchable library picker)
const showOpen = ref(false);
const openQuery = ref("");
const filteredLibrary = computed(() => {
  const q = openQuery.value.trim().toLowerCase();
  const lib = store.library;
  return q ? lib.filter((e) => e.name.toLowerCase().includes(q)) : lib;
});
async function openEntry(id: string) {
  await store.open(id);
  emit("opened");
  showOpen.value = false;
  openQuery.value = "";
}

function duplicateLibraryEntry(id: string): void {
  store.duplicate(id);
  showOpen.value = false;
  openQuery.value = "";
}

const removeLibraryTarget = ref<{ id: string; name: string } | null>(null);
function requestRemoveLibraryEntry(id: string, name: string): void {
  removeLibraryTarget.value = { id, name };
}
async function confirmRemoveLibraryEntry(): Promise<void> {
  if (removeLibraryTarget.value) await store.remove(removeLibraryTarget.value.id);
  removeLibraryTarget.value = null;
}

// Discard-unsaved-changes guard for "+ New" and "Browse…", gated on real
// dirtiness (store.isDirty) rather than the old "draft is non-empty" heuristic.
const showDiscardConfirm = ref(false);
const pendingAction = ref<"new" | "open" | null>(null);

function requestNew(): void {
  if (store.isDirty) {
    pendingAction.value = "new";
    showDiscardConfirm.value = true;
  } else {
    store.newConfig();
    emit("new");
  }
}
function requestBrowse(): void {
  if (store.isDirty) {
    pendingAction.value = "open";
    showDiscardConfirm.value = true;
  } else {
    showOpen.value = true;
  }
}
function confirmDiscard(): void {
  showDiscardConfirm.value = false;
  if (pendingAction.value === "new") {
    store.newConfig();
    emit("new");
  } else if (pendingAction.value === "open") {
    showOpen.value = true;
  }
  pendingAction.value = null;
}

const showNamePrompt = ref(false);
const namePromptValue = ref("");

async function requestSave() {
  if (!d.name.trim()) {
    namePromptValue.value = "";
    showNamePrompt.value = true;
    return;
  }
  await store.save();
}
async function confirmNamePrompt() {
  const name = namePromptValue.value.trim();
  if (!name) return;
  d.name = name;
  showNamePrompt.value = false;
  await store.save();
}
</script>

<template>
  <header class="bar">
    <div class="grp">
      <button class="btn" @click="requestNew">+ New</button>
      <button class="btn" @click="requestBrowse">Browse…</button>

      <button class="btn primary" :disabled="store.busy" @click="requestSave">
        {{ store.busy ? "Saving…" : "Save" }}
      </button>
    </div>
    <div class="grp">
      <button class="btn" :class="{ on: showJson }" @click="toggleJson">{ } JSON</button>
    </div>
  </header>

  <!-- Discard-unsaved-changes confirm, for New/Open when the draft is dirty -->
  <ConfirmModal
    v-if="showDiscardConfirm"
    title="Discard unsaved changes?"
    :message="`${pendingAction === 'new' ? 'Starting a new use case' : 'Opening another use case'} will discard the changes you haven't saved.`"
    confirm-label="Discard"
    @confirm="confirmDiscard"
    @cancel="showDiscardConfirm = false"
  />

  <!-- Browse overlay: searchable library picker with hover duplicate/delete -->
  <div v-if="showOpen" class="modal-bg" @click.self="showOpen = false">
    <div class="modal card open-modal">
      <input v-model="openQuery" class="search" placeholder="Search saved use cases…" autofocus />
      <ul class="open-list">
        <!-- Each row is a distinct, self-contained block so a future "view
             results for this use case" action (once the results dashboard
             phase lands) can be added as one more item in .open-actions
             without restructuring this list. Not implemented yet. -->
        <li
          v-for="e in filteredLibrary"
          :key="e.id"
          :class="{ current: e.id === store.entryId }"
          @click="openEntry(e.id)"
        >
          <div class="open-main">
            <span class="open-name">{{ e.name }}</span>
            <span class="open-actions">
              <button
                type="button"
                class="icon-btn"
                title="Duplicate use case"
                @click.stop="duplicateLibraryEntry(e.id)"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <rect x="9" y="9" width="11" height="11" rx="2" />
                  <path d="M5 15V5a2 2 0 0 1 2-2h10" />
                </svg>
              </button>
              <button
                type="button"
                class="icon-btn danger"
                title="Delete use case"
                @click.stop="requestRemoveLibraryEntry(e.id, e.name)"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M5 7h14" />
                  <path d="M9 7V5h6v2" />
                  <path d="M7 7l1 13h8l1-13" />
                </svg>
              </button>
            </span>
          </div>
          <div class="open-sub">
            <span class="open-count"
              >{{ Object.keys(e.config.experiments || {}).length }} experiment{{
                Object.keys(e.config.experiments || {}).length === 1 ? "" : "s"
              }}</span
            >
            <span class="open-date">Updated {{ e.updated_at.slice(0, 10) }}</span>
          </div>
        </li>
        <li v-if="!filteredLibrary.length" class="empty">
          {{ store.library.length ? "No matching use cases." : "No saved use cases yet." }}
        </li>
      </ul>
    </div>
  </div>

  <ConfirmModal
    v-if="removeLibraryTarget"
    title="Delete this use case?"
    :message="`Delete “${removeLibraryTarget.name}”? This cannot be undone.`"
    confirm-label="Delete"
    @confirm="confirmRemoveLibraryEntry"
    @cancel="removeLibraryTarget = null"
  />

  <!-- Name-required prompt: shown when Save is clicked with no name set -->
  <div v-if="showNamePrompt" class="modal-bg" @click.self="showNamePrompt = false">
    <div class="modal card">
      <h2>Name this use case</h2>
      <p class="hint">Choose a name before saving.</p>
      <input
        v-model="namePromptValue"
        class="search"
        placeholder="Use case name"
        autofocus
        @keyup.enter="confirmNamePrompt"
      />
      <div class="modal-actions">
        <button class="btn" @click="showNamePrompt = false">Cancel</button>
        <button class="btn primary" :disabled="!namePromptValue.trim()" @click="confirmNamePrompt">
          Save
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bar {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  justify-content: space-between;
  margin-bottom: 1rem;
}
.grp {
  display: flex;
  gap: 0.4rem;
  align-items: center;
  flex-wrap: wrap;
}
.btn {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.8rem;
  cursor: pointer;
  font-family: var(--sans);
}
.btn:hover:not(:disabled) {
  border-color: var(--accent);
}
.btn:disabled {
  opacity: 0.4;
  cursor: default;
}
.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.btn.on {
  border-color: var(--accent);
  color: var(--accent);
}
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

input,
textarea,
select {
  background: var(--bg2);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--r);
  padding: 0.35rem 0.5rem;
  font-family: var(--mono);
  font-size: var(--t-md);
}
input:focus,
textarea:focus,
select:focus {
  outline: none;
  border-color: var(--accent);
}

.empty {
  color: var(--text3);
  font-size: var(--t-md);
}

.modal-bg {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}
.modal {
  width: min(40rem, 92vw);
  padding: 1.25rem;
}
.modal h2 {
  font-family: var(--sans);
  font-size: var(--t-lg);
  margin-bottom: 0.3rem;
}
.modal .hint {
  color: var(--text2);
  font-size: var(--t-md);
  margin-bottom: 0.75rem;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 0.75rem;
}
.card {
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: var(--r2);
}

/* Open overlay */
.open-modal {
  width: min(34rem, 92vw);
  padding: 0.75rem;
}
.search {
  width: 100%;
  margin-bottom: 0.6rem;
  font-size: var(--t-md);
  padding: 0.5rem 0.6rem;
}
.open-list {
  list-style: none;
  max-height: 24rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}
.open-list li {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding: 0.55rem 0.65rem;
  border: 1px solid transparent;
  border-radius: var(--r);
  cursor: pointer;
}
.open-list li:hover {
  background: var(--bg2);
}
.open-list li.current {
  border-color: var(--accent);
}
/* Name + hover actions share the top line; actions are absolutely positioned
   in a gutter sized for exactly the 2 icon buttons they hold (not shared with
   any text, unlike the previous single-line layout), so they never reserve
   dead space and never fight the name for width. */
.open-main {
  position: relative;
  display: flex;
  align-items: center;
  padding-right: 3.6rem;
}
.open-name {
  color: var(--text);
  font-weight: 600;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.open-actions {
  position: absolute;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  display: inline-flex;
  align-items: center;
  gap: 0.15rem;
  opacity: 0;
  transition: opacity 0.1s ease;
}
.open-list li:hover .open-actions {
  opacity: 1;
}
/* Count/date sit on their own quieter line below, entirely clear of the name
   and the hover actions, so nothing can ever overlap. */
.open-sub {
  display: flex;
  align-items: center;
  gap: 0.6rem;
}
.open-count {
  color: var(--text2);
  font-size: var(--t-sm);
  font-weight: 500;
}
.open-date {
  color: var(--text3);
  font-size: var(--t-xs);
}
</style>
