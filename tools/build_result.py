#!/usr/bin/env python3
"""Build a deterministic production-result projection from a materialized project."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.diagnostics import DiagnosticError, EXIT_SUCCESS, EXIT_VALIDATION, Finding, emit_findings
from tools.lib.result import build_result


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--result-id", default="PR001")
    parser.add_argument("--generated-at", required=True, help="Injected RFC 3339 result generation timestamp")
    parser.add_argument("--production-commit", default=None, help="40-character production commit; defaults to repository HEAD")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    findings: list[Finding] = []
    try:
        if not re.fullmatch(r"^PR[0-9]{3,}$", args.result_id):
            raise DiagnosticError(Finding("RESULT_ID", "result_id must match PR followed by at least three digits", remediation="Use a result ID such as PR001."))
        if args.production_commit is not None and not re.fullmatch(r"^[0-9a-f]{40}$", args.production_commit):
            raise DiagnosticError(Finding("RESULT_PROVENANCE", "production commit must be a 40-character lowercase SHA", remediation="Pass an immutable Git commit SHA."))
        result, idempotent = build_result(args.project_root, repository_root(), result_id=args.result_id, generated_at=args.generated_at, production_commit=args.production_commit)
        summary = {"result_id": result["result_id"], "production_project_id": result["production_project_id"], "content_sha256": result["integrity"]["content_sha256"], "idempotent": idempotent, "path": str(args.project_root.resolve() / "08_runtime/production-result.yaml")}
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=None if args.format == "json" else 2))
    except DiagnosticError as exc:
        findings.append(exc.finding)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        findings.append(Finding("RESULT_BUILD", str(exc), remediation="Correct the project records and retry result generation."))
    if findings:
        emit_findings(findings, output_format=args.format)
        return EXIT_VALIDATION
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
