import { defineStore } from "pinia";
import { computed, reactive, ref } from "vue";
import { api, ApiError } from "@/api/client";
import type { LibraryEntry } from "@/api/types";
import { type Draft, emptyDraft, fromConfig, toConfig } from "@/lib/config";

function msg(e: unknown): string {
  return e instanceof ApiError ? e.message : "Unexpected error";
}

export const useAuthorStore = defineStore("author", () => {
  const library = ref<LibraryEntry[]>([]);
  // The config currently being edited. `entryId` is null until it is saved.
  const entryId = ref<string | null>(null);
  const draft = reactive<Draft>(emptyDraft());
  const error = ref<string | null>(null);
  const busy = ref(false);

  // Live engine config + its JSON rendering (for the JSON view / copy).
  const config = computed(() => toConfig(draft));
  const configJson = computed(() => JSON.stringify(config.value, null, 2));

  function _load(d: Draft) {
    Object.assign(draft, emptyDraft(), d);
  }

  async function loadLibrary() {
    try {
      library.value = await api.experiments.list();
    } catch (e) {
      error.value = msg(e);
    }
  }

  function newConfig() {
    entryId.value = null;
    _load(emptyDraft());
    error.value = null;
  }

  async function open(id: string) {
    error.value = null;
    busy.value = true;
    try {
      const entry = await api.experiments.get(id);
      entryId.value = entry.id;
      _load(fromConfig(entry.config));
    } catch (e) {
      error.value = msg(e);
    } finally {
      busy.value = false;
    }
  }

  async function save() {
    error.value = null;
    busy.value = true;
    try {
      const name = draft.name.trim() || "Untitled";
      const entry = entryId.value
        ? await api.experiments.update(entryId.value, name, config.value)
        : await api.experiments.create(name, config.value);
      entryId.value = entry.id;
      await loadLibrary();
      return true;
    } catch (e) {
      error.value = msg(e);
      return false;
    } finally {
      busy.value = false;
    }
  }

  async function duplicate(id: string) {
    try {
      const entry = await api.experiments.duplicate(id);
      await loadLibrary();
      await open(entry.id);
    } catch (e) {
      error.value = msg(e);
    }
  }

  async function remove(id: string) {
    try {
      await api.experiments.remove(id);
      if (entryId.value === id) newConfig();
      await loadLibrary();
    } catch (e) {
      error.value = msg(e);
    }
  }

  /** Parse pasted JSON into the editor as a new (unsaved) config. */
  function importJson(text: string): boolean {
    error.value = null;
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      error.value = "That isn't valid JSON.";
      return false;
    }
    if (!parsed || typeof parsed !== "object") {
      error.value = "Expected a JSON object with global_options and experiments.";
      return false;
    }
    entryId.value = null;
    _load(fromConfig(parsed as never));
    return true;
  }

  return {
    library, entryId, draft, error, busy, config, configJson,
    loadLibrary, newConfig, open, save, duplicate, remove, importJson,
  };
});
