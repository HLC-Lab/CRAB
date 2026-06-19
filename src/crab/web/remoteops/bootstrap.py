"""Guided CRAB install on a remote cluster (Phase 2b, decision D5).

If a remote has no usable CRAB, this installs it: clone the repository, then
build it with ``make venv`` plus an editable install. The build skips the plain
``make`` target on purpose, because that target ends by launching the
interactive ``crab setup`` wizard, which cannot run over a non-interactive SSH
session. Configuring benchmarks (``crab setup``) stays out of scope (D10).

The repository URL, branch, clone target and build commands are fixed so the
flow stays the same on every cluster. The one thing the user can edit is an
optional list of pre-commands that run once at the start, for clusters that
need to prepare their environment first (for example loading a recent Python).

Everything runs in a single login shell so the pre-commands carry over into the
clone and build. The result is captured and returned in one go, and a final
:func:`detect` re-runs ``crab info --json`` to confirm CRAB is there.
"""

from __future__ import annotations

import shlex

from pydantic import BaseModel

from crab.web.connections.transport import Transport
from crab.web.errors import ContractError, RemoteCommandError
from crab.web.remoteops.crab_cli import _remote_path_expr, run_crab_json
from crab.web.store.profiles import Profile

CRAB_REPO_URL = "https://github.com/HLC-Lab/CRAB.git"

# TODO(pre-v1): the `--json` CLI seam this dashboard depends on only exists on
# the feature branch, not master yet. Clone that branch for now. Once it is
# merged, change this back to the default branch (master) and drop --branch.
# Tracked in .crab-web-dev/06-pre-v1-todos.md.
CRAB_REPO_BRANCH = "feature/web-dashboard"

# Clone + build can be slow (network + pip). Generous ceiling; transport.run
# still maps the timeout to a connection error so the UI never hangs forever.
INSTALL_TIMEOUT = 600.0

# Cap captured output so a chatty build doesn't bloat the response.
_MAX_CAPTURE = 40_000


class BootstrapStep(BaseModel):
    """One install command, shown to the user as a readable preview."""

    id: str
    label: str
    command: str


class StepResult(BaseModel):
    rc: int
    ok: bool
    stdout: str
    stderr: str


class DetectResult(BaseModel):
    installed: bool
    info: dict | None = None
    reason: str | None = None


def _clone_command(profile: Profile) -> str:
    return (
        f"git clone --branch {CRAB_REPO_BRANCH} {CRAB_REPO_URL} "
        f"{_remote_path_expr(profile.remote_crab)}"
    )


def _build_command(profile: Profile) -> str:
    dir_expr = _remote_path_expr(profile.remote_crab)
    return f"cd {dir_expr} && make venv && .venv/bin/pip install -e ."


def default_plan(profile: Profile) -> list[BootstrapStep]:
    """The install commands for ``profile``, as a readable preview (the actual
    run joins them into one shell, see :func:`build_install_command`)."""
    return [
        BootstrapStep(id="clone", label="Clone the repository", command=_clone_command(profile)),
        BootstrapStep(id="build", label="Build and install", command=_build_command(profile)),
    ]


def build_install_command(profile: Profile, pre_commands: list[str]) -> str:
    """The single command that installs CRAB: optional pre-commands once, then
    the clone and build, all in one login shell so module loads carry over."""
    pre = [c.strip() for c in pre_commands if c and c.strip()]
    parts = [*pre, _clone_command(profile), _build_command(profile)]
    inner = " && ".join(parts)
    return f"bash -lc {shlex.quote(inner)}"


async def detect(transport: Transport, profile: Profile) -> DetectResult:
    """Is a usable CRAB present? Runs ``crab info --json``.

    A missing or broken CRAB comes back as ``installed=False`` (the expected
    case that triggers install), not an exception. A dropped or timed-out
    connection still raises ``RemoteConnectionError`` from the transport.
    """
    try:
        info = await run_crab_json(transport, profile, ["info", "--json"])
    except (RemoteCommandError, ContractError) as exc:
        return DetectResult(installed=False, reason=exc.message)
    return DetectResult(installed=True, info=info)


async def install(
    transport: Transport,
    profile: Profile,
    pre_commands: list[str],
    timeout: float = INSTALL_TIMEOUT,
) -> StepResult:
    """Run the whole install in one shell and return its captured output."""
    command = build_install_command(profile, pre_commands)
    result = await transport.run(command, timeout=timeout)
    return StepResult(
        rc=result.rc,
        ok=result.ok,
        stdout=result.stdout[-_MAX_CAPTURE:],
        stderr=result.stderr[-_MAX_CAPTURE:],
    )
