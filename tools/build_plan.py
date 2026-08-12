#!/usr/bin/env python3
"""Build a deterministic production plan from an accepted handoff project."""

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

    plan = {
        "schema_version": "1.0.0", "plan_id": "PL001", "plan_revision": 1, "project_id": str(_require_mapping(project_root / "manifest.yaml")["project_id"]),
        "state": "PLANNING", "generated_at": handoff["generated_at"], "handoff_ref": handoff_ref,
        "mandatory_requirement_ids": requirement_ids, "acceptance_test_ids": acceptance_test_ids, "acceptance_tests": acceptance_tests,
        "selection_record": selection_record, "scope_baseline": scope_baseline, "assumptions": assumptions,
        "deliverables": deliverables, "technical_specifications": technical_specifications, "materials": materials, "resources": resources,
        "work_packages": work_packages, "tasks": tasks, "schedule": schedule, "budget": budget, "risks": risks,
        "approval_register": approval_register, "coverage_report": coverage_report, "dependency_graph": graph,
        "critical_path_task_ids": ["TK001", "TK002", "TK003"], "gaps": gaps,
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
    contexts = {
        "planning-agent.md": common + "\n## Allowed scope\n\nRead-only validation of plan references, coverage, dependency order, and documented gaps.\n\n## Task\n\n`TK004` — Validate plan references and coverage.\n",
        "prototype-agent.md": common + "\n## Gate\n\n`TK001` and `TK002` are `BLOCKED` pending `AR001` HUMAN approval. Do not build, purchase, record, publish, or contact an external party.\n\n## Tasks\n\n- `TK001` — Build the one-tenth scale model.\n- `TK002` — Record three fixed viewpoints.\n",
        "review-agent.md": common + "\n## Gate\n\n`TK003` remains `BACKLOG` until `TK002` has external evidence.\n\n## Task\n\n`TK003` — Review the three frames against `AT001`.\n",
    }
    for filename, text in contexts.items():
        (context_root / filename).parent.mkdir(parents=True, exist_ok=True)
        (context_root / filename).write_text(text, encoding="utf-8")


def _write_outputs(project_root: Path, plan: dict[str, Any]) -> None:
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
    brief = f"""# Harmony Study — production planning brief\n\n- Plan: `PL001` revision 1\n- State: `PLANNING`\n- Selection: `{plan['selection_record']['status']}` (`PH001`)\n- Requirement coverage: `{plan['coverage_report']['coverage_percent']}%`\n- Critical path: `TK001 → TK002 → TK003`\n- Safe read-only task: `TK004`\n\n## Scope\n\nThe plan covers a one-tenth scale model and three fixed-viewpoint frame review for the repeated interval and one deliberate interruption. The target venue lighting remains an open, nonblocking gap (`GP001`).\n\n## Gates\n\n`TK001` and `TK002` require `AR001` HUMAN approval because they have physical/external effects. This plan does not authorize purchase, contract, publication, submission, contact, deletion, or physical work.\n\n## Unresolved planning inputs\n\nBudget amounts, quotes, supplier, venue availability, and calendar dates are not present. The budget is estimate-only with null amounts, and the schedule is relative.\n"""
    (project_root / "03_plan/human-brief.md").write_text(brief, encoding="utf-8")
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
        print(str(project_root / "03_plan/production-plan.yaml"))
        return EXIT_SUCCESS
    except DiagnosticError as exc:
        emit_findings([exc.finding], output_format=args.format)
        return EXIT_VALIDATION
    except (OSError, KeyError, TypeError, ValueError) as exc:
        emit_findings([_finding("PLANNING_BUILD", str(exc), file=args.project_root, remediation="Correct the accepted project input and retry plan generation.")], output_format=args.format)
        return EXIT_VALIDATION


if __name__ == "__main__":
    raise SystemExit(main())
