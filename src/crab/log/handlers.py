"""
Output handlers for CRAB log records.

Handlers receive formatted log records and write them to a destination.
Each handler owns a formatter that determines the visual style.
"""

import sys
from typing import Union, TYPE_CHECKING

if TYPE_CHECKING:
    from .logger import LogRecord
    from .formatters import RichFormatter, PlainFormatter


class BaseHandler:
    """Abstract base — subclasses must implement `emit()`."""

    def __init__(self, formatter: Union["RichFormatter", "PlainFormatter"]):
        self.formatter = formatter

    def emit(self, record: "LogRecord") -> None:
        raise NotImplementedError


class StreamHandler(BaseHandler):
    """
    Writes formatted log lines to a stream (stdout by default).

    Under SLURM, stdout is redirected to slurm_output.log, so writing
    here is equivalent to writing to the log file.
    """

    def __init__(self, formatter: Union["RichFormatter", "PlainFormatter"],
                 stream=None):
        super().__init__(formatter)
        self.stream = stream or sys.stdout

    def emit(self, record: "LogRecord") -> None:
        line = self.formatter.format(record)
        self.stream.write(line + "\n")
        self.stream.flush()


class TUIHandler(BaseHandler):
    """
    Routes log records to a Textual RichLog widget via a callback.

    The callback is typically `BenchmarkApp.log_to_tui`, which uses
    `call_from_thread` to safely write from any thread.
    """

    def __init__(self, callback):
        # TUI handler uses its own inline formatting (Rich markup)
        super().__init__(formatter=None)
        self._callback = callback

    def emit(self, record: "LogRecord") -> None:
        line = self._format_for_tui(record)
        self._callback(line)

    @staticmethod
    def _format_for_tui(record: "LogRecord") -> str:
        """Produce Rich markup suitable for Textual's RichLog widget."""
        from .logger import LogLevel, LogSource

        # Level styling
        level_styles = {
            LogLevel.DEBUG: ("dim", "DEBUG"),
            LogLevel.INFO: ("bold green", "INFO "),
            LogLevel.WARNING: ("bold yellow", "WARN "),
            LogLevel.ERROR: ("bold red", "ERROR"),
            LogLevel.CRITICAL: ("bold white on red", "FATAL"),
        }
        style, label = level_styles.get(record.level, ("", "?????"))

        # Source styling
        if record.source == LogSource.APP:
            source_markup = "[magenta]APP [/]"
        else:
            source_markup = "[blue]CRAB[/]"

        # Context breadcrumb
        if record.context_stack:
            ctx = " [dim]|[/] ".join(
                f"[dim]{c}[/]" for c in record.context_stack
            )
            ctx = f" [dim]|[/] {ctx}"
        else:
            ctx = ""

        # Message — APP output gets a distinct color
        if record.source == LogSource.APP:
            msg = f"[magenta]{record.message}[/]"
        else:
            msg = record.message

        return (
            f"[dim]\\[{record.timestamp}][/] "
            f"[{style}]{label}[/] "
            f"{source_markup}{ctx}  {msg}"
        )
