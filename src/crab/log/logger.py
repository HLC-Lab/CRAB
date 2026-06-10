"""
Core context-aware logger for CRAB.

Provides a hierarchical logger that tracks execution context
(worker -> experiment -> run -> app) and routes messages through
pluggable handlers.
"""

import datetime
import threading
from enum import IntEnum
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .handlers import BaseHandler


class LogLevel(IntEnum):
    """Standard log levels, ordered by severity."""
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


class LogSource:
    """Identifies the origin of a log message."""
    CRAB = "CRAB"
    APP = "APP"


class LogRecord:
    """Single log entry carrying all context needed for formatting."""

    __slots__ = ("level", "source", "message", "context_stack", "timestamp")

    def __init__(self, level: LogLevel, source: str, message: str,
                 context_stack: List[str], timestamp: str):
        self.level = level
        self.source = source
        self.message = message
        self.context_stack = context_stack
        self.timestamp = timestamp


class CrabLogger:
    """
    Context-aware logger that supports hierarchical nesting.

    Each call to `enter()` returns a child logger with an extended
    context stack. All children share the same handlers and write lock,
    so output from concurrent apps never interleaves mid-line.
    """

    def __init__(self, level: LogLevel = LogLevel.INFO,
                 handlers: Optional[List["BaseHandler"]] = None,
                 _context: Optional[List[str]] = None,
                 _lock: Optional[threading.Lock] = None):
        self.level = level
        self._handlers: List["BaseHandler"] = handlers or []
        self._context: List[str] = _context or []
        self._lock = _lock or threading.Lock()

    # -- Context management --

    def enter(self, context_name: str) -> "CrabLogger":
        """
        Create a child logger with an additional context level.

        The child shares handlers and lock with the parent, so all
        output is serialized regardless of which thread writes.
        """
        return CrabLogger(
            level=self.level,
            handlers=self._handlers,
            _context=self._context + [context_name],
            _lock=self._lock,
        )

    # -- Handler management --

    def add_handler(self, handler: "BaseHandler") -> None:
        self._handlers.append(handler)

    # -- Core emit --

    def _emit(self, level: LogLevel, source: str, message: str) -> None:
        """Build a record and dispatch to all handlers under the lock."""
        if level < self.level:
            return

        ts = datetime.datetime.now().strftime("%H:%M:%S")
        record = LogRecord(
            level=level,
            source=source,
            message=message,
            context_stack=list(self._context),
            timestamp=ts,
        )

        with self._lock:
            for handler in self._handlers:
                handler.emit(record)

    # -- Convenience methods (CRAB source) --

    def debug(self, message: str) -> None:
        self._emit(LogLevel.DEBUG, LogSource.CRAB, message)

    def info(self, message: str) -> None:
        self._emit(LogLevel.INFO, LogSource.CRAB, message)

    def warning(self, message: str) -> None:
        self._emit(LogLevel.WARNING, LogSource.CRAB, message)

    def error(self, message: str) -> None:
        self._emit(LogLevel.ERROR, LogSource.CRAB, message)

    def critical(self, message: str) -> None:
        self._emit(LogLevel.CRITICAL, LogSource.CRAB, message)

    # -- App output forwarding --

    def app_output(self, stdout: str, stderr: str = "") -> None:
        """
        Forward captured application output through the logger.
        Uses the APP source so formatters can visually distinguish it.
        """
        if stdout and stdout.strip():
            self._emit(LogLevel.INFO, LogSource.APP, stdout.strip())
        if stderr and stderr.strip():
            self._emit(LogLevel.WARNING, LogSource.APP, stderr.strip())

    # -- Live output streaming --

    def stream_process(self, process, app_label: str) -> threading.Thread:
        """
        Start a background thread that reads stdout from a subprocess
        line-by-line and forwards each line through the logger in real time.

        Returns the reader thread (caller may join it after process ends).
        Stderr is captured post-run since it's typically small.
        """
        child = self.enter(app_label)

        def _reader():
            try:
                for raw_line in iter(process.stdout.readline, b""):
                    line = raw_line.decode("utf-8", errors="replace").rstrip()
                    if line:
                        child._emit(LogLevel.INFO, LogSource.APP, line)
            except (ValueError, OSError):
                # Pipe closed or process killed
                pass

        thread = threading.Thread(target=_reader, daemon=True)
        thread.start()
        return thread
