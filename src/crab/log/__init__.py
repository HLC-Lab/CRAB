"""
CRAB Logging Package — public API.

Usage:
    from crab.log import get_logger, LogLevel

    logger = get_logger()                    # root logger
    exp_log = logger.enter("exp_baseline")   # experiment context
    run_log = exp_log.enter("run_1")         # run context
    app_log = run_log.enter("app_0::a2a_b")  # app context

    app_log.info("Launched on 4 nodes")
    app_log.app_output(stdout_text)

Log level is controlled by the CRAB_LOG_LEVEL environment variable
(DEBUG, INFO, WARNING, ERROR, CRITICAL). Defaults to INFO.
"""

import os

from .logger import CrabLogger, LogLevel, LogSource, LogRecord
from .formatters import RichFormatter, PlainFormatter
from .handlers import StreamHandler, TUIHandler

__all__ = [
    "get_logger",
    "CrabLogger",
    "LogLevel",
    "LogSource",
    "LogRecord",
    "RichFormatter",
    "PlainFormatter",
    "StreamHandler",
    "TUIHandler",
]

# Canonical level names accepted by CRAB_LOG_LEVEL
_LEVEL_MAP = {
    "DEBUG": LogLevel.DEBUG,
    "INFO": LogLevel.INFO,
    "WARNING": LogLevel.WARNING,
    "ERROR": LogLevel.ERROR,
    "CRITICAL": LogLevel.CRITICAL,
}


def _resolve_level() -> LogLevel:
    """Read CRAB_LOG_LEVEL from the environment, default to INFO."""
    raw = os.environ.get("CRAB_LOG_LEVEL", "INFO").upper().strip()
    return _LEVEL_MAP.get(raw, LogLevel.INFO)


def get_logger(use_rich: bool = True, level: LogLevel = None) -> CrabLogger:
    """
    Create a fresh root CrabLogger wired to stdout.

    Parameters
    ----------
    use_rich : bool
        If True (default), use the colorized RichFormatter.
        If False, use PlainFormatter for grep-friendly output.
    level : LogLevel, optional
        Override the log level. If None, reads CRAB_LOG_LEVEL env var.

    Returns a ready-to-use CrabLogger instance.
    """
    if level is None:
        level = _resolve_level()

    formatter = RichFormatter() if use_rich else PlainFormatter()
    handler = StreamHandler(formatter)

    logger = CrabLogger(level=level, handlers=[handler])
    return logger
