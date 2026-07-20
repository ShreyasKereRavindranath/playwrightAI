"""Ollama local-runtime bootstrapper: detect → install → serve → pull.

Per the user's configuration, installation of the Ollama binary is fully
automatic (downloads and runs the official installer for the detected OS).
Service start and model pull are always automatic. Everything is best-effort
and reports progress via a callback; failures degrade to a clear HealthResult
rather than raising.
"""

from __future__ import annotations

import json
import logging
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

import requests

from ..core.interfaces import LocalBootstrapper
from ..core.metadata import Availability, HealthResult

logger = logging.getLogger("llm.ollama")
_Progress = Callable[[str], None]


class OllamaBootstrapper(LocalBootstrapper):
    def __init__(self, host: str):
        self._host = host.rstrip("/")

    # -- probes --------------------------------------------------------------

    def is_installed(self) -> bool:
        return shutil.which("ollama") is not None

    def is_running(self) -> bool:
        try:
            return requests.get(f"{self._host}/api/version", timeout=2).status_code == 200
        except requests.RequestException:
            return False

    def has_model(self, model: str) -> bool:
        try:
            data = requests.get(f"{self._host}/api/tags", timeout=5).json()
        except Exception:
            return False
        base = model.split(":")[0]
        return any((m.get("name", "") == model or m.get("name", "").split(":")[0] == base)
                   for m in data.get("models", []))

    # -- actions -------------------------------------------------------------

    def install(self, progress: _Progress) -> bool:
        system = platform.system()
        progress(f"Ollama not found — installing automatically for {system}…")
        try:
            if system == "Linux":
                subprocess.run("curl -fsSL https://ollama.com/install.sh | sh",
                               shell=True, check=True)
            elif system == "Darwin":
                if shutil.which("brew"):
                    subprocess.run(["brew", "install", "--cask", "ollama"], check=True)
                else:
                    return self._download_and_open(
                        "https://ollama.com/download/Ollama-darwin.zip", progress)
            elif system == "Windows":
                exe = self._download("https://ollama.com/download/OllamaSetup.exe", progress)
                if exe:
                    subprocess.run([exe, "/VERYSILENT", "/NORESTART"], check=False)
            else:
                progress(f"Unsupported OS '{system}'. Install manually: https://ollama.com/download")
                return False
        except Exception as exc:
            progress(f"Auto-install failed ({exc}). Install manually: https://ollama.com/download")
            return False
        return self.is_installed()

    def start_service(self, progress: _Progress) -> bool:
        if self.is_running():
            return True
        progress("Starting the Ollama service…")
        try:
            proc = subprocess.Popen(["ollama", "serve"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                from utils import process_registry
                process_registry.register(proc, "ollama-serve")
            except Exception:
                pass
        except Exception as exc:
            progress(f"Could not start Ollama service: {exc}")
            return False
        for _ in range(30):
            if self.is_running():
                return True
            time.sleep(1)
        return self.is_running()

    def pull_model(self, model: str, progress: _Progress) -> bool:
        progress(f"Downloading model '{model}' (first run may take a while)…")
        try:
            with requests.post(f"{self._host}/api/pull", json={"name": model, "stream": True},
                               stream=True, timeout=None) as resp:
                last = -1
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except Exception:
                        continue
                    if evt.get("total"):
                        pct = int(evt.get("completed", 0) / evt["total"] * 100)
                        if pct != last:  # throttle progress noise
                            progress(f"{evt.get('status', 'downloading')}: {pct}%")
                            last = pct
                    elif evt.get("status"):
                        progress(evt["status"])
        except Exception as exc:
            progress(f"Model pull failed: {exc}")
            return False
        return self.has_model(model)

    # -- orchestration -------------------------------------------------------

    def ensure_ready(self, model: str, progress: Optional[_Progress] = None) -> HealthResult:
        progress = progress or logger.info
        if not self.is_installed():
            if not self.install(progress):
                return HealthResult(Availability.NOT_INSTALLED,
                                    "Ollama is not installed. See https://ollama.com/download")
        if not self.start_service(progress):
            return HealthResult(Availability.UNREACHABLE, "Ollama service did not start.")
        if model and not self.has_model(model):
            if not self.pull_model(model, progress):
                return HealthResult(Availability.ERROR, f"Model '{model}' could not be downloaded.")
        progress("Ollama is ready.")
        return HealthResult(Availability.AVAILABLE, "Ollama ready.",
                            models=[model] if model else [])

    # -- download helpers ----------------------------------------------------

    def _download(self, url: str, progress: _Progress) -> Optional[str]:
        try:
            dest = Path(tempfile.gettempdir()) / Path(url).name
            progress(f"Downloading {url} …")
            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(dest, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
            return str(dest)
        except Exception as exc:
            progress(f"Download failed: {exc}")
            return None

    def _download_and_open(self, url: str, progress: _Progress) -> bool:
        path = self._download(url, progress)
        if not path:
            return False
        try:
            subprocess.run(["open", path], check=False)  # macOS: mount/launch installer
        except Exception:
            pass
        progress("Complete the Ollama installer that just opened, then retry.")
        return self.is_installed()
