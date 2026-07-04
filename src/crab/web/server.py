"""FastAPI application factory for the CRAB web dashboard.

``create_app()`` builds the ASGI app: API routes under ``/api`` plus the built
Vue single-page app served from ``web/static`` (with SPA-fallback routing). The
factory pattern keeps the app importable and testable without launching a server.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING

from crab.web.errors import register_exception_handlers
from crab.web.settings import Settings, get_settings

if TYPE_CHECKING:
    from fastapi import FastAPI, Response

    from crab.web.connections.manager import ConnectionManager

logger = logging.getLogger("crab.web")

# Version of the laptop<->browser API surface. Bump on breaking changes (see
# .crab-web-dev/05-instructions.md §9). Distinct from the CRAB package version.
API_SCHEMA_VERSION = 1


def _crab_version() -> str:
    try:
        return _pkg_version("crab")
    except PackageNotFoundError:  # e.g. running from a non-installed checkout
        return "unknown"


def create_app(
    settings: Settings | None = None, manager: ConnectionManager | None = None
) -> FastAPI:
    """Build and return the FastAPI app.

    Args:
        settings: optional override (tests inject a temp-dir Settings); defaults
            to the process-wide :func:`get_settings`.
        manager: optional pre-built ConnectionManager (tests inject one with a
            fake connector); otherwise one is created at startup.
    """
    from fastapi import APIRouter, FastAPI

    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_dirs()
        if getattr(app.state, "manager", None) is None:
            from crab.web.connections.manager import ConnectionManager

            app.state.manager = ConnectionManager()
        logger.info("CRAB web starting — data dir: %s", settings.data_dir)
        yield
        await app.state.manager.close_all()
        logger.info("CRAB web shutting down")

    app = FastAPI(
        title="CRAB Web Dashboard",
        version=_crab_version(),
        lifespan=lifespan,
    )
    # Shared state for routes (settings drive the per-request stores).
    app.state.settings = settings
    app.state.manager = manager
    register_exception_handlers(app)

    api = APIRouter(prefix="/api")

    @api.get("/health")
    async def health() -> dict:
        """Liveness + version handshake for the frontend."""
        return {
            "status": "ok",
            "crab_version": _crab_version(),
            "api_schema": API_SCHEMA_VERSION,
        }

    app.include_router(api)

    from crab.web.api.bootstrap import router as bootstrap_router
    from crab.web.api.experiments import router as experiments_router
    from crab.web.api.local import router as local_router
    from crab.web.api.remotes import router as remotes_router

    app.include_router(remotes_router)
    app.include_router(bootstrap_router)
    app.include_router(experiments_router)
    app.include_router(local_router)

    _mount_frontend(app, settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    """Serve the built SPA, or a helpful placeholder when it hasn't been built.

    Registered after the API router so ``/api/*`` always takes precedence.
    """
    from fastapi.responses import FileResponse, HTMLResponse

    index = settings.static_dir / "index.html"

    if not index.is_file():
        # Dev convenience: the wheel ships built assets, but a source checkout
        # may not have run the Vite build yet. Fail gracefully, not with a 500.
        @app.get("/", response_class=HTMLResponse)
        async def _placeholder() -> str:
            return (
                "<!doctype html><meta charset=utf-8>"
                "<title>CRAB Web</title>"
                "<body style='font-family:system-ui;max-width:40rem;margin:4rem auto'>"
                "<h1>CRAB Web backend is running</h1>"
                "<p>The frontend has not been built. From <code>src/crab/webui</code> run "
                "<code>npm install &amp;&amp; npm run build</code>, or use the Vite dev server "
                "(<code>npm run dev</code>) which proxies <code>/api</code> here.</p>"
                "<p>API health: <a href='/api/health'>/api/health</a></p>"
                "</body>"
            )

        logger.warning("Frontend not built (%s missing) — serving placeholder.", index)
        return

    from fastapi.staticfiles import StaticFiles

    # Hashed build assets (Vite emits an immutable assets/ dir).
    assets = settings.static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    # The SPA shell must never be cached: it names the current (hashed) asset
    # bundles, so a stale index.html would load an old frontend even after a
    # rebuild. The hashed assets themselves are immutable and cache freely.
    _index_headers = {"Cache-Control": "no-cache"}

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> Response:
        """Serve a real static file if present, else fall back to index.html
        so client-side routes (deep links / reloads) work."""
        candidate = (settings.static_dir / full_path).resolve()
        # Path-traversal guard: never serve outside static_dir.
        if full_path and settings.static_dir in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index, headers=_index_headers)
