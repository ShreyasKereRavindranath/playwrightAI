"""
Synthetic Test Data Generator — Capability #14

Uses an LLM to generate contextually realistic, schema-consistent test data.
Output is saved as JSON to data/ for direct use in parametrized tests.

CLI: python tools/generate_data.py "checkout customer" --count 5
Programmatic:
    from utils.test_data_generator import TestDataGenerator
    data = TestDataGenerator().generate("invalid login credentials", count=3)
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a test data engineer. Generate realistic, diverse, and valid test data. "
    "Each record must be unique. Avoid trivially fake values like 'test@test.com'. "
    "Always respond with a JSON object containing a 'records' array."
)

_PROMPT = """
Generate {count} unique test data records for the following scenario:

{description}

{schema_hint}

Requirements:
- Each record must be realistic and varied (different names, values, edge cases)
- Include at least one boundary/edge case if applicable
- Return as JSON: {{"records": [...]}}
- Each record should be a flat or nested object matching the scenario

JSON:
"""


class TestDataGenerator:
    """LLM-powered synthetic test data factory."""

    def generate(
        self,
        description: str,
        count: int = 5,
        schema: Optional[dict] = None,
        output_path: Optional[str] = None,
    ) -> list:
        """Generate `count` test data records matching the description.

        Args:
            description: Plain-English description of the data needed.
            count:       Number of records to generate.
            schema:      Optional dict schema hint (field names + types).
            output_path: If given, save as JSON to this path under data/.

        Returns:
            List of dicts. Empty list if LLM unavailable.
        """
        from utils.llm_client import LLMClient

        llm = LLMClient()
        if not llm.available:
            logger.warning("TestDataGenerator: OPENAI_API_KEY not set — returning empty list.")
            return []

        schema_hint = ""
        if schema:
            schema_hint = f"Schema hint: {json.dumps(schema, indent=2)}"

        result = llm.complete_json(
            prompt=_PROMPT.format(
                count=count,
                description=description,
                schema_hint=schema_hint,
            ),
            system=_SYSTEM,
        )

        records = result.get("records", [])
        if not records:
            logger.warning("TestDataGenerator: LLM returned no records for '%s'.", description)
            return []

        logger.info("Generated %d synthetic data records for: %s", len(records), description)

        if output_path:
            dest = Path(output_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(records, indent=2))
            logger.info("Synthetic data saved: %s", dest)

        return records

    def generate_edge_cases(self, description: str, count: int = 5) -> list:
        """Generate edge-case / boundary-condition test data specifically."""
        edge_description = (
            f"{description} — focus on: empty strings, null values, "
            "max-length strings, special characters, SQL injection patterns, "
            "XSS strings, negative numbers, future/past dates, unicode."
        )
        return self.generate(edge_description, count=count)
