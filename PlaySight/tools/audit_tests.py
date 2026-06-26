#!/usr/bin/env python3
"""
LLM-as-Judge Test Auditor CLI — Capability #12

Audits test files for assertion quality, independence, and coverage gaps.
Generates a scored report with actionable findings.

Usage:
    python tools/audit_tests.py                            # audit all tests/
    python tools/audit_tests.py --file tests/test_login.py # single file
    python tools/audit_tests.py --output reports/audit.json
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.llm_judge import LLMJudge


def print_report(reports: list) -> None:
    print("\n" + "═" * 60)
    print("  TEST QUALITY AUDIT REPORT")
    print("═" * 60)

    total_score = 0
    for r in reports:
        grade = r.get("grade", "?")
        score = r.get("score", 0)
        total_score += score
        colour = {"A": "✅", "B": "🟡", "C": "🟠", "D": "🔴", "F": "❌"}.get(grade, "?")
        print(f"\n{colour}  {r.get('file', 'unknown')}  [{grade}] {score}/100")
        print(f"   {r.get('summary', '')}")

        findings = r.get("findings", [])
        if findings:
            print(f"   Findings ({len(findings)}):")
            for f in findings[:5]:  # top 5
                sev = f.get("severity", "?").upper()
                print(f"     [{sev}] {f.get('test_name', '?')} — {f.get('issue', '')}")
                if f.get("suggestion"):
                    print(f"            → {f['suggestion']}")

        missing = r.get("missing_coverage", [])
        if missing:
            print(f"   Missing coverage: {', '.join(missing[:3])}")

    if reports:
        avg = total_score / len(reports)
        print(f"\n{'═'*60}")
        print(f"  Average Score: {avg:.0f}/100  ({len(reports)} files audited)")
        print("═" * 60)


def main():
    parser = argparse.ArgumentParser(description="AI audit of test assertion quality")
    parser.add_argument("--file",   default="", help="Audit a single test file")
    parser.add_argument("--dir",    default="tests/", help="Directory to audit (default: tests/)")
    parser.add_argument("--output", default="logs_and_reports/audit_report.json",
                        help="Output JSON path (default: logs_and_reports/audit_report.json)")
    args = parser.parse_args()

    judge = LLMJudge()

    if args.file:
        print(f"\n🔍 Auditing: {args.file}")
        report = judge.audit_file(args.file)
        reports = [report] if report else []
    else:
        print(f"\n🔍 Auditing all tests in: {args.dir}")
        reports = judge.audit_directory(args.dir)

    if not reports:
        print("No reports generated. Check OPENAI_API_KEY in config/.env")
        sys.exit(1)

    print_report(reports)

    saved = judge.save_report(reports, args.output)
    print(f"\n📄 Full report saved: {saved}")


if __name__ == "__main__":
    main()
