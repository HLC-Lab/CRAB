// Pure job-identity helpers (plan 077): a job_basename is the first path
// segment under a system's data root -- the same identity `crab history`'s
// relative_path and a JobRecord's data_dir both encode (mirrors
// web/api/results.py's `_job_basename`). Shared by the results store and the
// views that key into it (job detail, the results picker).

export function jobBasenameFromDataDir(dataDir: string): string {
  const parts = dataDir.split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "";
}

export function jobBasenameFromRelativePath(relativePath: string): string {
  const parts = relativePath.split("/").filter((p) => p && p !== ".");
  return parts.length ? parts[0] : "";
}

export function resultsKey(cluster: string, system: string, jobBasename: string): string {
  return `${cluster}/${system}/${jobBasename}`;
}
