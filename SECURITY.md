# Security policy

## Reporting a vulnerability

Report a vulnerability privately, through GitHub's private vulnerability reporting: open the
[Security tab of the repository](https://github.com/HLC-Lab/CRAB/security/advisories/new) and
choose "Report a vulnerability". Please do not open a public issue or pull request for it.

Include the CRAB version (`crab info`), the layout (checkout or installed), and the steps to
reproduce. We read the report, confirm it, and work on a fix with you in the private advisory. The
advisory is published together with the release that fixes it.

## What is in scope

- **The `crab web` dashboard.** It listens on localhost only. Every `/api/*` request must carry
  the per-session token and a `Host` and `Origin` of `127.0.0.1`, `localhost` or `::1`
  ([ADR-013](docs/dev/dashboard/decisions/adr-013-localhost-api-auth.md)). A way around either
  check, or any other way for a web page or another local user to drive the API, is in scope.
- **SSH handling.** The dashboard reaches clusters through asyncssh: connection and
  authentication, host key checking, and what is kept on disk
  ([ADR-003](docs/dev/dashboard/decisions/adr-003-no-secrets-at-rest.md)).
- **The remote command builder.** The code that builds the commands the dashboard runs on a
  cluster (`src/crab/web/remoteops/`). Input that escapes its quoting, or runs something other
  than the intended `crab` command, is in scope.

## What is out of scope

- Experiment configs, wrappers and presets are code that runs on your machine and on the clusters
  you submit to. A config or wrapper that does something harmful when you run it is not a
  vulnerability in CRAB. Only run wrappers you trust.
- Weaknesses of the clusters, of Slurm, or of other software that CRAB drives.
- A dashboard that was deliberately exposed beyond localhost, for example behind a proxy that
  rewrites the `Host` header.

## Supported versions

| Version | Supported |
|---------|-----------|
| 1.0.x   | Yes       |
| Earlier | No        |

Until 1.0.0 is released, fixes go to the `develop` branch.
