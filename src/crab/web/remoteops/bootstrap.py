"""Guided CRAB bootstrap on a remote cluster (Phase 2b, decision D5).

When a remote has no usable CRAB (``crab info --json`` doesn't parse), walk the
user through installing it. The recipe mirrors the project's own quick-start
(``git clone`` → ``make``, which creates ``.venv``, ``pip install -e .`` and
leaves the ``crab`` binary in ``.venv/bin``): the **structural** commands — the
repo URL, the clone target, ``make`` — are hard-coded so the flow can't drift
per cluster. The only editable, optional knob is a list of *pre-commands* run
first (e.g. ``module load python`` on clusters whose default Python is too old);
this keeps the recipe universal without baking in any one cluster's environment.

Each step is run on confirmation and its captured output returned (step-by-step
capture — no live streaming). A final :func:`detect` re-runs ``crab info --json``
to confirm success regardless of ``make``'s exit code: ``make`` finishes by
launching the interactive ``crab setup`` wizard, which aborts on the EOF stdin
of a non-interactive SSH exec — harmless, because ``pip install -e .`` has
already produced the binary by then.
"""

from __future__ import annotations

import shlex

from pydantic import BaseModel

from crab.web.connections.transport import Transport
from crab.web.errors import ContractError, InputError, RemoteCommandError
from crab.web.remoteops.crab_cli import _remote_path_expr, run_crab_json
from crab.web.store.profiles import Profile

# Canonical install source. Hard-coded on purpose (see module docstring).
CRAB_REPO_URL = "https://github.com/HLC-Lab/CRAB.git"

# Clone/build can be slow (network + pip). Generous ceiling; transport.run still
# maps the timeout to a connection error so the UI never hangs forever.
INSTALL_TIMEOUT = 600.0

# Cap captured output so a chatty build doesn't bloat the response.
_MAX_CAPTURE = 20_000

# Ordered recipe: id → human label. The command for each id is built by
# :func:`_inner_command`; structural parts are fixed there.
_STEP_LABELS: dict[str, str] = {
    "clone": "Clone the CRAB repository",
    "build": "Build & install CRAB (make)",
}


class BootstrapStep(BaseModel):
    """One install step, as shown to the user (the command is for display; it is
    always rebuilt server-side from ``id`` + pre-commands when actually run)."""

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


def _prefix(pre_commands: list[str]) -> str:
    """Join non-empty pre-commands into a ``… && `` prefix (empty if none)."""
    parts = [c.strip() for c in pre_commands if c and c.strip()]
    return " && ".join(parts) + " && " if parts else ""


def _inner_command(profile: Profile, step_id: str, pre_commands: list[str]) -> str:
    """Return the shell snippet for ``step_id`` (without the ``bash -lc`` wrap).

    Only ``pre_commands`` is user-supplied; everything else is fixed.
    """
    dir_expr = _remote_path_expr(profile.remote_crab)
    prefix = _prefix(pre_commands)
    if step_id == "clone":
        return f"{prefix}git clone {CRAB_REPO_URL} {dir_expr}"
    if step_id == "build":
        return f"{prefix}cd {dir_expr} && make"
    raise InputError(f"Unknown bootstrap step {step_id!r}.")


def build_command(profile: Profile, step_id: str, pre_commands: list[str]) -> str:
    """The exact command run for ``step_id``, wrapped in a login bash so module
    systems and rc files load (same convention as ``crab_cli``)."""
    return f"bash -lc {shlex.quote(_inner_command(profile, step_id, pre_commands))}"


def default_plan(profile: Profile, pre_commands: list[str] | None = None) -> list[BootstrapStep]:
    """The install steps for ``profile``. Pre-commands default to the profile's
    ``remote_setup`` (the same generic slot ``crab_cli`` uses before activation)."""
    pre = profile.remote_setup if pre_commands is None else pre_commands
    return [
        BootstrapStep(id=sid, label=label, command=build_command(profile, sid, pre))
        for sid, label in _STEP_LABELS.items()
    ]


async def detect(transport: Transport, profile: Profile) -> DetectResult:
    """Is a usable CRAB present? Runs ``crab info --json``.

    A missing/old CRAB surfaces as ``installed=False`` (the *expected* case that
    triggers bootstrap), not an exception. A dropped/timed-out connection still
    raises ``RemoteConnectionError`` from the transport — that's a real fault.
    """
    try:
        info = await run_crab_json(transport, profile, ["info", "--json"])
    except (RemoteCommandError, ContractError) as exc:
        return DetectResult(installed=False, reason=exc.message)
    return DetectResult(installed=True, info=info)


async def run_step(
    transport: Transport,
    profile: Profile,
    step_id: str,
    pre_commands: list[str],
    timeout: float = INSTALL_TIMEOUT,
) -> StepResult:
    """Run one install step and return its captured (tail-truncated) output."""
    command = build_command(profile, step_id, pre_commands)
    result = await transport.run(command, timeout=timeout)
    return StepResult(
        rc=result.rc,
        ok=result.ok,
        stdout=result.stdout[-_MAX_CAPTURE:],
        stderr=result.stderr[-_MAX_CAPTURE:],
    )
