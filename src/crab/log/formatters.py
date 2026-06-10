"""
Output formatters for CRAB log records.

Two formatters are provided:
- RichFormatter: colorized tree-style output using ANSI codes via Rich
- PlainFormatter: simple bracketed text for grep-friendly log files
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .logger import LogRecord

# Level labels, padded to 5 chars for alignment
_LEVEL_LABELS = {
    10: "DEBUG",
    20: "INFO ",
    30: "WARN ",
    40: "ERROR",
    50: "FATAL",
}

# ANSI color codes (256-color safe)
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"

_LEVEL_COLORS = {
    10: "\033[36m",       # cyan for DEBUG
    20: "\033[32m",       # green for INFO
    30: "\033[33m",       # yellow for WARN
    40: "\033[31m",       # red for ERROR
    50: "\033[1;31m",     # bold red for FATAL
}

_SOURCE_COLORS = {
    "CRAB": "\033[34m",   # blue
    "APP": "\033[35m",    # magenta
}

# Tree-drawing characters
_TREE_PIPE = "\033[2m\u2502\033[0m"   # dimmed |
_TREE_TEE = "\033[2m\u251c\u2500\033[0m"  # dimmed |-
_TREE_INDENT = "   "


class RichFormatter:
    """
    Produces colorized, tree-structured log lines with ANSI escape codes.

    Output example:
        [14:32:01] INFO  CRAB  | exp_baseline | Run 1  Launched App 0
        [14:32:04] INFO  APP   | exp_baseline | Run 1 | app_0::a2a_b  0.123, 0.120
    """

    def format(self, record: "LogRecord") -> str:
        level_color = _LEVEL_COLORS.get(record.level, "")
        source_color = _SOURCE_COLORS.get(record.source, "")
        level_label = _LEVEL_LABELS.get(record.level, "?????")

        # Timestamp
        ts = f"{_DIM}[{record.timestamp}]{_RESET}"

        # Level tag
        level_tag = f"{level_color}{level_label}{_RESET}"

        # Source tag
        source_tag = f"{source_color}{record.source:4s}{_RESET}"

        # Context breadcrumb with tree separators
        if record.context_stack:
            ctx_parts = []
            for part in record.context_stack:
                ctx_parts.append(f"{_DIM}{part}{_RESET}")
            context_str = f" {_DIM}\u2502{_RESET} ".join(ctx_parts)
            context_str = f" {_DIM}\u2502{_RESET} {context_str}"
        else:
            context_str = ""

        # Message color: APP source gets dimmed slightly
        if record.source == "APP":
            msg = f"{source_color}{record.message}{_RESET}"
        else:
            msg = record.message

        return f"{ts} {level_tag} {source_tag}{context_str}  {msg}"


class PlainFormatter:
    """
    Produces plain-text log lines with no ANSI codes.

    Output example:
        [14:32:01] [INFO ] [CRAB] [exp_baseline] [Run 1] Launched App 0
        [14:32:04] [INFO ] [APP ] [exp_baseline] [Run 1] [app_0::a2a_b] 0.123
    """

    def format(self, record: "LogRecord") -> str:
        level_label = _LEVEL_LABELS.get(record.level, "?????")
        source_label = f"{record.source:4s}"

        # Context breadcrumb
        if record.context_stack:
            context_str = " ".join(f"[{c}]" for c in record.context_stack)
            context_str = f" {context_str}"
        else:
            context_str = ""

        return f"[{record.timestamp}] [{level_label}] [{source_label}]{context_str} {record.message}"
