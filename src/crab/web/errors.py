"""Error taxonomy and the stable error envelope for the web backend.

Every failure that crosses an API boundary becomes a structured, actionable
response — the backend never crashes the process on a remote or user error
(see ``.crab-web-dev/05-instructions.md`` §5). Each domain error carries:

* ``code``    — stable machine string the frontend can switch on,
* ``message`` — human, actionable text safe to display,
* ``detail``  — optional extra context (e.g. stderr); secrets must be redacted
                by the raiser before it reaches here.

Builtin names (``ConnectionError``, ``ValidationError``) are deliberately *not*
reused, to avoid shadowing.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("crab.web")


class CrabWebError(Exception):
    """Base class for all expected, handled backend errors."""

    code: str = "internal_error"
    http_status: int = 500

    def __init__(self, message: str, *, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_envelope(self) -> dict[str, Any]:
        env: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail:
            env["detail"] = self.detail
        return env


class AuthError(CrabWebError):
    """SSH authentication rejected, OTP wrong, or key missing/locked."""

    code = "auth_error"
    http_status = 401


class RemoteConnectionError(CrabWebError):
    """Host unreachable, channel dropped, or a remote call timed out."""

    code = "connection_error"
    http_status = 502


class RemoteCommandError(CrabWebError):
    """A ``crab``/Slurm command on the cluster exited non-zero."""

    code = "remote_command_error"
    http_status = 502


class ContractError(CrabWebError):
    """``--json`` output was unparsable or the cluster CRAB schema is incompatible."""

    code = "contract_error"
    http_status = 502


class InputError(CrabWebError):
    """Bad user input / malformed config — never forwarded to the cluster."""

    code = "input_error"
    http_status = 422


class NotFoundError(CrabWebError):
    code = "not_found"
    http_status = 404


class ConflictError(CrabWebError):
    """Duplicate name / state conflict (e.g. profile already exists)."""

    code = "conflict"
    http_status = 409


def register_exception_handlers(app: Any) -> None:
    """Install handlers that turn errors into the stable envelope.

    Imports FastAPI lazily so this module stays importable without the web extra.
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(CrabWebError)
    async def _handle_known(_request: Request, exc: CrabWebError) -> JSONResponse:
        # Expected errors: log at info/warning, return the actionable envelope.
        logger.warning("%s: %s", exc.code, exc.message)
        return JSONResponse(status_code=exc.http_status, content=exc.to_envelope())

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        # Unexpected: log the full traceback locally, return a safe generic message.
        logger.exception("Unhandled backend error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": "An unexpected error occurred. See the dashboard logs for details.",
            },
        )
