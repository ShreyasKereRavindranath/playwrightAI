"""
Record-and-generate — Capability: author tests from a live browser session.

Wraps `playwright codegen` so a user can record a flow in a real browser and get
a framework-shaped Page Object (+ optional smoke test) out the other end,
without hand-writing selectors. Studio drives this from a button; it also works
from the CLI.

Flow:
  1. launch_codegen(url) opens the Playwright Inspector + a browser. The user
     clicks through their flow and closes the browser.
  2. The recorded script is captured to a temp file.
  3. convert_recording() reuses the LLM converter to emit a POM class (and,
     optionally, a smoke test), returning the code as strings (and writing files
     when asked).

Because codegen needs a display, launch_codegen only works where a browser can
open (i.e. the machine running Studio, not a headless CI box).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from utils.llm_client import LLMClient
from utils.logger import get_logger

logger = get_logger(__name__)

_ROOT = Path(__file__).resolve().parent.parent

_SYSTEM = (
    "You are a senior QA automation engineer converting raw Playwright scripts to Page Object Model. "
    "Follow the framework conventions exactly. "
    "Return only valid Python code — no markdown, no prose."
)

_PROMPT = """
Convert this raw Playwright codegen script into a proper Page Object class.

## Framework conventions:
- Class inherits from BasePage (from pages.base_page import BasePage)
- All locators declared as @property methods returning Locator
- Locator priority: data-testid > aria role > stable id > name > CSS class
- Group related actions into one method (e.g., fill username + fill password + click login = login())
- NO assertions inside the Page Object
- NO page.goto() calls — use navigate() method with self.goto(URL)
- Use self.click(), self.fill(), self.select_option() etc. (BasePage helpers)
- Include a URL = "/path" class variable if identifiable
- Include clear docstrings

## Raw recorded script:
```python
{raw_code}
```

## Page name: {page_name}

Generate the complete Page Object class:
"""

_TEST_PROMPT = """
Generate a basic smoke test for this Page Object class.

Page Object file: pages/{page_name}_page.py
Page Object class: {class_name}

## Page Object source:
```python
{po_code}
```

Generate one smoke test function that exercises the main user flow.
Use the framework fixtures (page). Include imports, a @pytest.mark.smoke marker,
and a docstring. Return only valid Python — no markdown.
"""


def class_name_for(page_name: str) -> str:
    return "".join(w.capitalize() for w in page_name.split("_")) + "Page"


def launch_codegen(url: str, *, timeout_s: int = 1800) -> str:
    """
    Open Playwright codegen against `url` and return the recorded Python script.

    Blocks until the user closes the recorder window (or timeout). Raises
    RuntimeError if codegen can't run (e.g. no display / not installed).
    """
    tmp = Path(tempfile.mkstemp(suffix="_recording.py", prefix="shreyzen_")[1])
    cmd = [sys.executable, "-m", "playwright", "codegen", "--target", "python",
           "-o", str(tmp), url]
    logger.info("Launching codegen: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, cwd=str(_ROOT), timeout=timeout_s, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("Playwright CLI not available for codegen.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"codegen exited with an error: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("codegen timed out waiting for the recording to finish.") from exc

    if not tmp.exists():
        raise RuntimeError("codegen produced no recording.")
    code = tmp.read_text(encoding="utf-8")
    try:
        tmp.unlink()
    except OSError:
        pass
    return code


def _strip_fences(code: str) -> str:
    text = (code or "").strip()
    if text.startswith("```"):
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    return text


def convert_recording(
    raw_code: str,
    page_name: str,
    *,
    with_test: bool = True,
    write: bool = False,
) -> dict:
    """
    Convert a raw codegen script into a POM class (+ optional smoke test).

    Returns {page_object, test, page_path, test_path, written: [...] , errors: [...]}.
    When write=True, files are written only if they don't already exist (never
    clobbers hand-written page objects).
    """
    result = {"page_object": "", "test": "", "page_path": f"pages/{page_name}_page.py",
              "test_path": f"tests/web/test_{page_name}.py", "written": [], "errors": []}

    llm = LLMClient()
    if not llm.available:
        result["errors"].append("No LLM provider configured — cannot convert recording.")
        return result

    po_code = _strip_fences(llm.complete(
        prompt=_PROMPT.format(raw_code=raw_code[:6_000], page_name=page_name),
        system=_SYSTEM, max_tokens=1200,
    ))
    result["page_object"] = po_code
    if not po_code:
        result["errors"].append("LLM returned an empty Page Object.")
        return result

    if with_test:
        cname = class_name_for(page_name)
        result["test"] = _strip_fences(llm.complete(
            prompt=_TEST_PROMPT.format(page_name=page_name, class_name=cname, po_code=po_code[:4_000]),
            system=_SYSTEM, max_tokens=800,
        ))

    if write:
        po_dest = _ROOT / result["page_path"]
        if po_dest.exists():
            result["errors"].append(f"{result['page_path']} already exists — not overwritten.")
        else:
            po_dest.write_text(po_code, encoding="utf-8")
            result["written"].append(result["page_path"])
        if with_test and result["test"]:
            t_dest = _ROOT / result["test_path"]
            if t_dest.exists():
                result["errors"].append(f"{result['test_path']} already exists — not overwritten.")
            else:
                t_dest.parent.mkdir(parents=True, exist_ok=True)
                t_dest.write_text(result["test"], encoding="utf-8")
                result["written"].append(result["test_path"])

    return result


def record_and_generate(url: str, page_name: str, *, with_test: bool = True,
                        write: bool = True) -> dict:
    """End-to-end: record in a browser, then convert to POM (+ test)."""
    raw = launch_codegen(url)
    out = convert_recording(raw, page_name, with_test=with_test, write=write)
    out["raw_recording"] = raw
    return out


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Record a browser flow and generate a Page Object (+ smoke test).")
    parser.add_argument("url", help="URL to open in the recorder.")
    parser.add_argument("--page", required=True,
                        help="Page name, e.g. 'login' → pages/login_page.py.")
    parser.add_argument("--no-test", action="store_true", help="Skip smoke-test generation.")
    parser.add_argument("--print-only", action="store_true",
                        help="Print generated code without writing files.")
    args = parser.parse_args(argv)

    print(f"Opening recorder at {args.url} — click through your flow, then close the browser…")
    try:
        out = record_and_generate(args.url, args.page,
                                  with_test=not args.no_test, write=not args.print_only)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1

    print("\n── Page Object ──────────────────────────────")
    print(out["page_object"])
    if out.get("test"):
        print("\n── Smoke Test ───────────────────────────────")
        print(out["test"])
    for w in out.get("written", []):
        print(f"✅ wrote {w}")
    for e in out.get("errors", []):
        print(f"⚠  {e}")
    print("\nReview generated files before committing.")
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
