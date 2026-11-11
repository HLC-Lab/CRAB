"""Guided CRAB install on a remote cluster.

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

import re
import shlex

from pydantic import BaseModel

from crab.cli.contract import CONTRACT_SCHEMA
from crab.web.connections.transport import CmdResult, Transport
from crab.web.errors import ContractError, InputError
from crab.web.remoteops.crab_cli import (
    build_crab_command,
    crab_dir,
    parse_crab_json,
    remote_path_expr,
)
from crab.web.store.profiles import Profile

CRAB_REPO_URL = "https://github.com/HLC-Lab/CRAB.git"

# TODO(pre-v1): clone `develop`, the product line, for now. After the v1.0
# release the bootstrap should install the release (PyPI or `master`), not a
# development branch: then drop --branch.
CRAB_REPO_BRANCH = "develop"

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
    # Set when the cluster's `--json` contract differs from this dashboard's (see contract_skew).
    skew: str | None = None


def contract_skew(info: dict) -> str | None:
    """A fix-it message when ``crab info``'s contract schema differs from ours, else None.

    A missing schema means a CRAB older than the field, so it counts as older.
    """
    remote = info.get("schema")
    if remote == CONTRACT_SCHEMA:
        return None
    if isinstance(remote, int) and remote > CONTRACT_SCHEMA:
        return (
            "This cluster's CRAB is newer than the dashboard. "
            "Run crab update on this laptop, then restart crab web."
        )
    return (
        "This cluster's CRAB is older than the dashboard. "
        "Run crab update on the cluster, then reconnect."
    )


def ensure_installable(profile: Profile) -> None:
    """Raise InputError for a profile that names its own crab command.

    That install is the user's own (pipx), so the dashboard has nothing to clone or build.
    """
    if profile.crab_command is not None:
        raise InputError(
            f"{profile.name!r} runs {profile.crab_command!r}: you manage this install: "
            "pipx install crab-hpc"
        )


def _clone_command(profile: Profile) -> str:
    # Clones into a `CRAB` subfolder of the profile's base dir (see crab_dir), unless that
    # folder is already a clone (a half-built earlier install): then the `&&` chain goes
    # straight on to the build steps.
    target = remote_path_expr(crab_dir(profile))
    return (
        f"{{ test -d {remote_path_expr(crab_dir(profile) + '/.git')} || "
        f"git clone --branch {CRAB_REPO_BRANCH} {CRAB_REPO_URL} {target}; }}"
    )


def _build_command(profile: Profile) -> str:
    dir_expr = remote_path_expr(crab_dir(profile))
    # `crab update --to-release` switches the fresh clone to the newest release tag of its
    # branch's line (ADR-030), and stays on the branch while no release exists yet.
    return (
        f"cd {dir_expr} && make venv && .venv/bin/pip install -e . "
        "&& .venv/bin/crab update --to-release"
    )


def default_plan(profile: Profile) -> list[BootstrapStep]:
    """The install commands for ``profile``, as a readable preview (the actual
    run joins them into one shell, see :func:`build_install_command`)."""
    ensure_installable(profile)
    return [
        BootstrapStep(
            id="clone",
            label="Clone the repository, unless it is already there",
            command=_clone_command(profile),
        ),
        BootstrapStep(id="build", label="Build and install", command=_build_command(profile)),
    ]


def build_install_command(profile: Profile, pre_commands: list[str]) -> str:
    """The single command that installs CRAB: optional pre-commands once, then
    the clone and build, all in one login shell so module loads carry over."""
    ensure_installable(profile)
    pre = [c.strip() for c in pre_commands if c and c.strip()]
    parts = [*pre, _clone_command(profile), _build_command(profile)]
    inner = " && ".join(parts)
    return f"bash -lc {shlex.quote(inner)}"


# What bash prints when the checkout, the venv's activate file or the `crab` command is
# missing. Anchored to a shell-prefixed line, so a Python traceback that merely mentions a
# missing file is not mistaken for "no CRAB here".
_NO_CRAB_LINE = re.compile(
    r"^(?:-?bash|sh)(?:: line \d+)?: .*(?:No such file or directory|command not found)",
    re.MULTILINE,
)
_SHELL_COMMAND_NOT_FOUND = 127


def _no_crab_reason(result: CmdResult) -> str | None:
    """A plain sentence when a failed ``crab info`` means "there is no CRAB here", else None."""
    text = f"{result.stderr}\n{result.stdout}"
    match = _NO_CRAB_LINE.search(text)
    if match is not None:
        return f"CRAB was not found on this cluster: {match.group(0).strip()}"
    if result.rc == _SHELL_COMMAND_NOT_FOUND:
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        reason = "CRAB was not found on this cluster (the crab command does not exist there)."
        return f"{reason} {first}" if first else reason
    return None


async def detect(transport: Transport, profile: Profile) -> DetectResult:
    """Is a usable CRAB present? Runs ``crab info --json``.

    There are three outcomes:

    * CRAB answered: ``installed=True``.
    * There is no CRAB to run (checkout or venv missing, ``crab`` not found, or output with
      no JSON at all): ``installed=False`` with a reason, the case that offers Install.
    * CRAB is there but failed (for example a Python traceback): ``RemoteCommandError``
      propagates with the cluster's own output as ``detail``, so the user sees why and no
      install is offered over a working checkout.

    A dropped or timed-out connection raises ``RemoteConnectionError`` from the transport.
    """
    args = ["info", "--json"]
    result = await transport.run(build_crab_command(profile, args), timeout=30.0)
    if not result.ok:
        reason = _no_crab_reason(result)
        if reason is not None:
            return DetectResult(installed=False, reason=reason)
    try:
        info = parse_crab_json(args, result)
    except ContractError as exc:
        return DetectResult(installed=False, reason=exc.message)
    return DetectResult(installed=True, info=info, skew=contract_skew(info))


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
