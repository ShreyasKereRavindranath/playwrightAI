#!/usr/bin/env python3
"""
Synthetic Test Data Generator CLI — Capability #14

CLI wrapper around utils/test_data_generator.TestDataGenerator.

Usage:
    python tools/generate_data.py "checkout customer with valid credit card" --count 5
    python tools/generate_data.py "invalid login credentials" --count 3 --output data/login_edge_cases.json
    python tools/generate_data.py "user profile" --edge-cases --count 5
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.test_data_generator import TestDataGenerator


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic test data using AI")
    parser.add_argument("description",  help="Plain-English description of the data to generate")
    parser.add_argument("--count",      type=int, default=5, help="Number of records (default: 5)")
    parser.add_argument("--output",     default="", help="Output JSON file path under data/")
    parser.add_argument("--edge-cases", action="store_true", help="Focus on edge/boundary cases")
    parser.add_argument("--schema",     default="", help='JSON schema hint, e.g. \'{"name":"string","age":"int"}\'')
    args = parser.parse_args()

    schema = None
    if args.schema:
        try:
            schema = json.loads(args.schema)
        except json.JSONDecodeError:
            print("ERROR: --schema must be valid JSON")
            sys.exit(1)

    gen = TestDataGenerator()

    print(f"\n🧪 Generating {args.count} data records for: '{args.description}'\n")

    if args.edge_cases:
        records = gen.generate_edge_cases(args.description, count=args.count)
    else:
        output_path = args.output or None
        records = gen.generate(args.description, count=args.count, schema=schema,
                               output_path=output_path)

    if not records:
        print("ERROR: No records generated. Check your OPENAI_API_KEY in config/.env")
        sys.exit(1)

    print(json.dumps(records, indent=2))

    if args.output and not args.edge_cases:
        print(f"\n✅ Saved to {args.output}")
    elif args.edge_cases and args.output:
        dest = Path(args.output)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(records, indent=2))
        print(f"\n✅ Saved to {args.output}")
    else:
        print(f"\n💡 Tip: add --output data/my_data.json to save to a file.")


if __name__ == "__main__":
    main()
