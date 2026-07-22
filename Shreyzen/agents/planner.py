"""
Planner — Capability: AI Test Planning.

Turns a plain-English feature description or user story into a structured
TestPlan: a set of independent, idempotent scenarios, each with a priority,
pytest markers, the page objects it touches, its data needs, and Arrange/Act/
Assert steps.

    plan = Planner().plan("User can filter products by price low-to-high")

The LLM path asks the model for a JSON plan and validates it against the
schema. The offline path uses a keyword-driven heuristic so the planner is
useful (and the demos runnable) with no API key.
"""

from __future__ import annotations

import logging
from typing import List

from agents.base_agent import BaseAgent
from agents.schemas import Scenario, TestPlan, TestStep

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a principal QA strategist. You decompose a feature into a minimal "
    "but complete set of independent, idempotent test scenarios (happy path, "
    "negative, boundary/edge). You never invent locators or code — you plan."
)

_PROMPT = """
Decompose the following feature into a test plan for a Playwright + pytest suite.

Feature / user story:
{feature}

Known page objects available in this framework: {known_pages}

Return JSON with this exact shape:
{{
  "summary": "one sentence describing the feature's test coverage",
  "scenarios": [
    {{
      "id": "short_snake_case_slug",
      "title": "Human readable scenario title",
      "description": "what this verifies",
      "priority": "P0|P1|P2|P3",
      "markers": ["smoke"|"regression"|"e2e"|"negative"|"accessibility"],
      "pages": ["login", "inventory"],
      "data_needs": ["standard_user"],
      "steps": [
        {{"kind": "arrange|act|assert", "description": "...", "page": "login"}}
      ]
    }}
  ]
}}

Rules:
- One behaviour per scenario. Include at least one happy-path and one negative scenario.
- Priorities: P0 = critical happy path, P1 = important, P2/P3 = edge cases.
- Prefer pages from the known list; only add a new page name if truly needed.
- Steps must be plain language (no code, no locators).
Return ONLY the JSON object.
"""

# Keyword → page-object mapping used by both the LLM prompt (as hints) and the
# offline heuristic. Extend as the framework grows.
_KNOWN_PAGES = {
    "login": ["login", "log in", "sign in", "credential", "password", "username", "authenticate"],
    "inventory": ["inventory", "product", "catalog", "item", "sort", "filter", "listing", "browse"],
    "cart": ["cart", "basket", "add to cart", "remove", "quantity"],
    "checkout": ["checkout", "check out", "purchase", "order", "payment", "shipping", "buy"],
}


