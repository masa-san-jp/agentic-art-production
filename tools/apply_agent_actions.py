#!/usr/bin/env python3
"""Apply worker proposals through the single-writer harness broker."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.agent_harness import apply_actions
from tools.lib.diagnostics import DiagnosticError, EXIT_VALIDATION, emit_findings
from tools.lib.yaml_io import load_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--invocation-id", required=True)
    parser.add_argument("--lease-token", required=True)
    parser.add_argument("--response", type=Path, required=True)
    parser.add_argument("--applied-at", required=True)
    parser.add_argument("--actor-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = Path(__file__).resolve().parents[1]
    try:
        response = load_json(args.response)
        if not isinstance(response, dict) or not isinstance(response.get("actions"), list):
            raise ValueError("response must be an object containing actions")
        result = apply_actions(args.project_root, repository, run_id=args.run_id, invocation_id=args.invocation_id, lease_token=args.lease_token, actions=response["actions"], applied_at=args.applied_at, actor_id=args.actor_id)
    except DiagnosticError as exc:
        emit_findings([exc.finding], output_format="json")
        return EXIT_VALIDATION
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return EXIT_VALIDATION
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
