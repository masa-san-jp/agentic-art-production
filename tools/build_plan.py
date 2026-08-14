#!/usr/bin/env python3
"""Build a deterministic production plan from an accepted handoff project."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lib.canonical import canonical_sha256
from tools.lib.config import load_config
from tools.lib.diagnostics import DiagnosticError, EXIT_SUCCESS, EXIT_VALIDATION, Finding, emit_findings
from tools.lib.planning import validate_plan_document
from tools.lib.security import validate_asset_uri
from tools.lib.yaml_io import dump_yaml, load_json, load_yaml


ARTIFACT_FILES = {
    "requirements": "production-requirements.yaml",
    "hypotheses": "production-hypotheses.yaml",
    "prototype_plans": "prototype-plans.yaml",
    "acceptance_tests": "acceptance-tests.yaml",
    "source_refs": "source-ref-index.yaml",
}
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
ZERO_SHA256 = "sha256:" + "0" * 64


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _finding(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation)


def _require_mapping(path: Path) -> dict[str, Any]:
    value = load_yaml(path)
    if not isinstance(value, dict):
        raise DiagnosticError(_finding("PLANNING_INPUT_OBJECT", "planning input must be a YAML mapping", file=path, remediation="Restore the accepted handoff artifact as a mapping."))
    return value


def _records(value: dict[str, Any], key: str, path: Path) -> list[dict[str, Any]]:
    records = value.get(key, [])
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise DiagnosticError(_finding("PLANNING_INPUT_RECORDS", f"{key} must be a list of objects", file=path, location=f"/{key}", remediation="Regenerate the research handoff artifact with its declared record collection."))
    return sorted(records, key=lambda record: str(record.get("id", "")))


def _load_inputs(project_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    handoff_path = project_root / "00_handoff/production-handoff.yaml"
    manifest_path = project_root / "00_handoff/source-bundle-manifest.yaml"
    bundle_root = project_root / "00_handoff/source-bundle"
    handoff = _require_mapping(handoff_path)
    bundle_manifest = _require_mapping(manifest_path)
    if not bundle_root.is_dir():
        raise DiagnosticError(_finding("PLANNING_BUNDLE_MISSING", "accepted project does not contain its source bundle", file=bundle_root, remediation="Re-accept a self-contained handoff before building a plan."))
    artifacts: dict[str, dict[str, Any]] = {}
    for name, filename in ARTIFACT_FILES.items():
        artifacts[name] = _require_mapping(bundle_root / "artifacts" / filename)
    source_input = {"handoff": handoff, "bundle_manifest": bundle_manifest, "artifacts": artifacts}
    return handoff, bundle_manifest, source_input, artifacts


def _trace(*values: str) -> list[str]:
    return list(dict.fromkeys(values))


def _reference_access(project_root: Path, handoff: dict[str, Any], artifacts: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize production-relevant source references and derive missing-URL gaps."""
    path = project_root / "00_handoff/source-bundle/artifacts/source-ref-index.yaml"
    index = artifacts["source_refs"]
    index_key = "records"
    if "records" not in index:
        raise DiagnosticError(_finding("PLANNING_SOURCE_REF_SCHEMA", "source-ref index must use the canonical records collection", file=path, location="/records", remediation="Regenerate the Research handoff with source-ref-index.records and record_sha256 fields."))
    records = _records(index, index_key, path)
    policy = load_config(repository_root(), "reference-policy.yaml")
    configured_categories = policy.get("categories", [])
    if not isinstance(configured_categories, list) or not all(isinstance(item, dict) for item in configured_categories):
        raise DiagnosticError(_finding("PLANNING_REFERENCE_POLICY", "reference categories must be configured as objects", file=repository_root() / "config/reference-policy.yaml", location="/categories", remediation="Restore the repository reference policy."))
    category_by_id = {str(item.get("id")): item for item in configured_categories}
    required_categories = [category_id for category_id, item in category_by_id.items() if item.get("required") is True]
    handoff_refs = handoff.get("source_refs", {})
    referenced_ids = _trace(*[
        str(value)
        for key in ("decision_ids", "insight_ids", "evidence_ids")
        for value in (handoff_refs.get(key, []) if isinstance(handoff_refs, dict) else [])
    ])
    by_id = {str(record.get("id")): record for record in records if record.get("id")}
    normalized: list[dict[str, Any]] = []
    available_categories: set[str] = set()
    for source_id in referenced_ids:
        record = by_id.get(source_id)
        if record is None:
            raise DiagnosticError(_finding("PLANNING_REFERENCE_MISSING", f"source-ref index does not contain referenced ID {source_id}", file=path, location=f"/{index_key}", remediation="Regenerate the handoff with every referenced decision, insight, and evidence record."))
        categories = record.get("reference_categories", [])
        if not isinstance(categories, list) or not all(isinstance(value, str) for value in categories):
            raise DiagnosticError(_finding("PLANNING_REFERENCE_CATEGORY", "reference_categories must be a list of category IDs", file=path, location=f"/{index_key}/{source_id}/reference_categories", remediation="Use category IDs declared in config/reference-policy.yaml."))
        unknown = sorted(set(categories) - set(category_by_id))
        if unknown:
            raise DiagnosticError(_finding("PLANNING_REFERENCE_CATEGORY", f"unknown reference categories: {', '.join(unknown)}", file=path, location=f"/{index_key}/{source_id}/reference_categories", remediation="Use category IDs declared in config/reference-policy.yaml."))
        access_url = record.get("access_url")
        if access_url is not None:
            if not isinstance(access_url, str) or not access_url or any(character.isspace() for character in access_url):
                raise DiagnosticError(_finding("PLANNING_REFERENCE_URL", "access_url must be a non-empty URL without whitespace", file=path, location=f"/{index_key}/{source_id}/access_url", remediation="Provide a stable permanent HTTPS URL without credentials or signed parameters."))
            uri_finding = validate_asset_uri(
                access_url,
                allowed_schemes=policy.get("allowed_uri_schemes", ["https"]),
                allow_query=bool(policy.get("https", {}).get("allow_query", False)),
            )
            parsed = urlsplit(access_url)
            if uri_finding is not None or not parsed.hostname:
                reason = uri_finding.reason if uri_finding is not None else "HTTPS reference URL must contain a hostname"
                raise DiagnosticError(_finding("PLANNING_REFERENCE_URL", reason, file=path, location=f"/{index_key}/{source_id}/access_url", remediation="Provide a stable permanent HTTPS URL without credentials, query parameters, or fragments."))
        record_hash = record.get("record_sha256")
        if not isinstance(record_hash, str) or not SHA256_PATTERN.fullmatch(record_hash) or record_hash == ZERO_SHA256:
            raise DiagnosticError(_finding("PLANNING_REFERENCE_HASH", "source-ref record hash must be a non-zero canonical SHA-256", file=path, location=f"/{index_key}/{source_id}/record_sha256", remediation="Regenerate the Research handoff so every source record carries its canonical record_sha256."))
        if access_url is not None:
            available_categories.update(categories)
        normalized.append({
            "source_ref_id": source_id,
            "kind": str(record.get("kind") or "unknown"),
            "reference_categories": categories,
            "summary": str(record.get("summary") or "Summary not supplied."),
            "access_url": access_url,
            "access_status": "AVAILABLE" if access_url is not None else "MISSING",
            "record_hash": record_hash,
        })
    gaps = [
        {
            "id": f"PG{index + 3:03d}",
            "statement": f"Reference {record['source_ref_id']} has no access URL in the accepted handoff.",
            "blocking": False,
        }
        for index, record in enumerate(item for item in normalized if item["access_status"] == "MISSING")
    ]
    gap_offset = len(gaps) + 3
    gaps.extend(
        {
            "id": f"PG{gap_offset + index:03d}",
            "statement": f"{category_by_id[category_id].get('label', category_id)} reference access URL is not supplied by the accepted handoff.",
            "blocking": True,
        }
        for index, category_id in enumerate(
            category_id for category_id in required_categories if category_id not in available_categories
        )
    )
    return normalized, gaps


