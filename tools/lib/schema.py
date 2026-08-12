"""JSON Schema loading and validation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, RefResolver

from .diagnostics import DiagnosticError, Finding
from .yaml_io import load_json


COMMON_SCHEMA_ID = "https://example.invalid/agentic-art-production/common.schema.json"


def load_schema(path: Path) -> dict[str, Any]:
    value = load_json(path)
    if not isinstance(value, dict):
        raise DiagnosticError(Finding("SCHEMA_OBJECT_REQUIRED", "schema root must be a JSON object", file=str(path), remediation="Make the schema root an object."))
    try:
        Draft202012Validator.check_schema(value)
    except Exception as exc:  # jsonschema exposes several schema error types
        raise DiagnosticError(Finding("SCHEMA_INVALID", str(exc), file=str(path), remediation="Correct the Draft 2020-12 schema.")) from exc
    return value


def validate_instance(instance: Any, schema: dict[str, Any], *, schema_path: Path, common_schema: dict[str, Any] | None = None) -> list[Finding]:
    store: dict[str, Any] = {}
    if common_schema is not None:
        store[COMMON_SCHEMA_ID] = common_schema
        common_schema_id = common_schema.get("$id")
        if isinstance(common_schema_id, str) and common_schema_id:
            # Received handoffs carry the common schema owned by research.
            # Register its declared ID so validation stays fully offline.
            store[common_schema_id] = common_schema
    resolver = RefResolver.from_schema(schema, store=store)
    validator = Draft202012Validator(schema, resolver=resolver, format_checker=FormatChecker())
    findings: list[Finding] = []
    for error in sorted(validator.iter_errors(instance), key=lambda item: list(item.absolute_path)):
        pointer = "".join(f"/{str(part).replace('~', '~0').replace('/', '~1')}" for part in error.absolute_path) or "/"
        findings.append(
            Finding(
                "SCHEMA_VALIDATION",
                error.message,
                file=str(schema_path),
                location=pointer,
                remediation="Correct the value to satisfy the canonical schema.",
                context={"schema_path": "/" + "/".join(str(part) for part in error.absolute_schema_path)},
            )
        )
    return findings
