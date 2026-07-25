"""
Unit tests for the NL-generation validate-and-repair loop.

The loop is exercised with an **injected** fake validator and fake repair
function, so convergence/attempt-budget behaviour is tested deterministically
without a real pytest subprocess or LLM. One slower test drives the real
`collect_only` gate over a temp file to prove the subprocess wiring works.
"""

import sys
from pathlib import Path

import pytest

from utils import generation_validator as gv


def _files():
    return [
        gv.GenFile(path="pages/x_page.py", code="PAGE_V0", kind="page"),
        gv.GenFile(path="tests/web/test_x.py", code="TEST_V0", kind="test"),
    ]


# ── extract_error ────────────────────────────────────────────────────────────

def test_extract_error_prefers_errors_section():
    out = "collected 0 items\n=== ERRORS ===\nImportError: no module named foo\n"
    err = gv.extract_error(out)
    assert err.startswith("=== ERRORS ===")
    assert "ImportError" in err


def test_extract_error_falls_back_to_tail():
    assert gv.extract_error("just some tail output").endswith("tail output")
    assert gv.extract_error("") == ""


# ── repair_generation: convergence & budget (injected fakes) ─────────────────

def test_repairs_until_valid_then_stops():
    calls = {"validate": 0, "repair": 0}

    def validator(paths):
        calls["validate"] += 1
        # Fail the first validation, pass the second (after one repair).
        return (calls["validate"] >= 2, "=== ERRORS ===\nNameError: foo")

    def repair(code, error, kind):
        calls["repair"] += 1
        return code + "_FIXED"

    files = _files()
    outcome = gv.repair_generation(files, repair, validator=validator,
                                   max_attempts=2, write=False)
    assert outcome.ok is True
    assert outcome.repairs == 1
    assert calls["validate"] == 2
    # One repair round touches every file.
    assert all(f.code.endswith("_FIXED") for f in files)


def test_gives_up_after_max_attempts():
    def always_fail(paths):
        return (False, "=== ERRORS ===\nSyntaxError: bad")

    seen = {"repairs": 0}

    def repair(code, error, kind):
        seen["repairs"] += 1
        return code + "!"

    outcome = gv.repair_generation(_files(), repair, validator=always_fail,
                                   max_attempts=2, write=False)
    assert outcome.ok is False
    assert outcome.repairs == 2                 # capped at max_attempts
    assert "SyntaxError" in outcome.last_error


def test_passes_first_try_does_no_repair():
    def ok_validator(paths):
        return (True, "collected 1 item")

    def repair(code, error, kind):  # pragma: no cover - must never be called
        raise AssertionError("repair should not run when validation passes")

    outcome = gv.repair_generation(_files(), repair, validator=ok_validator,
                                   max_attempts=2, write=False)
    assert outcome.ok is True and outcome.repairs == 0


def test_validator_receives_only_test_paths():
    captured = {}

    def validator(paths):
        captured["paths"] = list(paths)
        return (True, "")

    gv.repair_generation(_files(), lambda c, e, k: c, validator=validator,
                         max_attempts=1, write=False)
    # The page object is imported by the test, but only the test file is the
    # collection target.
    assert captured["paths"] == ["tests/web/test_x.py"]


def test_empty_repair_keeps_previous_code():
    state = {"n": 0}

    def validator(paths):
        state["n"] += 1
        return (state["n"] >= 2, "err")

    # A repair that returns "" must not blank out the file.
    outcome = gv.repair_generation(_files(), lambda c, e, k: "",
                                   validator=validator, max_attempts=1, write=False)
    assert all(f.code == f"{'PAGE_V0' if f.kind=='page' else 'TEST_V0'}" for f in outcome.files)


# ── real collect_only gate (subprocess) ──────────────────────────────────────

@pytest.mark.unit
def test_collect_only_passes_for_valid_test(tmp_path):
    t = tmp_path / "test_valid_sample.py"
    t.write_text("def test_ok():\n    assert True\n")
    ok, out = gv.collect_only([t], cwd=tmp_path)
    assert ok, out


@pytest.mark.unit
def test_collect_only_fails_for_syntax_error(tmp_path):
    t = tmp_path / "test_broken_sample.py"
    t.write_text("def test_bad(:\n    pass\n")   # deliberate syntax error
    ok, out = gv.collect_only([t], cwd=tmp_path)
    assert not ok
    assert gv.extract_error(out)                 # produced a usable error blob


