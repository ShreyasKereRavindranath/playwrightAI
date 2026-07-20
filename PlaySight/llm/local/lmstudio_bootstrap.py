"""LM Studio bootstrapper: detect the local server, auto-start via `lms` if
present, otherwise guide the user through the minimum setup. LM Studio has no
reliable headless installer, so (unlike Ollama) the app itself is not
auto-installed — we detect, start the server when possible, and instruct.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

import requests

from ..core.interfaces import LocalBootstrapper
from ..core.metadata import Availability, HealthResult

logger = logging.getLogger("llm.lmstudio")
_Progress = Callable[[str], None]

_SETUP_STEPS = (
    "LM Studio local server isn't reachable. To enable it:\n"
    "  1. Install LM Studio from https://lmstudio.ai\n"
    "  2. Download a model in the app (Search tab).\n"
    "  3. Go to the Developer tab → Start Server (default port 1234).\n"
    "Then retry — PlaySight will connect automatically."
)


class LMStudioBootstrapper(LocalBootstrapper):
    def __init__(self, base_url: str):
        self._base = base_url.rstrip("/")

    def has_cli(self) -> bool:
        return shutil.which("lms") is not None

    def is_installed(self) -> bool:
        if self.has_cli():
            return True
        candidates = ["/Applications/LM Studio.app",
                      str(Path.home() / "AppData/Local/LM-Studio"),
                      str(Path.home() / ".lmstudio")]
        return any(Path(c).exists() for c in candidates)

    def is_running(self) -> bool:
        try:
            return requests.get(f"{self._base}/models", timeout=2).status_code == 200
        except requests.RequestException:
            return False

    def loaded_models(self) -> list[str]:
        try:
            data = requests.get(f"{self._base}/models", timeout=5).json()
            return [m.get("id", "") for m in data.get("data", [])]
        except Exception:
            return []

    def start_server(self, progress: _Progress) -> bool:
        if not self.has_cli():
            return False
        progress("Starting the LM Studio local server (lms server start)…")
        try:
            subprocess.run(["lms", "server", "start"], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            progress(f"Could not start LM Studio server: {exc}")
            return False
        for _ in range(15):
            if self.is_running():
                return True
            time.sleep(1)
        return self.is_running()

    def ensure_ready(self, model: str, progress: Optional[_Progress] = None) -> HealthResult:
        progress = progress or logger.info
        if self.is_running():
            return HealthResult(Availability.AVAILABLE, "LM Studio server reachable.",
                                models=self.loaded_models())
        if self.has_cli() and self.start_server(progress):
            return HealthResult(Availability.AVAILABLE, "LM Studio server started.",
                                models=self.loaded_models())
        availability = Availability.UNREACHABLE if self.is_installed() else Availability.NOT_INSTALLED
        return HealthResult(availability, _SETUP_STEPS)
