/**
 * Contract skew: the connect and verify responses carry a fix-it message when
 * the cluster's CRAB speaks a different `--json` contract; the store keeps it per remote.
 * Mocks the API client (the I/O boundary), not store internals.
 */
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  api: {
    remotes: {
      list: vi.fn(),
      connect: vi.fn(),
      disconnect: vi.fn(),
      bootstrap: { plan: vi.fn(), install: vi.fn(), verify: vi.fn() },
    },
  },
  ApiError: class ApiError extends Error {
    constructor(env: { code: string; message: string }) {
      super(env.message);
    }
  },
}));

import { api, ApiError } from "@/api/client";
import { useRemotesStore } from "@/stores/remotes";

const INFO = { schema: 1, crab_version: "0.1.0", crab_root: "/h/CRAB", presets: [] };
const OLDER =
  "This cluster's CRAB is older than the dashboard. Run crab update on the cluster, then reconnect.";

beforeEach(() => {
  setActivePinia(createPinia());
  vi.mocked(api.remotes.list).mockResolvedValue([]);
  vi.mocked(api.remotes.disconnect).mockResolvedValue(undefined as never);
});

function connectReturns(skew: string | null) {
  vi.mocked(api.remotes.connect).mockResolvedValue({
    connected: true,
    info: INFO,
    crab_installed: true,
    reason: null,
    skew,
  });
}

describe("remotes store contract skew", () => {
  it("keeps the skew message from connect", async () => {
    connectReturns(OLDER);
    const store = useRemotesStore();
    await store.connect("leo");
    expect(store.skew.leo).toBe(OLDER);
  });

  it("clears it when a later connect matches", async () => {
    const store = useRemotesStore();
    connectReturns(OLDER);
    await store.connect("leo");
    connectReturns(null);
    await store.connect("leo");
    expect(store.skew.leo).toBeUndefined();
  });

  it("clears it on disconnect", async () => {
    connectReturns(OLDER);
    const store = useRemotesStore();
    await store.connect("leo");
    await store.disconnect("leo");
    expect(store.skew.leo).toBeUndefined();
  });

  it("takes the skew message from verify after an install", async () => {
    vi.mocked(api.remotes.bootstrap.install).mockResolvedValue({
      rc: 0,
      ok: true,
      stdout: "",
      stderr: "",
    });
    vi.mocked(api.remotes.bootstrap.verify).mockResolvedValue({
      installed: true,
      info: INFO,
      reason: null,
      skew: OLDER,
    });
    const store = useRemotesStore();
    await store.install("leo", []);
    expect(store.skew.leo).toBe(OLDER);
  });
});

describe("remotes store connect error", () => {
  const TIMEOUT = "The cluster did not answer in time.";

  function connectFails(message: string) {
    vi.mocked(api.remotes.connect).mockRejectedValue(
      new ApiError({ code: "connection_error", message }, 502),
    );
  }

  it("sets the error after a failed connect", async () => {
    connectFails(TIMEOUT);
    const store = useRemotesStore();
    await store.connect("leo");
    expect(store.connectError.leo).toBe(TIMEOUT);
    expect(store.busy.leo).toBe(false);
  });

  it("clears the error after a later successful connect", async () => {
    connectFails(TIMEOUT);
    const store = useRemotesStore();
    await store.connect("leo");
    connectReturns(null);
    await store.connect("leo");
    expect(store.connectError.leo).toBeUndefined();
  });

  it("clears the error when a new connect starts", async () => {
    connectFails(TIMEOUT);
    const store = useRemotesStore();
    await store.connect("leo");
    let seen: string | undefined = "unset";
    vi.mocked(api.remotes.connect).mockImplementation(async () => {
      seen = store.connectError.leo;
      return { connected: true, info: INFO, crab_installed: true, reason: null, skew: null };
    });
    await store.connect("leo");
    expect(seen).toBeUndefined();
  });

  it("keeps the error with the profile listed as not connected", async () => {
    vi.mocked(api.remotes.list).mockResolvedValue([{ name: "leo", connected: false }] as never);
    connectFails(TIMEOUT);
    const store = useRemotesStore();
    await store.connect("leo");
    expect(store.items.find((r) => r.name === "leo")?.connected).toBe(false);
    expect(store.connectError.leo).toBe(TIMEOUT);
  });
});