def _derived_plan_records(
    *,
    plan: dict[str, Any],
    handoff: dict[str, Any],
    requirements: list[dict[str, Any]],
    hypotheses: list[dict[str, Any]],
    prototype_plans: list[dict[str, Any]],
    acceptance_tests: list[dict[str, Any]],
    trace: list[str],
) -> None:
    """Replace example-shaped projections with records derived from handoff inputs."""
    selected_id = str(plan["selection_record"]["selected_hypothesis_id"])
    selected = next(item for item in hypotheses if str(item.get("id")) == selected_id)
    requirement_ids = [str(item["id"]) for item in requirements]
    acceptance_by_id = {str(item["id"]): item for item in acceptance_tests}
    selected_ids = {str(item) for item in handoff.get("prototype_plan_ids", [])}
    prototypes = [item for item in prototype_plans if str(item.get("id")) in selected_ids]
    prototype_ids = [str(item["id"]) for item in prototypes]
    tests_for = lambda item: [str(value) for value in item.get("acceptance_test_ids", []) if str(value) in acceptance_by_id]

    deliverable_title = str(selected.get("title") or selected.get("proposition") or selected_id)
    plan["deliverables"] = [{
        "id": "DL001", "title": f"{deliverable_title} prototype",
        "type": "physical-prototype" if prototypes else "production-prototype-plan",
        "source_requirement_ids": requirement_ids, "technical_spec_ids": [f"TS{index:03d}" for index, _ in enumerate(requirements, 1)],
        "acceptance_test_ids": sorted({test_id for item in requirements for test_id in tests_for(item)}),
        "owner_capability": str(prototypes[0].get("executor_capability") if prototypes and prototypes[0].get("executor_capability") else "production-planner"),
        "due_milestone_id": "MS001", "status": "PLANNED", "trace_refs": _trace(*trace, *prototype_ids, "DL001"),
    }]
    plan["technical_specifications"] = [{
        "id": f"TS{index:03d}", "deliverable_id": "DL001", "parameter": str(requirement.get("category") or requirement["id"]).lower(),
        "target": {"kind": "QUALITATIVE", "statement": str(requirement.get("statement") or "Requirement statement not supplied.")},
        "tolerance": None, "measurement_method": str(acceptance_by_id.get(str((requirement.get("acceptance_test_ids") or [""])[0]), {}).get("method") or "human_review"),
        "source_requirement_ids": [str(requirement["id"])], "status": "PROVISIONAL", "trace_refs": _trace(*trace, str(requirement["id"]), f"TS{index:03d}"),
    } for index, requirement in enumerate(requirements, 1)]

    material_values: list[str] = []
    for prototype in prototypes:
        for value in prototype.get("inputs", []):
            value = str(value)
            if value not in material_values:
                material_values.append(value)
    plan["materials"] = [{
        "id": f"MT{index:03d}", "name": value, "specification": f"Input declared by {', '.join(prototype_ids)}.",
        "quantity": {"value": "1", "unit": "item"}, "rights_status": "UNKNOWN", "safety_status": "UNKNOWN",
        "source_prototype_plan_ids": prototype_ids, "status": "CANDIDATE", "trace_refs": _trace(*trace, *prototype_ids, f"MT{index:03d}"),
    } for index, value in enumerate(material_values, 1)]

    tasks: list[dict[str, Any]] = []
    task_ids_by_source: dict[tuple[str, str], str] = {}
    for prototype in prototypes:
        for source_task in prototype.get("tasks", []):
            task_id = f"TK{len(tasks) + 1:03d}"
            task_ids_by_source[(str(prototype["id"]), str(source_task.get("id")))] = task_id
            tasks.append({
                "id": task_id, "work_package_id": "WP001", "title": str(source_task.get("title") or source_task.get("id")),
                "depends_on": [], "required_resource_ids": [], "required_material_ids": [],
                "acceptance_condition": str(source_task.get("completion_condition") or "Completion condition not supplied."),
                "effect_type": "PHYSICAL_EXTERNAL", "approval_requirement_ids": [], "duration": {"value": "1", "unit": "h"},
                "status": "BLOCKED", "trace_refs": _trace(*trace, str(prototype["id"]), str(source_task.get("id")), task_id),
            })
    for prototype in prototypes:
        for source_task in prototype.get("tasks", []):
            task_id = task_ids_by_source[(str(prototype["id"]), str(source_task.get("id")))]
            next(task for task in tasks if task["id"] == task_id)["depends_on"] = [
                task_ids_by_source[(str(prototype["id"]), str(dependency))]
                for dependency in source_task.get("depends_on", [])
                if (str(prototype["id"]), str(dependency)) in task_ids_by_source
            ]
    if tasks:
        tasks.append({
            "id": "TK004", "work_package_id": "WP001", "title": "Validate derived plan references and coverage", "depends_on": [],
            "required_resource_ids": [], "required_material_ids": [], "acceptance_condition": "Every mandatory requirement is connected to the derived plan records.",
            "effect_type": "READ_ONLY", "approval_requirement_ids": [], "duration": {"value": "15", "unit": "min"}, "status": "READY", "trace_refs": _trace(*trace, "TK004"),
        })
    else:
        tasks = [{
            "id": "TK004", "work_package_id": "WP001", "title": "Validate derived plan references and coverage", "depends_on": [],
            "required_resource_ids": [], "required_material_ids": [], "acceptance_condition": "Every mandatory requirement is connected to the derived plan records.",
            "effect_type": "READ_ONLY", "approval_requirement_ids": [], "duration": {"value": "15", "unit": "min"}, "status": "READY", "trace_refs": _trace(*trace, "TK004"),
        }]
    physical_task_ids = [task["id"] for task in tasks if task["effect_type"] == "PHYSICAL_EXTERNAL"]
    resource_ids: list[str] = []
    plan["resources"] = []
    for index, prototype in enumerate(prototypes, 1):
        capability = prototype.get("executor_capability")
        if not capability:
            continue
        resource_id = f"RS{index:03d}"
        resource_ids.append(resource_id)
        plan["resources"].append({
            "id": resource_id, "type": "PERSON_CAPABILITY", "capability": str(capability), "quantity": {"value": "1", "unit": "item"},
            "availability": "REQUIRES_CONFIRMATION", "source_task_ids": [task["id"] for task in tasks if str(prototype["id"]) in task["trace_refs"] and task["effect_type"] == "PHYSICAL_EXTERNAL"], "trace_refs": _trace(*trace, str(prototype["id"]), resource_id),
        })
    material_ids = [item["id"] for item in plan["materials"]]
    for task in tasks:
        if task["id"] in physical_task_ids:
            task["required_resource_ids"] = resource_ids
            task["required_material_ids"] = material_ids
    approval_requirements: list[dict[str, Any]] = []
    if physical_task_ids:
        payload = {"action": "PHYSICAL_EXTERNAL", "target_ref": f"03_plan/task-plan.yaml#{','.join(physical_task_ids)}", "task_ids": physical_task_ids}
        approval_requirements = [{"id": "AR001", "action": "PHYSICAL_EXTERNAL", "target_ref": payload["target_ref"], "target_sha256": canonical_sha256(payload), "authority": "HUMAN", "status": "REQUIRED", "reason": "Prototype tasks have physical or external effects and require explicit human approval.", "task_ids": physical_task_ids, "trace_refs": _trace(*trace, "AR001", *physical_task_ids)}]
        for task in tasks:
            if task["id"] in physical_task_ids:
                task["approval_requirement_ids"] = ["AR001"]
    plan["tasks"] = tasks
    task_ids = [task["id"] for task in tasks]
    plan["work_packages"] = [{
        "id": "WP001", "title": f"{deliverable_title} production work package", "deliverable_ids": ["DL001"],
        "input_ids": [item["id"] for item in plan["technical_specifications"]], "output_ids": prototype_ids + [str(item["id"]) for item in acceptance_tests],
        "depends_on": [], "owner_capability": plan["deliverables"][0]["owner_capability"], "review_gate_id": None, "task_ids": task_ids,
        "status": "BLOCKED" if physical_task_ids else "PLANNED", "trace_refs": _trace(*trace, "WP001"),
    }]
    milestones = [{"id": "MS001", "title": "Derived planning baseline generated", "sequence": 1, "depends_on": [], "status": "PLANNED", "trace_refs": _trace(*trace, "MS001")}]
    for index, task in enumerate(tasks, 2):
        milestones.append({"id": f"MS{index:03d}", "title": f"{task['title']} complete", "sequence": index, "depends_on": [milestones[-1]["id"]], "status": "BLOCKED" if task["status"] == "BLOCKED" else "PLANNED", "trace_refs": _trace(*trace, task["id"], f"MS{index:03d}")})
    plan["deliverables"][0]["due_milestone_id"] = milestones[-1]["id"]
    remaining = {task["id"]: set(task.get("depends_on", [])) for task in tasks}
    topo: list[str] = []
    while remaining:
        ready = sorted(item for item, dependencies in remaining.items() if not dependencies)
        if not ready:
            ready = sorted(remaining)
        topo.extend(ready)
        for item in ready:
            remaining.pop(item)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)
    edges = [{"from": dependency, "to": task["id"]} for task in tasks for dependency in task.get("depends_on", [])]
    plan["dependency_graph"] = {"graph_id": "DG001", "nodes": task_ids, "edges": edges, "topological_order": topo, "trace_refs": _trace(*trace, "DG001")}
    critical_path = physical_task_ids or [tasks[0]["id"]]
    plan["critical_path_task_ids"] = critical_path
    plan["schedule"] = {"schedule_id": "SCH001", "mode": "RELATIVE", "baseline_status": "PROVISIONAL", "milestones": milestones, "task_schedule": [{"task_id": task["id"], "duration": task["duration"], "start_at": None, "due_at": None} for task in tasks], "critical_path_task_ids": critical_path, "gaps": ["Task duration and calendar date inputs are not supplied; the schedule is relative."], "trace_refs": _trace(*trace, "SCH001")}
    plan["budget"] = {"budget_id": "BDG001", "currency": "JPY", "baseline_total": None, "contingency": None, "approval_threshold": None, "items": [], "gaps": ["Budget amounts, quotes, suppliers, and commitments are not supplied by the accepted handoff."], "status": "ESTIMATED", "trace_refs": _trace(*trace, "BDG001")}
    plan["risks"] = [{"id": f"RK{index:03d}", "title": str(item.get("id") or "Uncertainty"), "source_handoff_gap_ids": [], "source_requirement_ids": requirement_ids, "severity": "MAJOR" if str(item.get("severity")) in {"MAJOR", "CRITICAL"} else "MEDIUM", "likelihood": "UNKNOWN", "impact": str(item.get("statement") or "Uncertainty remains unresolved."), "mitigation": "Resolve or externally validate this uncertainty before the affected prototype task.", "owner_capability": "production-validator", "status": "OPEN", "blocking": False, "trace_refs": _trace(*trace, str(item.get("id") or f"U{index:03d}"), f"RK{index:03d}")} for index, item in enumerate(selected.get("uncertainties", []), 1)]
    plan["approval_register"] = {"register_id": "AGR001", "requirements": approval_requirements, "approvals": [], "trace_refs": _trace(*trace, "AGR001")}
    coverage_items = []
    for requirement in requirements:
        requirement_id = str(requirement["id"])
        tests = [str(item) for item in requirement.get("acceptance_test_ids", []) if str(item) in acceptance_by_id]
        covered = bool(tests and requirement_id in plan["deliverables"][0]["source_requirement_ids"] and any(requirement_id in spec["source_requirement_ids"] for spec in plan["technical_specifications"]))
        coverage_items.append({"requirement_id": requirement_id, "deliverable_ids": ["DL001"] if covered else [], "technical_spec_ids": [spec["id"] for spec in plan["technical_specifications"] if requirement_id in spec["source_requirement_ids"]], "acceptance_test_ids": tests, "work_package_ids": ["WP001"] if covered else [], "task_ids": task_ids if covered else [], "status": "COVERED" if covered else "UNCOVERED"})
    uncovered = [item["requirement_id"] for item in coverage_items if item["status"] == "UNCOVERED"]
    plan["coverage_report"] = {"report_id": "CV001", "requirements": coverage_items, "coverage_percent": round((len(coverage_items) - len(uncovered)) * 100 / len(coverage_items)) if coverage_items else 0, "uncovered_requirement_ids": uncovered, "trace_refs": _trace(*trace, "CV001")}
    next_gap = len(plan["gaps"]) + 1
    plan["gaps"].extend({
        "id": f"PG{next_gap + index:03d}",
        "statement": f"Requirement {requirement_id} is not connected to an acceptance test in the derived plan.",
        "blocking": True,
    } for index, requirement_id in enumerate(uncovered))
    if uncovered or any(gap.get("blocking") for gap in plan["gaps"]):
        plan["state"] = "BLOCKED"
    plan["assumptions"] = [{
        "id": f"AS{index:03d}", "statement": str(item.get("statement") or "Uncertainty requires validation before prototype execution."),
        "validation_due": "BEFORE_PROTOTYPE", "status": "OPEN", "trace_refs": _trace(*trace, str(item.get("id") or f"U{index:03d}"), f"AS{index:03d}"),
    } for index, item in enumerate(selected.get("uncertainties", []), 1)]
    plan["scope_baseline"]["prototype_plan_ids"] = prototype_ids
    plan["scope_baseline"]["assumption_ids"] = [item["id"] for item in plan["assumptions"]]
    plan["scope_baseline"]["trace_refs"] = _trace(*trace, *prototype_ids, *[item["id"] for item in plan["assumptions"]])


