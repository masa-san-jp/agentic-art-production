#!/usr/bin/env python3
"""Build deterministic prototype-control records without performing a prototype."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.canonical import canonical_sha256
from tools.lib.diagnostics import DiagnosticError, EXIT_SUCCESS, EXIT_VALIDATION, Finding, emit_findings
from tools.lib.planning import validate_plan_document
from tools.lib.prototype import validate_prototype_document
from tools.lib.yaml_io import dump_yaml, load_yaml


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _finding(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation)


def _mapping(path: Path) -> dict[str, Any]:
    value = load_yaml(path)
    if not isinstance(value, dict):
        raise DiagnosticError(_finding("PROTOTYPE_INPUT_OBJECT", "prototype input must be a YAML mapping", file=path, remediation="Restore the accepted project input as a mapping."))
    return value


def _records(path: Path, key: str) -> list[dict[str, Any]]:
    value = _mapping(path).get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise DiagnosticError(_finding("PROTOTYPE_INPUT_RECORDS", f"{key} must be a list of objects", file=path, location=f"/{key}", remediation="Regenerate the handoff artifact with its declared record collection."))
    return sorted(value, key=lambda item: str(item.get("id", "")))


def _load_project(project_root: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    plan_path = project_root / "03_plan/production-plan.yaml"
    handoff_path = project_root / "00_handoff/production-handoff.yaml"
    plan = _mapping(plan_path)
    handoff = _mapping(handoff_path)
    prototype_plan_path = project_root / "00_handoff/source-bundle/artifacts/prototype-plans.yaml"
    acceptance_test_path = project_root / "00_handoff/source-bundle/artifacts/acceptance-tests.yaml"
    prototype_plans = _records(prototype_plan_path, "prototype_plans") if prototype_plan_path.is_file() else []
    acceptance_tests = _records(acceptance_test_path, "acceptance_tests") if acceptance_test_path.is_file() else []
    return plan, handoff, prototype_plans, acceptance_tests


def _trace(plan: dict[str, Any], *extra: str) -> list[str]:
    return list(dict.fromkeys([str(plan["handoff_ref"]["id"]), str(plan["selection_record"]["selected_hypothesis_id"]), *[str(item) for item in plan.get("mandatory_requirement_ids", [])], *extra]))


def _build_control(project_root: Path) -> dict[str, Any]:
    plan, handoff, prototype_plans, acceptance_tests = _load_project(project_root)
    plan_findings = validate_plan_document(plan, repository=repository_root(), plan_path=project_root / "03_plan/production-plan.yaml")
    if plan_findings:
        raise DiagnosticError(_finding("PROTOTYPE_PLAN_INVALID", "production plan must validate before prototype control is built", file=project_root / "03_plan/production-plan.yaml", remediation="Run tools/build_plan.py and correct its diagnostics first."))
    prototype_plan_ids = [str(item["id"]) for item in prototype_plans]
    acceptance_by_id = {str(item["id"]): item for item in acceptance_tests}
    tasks = [task for task in plan.get("tasks", []) if isinstance(task, dict)]
    runs: list[dict[str, Any]] = []
    test_results: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    iteration_decisions: list[dict[str, Any]] = []
    for index, prototype_plan in enumerate(prototype_plans, start=1):
        prototype_plan_id = str(prototype_plan["id"])
        run_id = f"PRT{index:03d}"
        production_task_ids = [str(task["id"]) for task in tasks if prototype_plan_id in task.get("trace_refs", []) and task.get("effect_type") == "PHYSICAL_EXTERNAL"]
        blocked_tasks = [task for task in tasks if task.get("id") in production_task_ids and task.get("status") == "BLOCKED"]
        run_status = "BLOCKED" if blocked_tasks else "PLANNED"
        external_status = "REQUIRED" if production_task_ids else "NOT_REQUIRED"
        test_ids: list[str] = []
        for test_index, acceptance_test_id in enumerate(prototype_plan.get("acceptance_test_ids", []), start=1):
            test_id = f"PTR{len(test_results) + 1:03d}"
            test_ids.append(test_id)
            source_test = acceptance_by_id.get(str(acceptance_test_id), {})
            test_results.append({
                "id": test_id,
                "run_id": run_id,
                "acceptance_test_id": str(acceptance_test_id),
                "result": "NOT_RUN",
                "executed_at": None,
                "external_validation_status": external_status if external_status != "NOT_REQUIRED" else "NOT_REQUIRED",
                "evidence_refs": [],
                "conditions": "No physical prototype or frame review has been executed by this builder.",
                "deviations": [],
                "limitations": str(source_test.get("pass_condition", "External execution and evidence are still required.")),
                "trace_refs": _trace(plan, prototype_plan_id, str(acceptance_test_id), test_id),
            })
        review_id = f"RV{index:03d}"
        dimensions = ["TECHNICAL", "ARTISTIC", "REQUIREMENT", "RIGHTS_PRIVACY", "FEASIBILITY", "SAFETY"]
        reviews.append({
            "id": review_id,
            "run_id": run_id,
            "status": "NOT_STARTED",
            "assessments": [{"dimension": dimension, "result": "NOT_REVIEWED", "rationale": "Review is pending prototype evidence."} for dimension in dimensions],
            "overall_result": "NOT_REVIEWED",
            "authority": "HUMAN",
            "human_required": True,
            "open_issue_ids": [],
            "external_validation_status": external_status if external_status != "NOT_REQUIRED" else "NOT_REQUIRED",
            "trace_refs": _trace(plan, prototype_plan_id, run_id, review_id),
        })
        iteration_decisions.append({
            "id": f"ITD{index:03d}",
            "run_id": run_id,
            "decision": "WAITING_FOR_RUN",
            "status": "RECORDED",
            "rationale": "Do not decide proceed/revise until the prototype test and separated reviews have evidence.",
            "next_iteration": None,
            "change_request_ids": [],
            "authority": "SYSTEM",
            "trace_refs": _trace(plan, prototype_plan_id, run_id, f"ITD{index:03d}"),
        })
        runs.append({
            "id": run_id,
            "prototype_plan_id": prototype_plan_id,
            "production_task_ids": production_task_ids,
            "iteration": 1,
            "status": run_status,
            "external_validation_status": external_status,
            "evidence_refs": [],
            "test_result_ids": test_ids,
            "review_id": review_id,
            "started_at": None,
            "finished_at": None,
            "stop_reason": "AR001 HUMAN approval is required before physical prototype tasks." if blocked_tasks else None,
            "trace_refs": _trace(plan, prototype_plan_id, run_id),
        })

    control = {
        "schema_version": "1.0.0",
        "control_id": "PC001",
        "control_revision": 1,
        "project_id": str(plan["project_id"]),
        "state": "PLANNING",
        "generated_at": str(plan["generated_at"]),
        "plan_ref": {"id": plan["plan_id"], "revision": plan["plan_revision"], "content_sha256": plan["integrity"]["content_sha256"]},
        "source_prototype_plan_ids": prototype_plan_ids,
        "runs": runs,
        "test_results": test_results,
        "reviews": reviews,
        "iteration_decisions": iteration_decisions,
        "change_requests": [],
    }
    control["integrity"] = {"content_sha256": canonical_sha256(control)}
    findings = validate_prototype_document(control, repository=repository_root(), control_path=project_root / "04_prototype/prototype-control.yaml", plan=plan, source_prototype_plan_ids=set(prototype_plan_ids))
    if findings:
        raise DiagnosticError(_finding("PROTOTYPE_GENERATION_VALIDATION", "generated prototype control did not pass its own validator", file=project_root / "04_prototype/prototype-control.yaml", remediation="Correct the generator and rerun the deterministic build."))
    return control


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_outputs(project_root: Path, control: dict[str, Any]) -> None:
    outputs = {
        "04_prototype/prototype-control.yaml": control,
        "04_prototype/prototype-runs.yaml": {"runs": control["runs"]},
        "04_prototype/test-results.yaml": {"test_results": control["test_results"]},
        "04_prototype/reviews.yaml": {"reviews": control["reviews"]},
        "04_prototype/iteration-decisions.yaml": {"iteration_decisions": control["iteration_decisions"]},
        "07_governance/change-requests.yaml": {"change_requests": control["change_requests"]},
    }
    for relative, value in outputs.items():
        dump_yaml(value, project_root / relative)
    (project_root / "04_prototype/prototype-brief.md").write_text(
        "# Prototype control brief\n\n"
        "This record defines the prototype, test, review, and change-control gates. It does not claim that a physical prototype, camera capture, or external validation has occurred.\n\n"
        "- Control: `PC001` revision 1\n"
        "- State: `PLANNING`\n"
        f"- Runs: `{len(control['runs'])}`\n"
        "- Test results: all generated results are `NOT_RUN` until external evidence exists.\n"
        "- Change requests: none; a failed test must remain visible and create a `CR###` before revision.\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path, help="accepted Git-external production project")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        project_root = args.project_root.resolve()
        control = _build_control(project_root)
        _write_outputs(project_root, control)
        print(str(project_root / "04_prototype/prototype-control.yaml"))
        return EXIT_SUCCESS
    except DiagnosticError as exc:
        emit_findings([exc.finding], output_format=args.format)
        return EXIT_VALIDATION
    except (OSError, KeyError, TypeError, ValueError) as exc:
        emit_findings([_finding("PROTOTYPE_BUILD", str(exc), file=args.project_root, remediation="Correct the accepted project input and retry prototype-control generation.")], output_format=args.format)
        return EXIT_VALIDATION


if __name__ == "__main__":
    raise SystemExit(main())
