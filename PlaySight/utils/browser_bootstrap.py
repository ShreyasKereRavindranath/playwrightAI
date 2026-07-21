"""
Automatic Playwright browser provisioning.

Instead of asking users to run `playwright install` by hand, this module
detects — at pytest session start — whether the browser binary required by
the current run is present, and installs it on the fly if it is missing.

The check is cheap (a filesystem stat via Playwright's own resolver) and is
skipped entirely once a per-interpreter marker has been set, so the common
case (browsers already installed) adds no measurable overhead.

Behaviour is controlled by config/.env flags read through Config:
    AUTO_INSTALL_BROWSERS=true|false   (default true)  — master switch
    INSTALL_BROWSER_DEPS=true|false    (default false) — also `--with-deps`
                                                          (Linux/CI; needs root)
"""

import subprocess
import sys
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

# Playwright driver name → the browser-type attribute exposed by sync_playwright.
# "chrome"/"msedge" are branded channels that reuse the chromium driver but
# ship with the OS, so they never need a download.
_BRANDED_CHANNELS = {"chrome", "chrome-beta", "msedge", "msedge-beta", "msedge-dev"}

# Guard so we only probe once per Python process even if called repeatedly.
_checked: set[str] = set()

# Rough on-disk footprint of each Playwright browser bundle, surfaced to the UI
# so users know how much space a one-time auto-download will occupy locally.
_BROWSER_DISK_SIZE = {
    "chromium": "~170 MB",
    "firefox": "~85 MB",
    "webkit": "~70 MB",
}

# Distinctive prefix so the Studio UI can spot an auto-download in the streamed
# run log and surface it as a toast. Do NOT change without updating tools/studio.py.
AUTODL_MARKER = "PLAYSIGHT-AUTODL"


def _binary_present(browser: str) -> bool:
    """Return True if the browser executable Playwright would launch exists."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            btype = getattr(p, browser, None)
            if btype is None:
                # Unknown name — let Playwright raise a clear error later.
                return True
            exe = btype.executable_path
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Could not resolve %s executable path: %s", browser, exc)
        return False
    return bool(exe) and Path(exe).exists()


def _run_install(browser: str, with_deps: bool) -> bool:
    """Invoke `python -m playwright install <browser>`; return success."""
    cmd = [sys.executable, "-m", "playwright", "install"]
    if with_deps:
        cmd.append("--with-deps")
    cmd.append(browser)

    size = _BROWSER_DISK_SIZE.get(browser, "a few hundred MB")
    # UI-facing, parseable marker (streamed to the Studio run log → toast).
    logger.warning(
        "%s | Downloading Playwright '%s' browser — not installed yet. "
        "This is a one-time download that will use %s of local disk.",
        AUTODL_MARKER, browser, size,
    )
    logger.warning(
        "Playwright browser '%s' not found — installing automatically (%s)…",
        browser, " ".join(cmd),
    )
    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.error(
            "Automatic browser install failed for '%s': %s\n"
            "Run it manually:  python -m playwright install %s",
            browser, exc, browser,
        )
        return False
    logger.info(
        "%s | Playwright '%s' browser installed (%s used on disk).",
        AUTODL_MARKER, browser, size,
    )
    return True


def ensure_browser_installed(browser: str, *, with_deps: bool = False) -> None:
    """
    Make sure the given Playwright browser is available, installing it if not.

    Safe to call multiple times: the result is memoised per interpreter, and
    branded channels (chrome/msedge) are treated as already present.
    """
    browser = (browser or "chromium").strip().lower()

    if browser in _checked:
        return
    _checked.add(browser)

    if browser in _BRANDED_CHANNELS:
        logger.debug("Browser '%s' is a system channel — skipping install.", browser)
        return

    if _binary_present(browser):
        logger.debug("Playwright browser '%s' already installed.", browser)
        return

    _run_install(browser, with_deps)
