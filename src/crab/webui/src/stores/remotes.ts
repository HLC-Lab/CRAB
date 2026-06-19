import { defineStore } from "pinia";
import { ref } from "vue";
import { api, ApiError } from "@/api/client";
import type {
  BootstrapPlan,
  CrabInfo,
  Profile,
  RemoteListItem,
  StepResult,
} from "@/api/types";

function msg(e: unknown): string {
  return e instanceof ApiError ? e.message : "Unexpected error";
}

function code(e: unknown): string {
  return e instanceof ApiError ? e.code : "unexpected";
}

export const useRemotesStore = defineStore("remotes", () => {
  const items = ref<RemoteListItem[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  // Per-remote transient state keyed by name.
  const busy = ref<Record<string, boolean>>({});
  const connectError = ref<Record<string, string>>({});
  // Error code alongside the message, so the UI can react (e.g. offer bootstrap
  // when a connect handshake reports the cluster CRAB is missing/incompatible).
  const connectCode = ref<Record<string, string>>({});
  const info = ref<Record<string, CrabInfo>>({});

  // Bootstrap (guided install) state, keyed by remote name.
  const plan = ref<Record<string, BootstrapPlan>>({});
  const stepResults = ref<Record<string, Record<string, StepResult>>>({});
  const bootstrapBusy = ref<Record<string, boolean>>({});
  const bootstrapError = ref<Record<string, string>>({});

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
    delete connectCode.value[name];
    try {
      const res = await api.remotes.connect(name, password);
      info.value[name] = res.info;
      await refresh();
    } catch (e) {
      connectError.value[name] = msg(e);
      connectCode.value[name] = code(e);
      // A contract error means the SSH side is up but CRAB is unusable — the
      // connection is live, so refresh to reflect the connected state.
      if (code(e) === "contract_error") await refresh();
    } finally {
      busy.value[name] = false;
    }
  }

  async function disconnect(name: string) {
    busy.value[name] = true;
    try {
      await api.remotes.disconnect(name);
      delete info.value[name];
      delete plan.value[name];
      delete stepResults.value[name];
      await refresh();
    } catch (e) {
      connectError.value[name] = msg(e);
    } finally {
      busy.value[name] = false;
    }
  }

  // -- Guided bootstrap ------------------------------------------------------
  async function loadPlan(name: string) {
    bootstrapBusy.value[name] = true;
    delete bootstrapError.value[name];
    try {
      plan.value[name] = await api.remotes.bootstrap.plan(name);
      stepResults.value[name] = {};
    } catch (e) {
      bootstrapError.value[name] = msg(e);
    } finally {
      bootstrapBusy.value[name] = false;
    }
  }

  async function runStep(name: string, stepId: string, preCommands: string[]) {
    bootstrapBusy.value[name] = true;
    delete bootstrapError.value[name];
    try {
      const res = await api.remotes.bootstrap.run(name, stepId, preCommands);
      stepResults.value[name] = { ...stepResults.value[name], [stepId]: res };
    } catch (e) {
      bootstrapError.value[name] = msg(e);
    } finally {
      bootstrapBusy.value[name] = false;
    }
  }

  async function verify(name: string) {
    bootstrapBusy.value[name] = true;
    delete bootstrapError.value[name];
    try {
      const res = await api.remotes.bootstrap.verify(name);
      if (res.installed && res.info) {
        info.value[name] = res.info;
        delete connectError.value[name];
        delete connectCode.value[name];
        delete plan.value[name];
      } else if (plan.value[name]) {
        plan.value[name].installed = false;
        bootstrapError.value[name] =
          res.reason ?? "CRAB is still not detected on the cluster.";
      }
      await refresh();
    } catch (e) {
      bootstrapError.value[name] = msg(e);
    } finally {
      bootstrapBusy.value[name] = false;
    }
  }

  return {
    items, loading, error, busy, connectError, connectCode, info,
    plan, stepResults, bootstrapBusy, bootstrapError,
    refresh, add, remove, connect, disconnect,
    loadPlan, runStep, verify,
  };
});
