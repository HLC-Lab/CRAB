"""
Output handlers for CRAB log records.

Handlers receive formatted log records and write them to a destination.
Each handler owns a formatter that determines the visual style.
"""

import sys
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from .formatters import PlainFormatter, RichFormatter
    from .logger import LogRecord


class BaseHandler:
    """Abstract base: subclasses must implement `emit()`."""

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

    def __init__(self, formatter: Union["RichFormatter", "PlainFormatter"], stream=None):
        super().__init__(formatter)
        self.stream = stream or sys.stdout

    def emit(self, record: "LogRecord") -> None:
        line = self.formatter.format(record)
        self.stream.write(line + "\n")
        self.stream.flush()
