#!/usr/bin/env python3
"""Replay or append lifecycle events for a materialized production project."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.diagnostics import DiagnosticError, EXIT_SUCCESS, EXIT_VALIDATION, Finding, emit_findings
from tools.lib.runtime import Runtime


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _finding(rule: str, reason: str, *, file: Path | str, remediation: str) -> Finding:
    return Finding(rule, reason, file=str(file), remediation=remediation)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path, help="Git-external production project")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    subparsers = parser.add_subparsers(dest="command", required=True)
    replay = subparsers.add_parser("replay", help="replay the append-only event log and verify the projection")
    replay.set_defaults(command="replay")
    bootstrap = subparsers.add_parser("bootstrap", help="create the initial HANDOFF_VALIDATED -> PLANNING event when needed")
    bootstrap.add_argument("--occurred-at", required=True)
    bootstrap.add_argument("--actor-kind", choices=("AGENT", "HUMAN", "SYSTEM"), default="SYSTEM")
    bootstrap.add_argument("--actor-id", required=True)
    bootstrap.add_argument("--idempotency-key", default="runtime/bootstrap/1")
    transition = subparsers.add_parser("transition", help="append one guarded lifecycle transition")
    transition.add_argument("--to-state", required=True)
    transition.add_argument("--occurred-at", required=True)
    transition.add_argument("--actor-kind", choices=("AGENT", "HUMAN", "SYSTEM"), required=True)
    transition.add_argument("--actor-id", required=True)
    transition.add_argument("--idempotency-key", required=True)
    transition.add_argument("--reason", required=True)
    transition.add_argument("--payload-json", default="{}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime = Runtime(args.project_root, repository_root())
    try:
        if args.command == "replay":
            state = runtime.replay()
        elif args.command == "bootstrap":
            state = runtime.bootstrap(occurred_at=args.occurred_at, actor_kind=args.actor_kind, actor_id=args.actor_id, idempotency_key=args.idempotency_key)
        else:
            try:
                payload: dict[str, Any] = json.loads(args.payload_json)
            except json.JSONDecodeError as exc:
                raise DiagnosticError(_finding("RUNTIME_PAYLOAD_JSON", str(exc), file="--payload-json", remediation="Pass one JSON object as --payload-json.")) from exc
            if not isinstance(payload, dict):
                raise DiagnosticError(_finding("RUNTIME_PAYLOAD_OBJECT", "--payload-json must decode to a JSON object", file="--payload-json", remediation="Pass a JSON object containing transition guard evidence."))
            state = runtime.transition(to_state=args.to_state, occurred_at=args.occurred_at, actor_kind=args.actor_kind, actor_id=args.actor_id, idempotency_key=args.idempotency_key, reason=args.reason, payload=payload)
        print(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2))
        return EXIT_SUCCESS
    except DiagnosticError as exc:
        emit_findings([exc.finding], output_format=args.format)
        return EXIT_VALIDATION
    except (OSError, KeyError, TypeError, ValueError) as exc:
        emit_findings([_finding("RUNTIME_CLI", str(exc), file=args.project_root, remediation="Correct the runtime input and retry.")], output_format=args.format)
        return EXIT_VALIDATION


if __name__ == "__main__":
    raise SystemExit(main())
