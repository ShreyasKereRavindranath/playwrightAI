#!/usr/bin/env python3
"""Backward-compatibility shim — the Load Runner is now **Shreyzen Studio**.

`python tools/studio.py ...` is the current entrypoint; this file keeps the old
`python tools/load_runner.py ...` command working.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.studio import main  # noqa: E402

if __name__ == "__main__":
    main()
