"""Release gate orchestration for the verified v1.0.0 candidate."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .diagnostics import DiagnosticError
from .yaml_io import dump_yaml, load_yaml


RELEASE_GATE_VERSION = "1.0.0"
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
ProgressReporter = Callable[[str], None]


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


def _external_path(path: Path | None, repository: Path) -> Path | None:
    if path is None:
        return None
    resolved = path.resolve()
    if resolved == repository or repository in resolved.parents:
        raise ValueError("release checkpoint must be written outside the protocol repository")
    return resolved


def _write_checkpoint(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        dump_yaml(report, temporary)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_checkpoint(path: Path) -> dict[str, Any]:
    try:
        value = load_yaml(path)
    except DiagnosticError as exc:
        raise ValueError(f"release checkpoint is invalid: {exc.finding.reason}") from exc
    if not isinstance(value, dict):
        raise ValueError("release checkpoint must be a YAML object")
    return value


def _validate_checkpoint(value: dict[str, Any], *, commit: str, runs: int) -> None:
    if value.get("gate_id") != "RELEASE-001" or value.get("candidate") != "v1.0.0":
        raise ValueError("release checkpoint belongs to a different gate or candidate")
    if value.get("verified_commit") != commit:
        raise ValueError("release checkpoint commit does not match repository HEAD")
    if value.get("requested_runs") != runs:
        raise ValueError("release checkpoint run count does not match --runs")
    if value.get("status") not in {"IN_PROGRESS", "PASS", "FAIL"}:
        raise ValueError("release checkpoint has an invalid status")
    recorded_runs = value.get("runs")
    if not isinstance(recorded_runs, list) or len(recorded_runs) > runs:
        raise ValueError("release checkpoint has an invalid run list")
    expected_names = [check.name for check in GATE_CHECKS]
    for expected_run, item in enumerate(recorded_runs, start=1):
        if not isinstance(item, dict) or item.get("run") != expected_run or item.get("status") not in {"IN_PROGRESS", "PASS", "FAIL"}:
            raise ValueError("release checkpoint has an invalid run record")
        checks = item.get("checks")
        if not isinstance(checks, list) or len(checks) > len(expected_names):
            raise ValueError("release checkpoint has an invalid check list")
        names = [check.get("name") for check in checks if isinstance(check, dict)]
        if names != expected_names[: len(checks)] or any(check.get("status") not in {"PASS", "FAIL"} for check in checks if isinstance(check, dict)):
            raise ValueError("release checkpoint check order or status is invalid")


def _next_check(report: dict[str, Any], runs: int) -> tuple[int, str] | None:
    recorded_runs = report.get("runs", [])
    for run_number in range(1, runs + 1):
        item = recorded_runs[run_number - 1] if run_number <= len(recorded_runs) else None
        check_count = len(item.get("checks", [])) if isinstance(item, dict) else 0
        if check_count < len(GATE_CHECKS):
            return run_number, GATE_CHECKS[check_count].name
    return None


def _persist_progress(report: dict[str, Any], checkpoint: Path | None, *, runs: int) -> None:
    if checkpoint is None:
        return
    report["status"] = "IN_PROGRESS"
    location = _next_check(report, runs)
    if location is None:
        report.pop("next_check", None)
    else:
        report["next_check"] = {"run": location[0], "name": location[1]}
    report["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_checkpoint(checkpoint, report)


def run_release_gate(
    repository: Path,
    *,
    runs: int = 3,
    runner: CommandRunner = subprocess.run,
    verified_commit: str | None = None,
    checkpoint: Path | None = None,
    resume: bool = False,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    """Run release checks consecutively, checkpointing completed checks when requested."""

    repository = repository.resolve()
    if runs < 1:
        raise ValueError("runs must be at least 1")
    checkpoint = _external_path(checkpoint, repository)
    if resume and checkpoint is None:
        raise ValueError("--resume requires an external --evidence checkpoint path")
    if checkpoint is not None and checkpoint.exists() and not resume:
        raise ValueError("release checkpoint already exists; use --resume or choose a new evidence path")
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
    if resume:
        report = _load_checkpoint(checkpoint)
        _validate_checkpoint(report, commit=commit, runs=runs)
        if report.get("status") == "PASS":
            if not clean:
                raise ValueError("repository working tree is no longer clean")
            return report
        if report.get("status") != "IN_PROGRESS":
            raise ValueError("only an IN_PROGRESS checkpoint can be resumed; start a new gate after a failure")
        if not clean:
            raise ValueError("repository working tree is no longer clean")
    else:
        report = {
            "schema_version": RELEASE_GATE_VERSION,
            "gate_id": "RELEASE-001",
            "candidate": "v1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "verified_commit": commit,
            "repository_clean": clean,
            "requested_runs": runs,
            "publication_status": "HUMAN_APPROVAL_REQUIRED",
            "runs": [],
        }
    if not clean:
        report["status"] = "FAIL"
        report["failure_reason"] = "repository working tree is not clean"
        report.pop("next_check", None)
        report["updated_at"] = datetime.now(timezone.utc).isoformat()
        if checkpoint is not None:
            _write_checkpoint(checkpoint, report)
        return report

    _persist_progress(report, checkpoint, runs=runs)
    for run_number in range(1, runs + 1):
        if len(report["runs"]) < run_number:
            report["runs"].append({"run": run_number, "status": "IN_PROGRESS", "checks": []})
        run_record = report["runs"][run_number - 1]
        for check in GATE_CHECKS[len(run_record["checks"]):]:
            current_commit = _git(repository, ["rev-parse", "HEAD"], runner)
            current_clean = not bool(_git(repository, ["status", "--porcelain"], runner))
            if current_commit != commit or not current_clean:
                report["status"] = "FAIL"
                report["failure_reason"] = "repository commit or working tree changed during release gate"
                report.pop("next_check", None)
                report["updated_at"] = datetime.now(timezone.utc).isoformat()
                if checkpoint is not None:
                    _write_checkpoint(checkpoint, report)
                return report
            if progress:
                progress(f"run {run_number}/{runs} check {check.name}: START")
            result = runner(list(check.argv), cwd=repository, capture_output=True, text=True, check=False)
            passed, detail = _check_output(check, result)
            run_record["checks"].append(detail)
            if progress:
                progress(f"run {run_number}/{runs} check {check.name}: {detail['status']}")
            _persist_progress(report, checkpoint, runs=runs)
        run_record["status"] = "PASS" if all(item.get("status") == "PASS" for item in run_record["checks"]) else "FAIL"
        _persist_progress(report, checkpoint, runs=runs)
    all_passed = all(item.get("status") == "PASS" and all(check.get("status") == "PASS" for check in item.get("checks", [])) for item in report["runs"])
    report["status"] = "PASS" if all_passed else "FAIL"
    report.pop("next_check", None)
    report["updated_at"] = datetime.now(timezone.utc).isoformat()
    if not all_passed:
        report["failure_reason"] = "one or more consecutive release gate runs failed"
    if checkpoint is not None:
        _write_checkpoint(checkpoint, report)
    return report
