"""Release gate orchestration for the verified v1.0.0 candidate."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


RELEASE_GATE_VERSION = "1.0.0"
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class GateCheck:
    name: str
    display_command: str
    argv: tuple[str, ...]


GATE_CHECKS = (
    GateCheck(
        "repository_validation",
        "python tools/validate.py --check --format json",
        (sys.executable, "tools/validate.py", "--check", "--format", "json"),
    ),
    GateCheck(
        "unit_tests",
        "python -m unittest discover -s tests -v",
        (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"),
    ),
    GateCheck(
        "evaluation",
        "python tools/run_evaluation.py --format json",
        (sys.executable, "tools/run_evaluation.py", "--format", "json"),
    ),
    GateCheck(
        "agent_harness",
        "python -m unittest tests.test_agent_harness -v",
        (sys.executable, "-m", "unittest", "tests.test_agent_harness", "-v"),
    ),
)


def _is_commit_sha(value: str) -> bool:
    return bool(re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value))


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _git(repository: Path, args: list[str], runner: CommandRunner) -> str:
    result = runner(["git", *args], cwd=repository, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def _check_output(check: GateCheck, result: subprocess.CompletedProcess[str]) -> tuple[bool, dict[str, Any]]:
    output = result.stdout or ""
    detail: dict[str, Any] = {
        "name": check.name,
        "command": check.display_command,
        "exit_code": result.returncode,
        "output_sha256": _sha256_text(output),
    }
    passed = result.returncode == 0
    if check.name == "repository_validation" and passed:
        try:
            passed = json.loads(output) == []
        except json.JSONDecodeError:
            passed = False
    if check.name == "evaluation" and passed:
        try:
            passed = json.loads(output).get("status") == "PASS"
        except (AttributeError, json.JSONDecodeError):
            passed = False
    detail["status"] = "PASS" if passed else "FAIL"
    if not passed and result.stderr:
        detail["error_sha256"] = _sha256_text(result.stderr)
    return passed, detail


def run_release_gate(
    repository: Path,
    *,
    runs: int = 3,
    runner: CommandRunner = subprocess.run,
    verified_commit: str | None = None,
) -> dict[str, Any]:
    """Run all release checks consecutively and return a path-free evidence report."""

    repository = repository.resolve()
    if runs < 1:
        raise ValueError("runs must be at least 1")
    head_commit = _git(repository, ["rev-parse", "HEAD"], runner)
    if not _is_commit_sha(head_commit):
        raise RuntimeError("git HEAD is not a full commit SHA")
    if verified_commit is not None:
        if not _is_commit_sha(verified_commit):
            raise ValueError("verified_commit must be a full commit SHA")
        if verified_commit != head_commit:
            raise ValueError("verified_commit must match repository HEAD")
    commit = head_commit
    clean = not bool(_git(repository, ["status", "--porcelain"], runner))
    report: dict[str, Any] = {
        "schema_version": RELEASE_GATE_VERSION,
        "gate_id": "RELEASE-001",
        "candidate": "v1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verified_commit": commit,
        "repository_clean": clean,
        "publication_status": "HUMAN_APPROVAL_REQUIRED",
        "runs": [],
    }
    if not clean:
        report["status"] = "FAIL"
        report["failure_reason"] = "repository working tree is not clean"
        return report

    all_passed = True
    for run_number in range(1, runs + 1):
        checks: list[dict[str, Any]] = []
        run_passed = True
        for check in GATE_CHECKS:
            result = runner(list(check.argv), cwd=repository, capture_output=True, text=True, check=False)
            passed, detail = _check_output(check, result)
            checks.append(detail)
            run_passed = run_passed and passed
        all_passed = all_passed and run_passed
        report["runs"].append({"run": run_number, "status": "PASS" if run_passed else "FAIL", "checks": checks})
    report["status"] = "PASS" if all_passed else "FAIL"
    if not all_passed:
        report["failure_reason"] = "one or more consecutive release gate runs failed"
    return report
