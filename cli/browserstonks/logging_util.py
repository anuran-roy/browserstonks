"""Central logging for BrowserStonks.

Levels (configurable via --verbose/--debug or BROWSERSTONKS_LOG env):
    ERROR   failures only
    WARN    degraded behavior (fallbacks, retries)
    INFO    one line per pipeline step (default)
    DEBUG   payloads and internals

Every subsystem logs through a named child logger ("ws", "chat",
"harness", "plan", "mcp", "session", "config", "plugins") so output like:

    [ws]      client connected 127.0.0.1:54321
    [chat]    turn start opencode/primary (msg 42 chars)
    [harness] opencode server POST /session -> ses_abc123 (0.4s)
    [actions] tab.new https://example.com -> ok
    [chat]    turn complete opencode/primary: reply 120 chars, 1 action(s)

tells you exactly where a request is at any moment.
"""
from __future__ import annotations

import logging
import os
import sys
import time

_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-5s [%(name)s] %(message)s"


class _NameFilter(logging.Filter):
    """Shorten 'browserstonks.chat' -> 'chat' in output."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith("browserstonks."):
            record.name = record.name.replace("browserstonks.", "", 1)
        return True


_DATEFMT = "%H:%M:%S"
_NAMES = ("ws", "stdio", "chat", "harness", "plan", "mcp", "session",
          "config", "plugins", "actions", "answers", "browser")

_configured = False


def setup_logging(verbose: bool = False, debug: bool = False) -> None:
    """Call once at CLI entry. --debug > --verbose > $BROWSERSTONKS_LOG > INFO."""
    global _configured
    if _configured:
        return
    level = logging.DEBUG if debug else logging.INFO if verbose else None
    if level is None:
        env = os.environ.get("BROWSERSTONKS_LOG", "").upper()
        level = getattr(logging, env, None) or logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    handler.addFilter(_NameFilter())
    root = logging.getLogger("browserstonks")
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False
    for name in _NAMES:
        logging.getLogger(f"browserstonks.{name}")
    _configured = True


def log(name: str) -> logging.Logger:
    """Get a child logger (one of _NAMES)."""
    return logging.getLogger(f"browserstonks.{name}")


class Timer:
    """Context manager logging duration: `with Timer(log('harness'), 'op'):`"""

    def __init__(self, logger: logging.Logger, label: str) -> None:
        self.logger = logger
        self.label = label
        self.t0 = 0.0

    def __enter__(self) -> "Timer":
        self.t0 = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        dt = (time.monotonic() - self.t0) * 1000
        if exc_type is None:
            self.logger.debug("%s done (%.0fms)", self.label, dt)
        else:
            self.logger.debug("%s FAILED after %.0fms: %s", self.label, dt, exc)
