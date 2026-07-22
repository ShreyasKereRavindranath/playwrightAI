"""
Shreyzen AI Agents — the Planner → Generator → Healer trio.

These three agents form an autonomous test-authoring and maintenance loop:

    Planner   feature / user story        → structured TestPlan (scenarios, steps, priorities)
    Generator TestPlan scenario           → Page Object + pytest test code
    Healer    pytest failure log / error  → diagnosis + concrete fix (diff or locator)

Every agent routes LLM calls through utils.llm_client.LLMClient, but each one
also ships a deterministic OFFLINE fallback so the framework — and the bundled
examples in examples/ — run end-to-end with no API key configured.

Usage:
    from agents import Planner, Generator, Healer

    plan = Planner().plan("User can filter products by price")
    for scenario in plan.scenarios:
        artifact = Generator().generate(scenario)
        artifact.write()
"""

from agents.planner import Planner
from agents.generator import Generator
from agents.healer import Healer
from agents.schemas import (
    TestPlan,
    Scenario,
    TestStep,
    GeneratedArtifact,
    HealDiagnosis,
    HealResult,
)

__all__ = [
    "Planner",
    "Generator",
    "Healer",
    "TestPlan",
    "Scenario",
    "TestStep",
    "GeneratedArtifact",
    "HealDiagnosis",
    "HealResult",
]
