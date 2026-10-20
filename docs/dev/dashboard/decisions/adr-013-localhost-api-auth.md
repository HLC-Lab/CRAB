# ADR-013 · Localhost API requires a per-session token plus host checks

- **Date:** 2026-07-04
- **Status:** accepted

## Context

The dashboard binds 127.0.0.1, but "local" is not "safe": any web page open in the same
browser can fire requests at localhost ports (CSRF-style), and DNS rebinding lets a hostile
page bypass same-origin assumptions entirely. This API opens SSH connections and can run
installs on clusters, so it must not be drivable by anything but the dashboard itself.

## Decision

Defense in two layers, enforced by middleware on every `/api/*` request:

1. **Host/Origin gate** — requests whose `Host` (or `Origin`, when present) is not
   `127.0.0.1`, `localhost`, or `::1` are rejected (403). This kills DNS rebinding, where the
   request arrives with the attacker's hostname.
2. **Session token** — `create_app` generates a random token per process; the served
   `index.html` carries it in a `<meta name="crab-token">` tag; the SPA echoes it as
   `X-Crab-Token` on every call; anything without it gets 401. There are **zero open API
   routes** (`/api/health` included). Static files and the SPA shell stay open — the shell is
   how the browser obtains the token, and layer 1 prevents a rebinding attacker from fetching
   it.

Vite dev mode serves its own index (no meta tag), so the client falls back to
`localStorage.CRAB_DEV_TOKEN`; `crab web -v` logs the token once for that purpose. Future
WebSockets must carry the token in the query string or subprotocol.

## Alternatives considered

- Cookies + CSRF tokens — cookies are exactly what cross-site requests ride on; a custom
  header is simpler and immune.
- Fixed token in a config file — a secret at rest (against ADR-003) and shared across sessions.
- Host check alone — leaves plain CSRF-style blind POSTs possible.

## Consequences

Easier: the whole attack class is closed with no user-visible change (the token rides the
HTML). Harder: anything new that calls the API (websockets, external scripts) must present
the token; plain `curl` debugging needs the token from the served HTML or the `-v` log.
