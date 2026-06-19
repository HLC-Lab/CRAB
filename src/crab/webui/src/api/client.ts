// Typed API client. All backend access goes through here so error handling
// and the envelope contract live in one place (instructions §4).

import type {
  BootstrapPlan,
  ConnectResult,
  DetectResult,
  ErrorEnvelope,
  Health,
  Profile,
  RemoteListItem,
  StepResult,
} from "./types";

/** Error thrown for any non-2xx response, carrying the backend envelope. */
export class ApiError extends Error {
  code: string;
  detail?: string;
  status: number;

  constructor(env: ErrorEnvelope, status: number) {
    super(env.message);
    this.name = "ApiError";
    this.code = env.code;
    this.detail = env.detail;
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch (e) {
    // Network-level failure (backend down, etc.) — normalise to ApiError.
    throw new ApiError(
      { code: "network_error", message: "Cannot reach the dashboard backend." },
      0,
    );
  }

  if (!resp.ok) {
    let env: ErrorEnvelope;
    try {
      env = (await resp.json()) as ErrorEnvelope;
    } catch {
      env = { code: "http_error", message: `Request failed (${resp.status}).` };
    }
    throw new ApiError(env, resp.status);
  }

  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const api = {
  health: () => request<Health>("/api/health"),

  remotes: {
    list: () => request<RemoteListItem[]>("/api/remotes"),
    add: (profile: Partial<Profile>) =>
      request<Profile>("/api/remotes", {
        method: "POST",
        body: JSON.stringify(profile),
      }),
    remove: (name: string) =>
      request<void>(`/api/remotes/${encodeURIComponent(name)}`, { method: "DELETE" }),
    connect: (name: string, password?: string) =>
      request<ConnectResult>(`/api/remotes/${encodeURIComponent(name)}/connect`, {
        method: "POST",
        body: JSON.stringify({ password: password ?? null }),
      }),
    disconnect: (name: string) =>
      request<void>(`/api/remotes/${encodeURIComponent(name)}/disconnect`, {
        method: "POST",
      }),

    bootstrap: {
      plan: (name: string) =>
        request<BootstrapPlan>(
          `/api/remotes/${encodeURIComponent(name)}/bootstrap/plan`,
          { method: "POST" },
        ),
      install: (name: string, preCommands: string[]) =>
        request<StepResult>(
          `/api/remotes/${encodeURIComponent(name)}/bootstrap/install`,
          { method: "POST", body: JSON.stringify({ pre_commands: preCommands }) },
        ),
      verify: (name: string) =>
        request<DetectResult>(
          `/api/remotes/${encodeURIComponent(name)}/bootstrap/verify`,
          { method: "POST" },
        ),
    },
  },
};
