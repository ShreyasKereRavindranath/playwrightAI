"""
Visual Regression Testing — Capability #4

Compares page screenshots against stored baselines using perceptual hashing
(imagehash.phash). Tolerates minor anti-aliasing and rendering differences
while catching real visual regressions.

First run: screenshot saved as baseline → test always passes.
Subsequent runs: diff computed; test fails if Hamming distance > threshold.

Baselines: data/visual_baselines/{name}.png
Diffs:     logs_and_reports/visual_diffs/run_{RUN_TS}/{name}_diff.png
"""

import logging
from io import BytesIO
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_BASELINE_DIR = Path("data/visual_baselines")
_DIFF_BASE    = Path("logs_and_reports/visual_diffs")


class VisualRegression:
    """Perceptual-hash visual diff engine."""

    def __init__(self, run_ts: str):
        self._run_ts  = run_ts
        self._diff_dir = _DIFF_BASE / f"run_{run_ts}"

    # ── Public API ────────────────────────────────────────────────────────────

    def check(self, page, name: str) -> dict:
        """Capture page screenshot and compare against stored baseline.

        Returns a result dict:
            status        : "baseline_created" | "pass" | "fail"
            diff_distance : int  (Hamming distance, 0-64)
            threshold     : int  (configured max)
            baseline_path : str
            diff_path     : str | None  (set only on fail)
            message       : str
        """
        from config.config import Config

        threshold = Config.VISUAL_DIFF_THRESHOLD
        _BASELINE_DIR.mkdir(parents=True, exist_ok=True)

        baseline_path = _BASELINE_DIR / f"{name}.png"
        screenshot_bytes = page.screenshot(full_page=True)

        if not baseline_path.exists():
            baseline_path.write_bytes(screenshot_bytes)
            logger.info("Visual baseline created: %s", baseline_path)
            return {
                "status": "baseline_created",
                "diff_distance": 0,
                "threshold": threshold,
                "baseline_path": str(baseline_path),
                "diff_path": None,
                "message": f"Baseline saved to {baseline_path}. Next run will compare.",
            }

        return self._compare(baseline_path, screenshot_bytes, name, threshold)

    def update_baseline(self, page, name: str) -> str:
        """Force-update the baseline for a given checkpoint name."""
        _BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        path = _BASELINE_DIR / f"{name}.png"
        path.write_bytes(page.screenshot(full_page=True))
        logger.info("Visual baseline updated: %s", path)
        return str(path)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _compare(
        self,
        baseline_path: Path,
        current_bytes: bytes,
        name: str,
        threshold: int,
    ) -> dict:
        try:
            import imagehash
            from PIL import Image

            baseline_img = Image.open(baseline_path)
            current_img  = Image.open(BytesIO(current_bytes))

            baseline_hash = imagehash.phash(baseline_img)
            current_hash  = imagehash.phash(current_img)
            distance = baseline_hash - current_hash

            if distance <= threshold:
                return {
                    "status": "pass",
                    "diff_distance": distance,
                    "threshold": threshold,
                    "baseline_path": str(baseline_path),
                    "diff_path": None,
                    "message": f"Visual match (distance={distance}, threshold={threshold}).",
                }

            # Save a side-by-side diff image for debugging
            diff_path = self._save_diff(baseline_img, current_img, name)
            logger.warning(
                "Visual REGRESSION: %s — distance=%d > threshold=%d. Diff: %s",
                name, distance, threshold, diff_path,
            )
            return {
                "status": "fail",
                "diff_distance": distance,
                "threshold": threshold,
                "baseline_path": str(baseline_path),
                "diff_path": str(diff_path),
                "message": (
                    f"Visual regression detected: distance={distance} > threshold={threshold}. "
                    f"Diff saved to {diff_path}."
                ),
            }
        except ImportError:
            logger.error("imagehash/Pillow not installed. Run: pip install imagehash Pillow")
            return {
                "status": "skip",
                "diff_distance": 0,
                "threshold": threshold,
                "baseline_path": str(baseline_path),
                "diff_path": None,
                "message": "imagehash not installed — visual check skipped.",
            }

    def _save_diff(self, baseline, current, name: str) -> Path:
        """Save a side-by-side baseline | current comparison image."""
        from PIL import Image, ImageDraw

        self._diff_dir.mkdir(parents=True, exist_ok=True)

        # Normalise sizes
        w = max(baseline.width,  current.width)
        h = max(baseline.height, current.height)
        b = baseline.resize((w, h))
        c = current.resize((w, h))

        canvas = Image.new("RGB", (w * 2 + 10, h), (200, 200, 200))
        canvas.paste(b, (0, 0))
        canvas.paste(c, (w + 10, 0))

        draw = ImageDraw.Draw(canvas)
        draw.text((4, 4),      "BASELINE", fill=(255, 0, 0))
        draw.text((w + 14, 4), "CURRENT",  fill=(255, 0, 0))

        dest = self._diff_dir / f"{name}_diff.png"
        canvas.save(str(dest))
        return dest
