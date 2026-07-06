// Typed API client. All backend access goes through here so error handling
// and the envelope contract live in one place (instructions §4).

import type {
  BenchmarksResult,
  BootstrapPlan,
  CancelResponse,
  ConnectResult,
  CrabConfig,
  DetectResult,
  ErrorEnvelope,
  ExperimentLogs,
  Health,
  JobDetail,
  JobListItem,
  JobLogs,
  LibraryEntry,
  NodesResult,
  Profile,
  RemoteListItem,
  SavedEntry,
  StepResult,
  SubmissionAccepted,
  SubmissionStatus,
  UseCaseReport,
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

// Per-session API token: the backend injects it into the served index.html as
// a meta tag; every /api request must echo it (X-Crab-Token) or gets a 401.
// Dev fallback: Vite's dev server serves its own index (no meta), so a token
// logged by `crab web -v` can be stored once as localStorage.CRAB_DEV_TOKEN.
function sessionToken(): string {
  const meta = document.querySelector('meta[name="crab-token"]');
  return meta?.getAttribute("content") || localStorage.getItem("CRAB_DEV_TOKEN") || "";
}
const API_TOKEN = sessionToken();

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(path, {
      headers: { "Content-Type": "application/json", "X-Crab-Token": API_TOKEN },
      ...init,
    });
  } catch {
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

  local: {
    benchmarks: () => request<BenchmarksResult>("/api/local/benchmarks"),
  },

  remotes: {
    list: () => request<RemoteListItem[]>("/api/remotes"),
    add: (profile: Partial<Profile>) =>
      request<Profile>("/api/remotes", {
        method: "POST",
        body: JSON.stringify(profile),
      }),
    update: (name: string, profile: Partial<Profile>) =>
      request<Profile>(`/api/remotes/${encodeURIComponent(name)}`, {
        method: "PUT",
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

    // Cluster catalog (require a live connection; for the authoring pickers).
    benchmarks: (name: string) =>
      request<BenchmarksResult>(`/api/remotes/${encodeURIComponent(name)}/benchmarks`),
    nodes: (name: string) => request<NodesResult>(`/api/remotes/${encodeURIComponent(name)}/nodes`),

    bootstrap: {
      plan: (name: string) =>
        request<BootstrapPlan>(`/api/remotes/${encodeURIComponent(name)}/bootstrap/plan`, {
          method: "POST",
        }),
      install: (name: string, preCommands: string[]) =>
        request<StepResult>(`/api/remotes/${encodeURIComponent(name)}/bootstrap/install`, {
          method: "POST",
          body: JSON.stringify({ pre_commands: preCommands }),
        }),
      verify: (name: string) =>
        request<DetectResult>(`/api/remotes/${encodeURIComponent(name)}/bootstrap/verify`, {
          method: "POST",
        }),
    },
  },

  experiments: {
    list: () => request<LibraryEntry[]>("/api/experiments"),
    get: (id: string) => request<LibraryEntry>(`/api/experiments/${encodeURIComponent(id)}`),
    create: (name: string, config: CrabConfig) =>
      request<SavedEntry>("/api/experiments", {
        method: "POST",
        body: JSON.stringify({ name, config }),
      }),
    update: (id: string, name: string, config: CrabConfig) =>
      request<SavedEntry>(`/api/experiments/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: JSON.stringify({ name, config }),
      }),
    duplicate: (id: string) =>
      request<LibraryEntry>(`/api/experiments/${encodeURIComponent(id)}/duplicate`, {
        method: "POST",
      }),
    remove: (id: string) =>
      request<void>(`/api/experiments/${encodeURIComponent(id)}`, { method: "DELETE" }),
  },

  jobs: {
    list: () => request<JobListItem[]>("/api/jobs"),
    submit: (body: {
      profile_name: string;
      config_id?: string;
      config?: CrabConfig;
      name?: string;
      preset?: string;
      only?: string[];
      rerun_of?: string;
    }) =>
      request<SubmissionAccepted>("/api/jobs/submit", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    submissionStatus: (submissionId: string) =>
      request<SubmissionStatus>(`/api/jobs/submissions/${encodeURIComponent(submissionId)}`),
    cancel: (id: string) =>
      request<CancelResponse>(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
    logs: (id: string) => request<JobLogs>(`/api/jobs/${encodeURIComponent(id)}/logs`),
    experimentLogs: (id: string, experiment: string) =>
      request<ExperimentLogs>(
        `/api/jobs/${encodeURIComponent(id)}/logs?experiment=${encodeURIComponent(experiment)}`,
      ),
    report: (configName: string) =>
      request<UseCaseReport>(`/api/jobs/report/${encodeURIComponent(configName)}`),
    experiments: (id: string) =>
      request<JobDetail>(`/api/jobs/${encodeURIComponent(id)}/experiments`),
  },
};
