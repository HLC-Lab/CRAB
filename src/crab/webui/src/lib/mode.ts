// SbatchMan integration mode (plan 084). Enabled per-launch via `crab web
// --sbatchman`; the backend injects <meta name="crab-sbatchman" content="true">
// into the served index.html (see web/server.py), mirroring how the session
// token is delivered. Dev fallback: the Vite dev server serves its own index
// without the meta, so a developer can set localStorage.CRAB_DEV_SBATCHMAN.

export function isSbatchmanMode(): boolean {
  const meta = document.querySelector('meta[name="crab-sbatchman"]');
  if (meta) return meta.getAttribute("content") === "true";
  return localStorage.getItem("CRAB_DEV_SBATCHMAN") === "true";
}
