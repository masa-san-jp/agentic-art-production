"""Prototype, review, iteration, and change-control validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .canonical import canonical_sha256
from .diagnostics import DiagnosticError, Finding
from .evidence import resolve_evidence_refs
from .schema import load_schema, validate_instance
from .yaml_io import load_yaml


PROTOTYPE_SCHEMAS = {
    "run": "prototype-run.schema.json",
    "test_result": "prototype-test-result.schema.json",
    "review": "prototype-review.schema.json",
    "iteration_decision": "iteration-decision.schema.json",
    "change_request": "change-request.schema.json",
    "control": "prototype-control.schema.json",
}


def load_prototype_schemas(repository: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    common = load_schema(repository / "schemas/common.schema.json")
    prototype = load_schema(repository / "schemas/prototype.schema.json")
    schemas = {name: load_schema(repository / "schemas" / filename) for name, filename in PROTOTYPE_SCHEMAS.items()}
    return common, prototype, schemas


def _finding(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation)


def _index(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(record["id"]): record for record in records if isinstance(record, dict) and "id" in record}


def _refs_exist(findings: list[Finding], records: list[dict[str, Any]], field: str, target_ids: set[str], *, file: Path) -> None:
    for record in records:
        values = record.get(field, [])
        if values is None:
            continue
        if isinstance(values, str):
            values = [values]
        for value in values:
            if value not in target_ids:
                findings.append(_finding("PROTOTYPE_REFERENCE", f"{field} references missing ID {value}", file=file, location=f"/{record.get('id', '<unknown>')}/{field}", remediation="Add the referenced prototype record or remove the stale reference."))


def validate_prototype_document(
    control: dict[str, Any],
    *,
    repository: Path,
    control_path: Path | str,
    plan: dict[str, Any] | None = None,
    source_prototype_plan_ids: set[str] | None = None,
    project_root: Path | None = None,
) -> list[Finding]:
    """Validate prototype records and enforce fail-closed review/change rules."""

    control_path = Path(control_path)
    findings: list[Finding] = []
    try:
        common, prototype, schemas = load_prototype_schemas(repository)
    except Exception as exc:
        return [_finding("PROTOTYPE_SCHEMA_LOAD", str(exc), file=repository / "schemas", remediation="Restore every prototype schema and retry validation.")]
    schema_store = [prototype, *schemas.values()]
    findings.extend(validate_instance(control, schemas["control"], schema_path=repository / "schemas/prototype-control.schema.json", common_schema=common, schema_store=schema_store))
    for field, schema_name in (("runs", "run"), ("test_results", "test_result"), ("reviews", "review"), ("iteration_decisions", "iteration_decision"), ("change_requests", "change_request")):
        for index, record in enumerate(control.get(field, [])):
            if not isinstance(record, dict):
                findings.append(_finding("PROTOTYPE_RECORD_OBJECT", "prototype collection item must be an object", file=control_path, location=f"/{field}/{index}", remediation="Regenerate prototype control output from structured records."))
                continue
            findings.extend(validate_instance(record, schemas[schema_name], schema_path=repository / "schemas" / PROTOTYPE_SCHEMAS[schema_name], common_schema=common, schema_store=schema_store))

    runs = _index(control.get("runs", []))
    test_results = _index(control.get("test_results", []))
    reviews = _index(control.get("reviews", []))
    decisions = _index(control.get("iteration_decisions", []))
    changes = _index(control.get("change_requests", []))
    prototype_plan_ids = source_prototype_plan_ids if source_prototype_plan_ids is not None else set(control.get("source_prototype_plan_ids", []))

    if plan is not None:
        task_ids = {str(task.get("id")) for task in plan.get("tasks", []) if isinstance(task, dict)}
        acceptance_test_ids = {str(test.get("id")) for test in plan.get("acceptance_tests", []) if isinstance(test, dict)}
        plan_ref = control.get("plan_ref", {})
        if plan_ref.get("id") != plan.get("plan_id") or plan_ref.get("revision") != plan.get("plan_revision") or plan_ref.get("content_sha256") != plan.get("integrity", {}).get("content_sha256"):
            findings.append(_finding("PROTOTYPE_PLAN_REF", "prototype control does not point to the exact production plan revision and hash", file=control_path, location="/plan_ref", remediation="Regenerate prototype control from the current production plan."))
    else:
        task_ids = set()
        acceptance_test_ids = set()

    if prototype_plan_ids:
        _refs_exist(findings, control.get("runs", []), "prototype_plan_id", prototype_plan_ids, file=control_path)
    _refs_exist(findings, control.get("runs", []), "test_result_ids", set(test_results), file=control_path)
    _refs_exist(findings, control.get("runs", []), "review_id", set(reviews), file=control_path)
    _refs_exist(findings, control.get("test_results", []), "run_id", set(runs), file=control_path)
    _refs_exist(findings, control.get("reviews", []), "run_id", set(runs), file=control_path)
    _refs_exist(findings, control.get("iteration_decisions", []), "run_id", set(runs), file=control_path)
    _refs_exist(findings, control.get("iteration_decisions", []), "change_request_ids", set(changes), file=control_path)
    if plan is not None:
        _refs_exist(findings, control.get("test_results", []), "acceptance_test_id", acceptance_test_ids, file=control_path)
        _refs_exist(findings, control.get("runs", []), "production_task_ids", task_ids, file=control_path)

    for run in control.get("runs", []):
        run_id = run.get("id")
        linked_tests = [test_results[test_id] for test_id in run.get("test_result_ids", []) if test_id in test_results]
        linked_decisions = [decision for decision in control.get("iteration_decisions", []) if decision.get("run_id") == run_id]
        linked_reviews = [review for review in control.get("reviews", []) if review.get("run_id") == run_id]
        if run.get("status") == "COMPLETE" and (not linked_tests or any(test.get("result") != "PASS" for test in linked_tests)):
            findings.append(_finding("PROTOTYPE_COMPLETION_UNSUBSTANTIATED", "a prototype run cannot be COMPLETE before every linked test passes", file=control_path, location=f"/runs/{run_id}/status", remediation="Record external evidence and PASS results, or keep the run non-terminal."))
        if run.get("status") == "FAILED" and not any(test.get("result") == "FAIL" for test in linked_tests):
            findings.append(_finding("PROTOTYPE_FAILURE_UNSUBSTANTIATED", "a FAILED prototype run requires a visible FAIL test result", file=control_path, location=f"/runs/{run_id}/status", remediation="Record the failed test instead of asserting failure without evidence."))
        if run.get("status") == "BLOCKED" and run.get("external_validation_status") == "VERIFIED":
            findings.append(_finding("PROTOTYPE_BLOCKED_VERIFIED", "a blocked run cannot claim verified external validation", file=control_path, location=f"/runs/{run_id}/external_validation_status", remediation="Use REQUIRED or PENDING until the blocker is resolved."))
        if project_root is not None and run.get("external_validation_status") == "VERIFIED":
            if not run.get("evidence_refs"):
                findings.append(_finding("PROTOTYPE_RUN_EVIDENCE", "verified external validation requires evidence references", file=control_path, location=f"/runs/{run_id}/evidence_refs", remediation="Register and attach verified evidence targeting the prototype run."))
            else:
                try:
                    resolve_evidence_refs(project_root, repository, run.get("evidence_refs"), expected_targets={str(run_id)}, file=control_path)
                except DiagnosticError as exc:
                    findings.append(exc.finding)
        if any(test.get("result") == "FAIL" for test in linked_tests):
            if not any(decision.get("decision") in {"REVISE", "BLOCK"} for decision in linked_decisions):
                findings.append(_finding("PROTOTYPE_FAIL_NO_DECISION", "a failed prototype requires an explicit REVISE or BLOCK decision", file=control_path, location=f"/runs/{run_id}", remediation="Record the failure visibly and add an iteration decision."))
            if any(decision.get("decision") == "REVISE" for decision in linked_decisions) and not any(decision.get("change_request_ids") for decision in linked_decisions if decision.get("decision") == "REVISE"):
                findings.append(_finding("PROTOTYPE_FAIL_NO_CHANGE", "a REVISE decision requires a change request", file=control_path, location=f"/runs/{run_id}", remediation="Create a change request with impact and authority before revising the baseline."))
        if linked_reviews and run.get("status") in {"COMPLETE", "FAILED"} and all(review.get("status") == "NOT_STARTED" for review in linked_reviews):
            findings.append(_finding("PROTOTYPE_REVIEW_MISSING", "a terminal prototype run requires a review record beyond NOT_STARTED", file=control_path, location=f"/runs/{run_id}", remediation="Complete the required review or keep the run non-terminal."))

    for result in control.get("test_results", []):
        result_id = result.get("id")
        if result.get("result") == "NOT_RUN" and result.get("executed_at") is not None:
            findings.append(_finding("PROTOTYPE_NOT_RUN_TIMESTAMP", "a NOT_RUN test cannot have an execution timestamp", file=control_path, location=f"/test_results/{result_id}/executed_at", remediation="Remove the timestamp until the test is actually executed."))
        if result.get("result") == "PASS":
            if result.get("executed_at") is None:
                findings.append(_finding("PROTOTYPE_PASS_TIMESTAMP", "a PASS test requires an execution timestamp", file=control_path, location=f"/test_results/{result_id}/executed_at", remediation="Record when the test was executed."))
            if result.get("external_validation_status") in {"REQUIRED", "PENDING", "REJECTED"}:
                findings.append(_finding("PROTOTYPE_PASS_EXTERNAL", "a PASS test cannot bypass required or unresolved external validation", file=control_path, location=f"/test_results/{result_id}/external_validation_status", remediation="Record verified evidence or keep the result NOT_RUN."))
            if result.get("external_validation_status") == "VERIFIED" and not result.get("evidence_refs"):
                findings.append(_finding("PROTOTYPE_PASS_EVIDENCE", "verified external validation requires evidence references", file=control_path, location=f"/test_results/{result_id}/evidence_refs", remediation="Add opaque evidence references without storing asset bodies."))
            if project_root is not None:
                try:
                    resolve_evidence_refs(
                        project_root,
                        repository,
                        result.get("evidence_refs"),
                        expected_targets={str(result_id), str(result.get("acceptance_test_id")), str(result.get("run_id"))},
                        file=control_path,
                    )
                except DiagnosticError as exc:
                    findings.append(exc.finding)
        if result.get("result") == "FAIL" and result.get("executed_at") is None:
            findings.append(_finding("PROTOTYPE_FAIL_TIMESTAMP", "a FAIL test requires an execution timestamp", file=control_path, location=f"/test_results/{result_id}/executed_at", remediation="Record when the failed test was executed."))

    for review in control.get("reviews", []):
        dimensions = [assessment.get("dimension") for assessment in review.get("assessments", [])]
        if len(dimensions) != len(set(dimensions)):
            findings.append(_finding("PROTOTYPE_REVIEW_DIMENSION_DUPLICATE", "review dimensions must be unique", file=control_path, location=f"/reviews/{review.get('id')}/assessments", remediation="Keep one assessment per review dimension."))
        if review.get("status") == "COMPLETE" and review.get("overall_result") == "NOT_REVIEWED":
            findings.append(_finding("PROTOTYPE_REVIEW_UNDECIDED", "a COMPLETE review needs an overall result", file=control_path, location=f"/reviews/{review.get('id')}/overall_result", remediation="Record PASS, FAIL, or CONDITIONAL with rationale."))

    for change in control.get("change_requests", []):
        impact = change.get("impact", {})
        classification = impact.get("classification")
        status = change.get("status")
        if classification in {"MAJOR", "CRITICAL"} and not change.get("research_review_required"):
            findings.append(_finding("CHANGE_RESEARCH_REVIEW", f"{classification} change must require research review", file=control_path, location=f"/change_requests/{change.get('id')}/research_review_required", remediation="Set research_review_required=true and record its review status."))
        if classification == "MAJOR" and status == "APPLIED" and (change.get("research_review_status") != "APPROVED" or change.get("approval_status") != "APPROVED"):
            findings.append(_finding("CHANGE_MAJOR_UNAPPROVED", "a MAJOR change cannot be APPLIED before research and human approval", file=control_path, location=f"/change_requests/{change.get('id')}/status", remediation="Keep the change PROPOSED or obtain both required approvals."))
        if classification == "CRITICAL" and status == "APPLIED":
            findings.append(_finding("CHANGE_CRITICAL_APPLIED", "a CRITICAL change cannot be applied by the prototype control layer", file=control_path, location=f"/change_requests/{change.get('id')}/status", remediation="Keep production BLOCKED and route the change to human review."))
        if impact.get("rights_safety") in {"REVIEW_REQUIRED", "BLOCKING"} and change.get("approval_required") == "NONE":
            findings.append(_finding("CHANGE_SAFETY_APPROVAL", "rights or safety impact cannot have no approval requirement", file=control_path, location=f"/change_requests/{change.get('id')}/approval_required", remediation="Require HUMAN approval for rights or safety impact."))
        if status == "APPLIED" and not any(alternative.get("selected") for alternative in change.get("alternatives", [])):
            findings.append(_finding("CHANGE_ALTERNATIVE_UNSELECTED", "an APPLIED change must record its selected alternative", file=control_path, location=f"/change_requests/{change.get('id')}/alternatives", remediation="Select the applied alternative explicitly."))

    expected_integrity = canonical_sha256({key: value for key, value in control.items() if key != "integrity"})
    if control.get("integrity", {}).get("content_sha256") != expected_integrity:
        findings.append(_finding("PROTOTYPE_INTEGRITY", "prototype control content_sha256 does not match the canonical payload", file=control_path, location="/integrity/content_sha256", remediation="Regenerate prototype control with its integrity block excluded from the hash."))
    return findings


def validate_prototype_project(project_root: Path, repository: Path) -> list[Finding]:
    control_path = project_root / "04_prototype/prototype-control.yaml"
    if not control_path.is_file():
        return []
    try:
        control = load_yaml(control_path)
        if not isinstance(control, dict):
            return [_finding("PROTOTYPE_OBJECT", "prototype-control.yaml must be a mapping", file=control_path, remediation="Regenerate prototype control output.")]
        plan_path = project_root / "03_plan/production-plan.yaml"
        plan = load_yaml(plan_path) if plan_path.is_file() else None
        if plan is not None and not isinstance(plan, dict):
            return [_finding("PROTOTYPE_PLAN_OBJECT", "production-plan.yaml must be a mapping", file=plan_path, remediation="Regenerate the production plan before validating prototype control.")]
        source_ids: set[str] | None = None
        prototype_plans_path = project_root / "00_handoff/source-bundle/artifacts/prototype-plans.yaml"
        if prototype_plans_path.is_file():
            source = load_yaml(prototype_plans_path)
            if isinstance(source, dict) and isinstance(source.get("prototype_plans"), list):
                source_ids = {str(item["id"]) for item in source["prototype_plans"] if isinstance(item, dict) and item.get("id")}
        return validate_prototype_document(control, repository=repository, control_path=control_path, plan=plan, source_prototype_plan_ids=source_ids, project_root=project_root)
    except Exception as exc:
        return [_finding("PROTOTYPE_INPUT", str(exc), file=control_path, remediation="Regenerate prototype control as valid YAML and retry validation.")]
