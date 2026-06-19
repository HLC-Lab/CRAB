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
  info: CrabInfo | null;
  crab_installed: boolean;
  reason: string | null;
}

/** One guided-install step (mirrors web/remoteops/bootstrap.py). */
export interface BootstrapStep {
  id: string;
  label: string;
  command: string;
}

/** POST /api/remotes/{name}/bootstrap/plan */
export interface BootstrapPlan {
  installed: boolean;
  info: CrabInfo | null;
  reason: string | null;
  pre_commands: string[];
  steps: BootstrapStep[];
}

/** POST /api/remotes/{name}/bootstrap/run */
export interface StepResult {
  rc: number;
  ok: boolean;
  stdout: string;
  stderr: string;
}

/** POST /api/remotes/{name}/bootstrap/verify */
export interface DetectResult {
  installed: boolean;
  info: CrabInfo | null;
  reason: string | null;
}

// -- Experiment authoring (Phase 3) ----------------------------------------

/** One application within an experiment (engine-shaped; extra keys allowed). */
export interface AppConfig {
  path: string;
  args?: string;
  collect?: boolean;
  start?: string;
  end?: string;
  partition?: string;
  [key: string]: unknown;
}

export interface Experiment {
  description?: string;
  local_options?: Record<string, unknown>;
  apps: Record<string, AppConfig>;
}

/** The engine config document a `crab run` consumes. */
export interface CrabConfig {
  global_options: Record<string, unknown>;
  experiments: Record<string, Experiment>;
}

/** A saved config in the local library (mirrors web/store/library.py). */
export interface LibraryEntry {
  id: string;
  name: string;
  updated_at: string;
  config: CrabConfig;
}

// -- Cluster catalog (Phase 3 pickers; crab list-benchmarks / nodes --json) --

export interface WrapperMetric {
  name: string | null;
  unit: string | null;
  conv: boolean;
}

/** One discovered wrapper file (loadable or not — unloadable are still listed). */
export interface Wrapper {
  file: string;
  relpath: string; // what goes into an app's `path`, e.g. "blink/a2a_comm_only.py"
  group: string;
  loadable: boolean;
  benchmark_id: string | null;
  bench_name: string | null;
  metadata: WrapperMetric[];
  error?: string;
}

/** GET /api/remotes/{name}/benchmarks */
export interface BenchmarksResult {
  schema: number;
  benchmarks: { id?: string; [key: string]: unknown }[];
  wrappers: Wrapper[];
}

export interface NodePartition {
  name: string;
  avail?: string;
  nodes?: number;
}

/** GET /api/remotes/{name}/nodes */
export interface NodesResult {
  schema: number;
  available: boolean;
  partitions: NodePartition[];
  nodes: string[];
  note?: string;
}
