"""CRAB web dashboard — laptop-local control plane.

A FastAPI app (launched by ``crab web``) that drives each cluster's existing CRAB
CLI over a reused SSH connection: author experiments, submit them, monitor jobs,
and fetch/visualise results. The cluster's engine stays authoritative — this package
never re-implements orchestration or submission logic.

See ``.crab-web-dev/`` (local-only) for the design, phased plan, and standards.
"""

__all__ = ["create_app", "get_settings"]


def __getattr__(name: str):
    # Lazy re-exports so importing crab.web stays cheap and dependency-light.
    if name == "create_app":
        from crab.web.server import create_app

        return create_app
    if name == "get_settings":
        from crab.web.settings import get_settings

        return get_settings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
