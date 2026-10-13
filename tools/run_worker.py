#!/usr/bin/env python3
"""Run one bounded provider-neutral worker invocation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.agent_harness import run_invocation
from tools.lib.diagnostics import DiagnosticError, EXIT_VALIDATION, emit_findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--lease-token", required=True)
    parser.add_argument("--context-sha256", required=True)
    parser.add_argument("--adapter-profile", default="scripted-fake")
    parser.add_argument("--invocation-id", required=True)
    parser.add_argument("--started-at", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = Path(__file__).resolve().parents[1]
    try:
        invocation, actions = run_invocation(args.project_root, repository, run_id=args.run_id, task_id=args.task_id, lease_token=args.lease_token, context_sha256=args.context_sha256, adapter_profile_id=args.adapter_profile, invocation_id=args.invocation_id, started_at=args.started_at)
    except DiagnosticError as exc:
        emit_findings([exc.finding], output_format="json")
        return EXIT_VALIDATION
    print(json.dumps({"invocation_id": invocation["invocation_id"], "status": invocation["status"], "action_count": len(actions), "response_sha256": invocation["response_sha256"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
