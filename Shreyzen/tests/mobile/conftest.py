"""
Mobile test fixtures — emulate a real mobile device for everything under tests/mobile/.

Overrides the framework's `browser_context_args` so each page runs with a mobile
viewport, mobile user-agent, touch, and device-scale-factor taken from
Playwright's built-in device registry. Pick a device with:

    MOBILE_DEVICE="iPhone 13" pytest tests/mobile -v

Defaults to "Pixel 5". Mobile emulation (`is_mobile`) is a Chromium feature, so
it is dropped automatically when running under Firefox.
"""

import os

import pytest

_DEFAULT_DEVICE = os.getenv("MOBILE_DEVICE", "Pixel 5")


@pytest.fixture(scope="session")
def mobile_device_name() -> str:
    return _DEFAULT_DEVICE


@pytest.fixture
def browser_context_args(browser_context_args, playwright, browser_name, mobile_device_name):
    """Merge a Playwright device descriptor over the base context args."""
    device = playwright.devices.get(mobile_device_name) or playwright.devices["Pixel 5"]
    # `default_browser_type` is metadata, not a valid new_context() argument.
    device_args = {k: v for k, v in device.items() if k != "default_browser_type"}
    if browser_name == "firefox":
        device_args.pop("is_mobile", None)  # unsupported on Firefox
    return {**browser_context_args, **device_args}
