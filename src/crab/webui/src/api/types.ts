// TS mirror of the backend contract (Pydantic models / CLI --json shapes).
// Hand-kept in sync with src/crab/web/*; consider code-gen later.

/** Stable error envelope returned by the backend (see web/errors.py). */
export interface ErrorEnvelope {
  code: string;
  message: string;
  detail?: string;
}

/** GET /api/health */
export interface Health {
  status: "ok";
  crab_version: string;
  api_schema: number;
}

/** A cluster connection profile (mirrors web/store/profiles.py Profile). */
export interface Profile {
  name: string;
  transport: "ssh" | "local";
  host: string | null;
  port: number;
  user: string | null;
  auth: "agent" | "key" | "password";
  key_path: string | null;
  hostkey_policy: "strict" | "insecure";
  remote_crab: string;
  venv_activate: string | null;
  remote_setup: string[];
  preset: string | null;
}

/** GET /api/remotes item = profile + live connection state. */
export type RemoteListItem = Profile & { connected: boolean };

/** crab info --json (subset the UI uses). */
export interface CrabInfo {
  schema: number;
  crab_version: string;
  crab_root: string;
  presets: { name: string; description: string }[];
}

/** POST /api/remotes/{name}/connect */
export interface ConnectResult {
  connected: boolean;
  info: CrabInfo;
}