class Planner(BaseAgent):
    """Feature description → structured TestPlan."""

    def plan(self, feature: str) -> TestPlan:
        feature = feature.strip()
        if not feature:
            raise ValueError("Planner.plan() requires a non-empty feature description.")

        if self.use_llm:
            plan = self._plan_llm(feature)
            if plan and plan.scenarios:
                return plan
            logger.warning("Planner LLM path returned no scenarios; using offline fallback.")

        return self._plan_offline(feature)

    # ── LLM path ──────────────────────────────────────────────────────────────

    def _plan_llm(self, feature: str) -> TestPlan | None:
        raw = self._llm.complete_json(
            prompt=_PROMPT.format(feature=feature, known_pages=", ".join(_KNOWN_PAGES)),
            system=_SYSTEM,
        )
        if not raw or "scenarios" not in raw:
            return None
        try:
            scenarios = [Scenario.model_validate(s) for s in raw.get("scenarios", [])]
            return TestPlan(
                feature=feature,
                summary=raw.get("summary", ""),
                generated_by="llm",
                scenarios=scenarios,
            )
        except Exception as exc:  # malformed LLM JSON → fall back
            logger.warning("Planner could not validate LLM plan: %s", exc)
            return None

    # ── Offline heuristic path ─────────────────────────────────────────────────

    def _plan_offline(self, feature: str) -> TestPlan:
        pages = self._infer_pages(feature)
        primary = pages[0] if pages else "general"
        base_slug = self.slugify(feature, max_len=40)

        scenarios: List[Scenario] = []

        # 1. Happy path (P0, smoke)
        scenarios.append(Scenario(
            id=f"{base_slug}_happy_path",
            title=f"{feature} — happy path",
            description=f"Verify the primary success path for: {feature}",
            priority="P0",
            markers=["smoke", "regression"],
            pages=pages,
            data_needs=self._infer_data(feature, negative=False),
            steps=self._happy_steps(feature, pages),
        ))

        # 2. Negative / validation path (P1, negative) — always worth covering
        scenarios.append(Scenario(
            id=f"{base_slug}_invalid_input",
            title=f"{feature} — invalid input is rejected",
            description=f"Verify the feature rejects invalid input and shows an error: {feature}",
            priority="P1",
            markers=["regression", "negative"],
            pages=pages,
            data_needs=self._infer_data(feature, negative=True),
            steps=self._negative_steps(feature, pages),
        ))

        # 3. Edge case — only for multi-page flows (e.g. full purchase)
        if len(pages) >= 2:
            scenarios.append(Scenario(
                id=f"{base_slug}_end_to_end",
                title=f"{feature} — end to end",
                description=f"Exercise the full multi-page flow for: {feature}",
                priority="P1",
                markers=["e2e", "regression"],
                pages=pages,
                data_needs=self._infer_data(feature, negative=False),
                steps=self._happy_steps(feature, pages) + [
                    TestStep(kind="assert", description="confirm the final success state persists", page=pages[-1]),
                ],
            ))

        return TestPlan(
            feature=feature,
            summary=f"Coverage for '{feature}' across {', '.join(pages) or 'the application'}.",
            generated_by="offline",
            scenarios=scenarios,
        )

    # ── Heuristic helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _infer_pages(feature: str) -> List[str]:
        text = feature.lower()
        hits = {page for page, kws in _KNOWN_PAGES.items() if any(kw in text for kw in kws)}
        # The AUT gates pages behind a navigation chain: you can't reach checkout
        # without a cart, a cart without inventory, or any of them without login.
        # Expand to the full prerequisite chain so generated flows are reachable.
        if "checkout" in hits:
            hits |= {"login", "inventory", "cart"}
        if "cart" in hits:
            hits |= {"login", "inventory"}
        if "inventory" in hits:
            hits |= {"login"}
        # Emit in flow order rather than set order.
        order = ["login", "inventory", "cart", "checkout"]
        ordered = [p for p in order if p in hits]
        return ordered or ["login"]  # login is the safest default entry point

    @staticmethod
    def _infer_data(feature: str, negative: bool) -> List[str]:
        text = feature.lower()
        needs: List[str] = []
        if any(k in text for k in ["login", "sign in", "user", "credential", "password"]):
            needs.append("invalid_user" if negative else "standard_user")
        if any(k in text for k in ["product", "cart", "checkout", "purchase", "item"]):
            needs.append("backpack")
        if any(k in text for k in ["checkout", "purchase", "order", "payment"]):
            needs.append("valid_customer")
        return needs

    @staticmethod
    def _happy_steps(feature: str, pages: List[str]) -> List[TestStep]:
        steps: List[TestStep] = []
        if "login" in pages:
            steps.append(TestStep(kind="arrange", description="navigate to the login page", page="login"))
            steps.append(TestStep(kind="act", description="log in with valid credentials", page="login"))
        if "inventory" in pages:
            steps.append(TestStep(kind="act", description="add the target product to the cart", page="inventory"))
            steps.append(TestStep(kind="assert", description="the cart badge reflects the added item", page="inventory"))
        if "cart" in pages:
            steps.append(TestStep(kind="act", description="open the cart and verify the item is present", page="cart"))
        if "checkout" in pages:
            steps.append(TestStep(kind="act", description="complete checkout with valid customer info", page="checkout"))
            steps.append(TestStep(kind="assert", description="the order confirmation is shown", page="checkout"))
        if not steps:  # generic fallback
            steps = [
                TestStep(kind="arrange", description=f"set up the preconditions for '{feature}'"),
                TestStep(kind="act", description=f"perform the primary action for '{feature}'"),
                TestStep(kind="assert", description="verify the expected success state"),
            ]
        return steps

    @staticmethod
    def _negative_steps(feature: str, pages: List[str]) -> List[TestStep]:
        entry = pages[0] if pages else None
        return [
            TestStep(kind="arrange", description=f"navigate to the {entry or 'starting'} page", page=entry),
            TestStep(kind="act", description="submit invalid or incomplete input", page=entry),
            TestStep(kind="assert", description="a descriptive error message is displayed", page=entry),
            TestStep(kind="assert", description="the user is not advanced to the next state", page=entry),
        ]
