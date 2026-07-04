# Web dashboard architecture

The web dashboard (`crab web`) is a **laptop-local control plane** for CRAB: a FastAPI backend
plus a Vue single-page app that let a researcher manage clusters, author experiment configs,
and (in upcoming phases) submit, monitor, and analyze runs — for all their clusters from one
UI. It never runs on the HPC system itself.

## The one immovable fact

The CRAB worker runs *inside the Slurm allocation*, on the cluster. The dashboard is a control
plane and viewer only: the cluster's own `crab` CLI/engine stays authoritative for everything
(sbatch header merging, security checks, allocation semantics). **Never re-implement engine
logic in the backend or the frontend.**

```
LAPTOP                                              CLUSTER (per remote)
crab web → uvicorn (127.0.0.1) → browser SPA        researcher's CRAB checkout
  FastAPI backend ── asyncssh (one reused ──────►   crab <cmd> --json
  local JSON stores    connection per remote)       sbatch → crab worker → data/
```

## Backend map — `src/crab/web/`

| Module | Role |
|---|---|
| `server.py` | App factory, `/api/health`, router wiring, SPA serving (traversal-guarded, `no-cache` shell) |
| `run.py` | `crab web` entry: uvicorn on localhost, opens the browser |
| `settings.py` | `platformdirs` paths; env overrides `CRAB_WEB_CONFIG_DIR` / `CRAB_WEB_DATA_DIR` / `CRAB_WEB_PORT` |
| `errors.py` | Error taxonomy; every API error is the stable envelope `{code, message, detail?}` |
| `api/` | Routes only — no business logic: `remotes.py`, `bootstrap.py`, `experiments.py`, `local.py` |
| `connections/` | `transport.py` (`Transport` ABC; `SSHTransport` via asyncssh, `LocalTransport` via subprocess), `manager.py` (one reused live connection per remote, liveness eviction) |
| `remoteops/` | `crab_cli.py` (builds/runs `bash -lc "… crab <args> --json"`, parses JSON), `bootstrap.py` (guided CRAB install) |
| `store/` | `profiles.py` (`clusters.json`), `library.py` (experiment configs) — atomic writes, slug-validated ids |

### Layering rule

`api/` → `remoteops/` + `store/` → `connections/`. Imports point down this list only;
`server.py` wires the layers together. All remote execution goes through `Transport.run`
(SSH and local are interchangeable), with a timeout on every call, and every failure mapped to
an `errors.py` class — the process must never crash on a remote or user error.

### HTTP API

| Route | Purpose |
|---|---|
| `GET /api/health` | Liveness + version handshake |
| `GET/POST /api/remotes`, `PUT/DELETE /api/remotes/{name}` | Cluster profile CRUD (non-secret) |
| `POST /api/remotes/{name}/connect` · `/disconnect` | Open/close the SSH session; connect runs `crab info --json` |
| `GET /api/remotes/{name}/benchmarks` · `/nodes` | Cluster catalog (wrapper picker, node reference) |
| `POST /api/remotes/{name}/bootstrap/plan` · `/install` · `/verify` | Guided CRAB install on a bare cluster |
| `GET /api/local/benchmarks` | Same catalog introspection for the CRAB checkout on this machine |
| `GET/POST /api/experiments`, `GET/PUT/DELETE /api/experiments/{id}`, `POST .../duplicate` | Local experiment library |
| `GET /{path}` | SPA fallback (serves `web/static/`) |

### Local state (laptop)

```
<user_config_dir>/crab/clusters.json      cluster profiles (never secrets)
<user_data_dir>/crab/experiments/*.json   authored experiment configs (library)
```

Auth is agent / key / password (in-memory only); host-key checking is `strict` or, for
round-robin login nodes, `insecure` per profile. No secrets are ever persisted or logged.

**API protection:** every `/api/*` request must carry the per-process session token
(`X-Crab-Token`, delivered to the SPA via a meta tag in the served `index.html`) and arrive
with a local `Host`/`Origin` — middleware rejects anything else, closing the CSRF and
DNS-rebinding attack class on an API that can execute SSH commands (see ADR-013).

## The CLI JSON seam — `src/crab/cli/contract.py`

The backend talks to a cluster **only** by running `crab <cmd> --json` there and parsing
stdout; it never screen-scrapes. The shapes carry `CONTRACT_SCHEMA` (integer) so version skew
between laptop and cluster is detectable. Adding a field is backward-safe; renaming or
removing one requires a schema bump.

| Command | Returns |
|---|---|
| `crab info --json` | version, schema, crab_root, presets — the connect handshake |
| `crab list-benchmarks --json` | receipts + wrapper introspection (id, metadata, loadable) |
| `crab nodes --json` | sinfo partitions/nodes (degrades gracefully off-Slurm) |
| `crab run <cfg> -p <preset> --json` | `{job_id, data_dir, system}` (logs go to stderr; stdout stays clean JSON) |
| `crab status [ids] --json` | job states via squeue → sacct → UNKNOWN |
| `crab history --json` | parsed per-system `metadata.csv` |

## Frontend map — `src/crab/webui/`

Vue 3 (`<script setup lang="ts">`, strict TS) + Vite + Pinia + vue-router. Views:
Remotes · Author · Wrappers (stub) · Jobs (stub) · Results (stub).

| Where | Role |
|---|---|
| `src/api/` | `client.ts` (the only place `fetch` is called; errors normalized to the envelope) + `types.ts` |
| `src/lib/config.ts` | The config brain: UI `Draft` ⇄ engine JSON (`toConfig`/`fromConfig`), validation, flow-diagram layout. Pure — no IO — and covered by the round-trip unit suite |
| `src/stores/` | Pinia: `app` (health/theme), `remotes`, `author` (draft + library), `catalog` (per-cluster benchmark/node cache) |
| `src/views/`, `src/components/` | Presentation; components never fetch directly |

Config-mapping rules that must not regress (enforced by the round-trip suite): options are
emitted only when set (untouched editors emit no key); tri-state booleans distinguish unset
from explicit false; numeric *options* are emitted as strings but allocation numbers
(`split`/`share`/`stride`/`seed`) as numbers; unknown app keys are wrapper attributes and are
preserved; a per-experiment `local_options.allocation` is force-emitted because omitting it
means "inherit the global"; legacy `applications` configs and top-level `split` allocations
are normalized on import (split becomes named partitions — semantic, not byte, equivalence).

## Build and serving model

- `npm run build` (in `src/crab/webui/`) type-checks and builds **into
  `src/crab/web/static/`, which is committed** and shipped in the wheel — end users never run
  node. Any frontend source change must include the rebuilt static output in the same commit.
- `crab` is normally installed editable during development: backend (Python) changes need a
  `crab web` restart; frontend rebuilds only need a browser hard-refresh (the SPA shell is
  served `Cache-Control: no-cache`; hashed assets cache forever).
- Dev loop alternative: `npm run dev` proxies `/api` to a running backend on port 8765.

## Related docs

- [Testing & verification](testing.md) — the gates and how to run them
- [Roadmap](roadmap.md) · [Deferred](deferred.md)
- [Decision records](decisions/index.md) — why things are the way they are
