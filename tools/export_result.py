#!/usr/bin/env python3
"""Export a production result as a minimal, manifest-declared external bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.canonical import canonical_sha256
from tools.lib.diagnostics import DiagnosticError, EXIT_SUCCESS, EXIT_VALIDATION, Finding, emit_findings
from tools.lib.result import _load_mapping, validate_result
from tools.lib.yaml_io import dump_yaml, load_yaml


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _yaml_bytes(value: dict[str, Any]) -> bytes:
    descriptor, temporary = tempfile.mkstemp(prefix=".result-yaml-")
    try:
        os.close(descriptor)
        dump_yaml(value, Path(temporary))
        return Path(temporary).read_bytes()
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _manifest(result: dict[str, Any], result_bytes: bytes) -> dict[str, Any]:
    entries = [{"path": "production-result.yaml", "role": "PRODUCTION_RESULT", "media_type": "application/yaml", "size_bytes": len(result_bytes), "sha256": _sha256_bytes(result_bytes)}]
    return {
        "bundle_schema_version": "1.0.0",
        "bundle_id": f"PR-{result['result_id']}",
        "entrypoint": "production-result.yaml",
        "result_id": result["result_id"],
        "result_sha256": result["integrity"]["content_sha256"],
        "files": entries,
        "integrity": {"file_set_sha256": canonical_sha256(entries)},
    }


def _assert_boundary(target: Path, project_root: Path, repository: Path) -> None:
    target = target.resolve()
    if target == repository or repository in target.parents:
        raise DiagnosticError(Finding("RESULT_EXPORT_BOUNDARY", "result bundle output is inside the protocol repository", file=str(target), remediation="Use a Git-external result bundle directory."))
    if target == project_root or project_root in target.parents:
        raise DiagnosticError(Finding("RESULT_EXPORT_BOUNDARY", "result bundle output is inside the materialized project", file=str(target), remediation="Export the result to a separate Git-external bundle directory."))


def export_result(project_root: Path, output: Path, repository: Path) -> tuple[Path, bool]:
    project_root = project_root.resolve()
    output = output.resolve()
    repository = repository.resolve()
    _assert_boundary(output, project_root, repository)
    result_path = project_root / "08_runtime/production-result.yaml"
    result = _load_mapping(result_path)
    validate_result(result, repository=repository, result_path=result_path)
    result_bytes = _yaml_bytes(result)
    manifest = _manifest(result, result_bytes)
    manifest_bytes = _yaml_bytes(manifest)
    expected_files = {"manifest.yaml": manifest_bytes, "production-result.yaml": result_bytes}
    if output.exists():
        if not output.is_dir():
            raise DiagnosticError(Finding("RESULT_EXPORT_TARGET", "result bundle target exists and is not a directory", file=str(output), remediation="Choose a new output directory or restore the original bundle."))
        actual_files = {path.name: path.read_bytes() for path in output.iterdir() if path.is_file() and path.name != "Icon\r"}
        if actual_files == expected_files:
            return output, True
        raise DiagnosticError(Finding("RESULT_EXPORT_IDEMPOTENCY_MISMATCH", "result bundle target exists with different content", file=str(output), remediation="Use the original result inputs or choose a new output directory."))
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        (staging / "production-result.yaml").write_bytes(result_bytes)
        (staging / "manifest.yaml").write_bytes(manifest_bytes)
        staging.rename(output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output, False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    findings: list[Finding] = []
    try:
        target, idempotent = export_result(args.project_root, args.output, repository_root())
        manifest = load_yaml(target / "manifest.yaml")
        summary = {"path": str(target), "result_id": manifest.get("result_id"), "result_sha256": manifest.get("result_sha256"), "idempotent": idempotent}
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=None if args.format == "json" else 2))
    except DiagnosticError as exc:
        findings.append(exc.finding)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        findings.append(Finding("RESULT_EXPORT", str(exc), remediation="Correct the result or export target and retry."))
    if findings:
        emit_findings(findings, output_format=args.format)
        return EXIT_VALIDATION
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
