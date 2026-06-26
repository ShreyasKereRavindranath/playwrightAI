"""
Framework-wide logger factory.

Usage:
    from utils.logger import get_logger
    logger = get_logger(__name__)
"""

import logging
import sys
from pathlib import Path

_LOG_DIR = Path(__file__).parent.parent / "logs_and_reports"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_FORMATTER = logging.Formatter(
    fmt="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """Return a named logger with console + file handlers attached."""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(level)

    # Console handler — INFO and above
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(_FORMATTER)
    logger.addHandler(console)

    # File handler — DEBUG and above
    file_handler = logging.FileHandler(_LOG_DIR / "framework.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_FORMATTER)
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger
