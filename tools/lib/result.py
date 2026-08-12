"""Build and validate the production-owned result contract from project records."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .canonical import result_sha256
from .config import load_config
from .diagnostics import DiagnosticError, Finding
from .execution import ExecutionManager
from .schema import load_schema, validate_instance
from .security import check_text_security, validate_asset_uri
from .yaml_io import dump_yaml, load_yaml


RESULT_SCHEMA = "production-result.schema.json"


def _finding(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation)


def _load_mapping(path: Path, *, required: bool = True) -> dict[str, Any]:
    if not path.is_file():
        if required:
            raise DiagnosticError(_finding("RESULT_INPUT_MISSING", "required project input is missing", file=path, remediation="Materialize the project stage before generating a production result."))
        return {}
    value = load_yaml(path)
    if not isinstance(value, dict):
        raise DiagnosticError(_finding("RESULT_INPUT_OBJECT", "result input must be a YAML mapping", file=path, remediation="Regenerate the project record as a mapping."))
    return value


def _records(document: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = document.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise DiagnosticError(_finding("RESULT_INPUT_RECORDS", f"{key} must be a list of mappings", file="project input", location=f"/{key}", remediation="Regenerate the project record with structured records."))
    return list(value)


def _latest_records(records: list[dict[str, Any]], id_field: str, *, allowed_statuses: set[str] | None = None) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        if allowed_statuses is not None and record.get("status") not in allowed_statuses:
            continue
        identity = record.get(id_field)
        if not isinstance(identity, str):
            continue
        previous = latest.get(identity)
        if previous is None or int(record.get("revision", 0)) > int(previous.get("revision", 0)):
            latest[identity] = record
    return [latest[key] for key in sorted(latest)]


def _git_commit(repository: Path) -> str:
    try:
        completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DiagnosticError(_finding("RESULT_PROVENANCE", f"could not resolve production commit: {exc}", file=repository, remediation="Run the result builder from a Git checkout or pass --production-commit.")) from exc
    value = completed.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise DiagnosticError(_finding("RESULT_PROVENANCE", "production commit is not a 40-character lowercase SHA", file=repository, remediation="Provide an immutable production commit SHA."))
    return value


def _selection(plan: dict[str, Any]) -> dict[str, Any]:
    selection = plan.get("selection_record", {})
    authority = {"HUMAN": "HUMAN", "AGENT": "AGENT_RECOMMENDED", "SYSTEM": "SYSTEM_POLICY"}.get(selection.get("authority"), "HUMAN_SELECTION_REQUIRED")
    decision = {"HUMAN_SELECTED": "SELECTED", "PROVISIONAL": "PROVISIONAL", "REJECTED": "NOT_SELECTED"}.get(selection.get("status"), "PENDING_HUMAN")
    value: dict[str, Any] = {"selected_hypothesis_id": selection.get("selected_hypothesis_id"), "authority": authority, "decision": decision}
    approval_ref = selection.get("approval_ref")
    if approval_ref is not None:
        value["approval_ref"] = approval_ref
    return value


def _output_records(projections: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for record in _latest_records(projections["outputs"].get("records", []), "output_id", allowed_statuses={"AVAILABLE"}):
        asset = record.get("asset_ref", {})
        output = {"id": record["output_id"], "deliverable_id": record["deliverable_id"], "uri": asset["uri"], "version": asset["version"], "sha256": asset["sha256"], "rights_status": asset["rights_status"]}
        if isinstance(asset.get("media_type"), str):
            output["media_type"] = asset["media_type"]
        outputs.append(output)
    return outputs


def _test_results(plan: dict[str, Any], prototype: dict[str, Any], result_id: str, generated_at: str) -> list[dict[str, Any]]:
    prototype_results = {item.get("acceptance_test_id"): item for item in _records(prototype, "test_results") if isinstance(item.get("acceptance_test_id"), str)}
    results: list[dict[str, Any]] = []
    for acceptance in sorted(_records(plan, "acceptance_tests"), key=lambda item: str(item.get("id", ""))):
        test_id = acceptance.get("id")
        if not isinstance(test_id, str):
            continue
        source = prototype_results.get(test_id)
        if source is None:
            result = acceptance.get("result", "NOT_RUN")
            conditions = acceptance.get("preconditions") or "No execution record was available."
            statement = acceptance.get("pass_condition")
            limitations = "The result is derived from the production plan; execution evidence was not recorded."
            evidence_ref = f"urn:production:result:{result_id}:test:{test_id}"
            executed_at = generated_at
        else:
            result = source.get("result", "NOT_RUN")
            conditions = source.get("conditions") or acceptance.get("preconditions") or "No execution conditions were recorded."
            statement = acceptance.get("pass_condition")
            limitations = source.get("limitations") or "External execution evidence is not available."
            evidence_refs = source.get("evidence_refs") or []
            evidence_ref = evidence_refs[0] if evidence_refs else f"urn:production:result:{result_id}:test:{test_id}"
            executed_at = source.get("executed_at") or generated_at
        value: dict[str, Any] = {"acceptance_test_id": test_id, "result": result if result in {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "EXTERNAL_VALIDATION_REQUIRED", "SKIPPED"} else "NOT_RUN", "executed_at": executed_at, "conditions": str(conditions), "evidence_ref": evidence_ref, "limitations": str(limitations)}
        if isinstance(statement, str) and statement:
            value["statement"] = statement
        results.append(value)
    return results


def _observations(project_id: str, state: str, requirement_ids: list[str]) -> list[dict[str, Any]]:
    return [{
        "id": "OB001",
        "statement": f"Production project {project_id} is in lifecycle state {state}; this result contains no unrecorded physical or installation outcome.",
        "method": "production-state-and-execution-register-review",
        "limitations": "Metadata and opaque references only; this observation is not physical or audience evidence.",
        "related_requirement_ids": requirement_ids,
    }]


def _gaps(handoff: dict[str, Any]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for gap in sorted(_records({"items": handoff.get("open_gaps", [])}, "items"), key=lambda item: str(item.get("id", ""))):
        if not isinstance(gap.get("id"), str):
            continue
        gaps.append({"id": gap["id"], "statement": gap.get("statement", "Open production gap."), "impact": gap.get("impact", "External validation remains unresolved."), "owner": gap.get("resolution_owner", "production"), "resolution_condition": gap.get("impact", "Resolve the recorded production gap.")})
    return gaps


def _deviations(prototype: dict[str, Any]) -> list[dict[str, Any]]:
    deviations: list[dict[str, Any]] = []
    for index, run in enumerate(_records(prototype, "runs"), start=1):
        if run.get("status") == "FAILED":
            deviations.append({"id": f"DEV{index:03d}", "statement": f"Prototype run {run.get('id', '<unknown>')} failed.", "reason": run.get("stop_reason", "The prototype run did not complete."), "impact_level": "MAJOR"})
    return deviations


def _incidents(runtime_state: dict[str, Any]) -> list[dict[str, Any]]:
    incidents: list[dict[str, Any]] = []
    for index, effect in enumerate((runtime_state.get("effects") or {}).values(), start=1):
        if isinstance(effect, dict) and effect.get("status") in {"FAILED", "UNKNOWN"}:
            incidents.append({"id": f"INC{index:03d}", "severity": "HIGH" if effect.get("status") == "FAILED" else "CRITICAL", "statement": f"Runtime effect {effect.get('effect_key', '<unknown>')} ended with {effect.get('status')}.", "impact_level": "MAJOR" if effect.get("status") == "FAILED" else "CRITICAL"})
    return incidents


def _change_requests(project_root: Path) -> list[dict[str, Any]]:
    document = _load_mapping(project_root / "07_governance/change-requests.yaml", required=False)
    values: list[dict[str, Any]] = []
    for record in _records(document, "change_requests"):
        if not isinstance(record.get("id"), str) or not isinstance(record.get("statement"), str):
            continue
        impact = record.get("impact", {})
        impact_level = impact.get("classification") if isinstance(impact, dict) else None
        value: dict[str, Any] = {
            "id": record["id"],
            "statement": record["statement"],
            "impact_level": impact_level if impact_level in {"NONE", "MINOR", "MAJOR", "CRITICAL"} else "MAJOR",
        }
        reason = record.get("reason")
        if isinstance(reason, str) and reason.strip():
            value["reason"] = reason
        approval_ref = record.get("approval_ref")
        if approval_ref is None or isinstance(approval_ref, str):
            value["approval_ref"] = approval_ref
        status = record.get("status")
        if status in {"PROPOSED", "PENDING_RESEARCH_REVIEW", "PENDING_HUMAN_APPROVAL", "ACCEPTED", "REJECTED"}:
            value["status"] = status
        values.append(value)
    return values


def _validate_uris(result: dict[str, Any], repository: Path) -> None:
    policy = load_config(repository, "asset-policy.yaml")
    for index, output in enumerate(result.get("outputs", [])):
        finding = validate_asset_uri(output.get("uri"), allowed_schemes=policy.get("allowed_uri_schemes", ["urn", "https"]), allow_query=False)
        if finding:
            raise DiagnosticError(_finding(finding.rule, finding.reason, file=repository / "schemas/production-result.schema.json", location=f"/outputs/{index}/uri", remediation=finding.remediation))
    for index, test in enumerate(result.get("test_results", [])):
        finding = validate_asset_uri(test.get("evidence_ref"), allowed_schemes=policy.get("allowed_uri_schemes", ["urn", "https"]), allow_query=False)
        if finding:
            raise DiagnosticError(_finding(finding.rule, finding.reason, file=repository / "schemas/production-result.schema.json", location=f"/test_results/{index}/evidence_ref", remediation=finding.remediation))


def validate_result(result: dict[str, Any], *, repository: Path, result_path: Path | str = "production-result") -> None:
    schema_path = repository / "schemas" / RESULT_SCHEMA
    schema = load_schema(schema_path)
    findings = validate_instance(result, schema, schema_path=schema_path)
    if findings:
        raise DiagnosticError(findings[0])
    expected_hash = result_sha256(result)
    if result.get("integrity", {}).get("content_sha256") != expected_hash:
        raise DiagnosticError(_finding("RESULT_INTEGRITY", "production result content_sha256 does not match its canonical payload", file=result_path, location="/integrity/content_sha256", remediation="Regenerate the result from canonical project records."))
    _validate_uris(result, repository)
    policy = load_config(repository, "safety-policy.yaml")
    security_findings = check_text_security(result, file=result_path, forbidden_markers=policy.get("forbidden_markers", []), signed_url_markers=policy.get("signed_url_markers", []))
    if security_findings:
        finding = security_findings[0]
        raise DiagnosticError(_finding(finding.rule, finding.reason, file=result_path, location=finding.location, remediation=finding.remediation))


def build_result(project_root: Path, repository: Path, *, result_id: str, generated_at: str, production_commit: str | None = None) -> tuple[dict[str, Any], bool]:
    project_root = project_root.resolve()
    repository = repository.resolve()
    manifest = _load_mapping(project_root / "manifest.yaml")
    handoff = _load_mapping(project_root / "00_handoff/production-handoff.yaml")
    plan = _load_mapping(project_root / "03_plan/production-plan.yaml")
    prototype = _load_mapping(project_root / "04_prototype/prototype-control.yaml", required=False)
    runtime_state = _load_mapping(project_root / "08_runtime/production-state.json")
    ExecutionManager(project_root, repository).replay()
    projections = {
        "outputs": _load_mapping(project_root / "05_execution/output-versions.yaml"),
        "quality": _load_mapping(project_root / "05_execution/quality-results.yaml"),
        "installation_plan": _load_mapping(project_root / "06_installation/installation-plan.yaml"),
        "installation_results": _load_mapping(project_root / "06_installation/installation-results.yaml"),
    }
    result: dict[str, Any] = {
        "schema_version": "1.0.0",
        "result_id": result_id,
        "production_project_id": manifest.get("project_id"),
        "production_commit": production_commit or _git_commit(repository),
        "generated_at": generated_at,
        "accepted_handoff": {"id": handoff.get("handoff_id"), "revision": handoff.get("revision"), "content_sha256": handoff.get("integrity", {}).get("content_sha256"), "research_project_id": handoff.get("research_project_id"), "research_commit": handoff.get("research_commit")},
        "selection": _selection(plan),
        "outputs": _output_records(projections),
        "test_results": _test_results(plan, prototype, result_id, generated_at),
        "observations": _observations(str(manifest.get("project_id")), str(runtime_state.get("state")), [str(value) for value in plan.get("mandatory_requirement_ids", [])]),
        "deviations": _deviations(prototype),
        "incidents": _incidents(runtime_state),
        "research_change_requests": _change_requests(project_root),
        "open_gaps": _gaps(handoff),
    }
    result["integrity"] = {"content_sha256": result_sha256(result)}
    validate_result(result, repository=repository, result_path=project_root / "08_runtime/production-result.yaml")
    result_path = project_root / "08_runtime/production-result.yaml"
    if result_path.is_file():
        existing = _load_mapping(result_path)
        if existing == result:
            return result, True
        if existing.get("result_id") == result_id:
            raise DiagnosticError(_finding("RESULT_IDEMPOTENCY_MISMATCH", "result_id already exists with different canonical content", file=result_path, remediation="Use the original result inputs or choose a new result_id."))
    _write_yaml_atomic(result_path, result)
    return result, False


def _write_yaml_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        os.close(descriptor)
        dump_yaml(value, Path(temporary))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
