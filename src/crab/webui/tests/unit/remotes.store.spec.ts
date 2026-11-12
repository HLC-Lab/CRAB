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
    detail?: string;
    constructor(env: { code: string; message: string; detail?: string }) {
      super(env.message);
      this.detail = env.detail;
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

describe("remotes store connect detail and stale info", () => {
  const STDERR = "PermissionDenied: Permission denied";

  function failsWithDetail(detail?: string) {
    vi.mocked(api.remotes.connect).mockRejectedValue(
      new ApiError(
        { code: "auth_error", message: "The cluster rejected your login.", detail },
        401,
      ),
    );
  }

  it("keeps the technical detail of a failed connect for that remote", async () => {
    failsWithDetail(STDERR);
    const store = useRemotesStore();
    await store.connect("leo");
    expect(store.connectError.leo).toBe("The cluster rejected your login.");
    expect(store.connectDetail.leo).toBe(STDERR);
  });

  it("has no detail when the error carries none", async () => {
    failsWithDetail(undefined);
    const store = useRemotesStore();
    await store.connect("leo");
    expect(store.connectDetail.leo).toBeUndefined();
  });

  it("clears the detail when the next connect starts and after it succeeds", async () => {
    failsWithDetail(STDERR);
    const store = useRemotesStore();
    await store.connect("leo");
    let seen: string | undefined = "unset";
    vi.mocked(api.remotes.connect).mockImplementation(async () => {
      seen = store.connectDetail.leo;
      return { connected: true, info: INFO, crab_installed: true, reason: null, skew: null };
    });
    await store.connect("leo");
    expect(seen).toBeUndefined();
    expect(store.connectDetail.leo).toBeUndefined();
  });

  it("has no detail for a non-ApiError failure", async () => {
    vi.mocked(api.remotes.connect).mockRejectedValue(new TypeError("fetch failed"));
    const store = useRemotesStore();
    await store.connect("leo");
    expect(store.connectDetail.leo).toBeUndefined();
    expect(store.connectError.leo).not.toBe("Unexpected error");
  });

  it("drops the old CRAB info line when a reconnect fails", async () => {
    connectReturns(null);
    const store = useRemotesStore();
    await store.connect("leo");
    expect(store.info.leo).toEqual(INFO);
    failsWithDetail(STDERR);
    await store.connect("leo");
    expect(store.info.leo).toBeUndefined();
  });
});