def _build_plan(project_root: Path) -> dict[str, Any]:
    handoff, bundle_manifest, source_input, artifacts = _load_inputs(project_root)
    selection_input = handoff.get("selection") or {}
    requirements = _records(artifacts["requirements"], "requirements", project_root / "00_handoff/source-bundle/artifacts/production-requirements.yaml")
    hypotheses = _records(artifacts["hypotheses"], "hypotheses", project_root / "00_handoff/source-bundle/artifacts/production-hypotheses.yaml")
    prototype_plans = _records(artifacts["prototype_plans"], "prototype_plans", project_root / "00_handoff/source-bundle/artifacts/prototype-plans.yaml")
    acceptance_tests = _records(artifacts["acceptance_tests"], "acceptance_tests", project_root / "00_handoff/source-bundle/artifacts/acceptance-tests.yaml")
    selected_hypothesis_id = str(selection_input.get("selected_hypothesis_id"))
    selected_hypothesis = next((item for item in hypotheses if item.get("id") == selected_hypothesis_id), None)
    if selected_hypothesis is None:
        raise DiagnosticError(_finding("PLANNING_SELECTION_REFERENCE", "handoff selection does not reference a bundled hypothesis", file=project_root / "00_handoff/production-handoff.yaml", location="/selection/selected_hypothesis_id", remediation="Re-export the handoff with the selected hypothesis snapshot."))
    if not isinstance(handoff.get("generated_at"), str):
        raise DiagnosticError(_finding("PLANNING_TIMESTAMP", "handoff generated_at is required for deterministic planning", file=project_root / "00_handoff/production-handoff.yaml", location="/generated_at", remediation="Provide an RFC 3339 generated_at value in the handoff."))
    reference_access, reference_gaps = _reference_access(project_root, handoff, artifacts)

    handoff_ref = {
        "id": handoff["handoff_id"],
        "revision": handoff["revision"],
        "content_sha256": handoff["integrity"]["content_sha256"],
    }
    requirement_ids = [str(item["id"]) for item in requirements]
    acceptance_test_ids = [str(item["id"]) for item in acceptance_tests]
    prototype_plan_ids = [str(item["id"]) for item in prototype_plans]
    gap_ids = [str(item["id"]) for item in handoff.get("open_gaps", []) if isinstance(item, dict) and item.get("id")]
    trace = _trace(handoff["handoff_id"], selected_hypothesis_id, *requirement_ids, *prototype_plan_ids, *acceptance_test_ids, *gap_ids)

    selection_status = "HUMAN_SELECTED" if selection_input.get("status") == "HUMAN_SELECTED" else "PROVISIONAL"
    selection_authority = "HUMAN" if selection_status == "HUMAN_SELECTED" else "AGENT"
    selection_record = {
        "selection_id": "SL001",
        "handoff_ref": handoff_ref,
        "selected_hypothesis_id": selected_hypothesis_id,
        "status": selection_status,
        "authority": selection_authority,
        "human_approval_required": bool(selection_input.get("human_approval_required", True)),
        "rationale": str(selected_hypothesis.get("single_hypothesis_rationale") or selected_hypothesis.get("proposition") or "Selected from the accepted handoff."),
        "trace_refs": _trace(*trace),
    }
    assumptions = [{
        "id": "AS001",
        "statement": "The target venue lighting condition remains untested until the prototype frame review.",
        "validation_due": "BEFORE_PROTOTYPE",
        "status": "OPEN",
        "trace_refs": _trace("AS001", "U001", "GP001", handoff["handoff_id"]),
    }]
    scope_baseline = {
        "baseline_id": "SB001",
        "handoff_ref": handoff_ref,
        "selection_id": "SL001",
        "selected_hypothesis_id": selected_hypothesis_id,
        "mandatory_requirement_ids": requirement_ids,
        "prototype_plan_ids": prototype_plan_ids,
        "excluded_scope": list(handoff.get("constraints", {}).get("prohibited_actions", [])),
        "assumption_ids": ["AS001"],
        "open_gap_ids": gap_ids,
        "status": "PROVISIONAL" if selection_status == "PROVISIONAL" else "BASELINED",
        "trace_refs": _trace(*trace, "AS001"),
    }

    deliverables = [{
        "id": "DL001",
        "title": "Interrupted interval scale installation prototype",
        "type": "physical-prototype",
        "source_requirement_ids": requirement_ids,
        "technical_spec_ids": ["TS001"],
        "acceptance_test_ids": acceptance_test_ids,
        "owner_capability": "physical-prototype-agent",
        "due_milestone_id": "MS004",
        "status": "PLANNED",
        "trace_refs": _trace(*trace, "TS001", "MS004"),
    }]
    technical_specifications = [{
        "id": "TS001",
        "deliverable_id": "DL001",
        "parameter": "repeated-interval-and-interruption-observability",
        "target": {"kind": "QUALITATIVE", "statement": "The repeated interval and one deliberate interruption are observable from the required viewpoints."},
        "tolerance": None,
        "measurement_method": "frame_review",
        "source_requirement_ids": requirement_ids,
        "status": "PROVISIONAL",
        "trace_refs": _trace(*trace, "DL001"),
    }]
    materials = [{
        "id": "MT001",
        "name": "paper scale-model elements",
        "specification": "Twelve paper elements with one deliberate omission position.",
        "quantity": {"value": "12", "unit": "item"},
        "rights_status": "CLEAR",
        "safety_status": "REVIEW_REQUIRED",
        "source_prototype_plan_ids": prototype_plan_ids,
        "status": "CANDIDATE",
        "trace_refs": _trace(*trace, "MT001"),
    }]
    resources = [
        {
            "id": "RS001", "type": "PERSON_CAPABILITY", "capability": "physical-prototype-agent",
            "quantity": {"value": "1", "unit": "item"}, "availability": "REQUIRES_CONFIRMATION",
            "source_task_ids": ["TK001", "TK002"], "trace_refs": _trace(*trace, "RS001"),
        },
        {
            "id": "RS002", "type": "EQUIPMENT", "capability": "three fixed cameras with identical exposure controls",
            "quantity": {"value": "3", "unit": "item"}, "availability": "REQUIRES_CONFIRMATION",
            "source_task_ids": ["TK002"], "trace_refs": _trace(*trace, "RS002"),
        },
    ]
    work_packages = [{
        "id": "WP001",
        "title": "Scale prototype and frame review",
        "deliverable_ids": ["DL001"],
        "input_ids": ["TS001"],
        "output_ids": ["PP001", "AT001"],
        "depends_on": [],
        "owner_capability": "physical-prototype-agent",
        "review_gate_id": None,
        "task_ids": ["TK001", "TK002", "TK003", "TK004"],
        "status": "BLOCKED",
        "trace_refs": _trace(*trace, "WP001"),
    }]
    approval_payload = {"action": "PHYSICAL_EXTERNAL", "target_ref": "03_plan/task-plan.yaml#TK001,TK002", "task_ids": ["TK001", "TK002"]}
    approval_requirement = {
        "id": "AR001",
        "action": "PHYSICAL_EXTERNAL",
        "target_ref": approval_payload["target_ref"],
        "target_sha256": canonical_sha256(approval_payload),
        "authority": "HUMAN",
        "status": "REQUIRED",
        "reason": "The prototype build and frame capture are physical/external effects and must not be executed by the planning agent without explicit approval.",
        "task_ids": ["TK001", "TK002"],
        "trace_refs": _trace(*trace, "AR001"),
    }
    tasks = [
        {
            "id": "TK001", "work_package_id": "WP001", "title": "Build the one-tenth scale model", "depends_on": [],
            "required_resource_ids": ["RS001"], "required_material_ids": ["MT001"],
            "acceptance_condition": "Twelve positions with one omitted element are fixed.", "effect_type": "PHYSICAL_EXTERNAL",
            "approval_requirement_ids": ["AR001"], "duration": {"value": "2", "unit": "h"}, "status": "BLOCKED", "trace_refs": _trace(*trace, "TK001", "AR001"),
        },
        {
            "id": "TK002", "work_package_id": "WP001", "title": "Record three fixed viewpoints", "depends_on": ["TK001"],
            "required_resource_ids": ["RS001", "RS002"], "required_material_ids": [],
            "acceptance_condition": "Three frames with identical exposure are available for review.", "effect_type": "PHYSICAL_EXTERNAL",
            "approval_requirement_ids": ["AR001"], "duration": {"value": "1", "unit": "h"}, "status": "BLOCKED", "trace_refs": _trace(*trace, "TK002", "AR001"),
        },
        {
            "id": "TK003", "work_package_id": "WP001", "title": "Review the three frames", "depends_on": ["TK002"],
            "required_resource_ids": [], "required_material_ids": [],
            "acceptance_condition": "The interval and interruption are identified in all three frames.", "effect_type": "READ_ONLY",
            "approval_requirement_ids": [], "duration": {"value": "1", "unit": "h"}, "status": "BACKLOG", "trace_refs": _trace(*trace, "TK003"),
        },
        {
            "id": "TK004", "work_package_id": "WP001", "title": "Validate plan references and coverage", "depends_on": [],
            "required_resource_ids": [], "required_material_ids": [],
            "acceptance_condition": "The plan graph and mandatory requirement coverage remain valid.", "effect_type": "READ_ONLY",
            "approval_requirement_ids": [], "duration": {"value": "15", "unit": "min"}, "status": "READY", "trace_refs": _trace(*trace, "TK004"),
        },
    ]
    milestones = [
        {"id": "MS001", "title": "Planning baseline generated", "sequence": 1, "depends_on": [], "status": "PLANNED", "trace_refs": _trace(*trace, "MS001")},
        {"id": "MS002", "title": "Scale model built", "sequence": 2, "depends_on": ["MS001"], "status": "BLOCKED", "trace_refs": _trace(*trace, "MS002")},
        {"id": "MS003", "title": "Three viewpoints recorded", "sequence": 3, "depends_on": ["MS002"], "status": "BLOCKED", "trace_refs": _trace(*trace, "MS003")},
        {"id": "MS004", "title": "Prototype frame review complete", "sequence": 4, "depends_on": ["MS003"], "status": "BLOCKED", "trace_refs": _trace(*trace, "MS004")},
    ]
    schedule = {
        "schedule_id": "SCH001", "mode": "RELATIVE", "baseline_status": "PROVISIONAL", "milestones": milestones,
        "task_schedule": [{"task_id": task["id"], "duration": task["duration"], "start_at": None, "due_at": None} for task in tasks],
        "critical_path_task_ids": ["TK001", "TK002", "TK003"],
        "gaps": ["Calendar dates are not supplied by the handoff; only relative durations are planned."],
        "trace_refs": _trace(*trace, "SCH001"),
    }
    budget = {
        "budget_id": "BDG001", "currency": "JPY", "baseline_total": None, "contingency": None, "approval_threshold": None,
        "items": [{"id": "BI001", "category": "material", "description": "Scale-model materials", "amount": None, "basis": "cost-band-only; no quote supplied", "confidence": "LOW", "status": "ESTIMATED", "trace_refs": _trace(*trace, "BI001")}],
        "gaps": ["No price, quote, supplier, reservation, or commitment is present in the accepted handoff."], "status": "ESTIMATED", "trace_refs": _trace(*trace, "BDG001"),
    }
    risks = [
        {"id": "RK001", "title": "Venue lighting may hide the interruption", "source_handoff_gap_ids": gap_ids, "source_requirement_ids": requirement_ids, "severity": "MAJOR", "likelihood": "MEDIUM", "impact": "The required visual interruption may not be observable from all viewpoints.", "mitigation": "Run the fixed-viewpoint frame review under the target lighting before installation-scale production.", "owner_capability": "production-validator", "status": "OPEN", "blocking": False, "trace_refs": _trace(*trace, "RK001")},
        {"id": "RK002", "title": "Material changes may alter safety or rights status", "source_handoff_gap_ids": [], "source_requirement_ids": requirement_ids, "severity": "MAJOR", "likelihood": "MEDIUM", "impact": "A substituted material could require a new safety or rights review.", "mitigation": "Recheck safety and rights before accepting any material lot or specification change.", "owner_capability": "production-validator", "status": "OPEN", "blocking": False, "trace_refs": _trace(*trace, "RK002")},
    ]
    approval_register = {"register_id": "AGR001", "requirements": [approval_requirement], "approvals": [], "trace_refs": _trace(*trace, "AGR001")}
    coverage_items = []
    for requirement in requirements:
        requirement_id = str(requirement["id"])
        tests = [str(item) for item in requirement.get("acceptance_test_ids", [])]
        coverage_items.append({"requirement_id": requirement_id, "deliverable_ids": ["DL001"], "technical_spec_ids": ["TS001"], "acceptance_test_ids": tests, "work_package_ids": ["WP001"], "task_ids": ["TK001", "TK002", "TK003"], "status": "COVERED"})
    coverage_report = {"report_id": "CV001", "requirements": coverage_items, "coverage_percent": 100 if coverage_items else 0, "uncovered_requirement_ids": [] if coverage_items else requirement_ids, "trace_refs": _trace(*trace, "CV001")}
    task_edges = [{"from": dependency, "to": task["id"]} for task in tasks for dependency in task.get("depends_on", [])]
    graph = {"graph_id": "DG001", "nodes": [task["id"] for task in tasks], "edges": task_edges, "topological_order": ["TK001", "TK004", "TK002", "TK003"], "trace_refs": _trace(*trace, "DG001")}
    gaps = [{"id": str(gap["id"]), "statement": str(gap.get("statement")), "blocking": bool(gap.get("blocking", False))} for gap in handoff.get("open_gaps", []) if isinstance(gap, dict) and gap.get("id")]
    gaps.extend([
        {"id": "PG001", "statement": "Budget amounts and quotes are not supplied; no spending is authorized by this plan.", "blocking": False},
        {"id": "PG002", "statement": "Calendar dates and venue availability are not supplied; the schedule remains relative.", "blocking": False},
    ])
    gaps.extend(reference_gaps)

    plan = {
        "schema_version": "1.0.0", "plan_id": "PL001", "plan_revision": 1, "project_id": str(_require_mapping(project_root / "manifest.yaml")["project_id"]),
        "state": "PLANNING", "generated_at": handoff["generated_at"], "handoff_ref": handoff_ref,
        "mandatory_requirement_ids": requirement_ids, "acceptance_test_ids": acceptance_test_ids, "acceptance_tests": acceptance_tests,
        "selection_record": selection_record, "scope_baseline": scope_baseline, "assumptions": assumptions,
        "deliverables": deliverables, "technical_specifications": technical_specifications, "materials": materials, "resources": resources,
        "work_packages": work_packages, "tasks": tasks, "schedule": schedule, "budget": budget, "risks": risks,
        "approval_register": approval_register, "coverage_report": coverage_report, "dependency_graph": graph,
        "critical_path_task_ids": ["TK001", "TK002", "TK003"], "reference_access": reference_access, "gaps": gaps,
        "determinism": {"algorithm": "production-plan-v1", "source_input_sha256": canonical_sha256(source_input)},
    }
    _derived_plan_records(
        plan=plan,
        handoff=handoff,
        requirements=requirements,
        hypotheses=hypotheses,
        prototype_plans=prototype_plans,
        acceptance_tests=acceptance_tests,
        trace=trace,
    )
    plan["integrity"] = {"content_sha256": canonical_sha256(plan)}
    return plan


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_contexts(project_root: Path, plan: dict[str, Any]) -> None:
    context_root = project_root / "03_plan/agent-contexts"
    common = f"""# Task-minimal production context\n\nThis context is generated from `{plan['plan_id']}` and must be used with the accepted handoff. It does not authorize external effects.\n\n- Project: `{plan['project_id']}`\n- Plan: `{plan['plan_id']}` revision {plan['plan_revision']}\n- State: `{plan['state']}`\n- Handoff: `{plan['handoff_ref']['id']}` revision {plan['handoff_ref']['revision']}\n"""
    tasks_by_effect = {
        "prototype-agent.md": [task for task in plan["tasks"] if task.get("effect_type") == "PHYSICAL_EXTERNAL"],
        "review-agent.md": [task for task in plan["tasks"] if task.get("effect_type") == "READ_ONLY"],
        "planning-agent.md": [task for task in plan["tasks"] if task.get("id") == "TK004"],
    }
    contexts = {}
    for filename, tasks in tasks_by_effect.items():
        lines = [common, "\n## Allowed scope\n", "Generated from the accepted handoff; no task authorizes an external effect by itself.", "\n## Tasks\n"]
        lines.extend(f"- `{task['id']}` — {task['title']} ({task['status']})." for task in tasks)
        if filename == "prototype-agent.md" and tasks:
            lines.extend(["\n## Gate\n", "Physical or external tasks remain BLOCKED until the referenced human approval is recorded."])
        contexts[filename] = "\n".join(lines) + "\n"
    for filename, text in contexts.items():
        (context_root / filename).parent.mkdir(parents=True, exist_ok=True)
        (context_root / filename).write_text(text, encoding="utf-8")


