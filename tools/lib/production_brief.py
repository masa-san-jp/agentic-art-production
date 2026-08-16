"""Validation and loading for the structured production brief."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .diagnostics import DiagnosticError, Finding
from .schema import load_schema, validate_instance
from .yaml_io import load_yaml


BRIEF_RELATIVE_PATH = Path("artifacts/production-brief.yaml")


def _finding(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation)


def validate_production_brief(brief: Any, *, repository: Path, brief_path: Path | str) -> list[Finding]:
    """Validate the wire shape and reject a non-argument masquerading as a claim."""
    schema_path = repository / "schemas/production-brief.schema.json"
    try:
        schema = load_schema(schema_path)
    except DiagnosticError as exc:
        return [exc.finding]
    findings = validate_instance(brief, schema, schema_path=schema_path)
    message = brief.get("message") if isinstance(brief, dict) else None
    who_disagrees = message.get("who_disagrees") if isinstance(message, dict) else None
    normalized = " ".join(str(who_disagrees or "").split()).casefold()
    no_counterargument = {
        "なし", "特にいない", "特にいません", "誰もいない", "誰も反対しない", "反対者はいない",
        "none", "no one", "nobody", "no-one",
    }
    if normalized in no_counterargument or not normalized:
        findings.append(_finding(
            "PRODUCTION_BRIEF_COUNTERARGUMENT",
            "message.who_disagrees must name a plausible opposing position; an unopposed sentence is not a claim",
            file=brief_path,
            location="/message/who_disagrees",
            remediation="Name a person, group, discourse, or position that could reasonably disagree with the claim.",
        ))
    return findings


def load_production_brief(path: Path, *, repository: Path, required: bool = True) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise DiagnosticError(_finding(
                "PRODUCTION_BRIEF_MISSING",
                "accepted handoff does not contain the structured production brief",
                file=path,
                remediation="Export artifacts/production-brief.yaml with the accepted handoff.",
            ))
        return {}
    brief = load_yaml(path)
    findings = validate_production_brief(brief, repository=repository, brief_path=path)
    if findings:
        raise DiagnosticError(findings[0])
    if not isinstance(brief, dict):
        raise DiagnosticError(_finding(
            "PRODUCTION_BRIEF_OBJECT",
            "production brief must be a YAML mapping",
            file=path,
            remediation="Regenerate production-brief.yaml as a structured mapping.",
        ))
    return brief
