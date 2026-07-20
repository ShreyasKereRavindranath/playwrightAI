#!/usr/bin/env python3
"""
PlaySight LLM provider manager (terminal).

Choose which LLM provider the framework uses, validate its configuration, and
check live availability — the same operations the UI exposes.

    python tools/llm_config.py list                 # all providers + status
    python tools/llm_config.py status               # the active provider
    python tools/llm_config.py select anthropic     # switch (remembered)
    python tools/llm_config.py validate             # config check (offline)
    python tools/llm_config.py health               # live reachability probe
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.service import LLMService  # noqa: E402


def _print_list(svc: LLMService) -> None:
    current = svc.current_provider_name()
    for p in svc.list_providers():
        mark = "➤" if p["name"] == current else " "
        key = "key" if p["requires_api_key"] else "no-key"
        print(f" {mark} {p['name']:18s} {p['label']:28s} [{p['kind']}, {key}]")
        print(f"     caps: {', '.join(p['capabilities'])}")


def _print_status(svc: LLMService) -> None:
    name = svc.current_provider_name()
    v = svc.validate()
    print(f"Active provider : {name}")
    print(f"Config valid    : {v.ok}  ({v.detail})")
    try:
        h = svc.health()
        print(f"Availability    : {h.availability.value}  ({h.detail})")
        if h.models:
            print(f"Models          : {', '.join(h.models[:8])}")
    except Exception as exc:
        print(f"Health probe    : error ({exc})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the active LLM provider")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("status")
    sub.add_parser("validate")
    sub.add_parser("health")
    p_sel = sub.add_parser("select")
    p_sel.add_argument("name")
    args = parser.parse_args()

    svc = LLMService()
    if args.command == "list":
        _print_list(svc)
    elif args.command == "status":
        _print_status(svc)
    elif args.command == "validate":
        r = svc.validate()
        print(f"{'OK' if r.ok else 'INVALID'}: {r.detail}")
        sys.exit(0 if r.ok else 1)
    elif args.command == "health":
        h = svc.health()
        print(f"{h.availability.value}: {h.detail}")
        sys.exit(0 if h.ok else 1)
    elif args.command == "select":
        r = svc.select_provider(args.name)
        print(f"Selected '{args.name}'. Config {'valid' if r.ok else 'INVALID'}: {r.detail}")


if __name__ == "__main__":
    main()
