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
    logger.info("Playwright browser '%s' installed.", browser)
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