# ── GeneratedArtifact.write() runs the gate (agent generator path) ───────────

def test_generated_artifact_write_invokes_repair(tmp_path, monkeypatch):
    from agents.schemas import GeneratedArtifact

    # Force the gate on regardless of the ambient .env.
    import config.config as cfg
    monkeypatch.setattr(cfg.Config, "NL_REPAIR_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg.Config, "NL_REPAIR_ATTEMPTS", 1, raising=False)

    captured = {}

    def fake_repair_generation(files, repair_fn, **kw):
        captured["paths"] = [f.path for f in files]
        return gv.RepairOutcome(ok=True, repairs=1, files=files, last_error="")

    monkeypatch.setattr(gv, "repair_generation", fake_repair_generation)
    monkeypatch.setattr(gv, "make_llm_repair_fn", lambda *a, **k: (lambda c, e, k2: c))

    art = GeneratedArtifact(
        scenario_id="s1",
        page_object_path=str(tmp_path / "p_page.py"), page_object_code="# po",
        test_path=str(tmp_path / "test_s1.py"), test_code="# test",
        generated_by="llm",
    )
    written = art.write()
    assert str(tmp_path / "test_s1.py") in written
    assert art.last_validation == {"ok": True, "repairs": 1, "error": ""}
    # Both the page object and the test were handed to the repair loop.
    assert captured["paths"] == [str(tmp_path / "p_page.py"), str(tmp_path / "test_s1.py")]


def test_generated_artifact_write_skips_repair_when_disabled(tmp_path, monkeypatch):
    from agents.schemas import GeneratedArtifact
    import config.config as cfg
    monkeypatch.setattr(cfg.Config, "NL_REPAIR_ENABLED", False, raising=False)

    def boom(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("repair must not run when NL_REPAIR_ENABLED is false")

    monkeypatch.setattr(gv, "repair_generation", boom)
    art = GeneratedArtifact(scenario_id="s2", test_path=str(tmp_path / "test_s2.py"),
                            test_code="# t", generated_by="offline")
    art.write()
    assert art.last_validation is None


def test_generated_artifact_repair_override_forces_on(tmp_path, monkeypatch):
    from agents.schemas import GeneratedArtifact
    import config.config as cfg
    monkeypatch.setattr(cfg.Config, "NL_REPAIR_ENABLED", False, raising=False)
    monkeypatch.setattr(cfg.Config, "NL_REPAIR_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr(gv, "make_llm_repair_fn", lambda *a, **k: (lambda c, e, k2: c))
    monkeypatch.setattr(gv, "repair_generation",
                        lambda files, fn, **kw: gv.RepairOutcome(True, 0, files, ""))
    art = GeneratedArtifact(scenario_id="s3", test_path=str(tmp_path / "test_s3.py"),
                            test_code="# t")
    art.write(repair=True)   # explicit override beats the disabled config
    assert art.last_validation == {"ok": True, "repairs": 0, "error": ""}


# ── record-and-generate path ─────────────────────────────────────────────────

def test_record_convert_runs_repair(tmp_path, monkeypatch):
    from tools import record_generate as rg
    import config.config as cfg

    monkeypatch.setattr(cfg.Config, "NL_REPAIR_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg.Config, "NL_REPAIR_ATTEMPTS", 1, raising=False)
    # Isolate all file writes to a temp tree (never touch the real repo).
    monkeypatch.setattr(rg, "_ROOT", tmp_path)
    (tmp_path / "pages").mkdir()

    # Fake the LLM: converter returns code, repair loop reports success.
    class FakeLLM:
        available = True
        def complete(self, *a, **k):
            return "from pages.base_page import BasePage\nclass LoginPage(BasePage):\n    pass\n"

    monkeypatch.setattr(rg, "LLMClient", lambda *a, **k: FakeLLM())
    monkeypatch.setattr(gv, "make_llm_repair_fn", lambda *a, **k: (lambda c, e, k2: c))
    seen = {}

    def fake_repair(files, fn, **kw):
        seen["kinds"] = sorted(f.kind for f in files)
        return gv.RepairOutcome(ok=True, repairs=0, files=files, last_error="")

    monkeypatch.setattr(gv, "repair_generation", fake_repair)

    out = rg.convert_recording("page.goto('/')", "login", with_test=True, write=True)
    assert out["validation"] == {"ok": True, "repairs": 0, "error": ""}
    assert seen["kinds"] == ["page", "test"]   # both files validated together

