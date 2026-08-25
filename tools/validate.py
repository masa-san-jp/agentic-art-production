#!/usr/bin/env python3
"""Validate the protocol repository or one materialized production project."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - exercised by the CLI entrypoint
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.config import load_config
from tools.lib.canonical import sha256_bytes
from tools.lib.diagnostics import DiagnosticError, EXIT_SUCCESS, EXIT_USAGE, EXIT_VALIDATION, Finding, emit_findings
from tools.lib.schema import load_schema, validate_instance
from tools.lib.security import safe_relative_path
from tools.lib.yaml_io import load_yaml
from tools.lib.planning import validate_planning_project
from tools.lib.prototype import validate_prototype_project
from tools.lib.runtime import validate_runtime_project
from tools.lib.execution import validate_execution_project
from tools.lib.evidence import validate_evidence_project
from tools.lib.result import validate_completion_report, validate_result


REQUIRED_CONFIGS = (
    "project-layout.yaml",
    "vocabularies.yaml",
    "units.yaml",
    "asset-policy.yaml",
    "schema-registry.yaml",
    "approval-policy.yaml",
    "budget-policy.yaml",
    "safety-policy.yaml",
    "retention-policy.yaml",
    "stopping-policy.yaml",
    "runtime-policy.yaml",
)
REQUIRED_SCHEMAS = (
    "common.schema.json",
    "production-project.schema.json",
    "production-result.schema.json",
    "completion-report.schema.json",
    "handoff-receipt.schema.json",
    "approval.schema.json",
    "runtime-event.schema.json",
    "diagnostic.schema.json",
    "asset-reference.schema.json",
    "planning.schema.json",
    "production-plan.schema.json",
    "runtime-lease.schema.json",
    "runtime-task.schema.json",
    "runtime-effect.schema.json",
    "output-version.schema.json",
    "output-versions.schema.json",
    "quality-result.schema.json",
    "quality-results.schema.json",
    "installation-plan.schema.json",
    "installation-result.schema.json",
    "installation-results.schema.json",
    "execution-event.schema.json",
    "evidence-record.schema.json",
    "evidence-event.schema.json",
    "evidence-register.schema.json",
)
PROJECT_ID = re.compile(r"^production/[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){1,62}[a-z0-9]$")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _finding(rule: str, reason: str, *, file: Path | str = "", location: str | None = None, remediation: str) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation)


def validate_repository(root: Path | None = None) -> list[Finding]:
    root = (root or repository_root()).resolve()
    findings: list[Finding] = []
    required_files = [
        root / "AGENTS.md",
        root / "PLANS.md",
        root / "README.md",
        root / "requirements.txt",
        root / ".github/workflows/validate.yml",
    ]
    required_files.extend(root / "config" / name for name in REQUIRED_CONFIGS)
    required_files.extend(root / "schemas" / name for name in REQUIRED_SCHEMAS)
    for path in required_files:
        if not path.is_file():
            findings.append(_finding("REPOSITORY_STRUCTURE", "required bootstrap file is missing", file=path.relative_to(root), remediation="Create the file required by BOOTSTRAP-001."))

    configs: dict[str, Any] = {}
    for name in REQUIRED_CONFIGS:
        path = root / "config" / name
        if not path.is_file():
            continue
        try:
            value = load_yaml(path)
        except DiagnosticError as exc:
            findings.append(exc.finding)
            continue
        if not isinstance(value, dict):
            findings.append(_finding("CONFIG_OBJECT_REQUIRED", "configuration root must be a YAML object", file=path.relative_to(root), remediation="Make the configuration root a mapping."))
        else:
            configs[name] = value

    common_schema: dict[str, Any] | None = None
    for name in REQUIRED_SCHEMAS:
        path = root / "schemas" / name
        if not path.is_file():
            continue
        try:
            schema = load_schema(path)
            if name == "common.schema.json":
                common_schema = schema
        except DiagnosticError as exc:
            findings.append(exc.finding)

    registry = configs.get("schema-registry.yaml")
    if isinstance(registry, dict) and isinstance(registry.get("schemas"), list):
        for index, entry in enumerate(registry["schemas"]):
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                findings.append(_finding("SCHEMA_REGISTRY_ENTRY", "schema registry item must contain path", file=root / "config/schema-registry.yaml", location=f"/schemas/{index}", remediation="Add a repository-relative schema path."))
                continue
            status = entry.get("status")
            schema_path = root / entry["path"]
            if not schema_path.is_file() and status != "PENDING_EXTERNAL_SNAPSHOT":
                findings.append(_finding("SCHEMA_REGISTRY_PATH", "registered schema path does not exist", file=root / "config/schema-registry.yaml", location=f"/schemas/{index}/path", remediation="Create the snapshot or mark the external dependency pending."))
            elif schema_path.is_file():
                try:
                    load_schema(schema_path)
                except DiagnosticError as exc:
                    findings.append(exc.finding)
                if status == "LOCAL" and entry.get("sha256") is not None and entry.get("sha256") != sha256_bytes(schema_path.read_bytes()):
                    findings.append(_finding("SCHEMA_REGISTRY_HASH", "registered schema raw SHA-256 does not match the local snapshot", file=root / "config/schema-registry.yaml", location=f"/schemas/{index}/sha256", remediation="Re-register the immutable schema snapshot from its source commit."))

    findings.extend(_check_repository_safety(root, configs.get("safety-policy.yaml", {})))
    if common_schema is not None:
        findings.extend(_check_reference_schema_instances(root, common_schema))
    return findings


def _check_repository_safety(root: Path, policy: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    forbidden_extensions = {str(item).lower() for item in policy.get("forbidden_extensions", [])}
    scan_roots = [root / name for name in ("config", "schemas", "templates", "tools", "tests", "execution", "data")]
    for scan_root in scan_roots:
        if not scan_root.exists():
            continue
        for path in scan_root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                findings.append(_finding("REPOSITORY_SYMLINK", "repository safety scan found a symlink", file=relative, remediation="Remove symlinks from canonical repository inputs."))
                continue
            if path.is_file():
                if path.suffix.lower() in forbidden_extensions:
                    findings.append(_finding("FORBIDDEN_EXTENSION", f"file extension {path.suffix!r} is forbidden", file=relative, remediation="Store large or sensitive assets as opaque external references."))
                try:
                    safe_relative_path(relative, max_bytes=int(policy.get("max_path_bytes", 240)), max_depth=int(policy.get("max_path_depth", 8)))
                except DiagnosticError as exc:
                    findings.append(exc.finding)
                if path.suffix.lower() in {".yaml", ".yml", ".json", ".jsonl", ".md"}:
                    try:
                        text = path.read_text(encoding="utf-8")
                    except (OSError, UnicodeError) as exc:
                        findings.append(_finding("TEXT_READ", str(exc), file=relative, remediation="Keep canonical text files UTF-8 encoded."))
                    else:
                        if path.name != "safety-policy.yaml" and ("PRIVATE_RAW" in text or "RESTRICTED" in text):
                            findings.append(_finding("PRIVATE_MARKER", "repository text contains a prohibited classification marker", file=relative, remediation="Use a fixture-safe derived value or an external opaque reference."))
    return findings


def _check_reference_schema_instances(root: Path, common_schema: dict[str, Any]) -> list[Finding]:
    """Validate local JSON examples if they exist; domain fixtures belong to later tasks."""

    findings: list[Finding] = []
    for path in (root / "schemas").glob("*.schema.json"):
        try:
            load_schema(path)
        except DiagnosticError as exc:
            findings.append(exc.finding)
    return findings


def validate_project(project_root: Path, repository: Path | None = None) -> list[Finding]:
    repository = (repository or repository_root()).resolve()
    project_root = project_root.resolve()
    findings: list[Finding] = []
    manifest_path = project_root / "manifest.yaml"
    if not manifest_path.is_file():
        return [_finding("PROJECT_MANIFEST_MISSING", "project manifest.yaml is missing", file=manifest_path, remediation="Generate the project with tools/new_production.py.")]
    try:
        manifest = load_yaml(manifest_path)
    except DiagnosticError as exc:
        return [exc.finding]
    if not isinstance(manifest, dict):
        return [_finding("PROJECT_MANIFEST_OBJECT", "project manifest must be a YAML object", file=manifest_path, remediation="Regenerate the project manifest.")]
    schema_path = repository / "schemas/production-project.schema.json"
    common_path = repository / "schemas/common.schema.json"
    if schema_path.is_file() and common_path.is_file():
        try:
            schema = load_schema(schema_path)
            common = load_schema(common_path)
            findings.extend(validate_instance(manifest, schema, schema_path=schema_path, common_schema=common))
        except DiagnosticError as exc:
            findings.append(exc.finding)
    project_id = manifest.get("project_id")
    if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
        findings.append(_finding("PROJECT_ID", "project_id does not match the canonical production/<slug> pattern", file=manifest_path, location="/project_id", remediation="Use a lowercase production slug with no leading or trailing hyphen."))
    elif project_root.name != project_id.split("/", 1)[1]:
        findings.append(_finding("PROJECT_PATH_ID", "project directory name does not match project_id", file=manifest_path, location="/project_id", remediation="Move the project to output-root/production/<slug> or regenerate it."))
    layout = load_config(repository, "project-layout.yaml")
    for directory in layout.get("directories", []):
        path = project_root / str(directory)
        if not path.is_dir():
            findings.append(_finding("PROJECT_LAYOUT", "required project directory is missing", file=path, remediation="Regenerate the project layout from config/project-layout.yaml."))
    receipt_path = project_root / "00_handoff/handoff-receipt.yaml"
    receipt_schema_path = repository / "schemas/handoff-receipt.schema.json"
    if not receipt_path.is_file():
        findings.append(_finding("RECEIPT_MISSING", "handoff receipt is missing", file=receipt_path, remediation="Accept the handoff through tools/new_production.py."))
    elif receipt_schema_path.is_file():
        try:
            receipt = load_yaml(receipt_path)
            receipt_schema = load_schema(receipt_schema_path)
            common = load_schema(repository / "schemas/common.schema.json")
            findings.extend(validate_instance(receipt, receipt_schema, schema_path=receipt_schema_path, common_schema=common))
        except DiagnosticError as exc:
            findings.append(exc.finding)
    findings.extend(_check_project_files(project_root, repository))
    findings.extend(validate_planning_project(project_root, repository))
    findings.extend(validate_prototype_project(project_root, repository))
    findings.extend(validate_runtime_project(project_root, repository))
    findings.extend(validate_execution_project(project_root, repository))
    findings.extend(validate_evidence_project(project_root, repository))
    result_value = None
    result_path = project_root / "08_runtime/production-result.yaml"
    if result_path.is_file():
        try:
            result_value = load_yaml(result_path)
            if not isinstance(result_value, dict):
                findings.append(_finding("RESULT_INPUT_OBJECT", "production-result.yaml must be a mapping", file=result_path, remediation="Regenerate the production result from canonical records."))
            else:
                validate_result(result_value, repository=repository, result_path=result_path)
        except DiagnosticError as exc:
            findings.append(exc.finding)
    completion_report = project_root / "08_runtime/completion-report.json"
    if completion_report.is_file():
        try:
            report_value = load_yaml(completion_report)
            if isinstance(report_value, dict) and report_value.get("status") != "OPEN" and result_value is None:
                findings.append(_finding("COMPLETION_REPORT_RESULT_MISSING", "terminal completion report exists without production-result.yaml", file=completion_report, remediation="Generate the hash-addressed production result before accepting a terminal completion report."))
            validate_completion_report(report_value, repository=repository, report_path=completion_report, expected_result=result_value if isinstance(result_value, dict) else None)
        except DiagnosticError as exc:
            findings.append(exc.finding)
    return findings


def _check_project_files(project_root: Path, repository: Path) -> list[Finding]:
    policy = load_config(repository, "safety-policy.yaml")
    findings: list[Finding] = []
    forbidden_extensions = {str(item).lower() for item in policy.get("forbidden_extensions", [])}
    for path in project_root.rglob("*"):
        relative = path.relative_to(project_root).as_posix()
        if path.is_symlink():
            findings.append(_finding("PROJECT_SYMLINK", "project contains a symlink", file=relative, remediation="Use regular files and opaque external references."))
            continue
        if path.is_file() and path.suffix.lower() in forbidden_extensions:
            findings.append(_finding("FORBIDDEN_EXTENSION", f"file extension {path.suffix!r} is forbidden", file=relative, remediation="Remove the asset body and retain URI, version, hash, and rights status."))
    return findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="validate the repository contracts")
    group.add_argument("--project-root", type=Path, help="validate a materialized production project")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        findings = validate_repository() if args.check else validate_project(args.project_root)
    except DiagnosticError as exc:
        findings = [exc.finding]
    except (OSError, TypeError, ValueError) as exc:
        findings = [_finding("VALIDATOR_ERROR", str(exc), remediation="Correct the local configuration and retry validation.")]
    emit_findings(findings, output_format=args.format)
    return EXIT_SUCCESS if not findings else EXIT_VALIDATION


if __name__ == "__main__":
    raise SystemExit(main())
