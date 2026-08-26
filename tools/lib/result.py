"""Build and validate the production-owned result contract from project records."""

from __future__ import annotations

import os
import copy
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .canonical import canonical_sha256, result_sha256
from .config import load_config
from .diagnostics import DiagnosticError, Finding
from .evidence import EVIDENCE_LOG, EVIDENCE_REGISTER, resolve_evidence_refs
from .execution import ExecutionManager
from .schema import load_schema, validate_instance
from .security import check_text_security, validate_asset_uri
from .yaml_io import dump_yaml, load_jsonl, load_yaml


RESULT_SCHEMA = "production-result.schema.json"
COMPLETION_REPORT_SCHEMA = "completion-report.schema.json"
TARGET_STATES = {"COMPLETE", "COMPLETE_WITH_GAPS", "BLOCKED"}


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


def _test_results(project_root: Path, repository: Path, plan: dict[str, Any], prototype: dict[str, Any], result_id: str, generated_at: str) -> list[dict[str, Any]]:
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
            if evidence_refs:
                resolved = resolve_evidence_refs(
                    project_root,
                    repository,
                    evidence_refs,
                    expected_targets={test_id, str(source.get("id")), str(source.get("run_id"))},
                    file=project_root / "04_prototype/prototype-control.yaml",
                )
                evidence_ref = resolved[0]["uri"]
            elif result == "PASS":
                raise DiagnosticError(_finding("RESULT_EVIDENCE_REQUIRED", f"PASS acceptance test {test_id} has no registered evidence", file=project_root / "04_prototype/prototype-control.yaml", remediation="Record VERIFIED evidence targeting the acceptance test before building the production result."))
            else:
                evidence_ref = f"urn:production:result:{result_id}:test:{test_id}"
            executed_at = source.get("executed_at") or generated_at
        value: dict[str, Any] = {"acceptance_test_id": test_id, "result": result if result in {"PASS", "FAIL", "BLOCKED", "NOT_RUN", "EXTERNAL_VALIDATION_REQUIRED", "SKIPPED"} else "NOT_RUN", "executed_at": executed_at, "conditions": str(conditions), "evidence_ref": evidence_ref, "limitations": str(limitations)}
        if isinstance(source, dict) and isinstance(source.get("viewer_response"), dict):
            value["viewer_response"] = copy.deepcopy(source["viewer_response"])
        if isinstance(statement, str) and statement:
            value["statement"] = statement
        results.append(value)
    return results


