"""Launch the CRAB web dashboard: start uvicorn on localhost and open the browser.

Kept separate from the app factory (``server.py``) so the app can be imported
and tested without binding a socket.
"""

from __future__ import annotations

import logging
import threading
import webbrowser

from crab.web.settings import get_settings

logger = logging.getLogger("crab.web")


def _open_browser_when_up(url: str, delay: float = 1.0) -> None:
    """Open the default browser shortly after the server starts (best-effort)."""

    def _open() -> None:
        try:
            webbrowser.open(url)
        except Exception:  # headless / no browser — non-fatal
            logger.info("Could not open a browser automatically; visit %s", url)

    threading.Timer(delay, _open).start()


def _configure_logging(verbose: bool) -> None:
    """Quiet by default, chatty with -v.

    Default: only warnings/errors surface (no per-request access spam, no
    startup chatter). Verbose: full INFO logs incl. our own ``crab.web`` events.
    Idempotent so re-launches in one process don't stack handlers.
    """
    web_logger = logging.getLogger("crab.web")
    web_logger.setLevel(logging.INFO if verbose else logging.WARNING)
    if not any(isinstance(h, logging.StreamHandler) for h in web_logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        web_logger.addHandler(handler)
    web_logger.propagate = False


def run_server(
    host: str | None = None,
    port: int | None = None,
    *,
    open_browser: bool = True,
    verbose: bool = False,
) -> None:
    """Start the dashboard. Blocks until the server is stopped (Ctrl-C).

    Args:
        host/port: override the localhost defaults from settings.
        open_browser: open the dashboard in the default browser on startup.
        verbose: emit full INFO logs (startup + per-request access). Off by
            default to keep the console clean.
    """
    import uvicorn

    settings = get_settings()
    settings.ensure_dirs()
    host = host or settings.host
    port = port or settings.port
    url = f"http://{host}:{port}"

    _configure_logging(verbose)
    app = __import__("crab.web.server", fromlist=["create_app"]).create_app(settings)

    print(f"[*] CRAB web dashboard → {url}  (Ctrl-C to stop)", flush=True)
    if open_browser:
        _open_browser_when_up(url)

    try:
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info" if verbose else "warning",
            access_log=verbose,
        )
    except OSError as exc:
        # Most commonly: address already in use.
        print(
            f"[ERROR] Could not start the server on {host}:{port}: {exc}\n"
            f"        Another instance may be running, or the port is taken. "
            f"Try: crab web --port <other>"
        )
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("\n[*] CRAB web dashboard stopped.")
