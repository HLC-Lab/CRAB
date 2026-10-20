import { defineStore } from "pinia";
import { ref } from "vue";
import { api, ApiError } from "@/api/client";
import type {
  CloneStatus,
  PullResult,
  ReceiptImport,
  WrapperSummary,
  WrappersError,
  WrappersResult,
} from "@/api/types";

// The Wrappers page's data. Two generations live here until the view is rebuilt:
//  - `catalog/load/importBinary`: the older per-cluster catalog (`/api/remotes/{name}/wrappers`).
//  - the per-target state (`lists`, `statuses`, `summaries`, `pull`, `importBinaryOn`), where a
//    target is "local" (this laptop) or a connected cluster's name.

// The cluster's own reason (e.g. "binary ... does not exist") is in the error's detail.
function msg(e: unknown): string {
  if (!(e instanceof ApiError)) return "Unexpected error";
  return e.detail ? `${e.message}\n${e.detail}` : e.message;
}

function toError(e: unknown): WrappersError {
  if (e instanceof ApiError) return { code: e.code, message: e.message, detail: e.detail };
  return { code: "unexpected", message: "Unexpected error" };
}

export const useWrappersStore = defineStore("wrappers", () => {
  const catalog = ref<Record<string, WrappersResult>>({});
  const loading = ref<Record<string, boolean>>({});
  const error = ref<Record<string, string>>({});
  // Keyed "<target>:<relpath>".
  const importing = ref<Record<string, boolean>>({});
  const importError = ref<Record<string, string>>({});

  // Per target. A failed reload keeps the old value next to its error.
  const lists = ref<Record<string, WrappersResult>>({});
  const listLoading = ref<Record<string, boolean>>({});
  const listError = ref<Record<string, WrappersError | undefined>>({});
  const statuses = ref<Record<string, CloneStatus>>({});
  const statusLoading = ref<Record<string, boolean>>({});
  const statusError = ref<Record<string, WrappersError | undefined>>({});
  // Keyed "<target>:<relpath>".
  const summaries = ref<Record<string, WrapperSummary>>({});
  const summaryLoading = ref<Record<string, boolean>>({});
  const summaryError = ref<Record<string, WrappersError | undefined>>({});
  const pulling = ref<Record<string, boolean>>({});
  const lastPull = ref<Record<string, PullResult>>({});
  const pullError = ref<Record<string, WrappersError | undefined>>({});

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

  async function loadList(target: string) {
    listLoading.value[target] = true;
    delete listError.value[target];
    try {
      lists.value[target] = await api.wrappers.list(target);
    } catch (e) {
      listError.value[target] = toError(e);
    } finally {
      listLoading.value[target] = false;
    }
  }

  async function loadStatus(target: string) {
    statusLoading.value[target] = true;
    delete statusError.value[target];
    try {
      statuses.value[target] = await api.wrappers.status(target);
    } catch (e) {
      statusError.value[target] = toError(e);
    } finally {
      statusLoading.value[target] = false;
    }
  }

  async function loadShow(target: string, relpath: string) {
    const key = `${target}:${relpath}`;
    summaryLoading.value[key] = true;
    delete summaryError.value[key];
    try {
      summaries.value[key] = await api.wrappers.show(target, relpath);
    } catch (e) {
      summaryError.value[key] = toError(e);
    } finally {
      summaryLoading.value[key] = false;
    }
  }

  /** Pulls (or clones) the target's crab-wrappers, then reloads its status and list. */
  async function pull(target: string): Promise<boolean> {
    pulling.value[target] = true;
    delete pullError.value[target];
    try {
      lastPull.value[target] = await api.wrappers.pull(target);
    } catch (e) {
      pullError.value[target] = toError(e);
      return false;
    } finally {
      pulling.value[target] = false;
    }
    await Promise.all([loadStatus(target), loadList(target)]);
    return true;
  }

  /** Writes a receipt on the target, then reloads its list (and the wrapper's summary if shown). */
  async function importBinaryOn(
    target: string,
    relpath: string,
    body: ReceiptImport,
  ): Promise<boolean> {
    const key = `${target}:${relpath}`;
    importing.value[key] = true;
    delete importError.value[key];
    try {
      await api.wrappers.importBinary(target, body);
    } catch (e) {
      importError.value[key] = msg(e);
      return false;
    } finally {
      importing.value[key] = false;
    }
    await Promise.all([
      loadList(target),
      key in summaries.value ? loadShow(target, relpath) : Promise.resolve(),
    ]);
    return true;
  }

  return {
    catalog,
    loading,
    error,
    importing,
    importError,
    load,
    importBinary,
    lists,
    listLoading,
    listError,
    statuses,
    statusLoading,
    statusError,
    summaries,
    summaryLoading,
    summaryError,
    pulling,
    lastPull,
    pullError,
    loadList,
    loadStatus,
    loadShow,
    pull,
    importBinaryOn,
  };
});