def _markdown_cell(value: Any) -> str:
    if value is None:
        return "未設定"
    if isinstance(value, bool):
        return "はい" if value else "いいえ"
    if isinstance(value, dict):
        return ", ".join(f"{key}={_markdown_cell(item)}" for key, item in value.items())
    if isinstance(value, list):
        return ", ".join(_markdown_cell(item) for item in value) or "なし"
    return str(value).replace("|", r"\|").replace("\n", "<br>")


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(_markdown_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(lines) if rows else "該当なし。"


def _markdown_bullets(values: list[Any]) -> str:
    return "\n".join(f"- {_markdown_cell(value)}" for value in values) if values else "- なし"


def _render_human_plan(project_root: Path, plan: dict[str, Any]) -> str:
    """Render the one complete production plan intended for human producers."""
    handoff = _require_mapping(project_root / "00_handoff/production-handoff.yaml")
    bundle_root = project_root / "00_handoff/source-bundle"
    requirements_path = bundle_root / "artifacts/production-requirements.yaml"
    hypotheses_path = bundle_root / "artifacts/production-hypotheses.yaml"
    requirements = _records(_require_mapping(requirements_path), "requirements", requirements_path)
    hypotheses = _records(_require_mapping(hypotheses_path), "hypotheses", hypotheses_path)
    selected_hypothesis = next(
        item for item in hypotheses if item.get("id") == plan["selection_record"]["selected_hypothesis_id"]
    )
    coverage_by_id = {str(item["requirement_id"]): item for item in plan["coverage_report"]["requirements"]}
    approval_requirements = plan["approval_register"]["requirements"]
    critical_path = " → ".join(f"`{task_id}`" for task_id in plan["critical_path_task_ids"])
    reference_policy = load_config(repository_root(), "reference-policy.yaml")
    category_labels = {
        str(item["id"]): str(item["label"])
        for item in reference_policy.get("categories", [])
        if isinstance(item, dict) and item.get("id") and item.get("label")
    }
    reference_rows = [
        [
            category_labels.get(category, category),
            item["source_ref_id"],
            item["kind"],
            item["summary"],
            f"<{item['access_url']}>" if item["access_url"] else "URL未提供（gap参照）",
            item["access_status"],
        ]
        for item in plan["reference_access"]
        for category in (item["reference_categories"] or ["未分類"])
    ]

    lines = [
        "# 統合制作計画書",
        "",
        "> この文書は、受理済みhandoffと検証済みの制作計画を、人間が読んで制作判断・制作実務に使える一つの計画書へ統合したものです。計画の生成は、購入・契約・公開・連絡・削除・物理作業の実行承認を意味しません。",
        "",
        "## 1. 文書概要",
        "",
        _markdown_table(["項目", "内容"], [
            ["プロジェクト", plan["project_id"]],
            ["計画", f"{plan['plan_id']} revision {plan['plan_revision']}"],
            ["計画状態", plan["state"]],
            ["生成日時", plan["generated_at"]],
            ["handoff", f"{plan['handoff_ref']['id']} revision {plan['handoff_ref']['revision']}"],
            ["要件カバレッジ", f"{plan['coverage_report']['coverage_percent']}%"],
            ["クリティカルパス", critical_path],
        ]),
        "",
        "## 2. 制作目的と採択内容",
        "",
        _markdown_table(["項目", "内容"], [
            ["採択仮説", selected_hypothesis.get("id")],
            ["仮説タイトル", selected_hypothesis.get("title")],
            ["仮説", selected_hypothesis.get("proposition")],
            ["仮説状態", selected_hypothesis.get("status")],
            ["選択状態", plan["selection_record"]["status"]],
            ["選択権限", plan["selection_record"]["authority"]],
            ["人間承認要否", plan["selection_record"]["human_approval_required"]],
            ["選択理由", plan["selection_record"]["rationale"]],
            ["創作指針", handoff.get("creative_direction_ref")],
        ]),
        "",
        "## 3. 制作リファレンス",
        "",
        "コンセプト、ビジュアル、制作手法の参照先です。URLは受理済みhandoffに含まれる恒久HTTPS URLだけを掲載し、アクセスできない参照や不足カテゴリはギャップとして残します。",
        "",
        _markdown_table(["分類", "出所ID", "種別", "概要", "アクセスURL", "状態"], reference_rows),
        "",
        "## 4. 要件と受入の目的",
        "",
        _markdown_table(["要件", "優先度", "要件内容", "出所", "受入テスト", "計画上の対応"], [
            [
                requirement.get("id"), requirement.get("priority"), requirement.get("statement"),
                requirement.get("source_decision_ids"), requirement.get("acceptance_test_ids"),
                coverage_by_id.get(str(requirement.get("id")), {}).get("status", "未確認"),
            ]
            for requirement in requirements
        ]),
        "",
        "## 5. 制作範囲と成果物",
        "",
        _markdown_table(["項目", "内容"], [
            ["スコープ状態", plan["scope_baseline"]["status"]],
            ["必須要件ID", plan["scope_baseline"]["mandatory_requirement_ids"]],
            ["試作計画ID", plan["scope_baseline"]["prototype_plan_ids"]],
            ["前提", [assumption["statement"] for assumption in plan["assumptions"]]],
            ["除外・未許可範囲", plan["scope_baseline"]["excluded_scope"]],
            ["権利制約", handoff.get("constraints", {}).get("rights", [])],
            ["安全制約", handoff.get("constraints", {}).get("safety", [])],
            ["プライバシー制約", handoff.get("constraints", {}).get("privacy", [])],
            ["再計画トリガー", handoff.get("replan_triggers", [])],
        ]),
        "",
        _markdown_table(["成果物", "種別", "内容", "受入テスト", "担当能力", "納期", "状態"], [
            [
                item["id"], item["type"], item["title"], item["acceptance_test_ids"],
                item["owner_capability"], item["due_milestone_id"], item["status"],
            ]
            for item in plan["deliverables"]
        ]),
        "",
        "## 6. 技術仕様・材料・資源",
        "",
        _markdown_table(["仕様", "対象", "目標", "許容差", "測定方法", "出所要件", "状態"], [
            [item["id"], item["parameter"], item["target"], item["tolerance"], item["measurement_method"], item["source_requirement_ids"], item["status"]]
            for item in plan["technical_specifications"]
        ]),
        "",
        _markdown_table(["材料", "仕様", "数量", "権利", "安全", "出所試作", "状態"], [
            [item["id"], item["name"] + " — " + item["specification"], item["quantity"], item["rights_status"], item["safety_status"], item["source_prototype_plan_ids"], item["status"]]
            for item in plan["materials"]
        ]),
        "",
        _markdown_table(["資源", "種別", "必要能力", "数量", "可用性", "関連タスク"], [
            [item["id"], item["type"], item["capability"], item["quantity"], item["availability"], item["source_task_ids"]]
            for item in plan["resources"]
        ]),
        "",
        "## 7. 工程と作業手順",
        "",
        _markdown_table(["作業パッケージ", "内容", "成果物", "タスク", "担当能力", "状態"], [
            [item["id"], item["title"], item["deliverable_ids"], item["task_ids"], item["owner_capability"], item["status"]]
            for item in plan["work_packages"]
        ]),
        "",
        _markdown_table(["タスク", "作業", "前提", "所要時間", "必要材料", "受入条件", "効果種別", "承認", "状態"], [
            [
                item["id"], item["title"], item["depends_on"], item["duration"], item["required_material_ids"],
                item["acceptance_condition"], item["effect_type"], item["approval_requirement_ids"], item["status"],
            ]
            for item in plan["tasks"]
        ]),
        "",
        f"**実施順の読み方:** クリティカルパスは {critical_path} です。READYタスクは {', '.join(f'`{task["id"]}`' for task in plan['tasks'] if task.get('status') == 'READY') or 'なし'} です。物理・外部効果を伴うタスクは承認待ちです。",
        "",
        "## 8. 試作・受入評価",
        "",
        _markdown_table(["テスト", "対象要件", "方法", "合格条件", "現在結果"], [
            [item["id"], item["target_requirement"], item["method"], item["pass_condition"], item["result"]]
            for item in plan["acceptance_tests"]
        ]),
        "",
        _markdown_table(["マイルストーン", "内容", "順序", "前提", "状態"], [
            [item["id"], item["title"], item["sequence"], item["depends_on"], item["status"]]
            for item in plan["schedule"]["milestones"]
        ]),
        "",
        "## 9. 日程と予算",
        "",
        _markdown_table(["日程・予算項目", "内容"], [
            ["日程モード", plan["schedule"]["mode"]],
            ["日程基準線", plan["schedule"]["baseline_status"]],
            ["タスク日程", plan["schedule"]["task_schedule"]],
            ["日程ギャップ", plan["schedule"]["gaps"]],
            ["通貨", plan["budget"]["currency"]],
            ["予算総額", plan["budget"]["baseline_total"]],
            ["予備費", plan["budget"]["contingency"]],
            ["承認閾値", plan["budget"]["approval_threshold"]],
            ["予算状態", plan["budget"]["status"]],
            ["予算ギャップ", plan["budget"]["gaps"]],
        ]),
        "",
        _markdown_table(["予算項目", "区分", "内容", "金額", "根拠", "確度", "状態"], [
            [item["id"], item["category"], item["description"], item["amount"], item["basis"], item["confidence"], item["status"]]
            for item in plan["budget"]["items"]
        ]),
        "",
        "## 10. リスクと未解決事項",
        "",
        _markdown_table(["リスク", "内容", "影響", "軽減策", "重要度", "可能性", "担当", "状態"], [
            [item["id"], item["title"], item["impact"], item["mitigation"], item["severity"], item["likelihood"], item["owner_capability"], item["status"]]
            for item in plan["risks"]
        ]),
        "",
        _markdown_table(["ギャップ", "内容", "ブロッキング"], [
            [item["id"], item["statement"], item["blocking"]] for item in plan["gaps"]
        ]),
        "",
        "## 11. 承認・安全境界",
        "",
        _markdown_table(["承認ID", "対象行為", "対象", "対象hash", "権限者", "状態", "理由", "関連タスク"], [
            [item["id"], item["action"], item["target_ref"], item["target_sha256"], item["authority"], item["status"], item["reason"], item["task_ids"]]
            for item in approval_requirements
        ]),
        "",
        "この計画書は、明示的な人間承認が記録されるまで、物理作業、外部サービスへの接続、購入、契約、支払い、公開、応募、連絡、削除を許可しません。材料の権利・安全状態、会場条件、担当能力、見積、日程は制作開始前に人間が確認してください。",
        "",
        "## 12. 人間向け実行前チェックリスト",
        "",
        _markdown_bullets([
            "採択仮説と要件の内容・優先度を確認する。",
            "コンセプト、ビジュアル、手法の参照URLを開き、制作時に参照可能か確認する。",
            "未設定の会場、照明、日程、予算、見積、担当能力を確定する。",
            "材料の権利状態と安全状態を確認し、変更時は再評価する。",
            "物理・外部効果タスクの対象・範囲・hashを確認して承認する。",
            "各受入テストの実施条件と証跡の保存先を決める。",
            "制作中の差分・失敗・変更要求を既存の計画に上書きせず記録する。",
        ]),
        "",
        "## 13. 証跡と再現性",
        "",
        _markdown_table(["項目", "値"], [
            ["handoff content hash", plan["handoff_ref"]["content_sha256"]],
            ["plan integrity hash", plan["integrity"]["content_sha256"]],
            ["source input hash", plan["determinism"]["source_input_sha256"]],
            ["生成アルゴリズム", plan["determinism"]["algorithm"]],
            ["トレーサビリティID", plan["selection_record"]["trace_refs"]],
            ["依存グラフ", plan["dependency_graph"]],
        ]),
        "",
        "### 受け渡し時の注意",
        "",
        "ユーザーに渡す計画書はこの `03_plan/production-plan.md` 一つです。`production-plan.yaml`などの構造化ファイルと`agent-contexts/`は、検証・再生成・内部運用のためにGit外の制作projectへ保持されます。完成作品、RAW、動画、音声、3D、大容量asset、credential、signed URLはこの計画書へ埋め込みません。",
        "",
    ]
    return "\n".join(lines)


def _write_outputs(project_root: Path, plan: dict[str, Any]) -> None:
    legacy_brief = project_root / "03_plan/human-brief.md"
    if legacy_brief.is_file():
        raise DiagnosticError(_finding(
            "PLANNING_LEGACY_OUTPUT",
            "legacy human-brief.md exists and cannot be silently replaced",
            file=legacy_brief,
            remediation="Move or archive the legacy brief, then regenerate the single integrated production-plan.md.",
        ))
    human_plan = _render_human_plan(project_root, plan)
    output = {
        "01_scope/selection-record.yaml": plan["selection_record"],
        "01_scope/scope-baseline.yaml": plan["scope_baseline"],
        "01_scope/assumptions-register.yaml": {"assumptions": plan["assumptions"]},
        "02_specification/deliverables.yaml": {"deliverables": plan["deliverables"]},
        "02_specification/technical-specifications.yaml": {"technical_specifications": plan["technical_specifications"]},
        "02_specification/acceptance-tests.yaml": {"acceptance_tests": plan["acceptance_tests"]},
        "02_specification/material-register.yaml": {"materials": plan["materials"]},
        "02_specification/asset-register.yaml": {"assets": []},
        "03_plan/production-plan.yaml": plan,
        "03_plan/work-packages.yaml": {"work_packages": plan["work_packages"]},
        "03_plan/task-plan.yaml": {"tasks": plan["tasks"]},
        "03_plan/schedule.yaml": plan["schedule"],
        "03_plan/budget.yaml": plan["budget"],
        "03_plan/resource-plan.yaml": {"resources": plan["resources"]},
        "03_plan/procurement-plan.yaml": {"status": "NOT_AUTHORIZED", "candidates": [], "reason": "No purchase or supplier action is authorized at planning stage."},
        "03_plan/requirement-coverage.yaml": plan["coverage_report"],
        "07_governance/approval-register.yaml": plan["approval_register"],
        "07_governance/risk-register.yaml": {"risks": plan["risks"]},
    }
    for relative, value in output.items():
        dump_yaml(value, project_root / relative)
    (project_root / "03_plan/production-plan.md").write_text(human_plan, encoding="utf-8")
    _write_contexts(project_root, plan)

    manifest_path = project_root / "manifest.yaml"
    manifest = _require_mapping(manifest_path)
    manifest["state"] = plan["state"]
    dump_yaml(manifest, manifest_path)
    state_path = project_root / "08_runtime/production-state.json"
    state = load_json(state_path)
    state.pop("state_sha256", None)
    state["state"] = plan["state"]
    state["revision"] = 1
    state["plan_id"] = plan["plan_id"]
    state["state_sha256"] = canonical_sha256(state)
    _json_write(state_path, state)
    _json_write(project_root / "08_runtime/dependency-index.json", {"schema_version": "1.0.0", "nodes": plan["dependency_graph"]["nodes"], "edges": plan["dependency_graph"]["edges"]})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path, help="accepted Git-external production project")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = repository_root()
    try:
        project_root = args.project_root.resolve()
        plan = _build_plan(project_root)
        findings = validate_plan_document(plan, repository=repository, plan_path=project_root / "03_plan/production-plan.yaml")
        if findings:
            emit_findings(findings, output_format=args.format)
            return EXIT_VALIDATION
        _write_outputs(project_root, plan)
        print(str(project_root / "03_plan/production-plan.md"))
        return EXIT_SUCCESS
    except DiagnosticError as exc:
        emit_findings([exc.finding], output_format=args.format)
        return EXIT_VALIDATION
    except (OSError, KeyError, TypeError, ValueError) as exc:
        emit_findings([_finding("PLANNING_BUILD", str(exc), file=args.project_root, remediation="Correct the accepted project input and retry plan generation.")], output_format=args.format)
        return EXIT_VALIDATION


if __name__ == "__main__":
    raise SystemExit(main())
