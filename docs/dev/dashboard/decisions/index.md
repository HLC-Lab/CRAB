# Decision records

Architecture Decision Records for the web dashboard. Each records one significant choice: the
context that forced it, the decision, the alternatives, and the consequences. New decisions
with real alternatives get a new ADR (copy [template.md](template.md)); a reversed decision is
marked superseded, never deleted.

| ADR | Decision | Status |
|---|---|---|
| [001](adr-001-laptop-control-plane.md) | Run the dashboard as a laptop-local control plane | accepted |
| [002](adr-002-cli-json-seam.md) | Talk to clusters only through `crab … --json` | accepted |
| [003](adr-003-no-secrets-at-rest.md) | No secrets at rest; one reused SSH connection per cluster | accepted |
| [004](adr-004-committed-frontend-build.md) | Commit the built frontend and ship it in the wheel | accepted |
| [005](adr-005-emit-on-set.md) | Authored configs emit only what the user set | accepted |
| [006](adr-006-collect-vs-role.md) | Roles derive from `end`; `collect` is a separate flag | accepted |
| [007](adr-007-split-normalizes-to-partitions.md) | The editor standardizes allocations on named partitions | accepted |
| [008](adr-008-generic-slices-ui.md) | Allocation UI: generic node slices, no scenario taxonomy | accepted |
| [009](adr-009-laptop-local-library.md) | Experiment library stored on the laptop | under review |
| [010](adr-010-preset-at-submit-time.md) | Presets are chosen at submit time, not stored in configs | accepted |
| [011](adr-011-legacy-applications-import.md) | Legacy `applications` configs import cleanly | accepted |
| [012](adr-012-ui-copy-style.md) | UI copy: plain language, real defaults, no long dashes | accepted |
| [013](adr-013-localhost-api-auth.md) | Localhost API requires a per-session token plus host checks | accepted |
