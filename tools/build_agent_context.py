#!/usr/bin/env python3
"""Build a hash-addressed task-scoped agent context and capability grant."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.agent_harness import build_context
from tools.lib.diagnostics import DiagnosticError, EXIT_VALIDATION, emit_findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--lease-token", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--actor-kind", choices=("AGENT", "SYSTEM"), default="AGENT")
    parser.add_argument("--actor-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = Path(__file__).resolve().parents[1]
    try:
        context, grant = build_context(args.project_root, repository, task_id=args.task_id, lease_token=args.lease_token, run_id=args.run_id, generated_at=args.generated_at, actor_kind=args.actor_kind, actor_id=args.actor_id)
    except DiagnosticError as exc:
        emit_findings([exc.finding], output_format="json")
        return EXIT_VALIDATION
    print(json.dumps({"context_id": context["context_id"], "context_sha256": context["integrity"]["content_sha256"], "grant_id": grant["grant_id"], "grant_sha256": grant["integrity"]["content_sha256"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
