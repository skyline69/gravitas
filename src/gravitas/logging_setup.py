"""Colored console logging for the gravitas process.

Pure stdlib, imported only by the composition root. Every module already does
``logging.getLogger(__name__)``; this is the one place that decides where those
records go and what they look like. Colors go to a TTY only (and honour
``NO_COLOR``), so redirected output stays clean ANSI-free text.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import IO

_RESET = "\x1b[0m"
_DIM = "\x1b[2m"
_LEVEL_COLORS = {
    logging.DEBUG: "\x1b[36m",  # cyan
    logging.INFO: "\x1b[32m",  # green
    logging.WARNING: "\x1b[33m",  # yellow
    logging.ERROR: "\x1b[31m",  # red
    logging.CRITICAL: "\x1b[1;31m",  # bold red
}


class ColorFormatter(logging.Formatter):
    """``HH:MM:SS LEVEL name: message`` with the level colored, name dimmed."""

    def __init__(self, *, color: bool) -> None:
        super().__init__(datefmt="%H:%M:%S")
        self._color = color

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info and record.exc_info[0] is not None:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        when = self.formatTime(record, self.datefmt)
        level = f"{record.levelname:<8}"
        if self._color:
            color = _LEVEL_COLORS.get(record.levelno, "")
            return (
                f"{_DIM}{when}{_RESET} {color}{level}{_RESET} {_DIM}{record.name}{_RESET} {message}"
            )
        return f"{when} {level} {record.name} {message}"


def _wants_color(stream: IO[str]) -> bool:
    # https://no-color.org/ -- any non-empty value disables color; a dumb
    # terminal or a pipe never gets escape codes.
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return hasattr(stream, "isatty") and stream.isatty()


def configure_logging(stream: IO[str] | None = None) -> None:
    """Install one colored stderr handler on the root logger.

    ``GRAVITAS_LOG_LEVEL`` overrides the INFO default (any name accepted by
    ``logging.getLevelNamesMapping``, case-insensitive). httpx logs a line per
    request at INFO; that is transport noise here, so it and httpcore start at
    WARNING -- a debug run (``GRAVITAS_LOG_LEVEL=DEBUG``) is the one time the
    wire chatter is wanted, so DEBUG lifts them along with everything else.
    """
    out = stream if stream is not None else sys.stderr
    level_name = os.environ.get("GRAVITAS_LOG_LEVEL", "INFO").upper()
    level = logging.getLevelNamesMapping().get(level_name, logging.INFO)

    handler = logging.StreamHandler(out)
    handler.setFormatter(ColorFormatter(color=_wants_color(out)))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    if level > logging.DEBUG:
        for noisy in ("httpx", "httpcore"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
