"""Release gate orchestration for the verified v1.0.0 candidate."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


RELEASE_GATE_VERSION = "1.0.0"
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _display_command(name: str) -> str:
    commands = {
        "repository_validation": "python tools/validate.py --check --format json",
        "unit_tests": "python -m unittest discover -s tests -v",
        "evaluation": "python tools/run_evaluation.py --format json",
    }
    return commands[name]


def _command(name: str) -> list[str]:
    commands = {
        "repository_validation": [sys.executable, "tools/validate.py", "--check", "--format", "json"],
        "unit_tests": [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        "evaluation": [sys.executable, "tools/run_evaluation.py", "--format", "json"],
    }
    return commands[name]


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _git(repository: Path, args: list[str], runner: CommandRunner) -> str:
    result = runner(["git", *args], cwd=repository, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def _check_output(name: str, result: subprocess.CompletedProcess[str]) -> tuple[bool, dict[str, Any]]:
    output = result.stdout or ""
    detail: dict[str, Any] = {
        "name": name,
        "command": _display_command(name),
        "exit_code": result.returncode,
        "output_sha256": _sha256_text(output),
    }
    passed = result.returncode == 0
    if name == "repository_validation" and passed:
        try:
            passed = json.loads(output) == []
        except json.JSONDecodeError:
            passed = False
    if name == "evaluation" and passed:
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
    commit = verified_commit or _git(repository, ["rev-parse", "HEAD"], runner)
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
        for name in ("repository_validation", "unit_tests", "evaluation"):
            result = runner(_command(name), cwd=repository, capture_output=True, text=True, check=False)
            passed, detail = _check_output(name, result)
            checks.append(detail)
            run_passed = run_passed and passed
        all_passed = all_passed and run_passed
        report["runs"].append({"run": run_number, "status": "PASS" if run_passed else "FAIL", "checks": checks})
    report["status"] = "PASS" if all_passed else "FAIL"
    if not all_passed:
        report["failure_reason"] = "one or more consecutive release gate runs failed"
    return report
