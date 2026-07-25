"""
Shared data contracts for the Planner → Generator → Healer agents.

These pydantic models are the *interfaces* between agents: the Planner emits a
TestPlan, the Generator consumes a Scenario, and the Healer emits a HealResult.
Keeping them here (rather than passing loose dicts) means every agent — and the
LLM prompts that populate them — agree on one schema, and plans can be persisted
to / loaded from JSON round-trip safely.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Priority = Literal["P0", "P1", "P2", "P3"]
Marker = Literal["smoke", "regression", "e2e", "negative", "accessibility"]


# ── Planner outputs ─────────────────────────────────────────────────────────

class TestStep(BaseModel):
    """A single Arrange/Act/Assert step in a scenario, in plain language."""

    kind: Literal["arrange", "act", "assert"] = "act"
    description: str = Field(..., description="Human-readable step, e.g. 'click the login button'")
    # Optional hint linking the step to a page-object method (best-effort).
    page: Optional[str] = Field(None, description="Target page object, e.g. 'login'")


class Scenario(BaseModel):
    """One independent, idempotent test scenario (== one pytest function)."""

    id: str = Field(..., description="Stable slug, e.g. 'login_valid_credentials'")
    title: str
    description: str = ""
    priority: Priority = "P2"
    markers: List[Marker] = Field(default_factory=lambda: ["regression"])
    pages: List[str] = Field(default_factory=list, description="Page objects this scenario touches")
    data_needs: List[str] = Field(default_factory=list, description="Test-data keys the scenario requires")
    steps: List[TestStep] = Field(default_factory=list)

    @property
    def feature(self) -> str:
        """Feature slug used for test/data file naming (first page or 'general')."""
        return self.pages[0] if self.pages else "general"


class TestPlan(BaseModel):
    """The Planner's deliverable: a decomposition of a feature into scenarios."""

    feature: str = Field(..., description="The original feature / user story")
    summary: str = ""
    generated_by: Literal["llm", "offline"] = "offline"
    scenarios: List[Scenario] = Field(default_factory=list)

    # ── Persistence helpers ──────────────────────────────────────────────────

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json(), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> "TestPlan":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


# ── Generator outputs ─────────────────────────────────────────────────────────

class GeneratedArtifact(BaseModel):
    """Page Object + test code produced for a single scenario."""

    scenario_id: str
    page_object_path: Optional[str] = None
    page_object_code: Optional[str] = None
    test_path: str
    test_code: str
    generated_by: Literal["llm", "offline"] = "offline"
    # Populated by write() when the validate-and-repair gate runs (Capability 28);
    # excluded from serialization so persisted plans stay unchanged.
    last_validation: Optional[dict] = Field(default=None, exclude=True)

    def write(self, overwrite: bool = False, repair: Optional[bool] = None) -> List[str]:
        """Write artifacts to disk. Returns the list of paths actually written.

        Existing files are never clobbered unless overwrite=True — the framework
        treats generated code as a starting point for human review.

        After writing, the freshly-written files are validated with
        `pytest --collect-only` and repaired by the LLM on failure (Capability 28),
        gated by Config.NL_REPAIR_ENABLED. Pass repair=True/False to override.
        The outcome is recorded on `self.last_validation` (best-effort — a
        validation error never breaks the write).
        """
        written: List[str] = []
        if self.page_object_path and self.page_object_code:
            po = Path(self.page_object_path)
            if overwrite or not po.exists():
                po.parent.mkdir(parents=True, exist_ok=True)
                po.write_text(self.page_object_code, encoding="utf-8")
                written.append(str(po))
        test = Path(self.test_path)
        if overwrite or not test.exists():
            test.parent.mkdir(parents=True, exist_ok=True)
            test.write_text(self.test_code, encoding="utf-8")
            written.append(str(test))

        self._maybe_repair(written, repair)
        return written

    def _maybe_repair(self, written: List[str], repair: Optional[bool]) -> None:
        """Validate-and-repair the written files; record self.last_validation."""
        if str(Path(self.test_path)) not in written:
            return  # only validate when we actually wrote the test
        try:
            from config.config import Config
            do_repair = Config.NL_REPAIR_ENABLED if repair is None else repair
            if not do_repair:
                return
            from utils import generation_validator as gv
            files = []
            if (self.page_object_path and self.page_object_code
                    and str(Path(self.page_object_path)) in written):
                files.append(gv.GenFile(path=self.page_object_path,
                                        code=self.page_object_code, kind="page"))
            files.append(gv.GenFile(path=self.test_path, code=self.test_code, kind="test"))
            outcome = gv.repair_generation(
                files, gv.make_llm_repair_fn(), max_attempts=Config.NL_REPAIR_ATTEMPTS)
            for f in files:  # reflect any repaired code back onto the artifact
                if f.kind == "test":
                    self.test_code = f.code
                elif f.kind == "page":
                    self.page_object_code = f.code
            self.last_validation = {"ok": outcome.ok, "repairs": outcome.repairs,
                                    "error": outcome.last_error if not outcome.ok else ""}
        except Exception:  # pragma: no cover - defensive; never break the write
            self.last_validation = None


# ── Healer outputs ─────────────────────────────────────────────────────────

FailureCategory = Literal[
    "locator_timeout",
    "strict_mode_violation",
    "assertion_mismatch",
    "url_mismatch",
    "hardcoded_wait",
    "import_or_collection_error",
    "unknown",
]


class HealDiagnosis(BaseModel):
    """The Healer's read of a single failure before it proposes a fix."""

    category: FailureCategory = "unknown"
    test_id: str = "unknown"
    file: Optional[str] = None
    failing_symbol: Optional[str] = Field(None, description="Locator string or symbol at fault")
    root_cause: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class HealResult(BaseModel):
    """A concrete, reviewable fix proposal."""

    diagnosis: HealDiagnosis
    explanation: str = ""
    suggested_fix: str = Field("", description="Unified diff or replacement snippet")
    fix_kind: Literal["diff", "locator", "code", "none"] = "none"
    generated_by: Literal["llm", "offline", "rule"] = "rule"
    applied: bool = False

    def to_json(self, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)
