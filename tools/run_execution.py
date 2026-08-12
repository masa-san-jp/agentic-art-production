#!/usr/bin/env python3
"""Initialize or append traceable output, quality, and installation records."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.diagnostics import DiagnosticError, EXIT_SUCCESS, EXIT_VALIDATION, Finding, emit_findings
from tools.lib.execution import ExecutionManager


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("command", choices=("init", "replay", "record-output", "record-quality", "set-installation-plan", "record-installation-result"))
    parser.add_argument("--record-json", type=Path, help="JSON object containing the record to append")
    parser.add_argument("--occurred-at", default=None)
    parser.add_argument("--actor-kind", choices=("AGENT", "HUMAN", "SYSTEM"), default="AGENT")
    parser.add_argument("--actor-id", default="execution-agent")
    parser.add_argument("--idempotency-key", default=None)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def _load_record(path: Path | None) -> dict:
    if path is None:
        raise DiagnosticError(Finding("EXECUTION_RECORD_INPUT", "--record-json is required for record commands", remediation="Provide a JSON file containing one schema-valid record."))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiagnosticError(Finding("EXECUTION_RECORD_INPUT", str(exc), file=str(path), remediation="Provide a readable JSON object.")) from exc
    if not isinstance(value, dict):
        raise DiagnosticError(Finding("EXECUTION_RECORD_INPUT", "record JSON must be an object", file=str(path), remediation="Use one JSON object per record command."))
    return value


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    findings: list[Finding] = []
    try:
        manager = ExecutionManager(args.project_root, repository_root())
        if args.command == "init":
            result = manager.init()
        elif args.command == "replay":
            result = manager.replay()
        else:
            record = _load_record(args.record_json)
            occurred_at = args.occurred_at or datetime.now(timezone.utc).isoformat()
            key = args.idempotency_key or f"execution/{args.command}/{record.get('output_id') or record.get('quality_id') or record.get('installation_plan_id') or record.get('installation_result_id')}/{record.get('revision', 1)}"
            method = {"record-output": manager.record_output, "record-quality": manager.record_quality, "set-installation-plan": manager.record_installation_plan, "record-installation-result": manager.record_installation_result}[args.command]
            result = method(record, occurred_at=occurred_at, actor_kind=args.actor_kind, actor_id=args.actor_id, idempotency_key=key)
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    except DiagnosticError as exc:
        findings.append(exc.finding)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        findings.append(Finding("EXECUTION_CLI", str(exc), remediation="Correct the record or project state and retry."))
    if findings:
        emit_findings(findings, output_format=args.format)
        return EXIT_VALIDATION
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
