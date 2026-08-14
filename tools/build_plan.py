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
    records = _records(artifacts["source_refs"], "records", path)
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
            raise DiagnosticError(_finding("PLANNING_REFERENCE_MISSING", f"source-ref index does not contain referenced ID {source_id}", file=path, location="/records", remediation="Regenerate the handoff with every referenced decision, insight, and evidence record."))
        categories = record.get("reference_categories", [])
        if not isinstance(categories, list) or not all(isinstance(value, str) for value in categories):
            raise DiagnosticError(_finding("PLANNING_REFERENCE_CATEGORY", "reference_categories must be a list of category IDs", file=path, location=f"/records/{source_id}/reference_categories", remediation="Use category IDs declared in config/reference-policy.yaml."))
        unknown = sorted(set(categories) - set(category_by_id))
        if unknown:
            raise DiagnosticError(_finding("PLANNING_REFERENCE_CATEGORY", f"unknown reference categories: {', '.join(unknown)}", file=path, location=f"/records/{source_id}/reference_categories", remediation="Use category IDs declared in config/reference-policy.yaml."))
        record_sha256 = record.get("record_sha256")
        if not isinstance(record_sha256, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", record_sha256) is None or set(record_sha256[7:]) == {"0"}:
            raise DiagnosticError(_finding("PLANNING_REFERENCE_HASH", "record_sha256 must be a non-zero canonical SHA-256 value", file=path, location=f"/records/{source_id}/record_sha256", remediation="Regenerate the handoff from Research so the source record hash is present and computed from the canonical record."))
        access_url = record.get("access_url")
        if access_url is not None:
            if not isinstance(access_url, str) or not access_url or any(character.isspace() for character in access_url):
                raise DiagnosticError(_finding("PLANNING_REFERENCE_URL", "access_url must be a non-empty URL without whitespace", file=path, location=f"/records/{source_id}/access_url", remediation="Provide a stable permanent HTTPS URL without credentials or signed parameters."))
            uri_finding = validate_asset_uri(
                access_url,
                allowed_schemes=policy.get("allowed_uri_schemes", ["https"]),
                allow_query=bool(policy.get("https", {}).get("allow_query", False)),
            )
            parsed = urlsplit(access_url)
            if uri_finding is not None or not parsed.hostname:
                reason = uri_finding.reason if uri_finding is not None else "HTTPS reference URL must contain a hostname"
                raise DiagnosticError(_finding("PLANNING_REFERENCE_URL", reason, file=path, location=f"/records/{source_id}/access_url", remediation="Provide a stable permanent HTTPS URL without credentials, query parameters, or fragments."))
            available_categories.update(categories)
        normalized.append({
            "source_ref_id": source_id,
            "kind": str(record.get("kind") or "unknown"),
            "reference_categories": categories,
            "summary": str(record.get("summary") or "Summary not supplied."),
            "access_url": access_url,
            "access_status": "AVAILABLE" if access_url is not None else "MISSING",
            "record_hash": record_sha256,
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


def _build_plan(project_root: Path) -> dict[str, Any]:
    return _build_plan_from_handoff(project_root)


def _plan_text(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())
    return fallback


def _duration_for_band(band: Any) -> dict[str, str]:
    return {
        "HOURS": {"value": "1", "unit": "h"},
        "DAYS": {"value": "8", "unit": "h"},
        "WEEKS": {"value": "1", "unit": "h"},
        "MONTHS": {"value": "1", "unit": "h"},
        "UNKNOWN": {"value": "1", "unit": "h"},
    }.get(str(band), {"value": "1", "unit": "h"})


def _topological_order(nodes: list[str], edges: list[dict[str, str]]) -> list[str]:
    remaining = {node: 0 for node in nodes}
    adjacency: dict[str, list[str]] = {node: [] for node in nodes}
    for edge in edges:
        remaining[edge["to"]] += 1
        adjacency[edge["from"]].append(edge["to"])
    ready = sorted(node for node, count in remaining.items() if count == 0)
    order: list[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for target in sorted(adjacency[node]):
            remaining[target] -= 1
            if remaining[target] == 0:
                ready.append(target)
    return order


def _build_plan_from_handoff(project_root: Path) -> dict[str, Any]:
    """Build every planning value from the accepted handoff collections.

    The builder deliberately creates provisional records when the handoff is
    incomplete, but their text, IDs, links, and statuses come from the input
    collections.  It never substitutes a project-specific example into an
    unrelated handoff.
    """
    handoff, _bundle_manifest, source_input, artifacts = _load_inputs(project_root)
    selection_input = handoff.get("selection") or {}
    requirements = _records(artifacts["requirements"], "requirements", project_root / "00_handoff/source-bundle/artifacts/production-requirements.yaml")
    hypotheses = _records(artifacts["hypotheses"], "hypotheses", project_root / "00_handoff/source-bundle/artifacts/production-hypotheses.yaml")
    prototype_plans = _records(artifacts["prototype_plans"], "prototype_plans", project_root / "00_handoff/source-bundle/artifacts/prototype-plans.yaml")
    acceptance_tests = _records(artifacts["acceptance_tests"], "acceptance_tests", project_root / "00_handoff/source-bundle/artifacts/acceptance-tests.yaml")
    selected_hypothesis_id = str(selection_input.get("selected_hypothesis_id"))
    selected_hypothesis = next((item for item in hypotheses if str(item.get("id")) == selected_hypothesis_id), None)
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
    handoff_gap_ids = [str(item["id"]) for item in handoff.get("open_gaps", []) if isinstance(item, dict) and item.get("id")]
    trace = _trace(handoff["handoff_id"], selected_hypothesis_id, *requirement_ids, *acceptance_test_ids, *prototype_plan_ids, *handoff_gap_ids)

    selection_status = "HUMAN_SELECTED" if selection_input.get("status") == "HUMAN_SELECTED" else "PROVISIONAL"
    selection_record = {
        "selection_id": "SL001",
        "handoff_ref": handoff_ref,
        "selected_hypothesis_id": selected_hypothesis_id,
        "status": selection_status,
        "authority": "HUMAN" if selection_status == "HUMAN_SELECTED" else "AGENT",
        "human_approval_required": bool(selection_input.get("human_approval_required", True)),
        "rationale": _plan_text(selected_hypothesis.get("single_hypothesis_rationale") or selected_hypothesis.get("proposition"), "Selected from the accepted handoff."),
        "trace_refs": _trace(*trace),
    }

    uncertainties = selected_hypothesis.get("uncertainties", [])
    if not isinstance(uncertainties, list):
        uncertainties = []
    assumptions = []
    for index, uncertainty in enumerate(uncertainties, start=1):
        if not isinstance(uncertainty, dict):
            continue
        uncertainty_id = str(uncertainty.get("id") or f"U{index:03d}")
        assumptions.append({
            "id": f"AS{index:03d}",
            "statement": _plan_text(uncertainty.get("statement"), f"Uncertainty {uncertainty_id} requires validation."),
            "validation_due": "BEFORE_PROTOTYPE",
            "status": "OPEN",
            "trace_refs": _trace(*trace, uncertainty_id),
        })
    assumption_ids = [item["id"] for item in assumptions]
    scope_baseline = {
        "baseline_id": "SB001",
        "handoff_ref": handoff_ref,
        "selection_id": "SL001",
        "selected_hypothesis_id": selected_hypothesis_id,
        "mandatory_requirement_ids": requirement_ids,
        "prototype_plan_ids": prototype_plan_ids,
        "excluded_scope": list((handoff.get("constraints") or {}).get("prohibited_actions", [])),
        "assumption_ids": assumption_ids,
        "open_gap_ids": handoff_gap_ids,
        "status": "PROVISIONAL" if selection_status == "PROVISIONAL" else "BASELINED",
        "trace_refs": _trace(*trace, *assumption_ids),
    }

    tests_by_requirement: dict[str, list[dict[str, Any]]] = {item: [] for item in requirement_ids}
    for test in acceptance_tests:
        target = str(test.get("target_requirement", ""))
        if target in tests_by_requirement:
            tests_by_requirement[target].append(test)
    for requirement in requirements:
        requirement_id = str(requirement["id"])
        for test_id in requirement.get("acceptance_test_ids", []):
            test = next((item for item in acceptance_tests if str(item.get("id")) == str(test_id)), None)
            if test is not None and test not in tests_by_requirement[requirement_id]:
                tests_by_requirement[requirement_id].append(test)

    owner_capabilities = []
    for prototype in prototype_plans:
        capability = _plan_text(prototype.get("executor_capability"), "production-owner-confirmation-required")
        if capability not in owner_capabilities:
            owner_capabilities.append(capability)
    for test in acceptance_tests:
        capability = _plan_text(test.get("executor"), _plan_text(test.get("method"), "production-owner-confirmation-required"))
        if capability not in owner_capabilities:
            owner_capabilities.append(capability)
    if not owner_capabilities:
        owner_capabilities.append("production-owner-confirmation-required")
    elif not prototype_plans and "production-owner-confirmation-required" not in owner_capabilities:
        owner_capabilities.insert(0, "production-owner-confirmation-required")

    materials = []
    material_by_plan: dict[str, list[str]] = {}
    material_index = 1
    for prototype in prototype_plans:
        prototype_id = str(prototype["id"])
        material_by_plan[prototype_id] = []
        for input_name in prototype.get("inputs", []):
            name = _plan_text(input_name, "Input not named by handoff")
            existing = next((item for item in materials if item["name"] == name), None)
            if existing is None:
                existing = {
                    "id": f"MT{material_index:03d}",
                    "name": name,
                    "specification": f"As specified by prototype plan {prototype_id}; quantity is not supplied by the handoff.",
                    "quantity": {"value": "1", "unit": "item"},
                    "rights_status": "REVIEW_REQUIRED",
                    "safety_status": "REVIEW_REQUIRED",
                    "source_prototype_plan_ids": [prototype_id],
                    "status": "CANDIDATE",
                    "trace_refs": _trace(*trace, prototype_id, f"MT{material_index:03d}"),
                }
                materials.append(existing)
                material_index += 1
            elif prototype_id not in existing["source_prototype_plan_ids"]:
                existing["source_prototype_plan_ids"].append(prototype_id)
            material_by_plan[prototype_id].append(existing["id"])
    if not materials and requirements:
        requirement = requirements[0]
        materials.append({
            "id": "MT001",
            "name": f"Inputs for {_plan_text(requirement.get('id'), 'the first requirement')}",
            "specification": "The accepted handoff does not specify material composition or quantity; confirm before use.",
            "quantity": {"value": "1", "unit": "item"},
            "rights_status": "REVIEW_REQUIRED",
            "safety_status": "REVIEW_REQUIRED",
            "source_prototype_plan_ids": [],
            "status": "CANDIDATE",
            "trace_refs": _trace(*trace, str(requirement.get("id")), "MT001"),
        })
    material_ids = [item["id"] for item in materials]

    resources = []
    resource_by_capability: dict[str, str] = {}
    for index, capability in enumerate(owner_capabilities, start=1):
        resource_id = f"RS{index:03d}"
        resource_by_capability[capability] = resource_id
        resources.append({
            "id": resource_id,
            "type": "PERSON_CAPABILITY",
            "capability": capability,
            "quantity": {"value": "1", "unit": "item"},
            "availability": "REQUIRES_CONFIRMATION",
            "source_task_ids": [],
            "trace_refs": _trace(*trace, resource_id),
        })

    tasks: list[dict[str, Any]] = []
    requirement_task_ids: dict[str, list[str]] = {item: [] for item in requirement_ids}
    physical_task_ids: list[str] = []
    task_id_by_prototype_task: dict[str, str] = {}
    task_number = 1
    for prototype in prototype_plans:
        prototype_id = str(prototype["id"])
        capability = _plan_text(prototype.get("executor_capability"), "production-owner-confirmation-required")
        for prototype_task in prototype.get("tasks", []):
            task_id = f"TK{task_number:03d}"
            task_number += 1
            task_id_by_prototype_task[str(prototype_task.get("id"))] = task_id
            dependencies = [task_id_by_prototype_task[str(dep)] for dep in prototype_task.get("depends_on", []) if str(dep) in task_id_by_prototype_task]
            task = {
                "id": task_id,
                "work_package_id": "WP001",
                "title": _plan_text(prototype_task.get("title"), f"Execute prototype step from {prototype_id}"),
                "depends_on": dependencies,
                "required_resource_ids": [resource_by_capability[capability]],
                "required_material_ids": material_by_plan.get(prototype_id, []),
                "acceptance_condition": _plan_text(prototype_task.get("completion_condition"), "The prototype step completion condition is recorded."),
                "effect_type": "PHYSICAL_EXTERNAL",
                "approval_requirement_ids": [],
                "duration": _duration_for_band(prototype.get("estimated_duration_band")),
                "status": "BLOCKED",
                "trace_refs": _trace(*trace, prototype_id, str(prototype_task.get("id")), task_id),
            }
            tasks.append(task)
            physical_task_ids.append(task_id)
            for requirement_id in requirement_ids:
                requirement_task_ids[requirement_id].append(task_id)
    if not prototype_plans:
        for requirement in requirements:
            requirement_id = str(requirement["id"])
            task_id = f"TK{task_number:03d}"
            task_number += 1
            capability = owner_capabilities[0]
            task = {
                "id": task_id,
                "work_package_id": "WP001",
                "title": f"Prepare production for {_plan_text(requirement.get('statement'), requirement_id)}",
                "depends_on": [],
                "required_resource_ids": [resource_by_capability[capability]],
                "required_material_ids": material_ids,
                "acceptance_condition": _plan_text(requirement.get("statement"), f"Requirement {requirement_id} is addressed."),
                "effect_type": "PHYSICAL_EXTERNAL",
                "approval_requirement_ids": [],
                "duration": {"value": "1", "unit": "h"},
                "status": "BLOCKED",
                "trace_refs": _trace(*trace, requirement_id, task_id),
            }
            tasks.append(task)
            physical_task_ids.append(task_id)
            requirement_task_ids[requirement_id].append(task_id)
            task_id = f"TK{task_number:03d}"
            task_number += 1
            evidence_source = tests_by_requirement.get(requirement_id, [])
            evidence_test = evidence_source[0] if evidence_source else {}
            evidence_capability = _plan_text(evidence_test.get("executor"), _plan_text(evidence_test.get("method"), owner_capabilities[-1]))
            evidence_task = {
                "id": task_id,
                "work_package_id": "WP001",
                "title": f"Record evidence for {requirement_id}",
                "depends_on": [tasks[-1]["id"]],
                "required_resource_ids": [resource_by_capability[evidence_capability]],
                "required_material_ids": [],
                "acceptance_condition": _plan_text(evidence_test.get("evidence_to_record"), _plan_text(evidence_test.get("method"), f"Evidence for {requirement_id} is recorded.")),
                "effect_type": "PHYSICAL_EXTERNAL",
                "approval_requirement_ids": [],
                "duration": {"value": "1", "unit": "h"},
                "status": "BLOCKED",
                "trace_refs": _trace(*trace, requirement_id, task_id),
            }
            tasks.append(evidence_task)
            physical_task_ids.append(task_id)
            requirement_task_ids[requirement_id].append(task_id)

    for acceptance_test in acceptance_tests:
        test_id = str(acceptance_test["id"])
        target_requirement = str(acceptance_test.get("target_requirement", ""))
        task_id = f"TK{task_number:03d}"
        task_number += 1
        dependencies = [tasks[-1]["id"]] if tasks[-1]["effect_type"] == "PHYSICAL_EXTERNAL" else []
        review_task = {
            "id": task_id,
            "work_package_id": "WP001",
            "title": f"Review acceptance evidence for {test_id}",
            "depends_on": dependencies,
            "required_resource_ids": [resource_by_capability[_plan_text(acceptance_test.get("executor"), _plan_text(acceptance_test.get("method"), owner_capabilities[0]))]],
            "required_material_ids": [],
            "acceptance_condition": _plan_text(acceptance_test.get("pass_condition"), f"Acceptance test {test_id} is evaluated."),
            "effect_type": "READ_ONLY",
            "approval_requirement_ids": [],
            "duration": {"value": "1", "unit": "h"},
            "status": "BACKLOG",
            "trace_refs": _trace(*trace, test_id, target_requirement, task_id),
        }
        tasks.append(review_task)
        if target_requirement in requirement_task_ids:
            requirement_task_ids[target_requirement].append(task_id)

    validation_task_id = f"TK{task_number:03d}"
    tasks.append({
        "id": validation_task_id,
        "work_package_id": "WP001",
        "title": f"Validate plan coverage for {_plan_text(selected_hypothesis.get('title'), selected_hypothesis_id)}",
        "depends_on": [],
        "required_resource_ids": [],
        "required_material_ids": [],
        "acceptance_condition": "The plan graph, source references, and mandatory requirement coverage are valid.",
        "effect_type": "READ_ONLY",
        "approval_requirement_ids": [],
        "duration": {"value": "15", "unit": "min"},
        "status": "READY",
        "trace_refs": _trace(*trace, validation_task_id),
    })
    for resource in resources:
        resource["source_task_ids"] = [task["id"] for task in tasks if resource["id"] in task["required_resource_ids"]]

    approval_requirements = []
    if physical_task_ids:
        approval_payload = {"action": "PHYSICAL_EXTERNAL", "target_ref": f"03_plan/task-plan.yaml#{','.join(physical_task_ids)}", "task_ids": physical_task_ids}
        approval_requirements.append({
            "id": "AR001",
            "action": "PHYSICAL_EXTERNAL",
            "target_ref": approval_payload["target_ref"],
            "target_sha256": canonical_sha256(approval_payload),
            "authority": "HUMAN",
            "status": "REQUIRED",
            "reason": "Physical or external prototype tasks require explicit human approval before execution.",
            "task_ids": physical_task_ids,
            "trace_refs": _trace(*trace, "AR001"),
        })
        for task in tasks:
            if task["id"] in physical_task_ids:
                task["approval_requirement_ids"] = ["AR001"]
                task["trace_refs"] = _trace(*task["trace_refs"], "AR001")

    technical_specifications = []
    deliverables = []
    for index, requirement in enumerate(requirements, start=1):
        requirement_id = str(requirement["id"])
        deliverable_id = f"DL{index:03d}"
        specification_id = f"TS{index:03d}"
        tests = tests_by_requirement.get(requirement_id, [])
        measurement_method = _plan_text(tests[0].get("method") if tests else None, "not-supplied-by-handoff")
        category = _plan_text(requirement.get("category"), "production")
        technical_specifications.append({
            "id": specification_id,
            "deliverable_id": deliverable_id,
            "parameter": f"{category}-{requirement_id}",
            "target": {"kind": "QUALITATIVE", "statement": _plan_text(requirement.get("statement"), requirement_id)},
            "tolerance": None,
            "measurement_method": measurement_method,
            "source_requirement_ids": [requirement_id],
            "status": "PROVISIONAL",
            "trace_refs": _trace(*trace, requirement_id, specification_id, deliverable_id),
        })
        deliverables.append({
            "id": deliverable_id,
            "title": f"{_plan_text(selected_hypothesis.get('title'), selected_hypothesis_id)} — {_plan_text(requirement.get('statement'), requirement_id)}",
            "type": f"{category}-deliverable",
            "source_requirement_ids": [requirement_id],
            "technical_spec_ids": [specification_id],
            "acceptance_test_ids": [str(test["id"]) for test in tests],
            "owner_capability": owner_capabilities[0],
            "due_milestone_id": "MS001",
            "status": "PLANNED",
            "trace_refs": _trace(*trace, requirement_id, specification_id, deliverable_id),
        })

    work_package = {
        "id": "WP001",
        "title": _plan_text(selected_hypothesis.get("title"), selected_hypothesis_id),
        "deliverable_ids": [item["id"] for item in deliverables],
        "input_ids": [item["id"] for item in technical_specifications] + material_ids,
        "output_ids": prototype_plan_ids + acceptance_test_ids,
        "depends_on": [],
        "owner_capability": owner_capabilities[0],
        "review_gate_id": None,
        "task_ids": [task["id"] for task in tasks],
        "status": "BLOCKED" if physical_task_ids else "PLANNED",
        "trace_refs": _trace(*trace, "WP001"),
    }

    milestones = [{"id": "MS001", "title": "Planning baseline generated", "sequence": 1, "depends_on": [], "status": "PLANNED", "trace_refs": _trace(*trace, "MS001")}]
    for index, task in enumerate(tasks, start=2):
        milestone_id = f"MS{index:03d}"
        milestones.append({
            "id": milestone_id,
            "title": f"{task['title']} completed",
            "sequence": index,
            "depends_on": [milestones[-1]["id"]],
            "status": "BLOCKED" if task["effect_type"] == "PHYSICAL_EXTERNAL" else "PLANNED",
            "trace_refs": _trace(*trace, task["id"], milestone_id),
        })
    final_milestone_id = milestones[-1]["id"]
    for deliverable in deliverables:
        deliverable["due_milestone_id"] = final_milestone_id
        deliverable["trace_refs"] = _trace(*deliverable["trace_refs"], final_milestone_id)

    coverage_items = []
    for requirement in requirements:
        requirement_id = str(requirement["id"])
        tests = [str(test["id"]) for test in tests_by_requirement.get(requirement_id, [])]
        mapped_tasks = requirement_task_ids.get(requirement_id, [])
        deliverable_ids = [item["id"] for item in deliverables if requirement_id in item["source_requirement_ids"]]
        specification_ids = [item["id"] for item in technical_specifications if requirement_id in item["source_requirement_ids"]]
        covered = bool(deliverable_ids and specification_ids and tests and mapped_tasks)
        coverage_items.append({
            "requirement_id": requirement_id,
            "deliverable_ids": deliverable_ids,
            "technical_spec_ids": specification_ids,
            "acceptance_test_ids": tests,
            "work_package_ids": ["WP001"] if mapped_tasks else [],
            "task_ids": list(dict.fromkeys(mapped_tasks)),
            "status": "COVERED" if covered else "UNCOVERED",
        })
    covered_count = sum(item["status"] == "COVERED" for item in coverage_items)
    uncovered_requirement_ids = [item["requirement_id"] for item in coverage_items if item["status"] == "UNCOVERED"]
    coverage_report = {
        "report_id": "CV001",
        "requirements": coverage_items,
        "coverage_percent": round(covered_count * 100 / len(coverage_items)) if coverage_items else 0,
        "uncovered_requirement_ids": uncovered_requirement_ids,
        "trace_refs": _trace(*trace, "CV001"),
    }

    gaps = [{"id": str(gap["id"]), "statement": _plan_text(gap.get("statement"), str(gap["id"])), "blocking": bool(gap.get("blocking", False))} for gap in handoff.get("open_gaps", []) if isinstance(gap, dict) and gap.get("id")]
    next_gap_number = max([int(match.group(1)) for gap in gaps if (match := re.fullmatch(r"PG(\d+)", gap["id"]))] or [0]) + 1
    generic_gaps = [
        ("Budget amounts and commitments are not supplied by the accepted handoff.", False),
        ("Calendar dates and availability are not supplied by the accepted handoff; the schedule remains relative.", False),
    ]
    if not prototype_plans:
        generic_gaps.append(("Prototype task duration and material quantities are not supplied by the accepted handoff.", False))
    for statement, blocking in generic_gaps:
        gap_id = f"PG{next_gap_number:03d}"
        next_gap_number += 1
        gaps.append({"id": gap_id, "statement": statement, "blocking": blocking})
    gaps.extend(reference_gaps)
    for requirement_id in uncovered_requirement_ids:
        gaps.append({"id": f"PG{next_gap_number:03d}", "statement": f"Requirement {requirement_id} is not connected to a complete acceptance path in the handoff.", "blocking": True})
        next_gap_number += 1

    risks = []
    for index, gap in enumerate(gaps, start=1):
        if str(gap["id"]).startswith("PG"):
            risks.append({
                "id": f"RK{index:03d}",
                "title": f"Planning gap: {gap['id']}",
                "source_handoff_gap_ids": handoff_gap_ids,
                "source_requirement_ids": requirement_ids,
                "severity": "MAJOR" if gap["blocking"] else "MEDIUM",
                "likelihood": "UNKNOWN",
                "impact": gap["statement"],
                "mitigation": "Resolve the stated handoff gap and regenerate the plan before relying on the affected decision.",
                "owner_capability": owner_capabilities[0],
                "status": "OPEN",
                "blocking": gap["blocking"],
                "trace_refs": _trace(*trace, gap["id"], f"RK{index:03d}"),
            })

    task_edges = [{"from": dependency, "to": task["id"]} for task in tasks for dependency in task.get("depends_on", [])]
    topological_order = _topological_order([task["id"] for task in tasks], task_edges)
    critical_path_task_ids = [task["id"] for task in tasks if task["id"] != validation_task_id]
    if not critical_path_task_ids:
        critical_path_task_ids = [validation_task_id]
    graph = {
        "graph_id": "DG001",
        "nodes": [task["id"] for task in tasks],
        "edges": task_edges,
        "topological_order": topological_order,
        "trace_refs": _trace(*trace, "DG001"),
    }
    schedule = {
        "schedule_id": "SCH001",
        "mode": "RELATIVE",
        "baseline_status": "PROVISIONAL",
        "milestones": milestones,
        "task_schedule": [{"task_id": task["id"], "duration": task["duration"], "start_at": None, "due_at": None} for task in tasks],
        "critical_path_task_ids": critical_path_task_ids,
        "gaps": [gap["statement"] for gap in gaps if "Calendar dates" in gap["statement"]],
        "trace_refs": _trace(*trace, "SCH001"),
    }
    budget_items = [{
        "id": f"BI{index:03d}",
        "category": "material",
        "description": f"Cost for {material['name']}",
        "amount": None,
        "basis": "No quote supplied by the accepted handoff.",
        "confidence": "LOW",
        "status": "ESTIMATED",
        "trace_refs": _trace(*trace, material["id"], f"BI{index:03d}"),
    } for index, material in enumerate(materials, start=1)]
    budget_policy = load_config(repository_root(), "budget-policy.yaml")
    allowed_currencies = budget_policy.get("allowed_currencies", [])
    if not isinstance(allowed_currencies, list) or not allowed_currencies:
        raise DiagnosticError(_finding("PLANNING_BUDGET_POLICY", "budget policy must declare at least one allowed currency", file=repository_root() / "config/budget-policy.yaml", location="/allowed_currencies", remediation="Configure an allowed currency before building a production plan."))
    budget = {
        "budget_id": "BDG001", "currency": str(allowed_currencies[0]), "baseline_total": None, "contingency": None, "approval_threshold": None,
        "items": budget_items, "gaps": ["No price, quote, supplier, reservation, or commitment is present in the accepted handoff."], "status": "ESTIMATED", "trace_refs": _trace(*trace, "BDG001"),
    }
    state = "BLOCKED" if uncovered_requirement_ids or any(gap["blocking"] for gap in gaps) else "PLANNING"
    project_id = str(_require_mapping(project_root / "manifest.yaml")["project_id"])
    plan = {
        "schema_version": "1.0.0", "plan_id": "PL001", "plan_revision": 1, "project_id": project_id,
        "state": state, "generated_at": handoff["generated_at"], "handoff_ref": handoff_ref,
        "mandatory_requirement_ids": requirement_ids, "acceptance_test_ids": acceptance_test_ids, "acceptance_tests": acceptance_tests,
        "selection_record": selection_record, "scope_baseline": scope_baseline, "assumptions": assumptions,
        "deliverables": deliverables, "technical_specifications": technical_specifications, "materials": materials, "resources": resources,
        "work_packages": [work_package], "tasks": tasks, "schedule": schedule, "budget": budget, "risks": risks,
        "approval_register": {"register_id": "AGR001", "requirements": approval_requirements, "approvals": [], "trace_refs": _trace(*trace, "AGR001")},
        "coverage_report": coverage_report, "dependency_graph": graph, "critical_path_task_ids": critical_path_task_ids,
        "reference_access": reference_access, "gaps": gaps,
        "determinism": {"algorithm": "production-plan-v1", "source_input_sha256": canonical_sha256(source_input)},
    }
    plan["integrity"] = {"content_sha256": canonical_sha256(plan)}
    return plan


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_contexts(project_root: Path, plan: dict[str, Any]) -> None:
    context_root = project_root / "03_plan/agent-contexts"
    common = f"""# Task-minimal production context\n\nThis context is generated from `{plan['plan_id']}` and must be used with the accepted handoff. It does not authorize external effects.\n\n- Project: `{plan['project_id']}`\n- Plan: `{plan['plan_id']}` revision {plan['plan_revision']}\n- State: `{plan['state']}`\n- Handoff: `{plan['handoff_ref']['id']}` revision {plan['handoff_ref']['revision']}\n"""
    physical = [task for task in plan["tasks"] if task["effect_type"] == "PHYSICAL_EXTERNAL"]
    read_only = [task for task in plan["tasks"] if task["effect_type"] == "READ_ONLY"]
    ready = [task for task in plan["tasks"] if task["status"] == "READY"]
    approval_ids = [approval["id"] for approval in plan["approval_register"]["requirements"] if approval["task_ids"]]
    physical_lines = "\n".join(f"- `{task['id']}` — {task['title']}" for task in physical) or "- なし"
    review_lines = "\n".join(f"- `{task['id']}` — {task['title']}" for task in read_only) or "- なし"
    ready_lines = ", ".join(f"`{task['id']}`" for task in ready) or "なし"
    approval_text = ", ".join(f"`{approval_id}`" for approval_id in approval_ids) or "なし"
    contexts = {
        "planning-agent.md": common + f"\n## Allowed scope\n\nRead-only validation of plan references, coverage, dependency order, and documented gaps.\n\n## Ready tasks\n\n{ready_lines}\n",
        "prototype-agent.md": common + f"\n## Gate\n\nPhysical tasks are blocked pending human approval. Required approval records: {approval_text}. Do not build, purchase, record, publish, or contact an external party.\n\n## Tasks\n\n{physical_lines}\n",
        "review-agent.md": common + f"\n## Gate\n\nRead-only tasks may evaluate evidence only after their declared dependencies are satisfied.\n\n## Tasks\n\n{review_lines}\n",
    }
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
        f"**実施順の読み方:** クリティカルパスは {critical_path} です。物理・外部効果タスクは承認要件を確認してから実施し、現在READYのタスクは {', '.join(f'`{task_id}`' for task_id in [task['id'] for task in plan['tasks'] if task['status'] == 'READY']) or 'なし'} です。",
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
    manifest["state"] = "PLANNING"
    dump_yaml(manifest, manifest_path)
    state_path = project_root / "08_runtime/production-state.json"
    state = load_json(state_path)
    state.pop("state_sha256", None)
    state["state"] = "PLANNING"
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
