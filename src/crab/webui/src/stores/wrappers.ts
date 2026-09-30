import { defineStore } from "pinia";
import { ref } from "vue";
import { api, ApiError } from "@/api/client";
import type { ReceiptImport, WrappersResult } from "@/api/types";

// The Wrappers page's per-cluster catalog (`crab wrappers list --json` on that cluster) and the
// "Import binary" action, which writes a receipt there and then reloads the catalog.

// The cluster's own reason (e.g. "binary ... does not exist") is in the error's detail.
function msg(e: unknown): string {
  if (!(e instanceof ApiError)) return "Unexpected error";
  return e.detail ? `${e.message}\n${e.detail}` : e.message;
}

export const useWrappersStore = defineStore("wrappers", () => {
  const catalog = ref<Record<string, WrappersResult>>({});
  const loading = ref<Record<string, boolean>>({});
  const error = ref<Record<string, string>>({});
  // Keyed "<cluster>:<relpath>".
  const importing = ref<Record<string, boolean>>({});
  const importError = ref<Record<string, string>>({});

  async function load(cluster: string) {
    loading.value[cluster] = true;
    delete error.value[cluster];
    try {
      catalog.value[cluster] = await api.remotes.wrappers(cluster);
    } catch (e) {
      error.value[cluster] = msg(e);
    } finally {
      loading.value[cluster] = false;
    }
  }

  async function importBinary(
    cluster: string,
    relpath: string,
    body: ReceiptImport,
  ): Promise<boolean> {
    const key = `${cluster}:${relpath}`;
    importing.value[key] = true;
    delete importError.value[key];
    try {
      await api.remotes.importBinary(cluster, body);
      await load(cluster);
      return true;
    } catch (e) {
      importError.value[key] = msg(e);
      return false;
    } finally {
      importing.value[key] = false;
    }
  }

  return { catalog, loading, error, importing, importError, load, importBinary };
});
