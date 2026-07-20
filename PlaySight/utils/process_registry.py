"""Global registry of spawned child processes for graceful shutdown.

Anything the app starts in the background (mock API servers, load/functional
test subprocesses, `ollama serve`) registers here. On UI shutdown — normal exit,
SIGINT, or SIGTERM — every tracked process is terminated, then killed if it
doesn't exit, so nothing is left hanging when the Studio is closed.
"""

from __future__ import annotations

import atexit
import logging
import signal
import subprocess
import threading
from typing import Optional

logger = logging.getLogger("studio.procs")

_procs: list[tuple[str, "subprocess.Popen"]] = []
_lock = threading.Lock()
_installed = False


def register(proc: "subprocess.Popen", label: str = "") -> "subprocess.Popen":
    if proc is None:
        return proc
    with _lock:
        _procs.append((label, proc))
    logger.debug("registered process %s (pid=%s)", label, getattr(proc, "pid", "?"))
    return proc


def unregister(proc: "subprocess.Popen") -> None:
    with _lock:
        _procs[:] = [(l, p) for (l, p) in _procs if p is not proc]


def active() -> list[dict]:
    """Snapshot of tracked processes and whether they're still running."""
    with _lock:
        return [{"label": label, "pid": getattr(p, "pid", None), "running": p.poll() is None}
                for label, p in _procs]


def shutdown_all(grace: float = 5.0) -> None:
    """Terminate every tracked process; kill any that don't exit in `grace`."""
    with _lock:
        procs = list(_procs)
        _procs.clear()
    for label, proc in procs:
        if proc.poll() is not None:
            continue
        logger.info("Stopping %s (pid=%s)…", label or "process", proc.pid)
        try:
            proc.terminate()
        except Exception:
            pass
    for _, proc in procs:
        if proc.poll() is not None:
            continue
        try:
            proc.wait(timeout=grace)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def install_signal_handlers() -> None:
    """Ensure shutdown_all runs on exit and on SIGINT/SIGTERM (idempotent)."""
    global _installed
    if _installed:
        return
    _installed = True
    atexit.register(shutdown_all)

    def _handler(signum, _frame):
        logger.info("Signal %s received — shutting down background processes.", signum)
        shutdown_all()
        raise SystemExit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass  # not on the main thread — atexit still covers us
