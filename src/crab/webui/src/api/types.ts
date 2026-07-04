// TS mirror of the backend contract (Pydantic models / CLI --json shapes).
// Hand-kept in sync with src/crab/web/*; consider code-gen later.

/** Stable error envelope returned by the backend (see web/errors.py). */
import type { components } from "./generated";

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
// Backend-owned shapes come from the generated OpenAPI types (npm run gen:api;
// staleness enforced by make verify-full), so they cannot silently drift from
// the Pydantic models. Cluster-contract passthrough shapes (benchmarks/nodes)
// stay hand-written below: the backend forwards them verbatim from `crab
// ... --json`, so they are not in the OpenAPI schema.
export type Profile = components["schemas"]["Profile"];

/** GET /api/remotes item = profile + live connection state. */
export type RemoteListItem = components["schemas"]["RemoteListItem"];

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

/** A saved config in the local library (generated from web/store/library.py). */
export type LibraryEntry = Omit<components["schemas"]["LibraryEntry"], "config"> & {
  config: CrabConfig;
};

/** Create/update response: the entry plus shape warnings for the saved config
 * (advisory only — the save always succeeds). */
export type SavedEntry = LibraryEntry & { warnings: string[] };

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

// -- Jobs (Phase 4: submit & monitor) ---------------------------------------

export type JobRecord = components["schemas"]["JobRecord"];
/** GET /api/jobs item = job record + whether its cluster is currently connected. */
export type JobListItem = components["schemas"]["JobListItem"];
/** POST /api/jobs/{id}/cancel — `cancelled` is false when the job was already gone. */
export type CancelResponse = components["schemas"]["CancelResponse"];

/** One captured stream in a job's logs (crab logs --json; cli/contract.py). */
export interface LogStream {
  path: string;
  exists: boolean;
  content: string;
  truncated: boolean;
}

/** GET /api/jobs/{id}/logs — cluster-contract passthrough, not in the OpenAPI schema. */
export interface JobLogs {
  schema: number;
  data_dir: string;
  stdout: LogStream;
  stderr: LogStream;
}
