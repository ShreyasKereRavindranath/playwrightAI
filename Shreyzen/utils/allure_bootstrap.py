"""
Automatic Allure CLI provisioning (no Homebrew / npm required).

`allure-pytest` only writes the `allure-results/` JSON — rendering the HTML
report needs the separate Allure command-line tool, which normally comes from
`brew install allure`. On a fresh machine without Homebrew that step silently
fails and the Allure view breaks.

This module removes that dependency: it resolves the Allure CLI in this order —

    1. an `allure` already on PATH (Homebrew/npm/manual install — respected),
    2. a copy previously downloaded by us under `.tools/allure/`,
    3. otherwise download the official release tarball straight from GitHub and
       unpack it locally (stdlib urllib + tarfile — no package manager).

The one thing Allure genuinely can't do without is a **JRE** (it's a Java app).
When Java is absent we don't download — we return None and let the caller fall
back to the always-present native reports (pytest-html / Extent). So Allure is a
self-bootstrapping *enhancement*, never a hard prerequisite for a first run.

Mirrors utils/browser_bootstrap.py: session-start friendly, memoised, and it
never raises into the run.
"""

from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

# UI-facing, parseable marker (streamed to the Studio run log → toast), matching
# the browser bootstrap's AUTODL_MARKER convention.
AUTODL_MARKER = "SHREYZEN_AUTODL"

# Pinned to a stable Allure 2 line, compatible with allure-pytest 2.13.x.
_ALLURE_VERSION = "2.29.0"
_DOWNLOAD_URL = (
    "https://github.com/allure-framework/allure2/releases/download/"
    f"{_ALLURE_VERSION}/allure-{_ALLURE_VERSION}.tgz"
)

# Repo-root anchored so it resolves the same regardless of CWD (utils/ → root).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOLS_DIR = _REPO_ROOT / ".tools" / "allure"

# Resolved once per interpreter; _attempted guards against re-downloading on
# every call once a download has failed.
_resolved: Optional[str] = None
_attempted = False


def _bin_name() -> str:
    return "allure.bat" if os.name == "nt" else "allure"


def _local_bin() -> Optional[Path]:
    """Path to a CLI we've already unpacked under .tools/, if present."""
    cand = _TOOLS_DIR / f"allure-{_ALLURE_VERSION}" / "bin" / _bin_name()
    return cand if cand.exists() else None


def _java_present() -> bool:
    return bool(shutil.which("java") or os.environ.get("JAVA_HOME"))


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """extractall with the py3.12 data filter when available (path-traversal safe)."""
    try:
        tar.extractall(dest, filter="data")  # Python ≥3.12
    except TypeError:
        tar.extractall(dest)  # older Python — no filter kwarg


def _download_and_unpack() -> Optional[Path]:
    _TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    logger.warning(
        "%s | Downloading Allure CLI %s — not installed. One-time ~20 MB download "
        "into .tools/allure/ (no Homebrew/npm needed).",
        AUTODL_MARKER, _ALLURE_VERSION,
    )
    tmp = Path(tempfile.mkdtemp()) / f"allure-{_ALLURE_VERSION}.tgz"
    try:
        urllib.request.urlretrieve(_DOWNLOAD_URL, tmp)  # noqa: S310 - fixed GitHub release URL
        with tarfile.open(tmp, "r:gz") as tar:
            _safe_extract(tar, _TOOLS_DIR)
    except Exception as exc:
        logger.error(
            "Allure CLI download failed (%s). Falling back to native reports "
            "(pytest-html / Extent). Install manually if you need Allure: %s",
            exc, _DOWNLOAD_URL,
        )
        return None
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

    local = _local_bin()
    if local:
        try:
            local.chmod(0o755)  # tarball usually preserves this; be safe on POSIX
        except Exception:
            pass
        logger.info("%s | Allure CLI %s ready → %s", AUTODL_MARKER, _ALLURE_VERSION, local)
    return local


def ensure_allure_cli(auto_install: bool = True) -> Optional[str]:
    """Return a usable `allure` executable path, or None if unavailable.

    None means "render with the native reports instead" — every caller treats
    Allure as optional. Safe to call repeatedly: the result is memoised and a
    failed download is attempted at most once per interpreter.
    """
    global _resolved, _attempted

    if _resolved:
        return _resolved

    # 1. Respect an existing system install (brew/npm/manual).
    found = shutil.which("allure")
    if found:
        _resolved = found
        return found

    # 2. A copy we downloaded on a previous run.
    local = _local_bin()
    if local:
        _resolved = str(local)
        return _resolved

    # 3. Download it ourselves (once).
    if not auto_install or _attempted:
        return None
    _attempted = True

    if not _java_present():
        logger.warning(
            "Allure CLI needs a Java runtime (JRE), which wasn't found — skipping "
            "the download. Reports still render via pytest-html / Extent. Install "
            "a JRE (e.g. `brew install openjdk` or your distro's default-jre) to "
            "enable Allure.",
        )
        return None

    local = _download_and_unpack()
    if local:
        _resolved = str(local)
        return _resolved
    return None
