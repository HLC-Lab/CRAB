import { defineStore } from "pinia";
import { ref } from "vue";
import { api, ApiError } from "@/api/client";
import type { CrabInfo, Profile, RemoteListItem } from "@/api/types";

function msg(e: unknown): string {
  return e instanceof ApiError ? e.message : "Unexpected error";
}

export const useRemotesStore = defineStore("remotes", () => {
  const items = ref<RemoteListItem[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  // Per-remote transient state keyed by name.
  const busy = ref<Record<string, boolean>>({});
  const connectError = ref<Record<string, string>>({});
  const info = ref<Record<string, CrabInfo>>({});

  async function refresh() {
    loading.value = true;
    error.value = null;
    try {
      items.value = await api.remotes.list();
    } catch (e) {
      error.value = msg(e);
    } finally {
      loading.value = false;
    }
  }

  async function add(profile: Partial<Profile>) {
    error.value = null;
    try {
      await api.remotes.add(profile);
      await refresh();
      return true;
    } catch (e) {
      error.value = msg(e);
      return false;
    }
  }

  async function remove(name: string) {
    try {
      await api.remotes.remove(name);
      await refresh();
    } catch (e) {
      error.value = msg(e);
    }
  }

  async function connect(name: string, password?: string) {
    busy.value[name] = true;
    delete connectError.value[name];
    try {
      const res = await api.remotes.connect(name, password);
      info.value[name] = res.info;
      await refresh();
    } catch (e) {
      connectError.value[name] = msg(e);
    } finally {
      busy.value[name] = false;
    }
  }

  async function disconnect(name: string) {
    busy.value[name] = true;
    try {
      await api.remotes.disconnect(name);
      delete info.value[name];
      await refresh();
    } catch (e) {
      connectError.value[name] = msg(e);
    } finally {
      busy.value[name] = false;
    }
  }

  return {
    items, loading, error, busy, connectError, info,
    refresh, add, remove, connect, disconnect,
  };
});