def _observations(projection: dict[str, Any]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in _records(projection, "records"):
        observation_id = record.get("observation_id")
        if not isinstance(observation_id, str):
            continue
        previous = latest.get(observation_id)
        if previous is None or int(record.get("revision", 0)) > int(previous.get("revision", 0)):
            latest[observation_id] = record
    result: list[dict[str, Any]] = []
    for observation_id in sorted(latest, key=lambda value: value.encode("utf-8")):
        record = latest[observation_id]
        if record.get("status") != "ACTIVE":
            continue
        result.append({
            "id": observation_id,
            "statement": record["statement"],
            "method": record["method"],
            "limitations": record["limitations"],
            "related_requirement_ids": list(record["related_requirement_ids"]),
        })
    return result


def _gap_finding(rule: str, reason: str, *, file: Path | str, location: str | None = None) -> DiagnosticError:
    return DiagnosticError(_finding(rule, reason, file=file, location=location, remediation="Record a schema-valid source gap with statement, impact, owner, and resolution_condition before generating the production result."))


def _canonical_gap_key(stage: str, record_type: str, record_id: str, revision: int, local_id: str) -> str:
    return f"{stage}/{record_type}/{record_id}/r{revision}/{local_id}"


def _source_gap(source: dict[str, Any], *, canonical_key: str, source_file: Path | str, local_id: str | None = None, owner_field: str = "owner", category: str | None = None) -> dict[str, Any]:
    identifier = local_id or source.get("id") or source.get("gap_id")
    if not isinstance(identifier, str) or not identifier:
        raise _gap_finding("RESULT_GAP_SOURCE_FIELDS", "gap source is missing a local gap ID", file=source_file)
    statement = source.get("statement")
    impact = source.get("impact")
    owner = source.get(owner_field)
    resolution_condition = source.get("resolution_condition")
    if not all(isinstance(value, str) and value.strip() for value in (statement, impact, owner, resolution_condition)):
        missing = [name for name, value in (("statement", statement), ("impact", impact), (owner_field, owner), ("resolution_condition", resolution_condition)) if not isinstance(value, str) or not value.strip()]
        raise _gap_finding("RESULT_GAP_SOURCE_FIELDS", f"gap source {canonical_key} is missing required fields: {', '.join(missing)}", file=source_file)
    if owner not in {"production", "research", "human", "external"}:
        raise _gap_finding("RESULT_GAP_SOURCE_FIELDS", f"gap source {canonical_key} has unsupported owner {owner!r}", file=source_file)
    blocking = source.get("blocking")
    if not isinstance(blocking, bool):
        raise _gap_finding("RESULT_GAP_SOURCE_FIELDS", f"gap source {canonical_key} is missing boolean blocking", file=source_file)
    value: dict[str, Any] = {"id": identifier, "statement": statement, "impact": impact, "owner": owner, "resolution_condition": resolution_condition}
    if isinstance(source.get("due_at"), str):
        value["due_at"] = source["due_at"]
    return {"canonical_key": canonical_key, "source_file": str(source_file), "blocking": blocking, "category": source.get("category") or category or "OTHER", "gap": value}


def _add_source_gap(gaps: dict[str, dict[str, Any]], entry: dict[str, Any]) -> None:
    key = entry["canonical_key"]
    previous = gaps.get(key)
    if previous is not None and previous != entry:
        raise _gap_finding("RESULT_GAP_SOURCE_DUPLICATE", f"canonical gap key {key} resolves to different source content", file=entry["source_file"])
    gaps[key] = entry


def _gap_from_optional_record(record: dict[str, Any], *, canonical_key: str, source_file: Path | str, required: bool, category: str = "OTHER") -> dict[str, Any] | None:
    gap = record.get("gap")
    if gap is None:
        if required:
            raise _gap_finding("RESULT_GAP_SOURCE_FIELDS", f"record {canonical_key} requires an explicit structured gap", file=source_file)
        return None
    if not isinstance(gap, dict):
        raise _gap_finding("RESULT_GAP_SOURCE_FIELDS", f"record {canonical_key} gap must be an object", file=source_file)
    return _source_gap(gap, canonical_key=canonical_key, source_file=source_file, category=category)


def _runtime_events(project_root: Path) -> list[dict[str, Any]]:
    path = project_root / "08_runtime/run-log.jsonl"
    if not path.is_file() or path.stat().st_size == 0:
        return []
    return [event for _, event in load_jsonl(path)]


def _gaps(project_root: Path, handoff: dict[str, Any], plan: dict[str, Any], prototype: dict[str, Any], runtime_state: dict[str, Any], projections: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    source_gaps: dict[str, dict[str, Any]] = {}
    handoff_path = project_root / "00_handoff/production-handoff.yaml"
    handoff_identity = str(handoff.get("handoff_id", "handoff"))
    handoff_revision = int(handoff.get("revision", 0))
    for gap in _records({"items": handoff.get("open_gaps", [])}, "items"):
        if not isinstance(gap.get("id"), str):
            raise _gap_finding("RESULT_GAP_SOURCE_FIELDS", "handoff open gap is missing id", file=handoff_path)
        entry = _source_gap(gap, canonical_key=_canonical_gap_key("handoff", "production-handoff", handoff_identity, handoff_revision, gap["id"]), source_file=handoff_path, owner_field="resolution_owner")
        _add_source_gap(source_gaps, entry)

    plan_path = project_root / "03_plan/production-plan.yaml"
    plan_identity = str(plan.get("plan_id", "plan"))
    plan_revision = int(plan.get("plan_revision", 0))
    for gap in _records({"items": plan.get("gaps", [])}, "items"):
        if not isinstance(gap.get("id"), str):
            raise _gap_finding("RESULT_GAP_SOURCE_FIELDS", "production plan gap is missing id", file=plan_path)
        category = "MANDATORY" if gap.get("blocking") is True else "OTHER"
        entry = _source_gap(gap, canonical_key=_canonical_gap_key("plan", "production-plan", plan_identity, plan_revision, gap["id"]), source_file=plan_path, category=category)
        _add_source_gap(source_gaps, entry)

    prototype_path = project_root / "04_prototype/prototype-control.yaml"
    control_identity = str(prototype.get("control_id", "prototype-control"))
    control_revision = int(prototype.get("control_revision", 0))
    for run in _records(prototype, "runs"):
        status = run.get("status")
        if status not in {"PLANNED", "BLOCKED", "IN_PROGRESS", "FAILED", "SKIPPED"} and run.get("external_validation_status") not in {"REQUIRED", "PENDING"}:
            continue
        run_id = run.get("id")
        if not isinstance(run_id, str):
            raise _gap_finding("RESULT_GAP_SOURCE_FIELDS", "prototype run gap source is missing id", file=prototype_path)
        entry = _gap_from_optional_record(run, canonical_key=_canonical_gap_key("prototype", "prototype-run", run_id, 1, "PROTOTYPE_RUN_STATUS"), source_file=prototype_path, required=True, category="MANDATORY")
        if entry:
            _add_source_gap(source_gaps, entry)
    for result in _records(prototype, "test_results"):
        if result.get("result") not in {"NOT_RUN", "FAIL"} and result.get("external_validation_status") not in {"REQUIRED", "PENDING"}:
            continue
        result_id = result.get("id")
        if not isinstance(result_id, str):
            raise _gap_finding("RESULT_GAP_SOURCE_FIELDS", "prototype test gap source is missing id", file=prototype_path)
        entry = _gap_from_optional_record(result, canonical_key=_canonical_gap_key("prototype", "prototype-test-result", result_id, 1, "PROTOTYPE_TEST_STATUS"), source_file=prototype_path, required=True, category="MANDATORY")
        if entry:
            _add_source_gap(source_gaps, entry)

    for record_type, projection_key, identity_field, statuses, category in (
        ("output-version", "outputs", "output_id", {"CANDIDATE", "REJECTED", "SUPERSEDED"}, "MANDATORY"),
        ("quality-result", "quality", "quality_id", {"NOT_RUN", "FAIL", "BLOCKED", "EXTERNAL_VALIDATION_REQUIRED", "SKIPPED"}, "MANDATORY"),
        ("installation-result", "installation_results", "installation_result_id", {"NOT_RUN", "FAILED", "EXTERNAL_VALIDATION_REQUIRED", "SKIPPED"}, "SAFETY"),
        ("installation-plan", "installation_plan", "installation_plan_id", {"BLOCKED", "CANCELLED"}, "SAFETY"),
    ):
        source_file = project_root / ("05_execution/" if projection_key in {"outputs", "quality"} else "06_installation/") / f"{projection_key.replace('_', '-')}.yaml"
        records = _latest_records(projections[projection_key].get("records", []), identity_field)
        for record in records:
            if record.get("status") not in statuses:
                continue
            identity = record.get(identity_field)
            if not isinstance(identity, str):
                raise _gap_finding("RESULT_GAP_SOURCE_FIELDS", f"{record_type} gap source is missing identity", file=source_file)
            entry = _gap_from_optional_record(record, canonical_key=_canonical_gap_key("execution", record_type, identity, int(record.get("revision", 0)), "EXECUTION_STATUS"), source_file=source_file, required=True, category=category)
            if entry:
                _add_source_gap(source_gaps, entry)

    for risk in _records({"items": plan.get("risks", [])}, "items"):
        if risk.get("status") not in {"OPEN", "ACCEPTED"} or risk.get("severity") not in {"MAJOR", "CRITICAL"}:
            continue
        risk_id = risk.get("id")
        if not isinstance(risk_id, str):
            raise _gap_finding("RESULT_GAP_SOURCE_FIELDS", "risk gap source is missing id", file=plan_path)
        risk_gap = risk.get("gap")
        if not isinstance(risk_gap, dict):
            raise _gap_finding("RESULT_GAP_SOURCE_FIELDS", f"unresolved risk {risk_id} requires an explicit structured gap", file=plan_path)
        entry = _source_gap(risk_gap, canonical_key=_canonical_gap_key("plan", "risk", risk_id, 1, "RISK_OPEN"), source_file=plan_path, category=risk.get("severity"))
        _add_source_gap(source_gaps, entry)

    for event in _runtime_events(project_root):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event.get("type") == "PROJECT_STATE_TRANSITIONED" and payload.get("to_state") == "BLOCKED":
            event_id = event.get("event_id")
            if not isinstance(event_id, str):
                raise _gap_finding("RESULT_GAP_SOURCE_FIELDS", "runtime BLOCKED event is missing event_id", file=project_root / "08_runtime/run-log.jsonl")
            source = {"id": "RUNTIME_BLOCKED", "statement": payload.get("blocker"), "impact": payload.get("impact"), "owner": payload.get("owner"), "blocking": True, "resolution_condition": payload.get("resolution_condition")}
            entry = _source_gap(source, canonical_key=_canonical_gap_key("runtime", "state-transition", event_id, int(event.get("sequence", 0)), "RUNTIME_BLOCKED"), source_file=project_root / "08_runtime/run-log.jsonl", category="MANDATORY")
            _add_source_gap(source_gaps, entry)

    runtime_path = project_root / "08_runtime/production-state.json"
    for task_id, task in (runtime_state.get("task_states") or {}).items():
        if not isinstance(task, dict) or task.get("status") in {"DONE", "SKIPPED"}:
            continue
        entry = _gap_from_optional_record(task, canonical_key=_canonical_gap_key("runtime", "runtime-task", str(task_id), 1, "RUNTIME_TASK_INCOMPLETE"), source_file=runtime_path, required=True, category="MANDATORY")
        if entry:
            _add_source_gap(source_gaps, entry)
    for effect_key, effect in (runtime_state.get("effects") or {}).items():
        if not isinstance(effect, dict) or effect.get("status") not in {"FAILED", "UNKNOWN"}:
            continue
        entry = _gap_from_optional_record(effect, canonical_key=_canonical_gap_key("runtime", "runtime-effect", str(effect_key), 1, "RUNTIME_EFFECT_STATUS"), source_file=runtime_path, required=True, category="SAFETY" if effect.get("status") == "UNKNOWN" else "MAJOR")
        if entry:
            _add_source_gap(source_gaps, entry)

    ordered = sorted(source_gaps.values(), key=lambda item: item["canonical_key"].encode("utf-8"))
    gaps: list[dict[str, Any]] = []
    gap_sources: dict[str, dict[str, Any]] = {}
    blocking_ids: list[str] = []
    for index, source in enumerate(ordered, start=1):
        result_id = f"GP{index:03d}"
        gap = dict(source["gap"])
        gap["id"] = result_id
        gaps.append(gap)
        gap_sources[result_id] = {"canonical_keys": [source["canonical_key"]], "blocking": source["blocking"], "category": source["category"], "source_file": source["source_file"]}
        if source["blocking"]:
            blocking_ids.append(result_id)
    return gaps, gap_sources, blocking_ids


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


def validate_completion_report(report: dict[str, Any], *, repository: Path, report_path: Path | str = "completion-report", expected_result: dict[str, Any] | None = None) -> None:
    schema_path = repository / "schemas" / COMPLETION_REPORT_SCHEMA
    schema = load_schema(schema_path)
    common = load_schema(repository / "schemas/common.schema.json")
    findings = validate_instance(report, schema, schema_path=schema_path, common_schema=common)
    if findings:
        raise DiagnosticError(findings[0])
    if report.get("status") == "OPEN":
        return
    expected_hash = canonical_sha256({key: value for key, value in report.items() if key != "integrity"})
    if report.get("integrity", {}).get("content_sha256") != expected_hash:
        raise DiagnosticError(_finding("COMPLETION_REPORT_INTEGRITY", "completion report content_sha256 does not match its canonical payload", file=report_path, location="/integrity/content_sha256", remediation="Regenerate the completion report from the canonical production result."))
    gap_ids = set(report.get("open_gap_ids", []))
    source_ids = set(report.get("gap_sources", {}))
    if gap_ids != source_ids:
        raise DiagnosticError(_finding("COMPLETION_REPORT_GAP_SOURCES", "gap_sources must map exactly every open gap ID", file=report_path, location="/gap_sources", remediation="Regenerate completion report gap_sources from the result gap projection."))
    if not set(report.get("blocking_gap_ids", [])).issubset(gap_ids):
        raise DiagnosticError(_finding("COMPLETION_REPORT_BLOCKING_GAPS", "blocking_gap_ids contains an ID that is not open", file=report_path, location="/blocking_gap_ids", remediation="Use only open result gap IDs as blocking gaps."))
    if expected_result is not None:
        if report.get("project_id") != expected_result.get("production_project_id") or report.get("result_id") != expected_result.get("result_id") or report.get("result_sha256") != expected_result.get("integrity", {}).get("content_sha256"):
            raise DiagnosticError(_finding("COMPLETION_REPORT_RESULT_MISMATCH", "completion report does not address the canonical production result", file=report_path, remediation="Regenerate the report from the same result ID and result hash."))
        expected_ids = {gap.get("id") for gap in expected_result.get("open_gaps", [])}
        if gap_ids != expected_ids:
            raise DiagnosticError(_finding("COMPLETION_REPORT_GAP_MISMATCH", "completion report open_gap_ids do not match production-result open_gaps", file=report_path, remediation="Regenerate the report from the canonical result gap projection."))
    policy = load_config(repository, "safety-policy.yaml")
    security_findings = check_text_security(report, file=report_path, forbidden_markers=policy.get("forbidden_markers", []), signed_url_markers=policy.get("signed_url_markers", []))
    if security_findings:
        finding = security_findings[0]
        raise DiagnosticError(_finding(finding.rule, finding.reason, file=report_path, location=finding.location, remediation=finding.remediation))


def _completion_check(rule: str, passed: bool, source_refs: list[str], details: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"rule": rule, "status": "PASS" if passed else "FAIL", "source_refs": sorted(set(source_refs)) or ["project"]}
    if details:
        value["details"] = details
    return value


def _latest_execution_records(projections: dict[str, dict[str, Any]], key: str, identity: str) -> list[dict[str, Any]]:
    return _latest_records(projections[key].get("records", []), identity)


def _completion_checks(manifest: dict[str, Any], handoff: dict[str, Any], plan: dict[str, Any], prototype: dict[str, Any], runtime_state: dict[str, Any], projections: dict[str, dict[str, Any]], result: dict[str, Any], gap_metadata: dict[str, dict[str, Any]], target_state: str, project_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    checks: list[dict[str, Any]] = []
    plan_path = "03_plan/production-plan.yaml"
    mandatory_requirements = set(plan.get("mandatory_requirement_ids", []))
    deliverables = [item for item in plan.get("deliverables", []) if isinstance(item, dict)]
    mandatory_deliverables = [item for item in deliverables if mandatory_requirements.intersection(item.get("source_requirement_ids", []))]
    available_by_deliverable = {item.get("deliverable_id") for item in result.get("outputs", [])}
    missing_deliverables = sorted({str(item.get("id")) for item in mandatory_deliverables if item.get("id") not in available_by_deliverable})
    missing_requirements = sorted(requirement for requirement in mandatory_requirements if not any(requirement in item.get("source_requirement_ids", []) for item in mandatory_deliverables))
    checks.append(_completion_check("accepted_handoff_scope", handoff.get("status") == "READY" and plan.get("scope_baseline", {}).get("status") == "BASELINED", ["00_handoff/production-handoff.yaml", plan_path]))
    checks.append(_completion_check("mandatory_deliverables_available", not missing_deliverables and not missing_requirements, [plan_path, "05_execution/output-versions.yaml"], "missing deliverables: " + ", ".join(missing_deliverables + missing_requirements) if (missing_deliverables or missing_requirements) else None))

    quality_records = _latest_execution_records(projections, "quality", "quality_id")
    quality_by_output = {record.get("output_id"): record for record in quality_records}
    quality_missing: list[str] = []
    for output in result.get("outputs", []):
        quality = quality_by_output.get(output.get("id"))
        if not isinstance(quality, dict) or quality.get("status") != "PASS" or quality.get("external_validation_status") != "VERIFIED" or not quality.get("evidence_refs"):
            quality_missing.append(str(output.get("id")))
    checks.append(_completion_check("available_output_quality_verified", not quality_missing, ["05_execution/quality-results.yaml"], "outputs without PASS/VERIFIED quality evidence: " + ", ".join(sorted(quality_missing)) if quality_missing else None))

    prototype_test_by_acceptance = {item.get("acceptance_test_id"): item for item in _records(prototype, "test_results") if isinstance(item.get("acceptance_test_id"), str)}
    failed_tests = sorted(str(test.get("acceptance_test_id")) for test in result.get("test_results", []) if test.get("result") != "PASS" or not isinstance(prototype_test_by_acceptance.get(test.get("acceptance_test_id")), dict) or not prototype_test_by_acceptance[test.get("acceptance_test_id")].get("evidence_refs"))
    checks.append(_completion_check("mandatory_acceptance_tests", not failed_tests, [plan_path, "04_prototype/prototype-control.yaml"], "tests not PASS with registered evidence: " + ", ".join(failed_tests) if failed_tests else None))

    specifications = [item for item in plan.get("technical_specifications", []) if isinstance(item, dict)]
    incomplete_specs = sorted(str(item.get("id")) for item in specifications if item.get("status") != "BASELINED" or (item.get("measurement_status") is not None and item.get("measurement_status") != "PASS"))
    checks.append(_completion_check("technical_measurements_complete", bool(specifications) and not incomplete_specs, [plan_path], "technical specifications not BASELINED: " + ", ".join(incomplete_specs) if incomplete_specs else None))

    budget = plan.get("budget", {}) if isinstance(plan.get("budget"), dict) else {}
    budget_complete = budget.get("status") == "ACTUAL" and isinstance(budget.get("currency"), str) and budget.get("baseline_total") is not None and budget.get("actual_total") is not None and budget.get("variance") is not None and all(isinstance(item, dict) and item.get("status") == "ACTUAL" for item in budget.get("items", []))
    checks.append(_completion_check("actual_budget_and_variance", budget_complete, [plan_path]))

    installation_records = _latest_execution_records(projections, "installation_results", "installation_result_id")
    installation_plans = _latest_execution_records(projections, "installation_plan", "installation_plan_id")
    installation_target = bool(installation_plans)
    if installation_target:
        installation_ok = any(record.get("status") == "SUCCEEDED" and record.get("safety_check_status") == "PASS" and record.get("evidence_refs") and record.get("completed_at") for record in installation_records)
        installation_ref = "06_installation/installation-results.yaml"
    else:
        skip = plan.get("installation_skip_decision")
        if not isinstance(skip, dict):
            for event in _runtime_events(project_root):
                payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                if event.get("type") == "PROJECT_STATE_TRANSITIONED" and payload.get("to_state") == "VALIDATING" and isinstance(payload.get("installation_skip_decision"), dict):
                    skip = payload["installation_skip_decision"]
                    break
        installation_ok = isinstance(skip, dict) and all(isinstance(skip.get(key), str) and skip.get(key).strip() for key in ("target", "reason", "basis")) and isinstance(skip.get("approval_required"), bool)
        installation_ref = plan_path
    checks.append(_completion_check("installation_or_structured_skip", installation_ok, [installation_ref]))

    open_risks = sorted(str(item.get("id")) for item in plan.get("risks", []) if isinstance(item, dict) and item.get("status") == "OPEN" and item.get("severity") in {"MAJOR", "CRITICAL"})
    checks.append(_completion_check("no_open_major_critical_risk", not open_risks, [plan_path], "open risks: " + ", ".join(open_risks) if open_risks else None))

    rights_statuses = {str(output.get("rights_status")) for output in result.get("outputs", [])}
    declared_statuses = [manifest.get("rights_status"), manifest.get("privacy_status"), manifest.get("publication_status")]
    definite = {"CLEAR", "PROJECT_INTERNAL", "NOT_APPLICABLE", "HUMAN_APPROVAL_REQUIRED", "HUMAN_APPROVED", "PUBLISHED", "UNPUBLISHED"}
    rights_ok = bool(result.get("outputs")) and rights_statuses.issubset({"CLEAR", "PROJECT_INTERNAL"}) and all(status in definite for status in declared_statuses)
    checks.append(_completion_check("rights_privacy_publication_status_definite", rights_ok, ["manifest.yaml", "05_execution/output-versions.yaml"]))

    if target_state in {"COMPLETE", "COMPLETE_WITH_GAPS"}:
        runtime_ok = runtime_state.get("state") in {target_state, "VALIDATING"}
        checks.append(_completion_check("runtime_terminal_candidate", runtime_ok, ["08_runtime/production-state.json"]))
    if target_state == "COMPLETE":
        checks.append(_completion_check("open_gaps_empty", not result.get("open_gaps"), ["production-result.yaml"], "open gaps: " + ", ".join(sorted(gap.get("id", "") for gap in result.get("open_gaps", []))) if result.get("open_gaps") else None))
    elif target_state == "COMPLETE_WITH_GAPS":
        forbidden_categories = {"SAFETY", "RIGHTS", "PRIVACY", "MANDATORY", "MAJOR", "CRITICAL"}
        forbidden = sorted(gap_id for gap_id, metadata in gap_metadata.items() if metadata.get("blocking") or metadata.get("category") in forbidden_categories)
        checks.append(_completion_check("remaining_gaps_non_blocking", bool(result.get("open_gaps")) and not forbidden, ["production-result.yaml", "08_runtime/completion-report.json"], "blocking or protected gaps: " + ", ".join(forbidden) if forbidden else None))

    checks.append(_completion_check("production_result_schema_and_hash", True, ["production-result.yaml"]))
    missing = sorted(check["rule"] for check in checks if check["status"] != "PASS")
    return checks, missing


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        os.close(descriptor)
        Path(temporary).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _completion_report(project_root: Path, result: dict[str, Any], *, target_state: str, generated_at: str, checks: list[dict[str, Any]], gap_metadata: dict[str, dict[str, Any]], blocking_gap_ids: list[str], rejected: bool) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": "1.0.0",
        "project_id": result["production_project_id"],
        "target_state": target_state,
        "result_id": result["result_id"],
        "result_sha256": result["integrity"]["content_sha256"],
        "evaluated_at": generated_at,
        "checks": checks,
        "open_gap_ids": [str(gap["id"]) for gap in result.get("open_gaps", [])],
        "blocking_gap_ids": sorted(set(blocking_gap_ids), key=lambda value: value.encode("utf-8")),
        "gap_sources": {gap_id: metadata["canonical_keys"] for gap_id, metadata in sorted(gap_metadata.items(), key=lambda item: item[0].encode("utf-8"))},
        "status": "REJECTED" if rejected else "READY",
    }
    report["integrity"] = {"content_sha256": canonical_sha256(report)}
    return report


def build_result(project_root: Path, repository: Path, *, result_id: str, generated_at: str, production_commit: str | None = None, target_state: str | None = None) -> tuple[dict[str, Any], bool]:
    project_root = project_root.resolve()
    repository = repository.resolve()
    manifest = _load_mapping(project_root / "manifest.yaml")
    handoff = _load_mapping(project_root / "00_handoff/production-handoff.yaml")
    plan = _load_mapping(project_root / "03_plan/production-plan.yaml")
    prototype = _load_mapping(project_root / "04_prototype/prototype-control.yaml", required=False)
    runtime_state = _load_mapping(project_root / "08_runtime/production-state.json")
    if target_state is None:
        target_state = runtime_state.get("state") if runtime_state.get("state") in TARGET_STATES else "BLOCKED"
    if target_state not in TARGET_STATES:
        raise DiagnosticError(_finding("RESULT_TARGET_STATE", "target_state must be COMPLETE, COMPLETE_WITH_GAPS, or BLOCKED", file=project_root / "08_runtime/completion-report.json", remediation="Choose an explicit result completion target state."))
    ExecutionManager(project_root, repository).replay()
    if (project_root / EVIDENCE_LOG).exists() or (project_root / EVIDENCE_REGISTER).exists():
        from .evidence import EvidenceManager

        EvidenceManager(project_root, repository).replay()
    projections = {
        "outputs": _load_mapping(project_root / "05_execution/output-versions.yaml"),
        "quality": _load_mapping(project_root / "05_execution/quality-results.yaml"),
        "observations": _load_mapping(project_root / "05_execution/observations.yaml"),
        "installation_plan": _load_mapping(project_root / "06_installation/installation-plan.yaml"),
        "installation_results": _load_mapping(project_root / "06_installation/installation-results.yaml"),
    }
    open_gaps, gap_metadata, blocking_gap_ids = _gaps(project_root, handoff, plan, prototype, runtime_state, projections)
    result: dict[str, Any] = {
        "schema_version": "1.0.0",
        "result_id": result_id,
        "production_project_id": manifest.get("project_id"),
        "production_commit": production_commit or _git_commit(repository),
        "generated_at": generated_at,
        "accepted_handoff": {"id": handoff.get("handoff_id"), "revision": handoff.get("revision"), "content_sha256": handoff.get("integrity", {}).get("content_sha256"), "research_project_id": handoff.get("research_project_id"), "research_commit": handoff.get("research_commit")},
        "selection": _selection(plan),
        "outputs": _output_records(projections),
        "test_results": _test_results(project_root, repository, plan, prototype, result_id, generated_at),
        "observations": _observations(projections["observations"]),
        "deviations": _deviations(prototype),
        "incidents": _incidents(runtime_state),
        "research_change_requests": _change_requests(project_root),
        "open_gaps": open_gaps,
    }
    result["integrity"] = {"content_sha256": result_sha256(result)}
    validate_result(result, repository=repository, result_path=project_root / "08_runtime/production-result.yaml")
    checks, missing_rules = _completion_checks(manifest, handoff, plan, prototype, runtime_state, projections, result, gap_metadata, target_state, project_root)
    if target_state == "BLOCKED":
        if not blocking_gap_ids:
            missing_rules = sorted(set(missing_rules + ["blocked_requires_blocking_gap_and_resume_condition"]))
    elif target_state == "COMPLETE_WITH_GAPS":
        if not result.get("open_gaps"):
            missing_rules = sorted(set(missing_rules + ["remaining_gaps_non_blocking"]))
    report_path = project_root / "08_runtime/completion-report.json"
    if report_path.is_file():
        existing_report = _load_mapping(report_path)
        if existing_report.get("status") != "OPEN" and existing_report.get("result_id") == result_id and existing_report.get("target_state") != target_state:
            raise DiagnosticError(_finding("RESULT_TARGET_STATE_MISMATCH", "the same result ID is already bound to a different target state", file=report_path, location="/target_state", remediation="Use the original target state or choose a new result ID."))
    rejected = (target_state == "BLOCKED" and not blocking_gap_ids) or (target_state != "BLOCKED" and bool(missing_rules))
    report = _completion_report(project_root, result, target_state=target_state, generated_at=generated_at, checks=checks, gap_metadata=gap_metadata, blocking_gap_ids=blocking_gap_ids, rejected=rejected)
    validate_completion_report(report, repository=repository, report_path=report_path, expected_result=result)
    if rejected:
        _write_json_atomic(report_path, report)
        raise DiagnosticError(_finding("RESULT_COMPLETION_INCOMPLETE", "completion target is not substantiated; missing rules: " + ", ".join(sorted(set(missing_rules))), file=report_path, location="/checks", remediation="Resolve every listed rule or select BLOCKED with a recorded blocking gap and resume condition."))
    result_path = project_root / "08_runtime/production-result.yaml"
    result_idempotent = False
    if result_path.is_file():
        existing = _load_mapping(result_path)
        if existing == result:
            result_idempotent = True
        if existing.get("result_id") == result_id:
            if not result_idempotent:
                raise DiagnosticError(_finding("RESULT_IDEMPOTENCY_MISMATCH", "result_id already exists with different canonical content", file=result_path, remediation="Use the original result inputs or choose a new result_id."))
    if not result_idempotent:
        _write_yaml_atomic(result_path, result)
    report_idempotent = False
    if report_path.is_file():
        existing_report = _load_mapping(report_path)
        if existing_report.get("result_id") == result_id:
            validate_completion_report(existing_report, repository=repository, report_path=report_path, expected_result=result) if existing_report.get("status") != "OPEN" else None
            if existing_report == report:
                report_idempotent = True
            else:
                raise DiagnosticError(_finding("RESULT_IDEMPOTENCY_MISMATCH", "result_id already exists with different completion report content", file=report_path, remediation="Use the original result inputs or choose a new result_id."))
    if not report_idempotent:
        _write_json_atomic(report_path, report)
    return result, result_idempotent and report_idempotent


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
