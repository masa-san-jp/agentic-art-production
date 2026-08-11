"""Stable diagnostics and CLI exit codes."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable


EXIT_SUCCESS = 0
EXIT_VALIDATION = 1
EXIT_USAGE = 2
EXIT_EXTERNAL_BLOCKED = 3
EXIT_APPROVAL_REQUIRED = 4


@dataclass(frozen=True)
class Finding:
    rule: str
    reason: str
    file: str = ""
    location: str | None = None
    line: int | None = None
    remediation: str = "Correct the input and run validation again."
    severity: str = "ERROR"
    context: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "rule": self.rule,
            "file": self.file,
            "location": self.location,
            "line": self.line,
            "reason": self.reason,
            "remediation": self.remediation,
            "context": self.context,
        }

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.file, self.location or "", self.rule, self.reason)


class DiagnosticError(Exception):
    """An expected validation or input error represented by one finding."""

    def __init__(self, finding: Finding):
        super().__init__(finding.reason)
        self.finding = finding


def sorted_findings(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(findings, key=Finding.sort_key)


def emit_findings(findings: Iterable[Finding], *, output_format: str = "text") -> None:
    ordered = sorted_findings(findings)
    if output_format == "json":
        print(json.dumps([finding.as_dict() for finding in ordered], ensure_ascii=False, indent=2))
        return
    for finding in ordered:
        location = f"#{finding.location}" if finding.location else ""
        line = f":{finding.line}" if finding.line else ""
        target = f"{finding.file}{line}{location}" if finding.file else "<input>"
        print(f"{finding.severity} {finding.rule} {target}: {finding.reason}")
        print(f"  remediation: {finding.remediation}")
