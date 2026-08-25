"""Planning schema loading and cross-reference validation."""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .canonical import canonical_sha256
from .diagnostics import Finding
from .schema import load_schema, validate_instance
from .yaml_io import load_yaml


DOMAIN_SCHEMAS = {
    "selection_record": "selection-record.schema.json",
    "assumption": "assumption.schema.json",
    "scope_baseline": "scope-baseline.schema.json",
    "deliverable": "deliverable.schema.json",
    "technical_specification": "technical-spec.schema.json",
    "acceptance_test": "acceptance-test.schema.json",
    "material": "material.schema.json",
    "resource": "resource.schema.json",
    "work_package": "work-package.schema.json",
    "task": "task.schema.json",
    "schedule": "schedule.schema.json",
    "budget": "budget.schema.json",
    "risk": "risk.schema.json",
    "approval_requirement": "approval-requirement.schema.json",
    "approval_register": "approval-register.schema.json",
    "coverage_report": "coverage-report.schema.json",
}


def load_planning_schemas(repository: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    common = load_schema(repository / "schemas/common.schema.json")
    planning = load_schema(repository / "schemas/planning.schema.json")
    schemas = {name: load_schema(repository / "schemas" / filename) for name, filename in DOMAIN_SCHEMAS.items()}
    schemas["production_plan"] = load_schema(repository / "schemas/production-plan.schema.json")
    return common, planning, schemas


def _finding(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation)


def _index(records: list[dict[str, Any]], field: str = "id") -> dict[str, dict[str, Any]]:
    return {str(record[field]): record for record in records if isinstance(record, dict) and field in record}


def _check_refs(
    findings: list[Finding],
    records: list[dict[str, Any]],
    field: str,
    target_ids: set[str],
    *,
    file: Path,
) -> None:
    for record in records:
        for value in record.get(field, []):
            if value not in target_ids:
                findings.append(_finding("PLANNING_REFERENCE", f"{field} references missing ID {value}", file=file, location=f"/{record.get('id', '<unknown>')}/{field}", remediation="Add the referenced record or remove the stale reference."))


def _cycle_or_order(nodes: list[str], edges: list[dict[str, str]]) -> tuple[list[str], bool]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree = {node: 0 for node in nodes}
    for edge in edges:
        source, target = edge["from"], edge["to"]
        adjacency[source].append(target)
        indegree[target] += 1
    queue = deque(sorted(node for node, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for target in sorted(adjacency[node]):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return order, len(order) != len(nodes)


def validate_plan_document(plan: dict[str, Any], *, repository: Path, plan_path: Path | str) -> list[Finding]:
    """Validate the aggregate plan, each domain record, and its graph references."""

    plan_path = Path(plan_path)
    findings: list[Finding] = []
    try:
        common, planning, schemas = load_planning_schemas(repository)
    except Exception as exc:
        return [_finding("PLANNING_SCHEMA_LOAD", str(exc), file=repository / "schemas", remediation="Restore every planning schema and retry validation.")]
    schema_store = [planning, *schemas.values()]
    findings.extend(validate_instance(plan, schemas["production_plan"], schema_path=repository / "schemas/production-plan.schema.json", common_schema=common, schema_store=schema_store))

    record_groups = {
        "assumptions": "assumption",
        "deliverables": "deliverable",
        "technical_specifications": "technical_specification",
        "acceptance_tests": "acceptance_test",
        "materials": "material",
        "resources": "resource",
        "work_packages": "work_package",
        "tasks": "task",
        "risks": "risk",
    }
    for field, schema_name in record_groups.items():
        for index, record in enumerate(plan.get(field, [])):
            if isinstance(record, dict):
                findings.extend(validate_instance(record, schemas[schema_name], schema_path=repository / "schemas" / DOMAIN_SCHEMAS[schema_name], common_schema=common, schema_store=schema_store))
            else:
                findings.append(_finding("PLANNING_RECORD_OBJECT", "planning collection item must be an object", file=plan_path, location=f"/{field}/{index}", remediation="Regenerate the plan from structured records."))

    findings.extend(validate_instance(plan.get("selection_record"), schemas["selection_record"], schema_path=repository / "schemas/selection-record.schema.json", common_schema=common, schema_store=schema_store))
    findings.extend(validate_instance(plan.get("scope_baseline"), schemas["scope_baseline"], schema_path=repository / "schemas/scope-baseline.schema.json", common_schema=common, schema_store=schema_store))
    findings.extend(validate_instance(plan.get("schedule"), schemas["schedule"], schema_path=repository / "schemas/schedule.schema.json", common_schema=common, schema_store=schema_store))
    findings.extend(validate_instance(plan.get("budget"), schemas["budget"], schema_path=repository / "schemas/budget.schema.json", common_schema=common, schema_store=schema_store))
    findings.extend(validate_instance(plan.get("approval_register"), schemas["approval_register"], schema_path=repository / "schemas/approval-register.schema.json", common_schema=common, schema_store=schema_store))
    findings.extend(validate_instance(plan.get("coverage_report"), schemas["coverage_report"], schema_path=repository / "schemas/coverage-report.schema.json", common_schema=common, schema_store=schema_store))

    deliverables = _index(plan.get("deliverables", []))
    technical_specs = _index(plan.get("technical_specifications", []))
    work_packages = _index(plan.get("work_packages", []))
    tasks = _index(plan.get("tasks", []))
    resources = _index(plan.get("resources", []))
    materials = _index(plan.get("materials", []))
    milestones = _index(plan.get("schedule", {}).get("milestones", []))
    approval_requirements = _index(plan.get("approval_register", {}).get("requirements", []))

    _check_refs(findings, plan.get("deliverables", []), "technical_spec_ids", set(technical_specs), file=plan_path)
    acceptance_tests = _index(plan.get("acceptance_tests", []))
    _check_refs(findings, plan.get("deliverables", []), "acceptance_test_ids", set(acceptance_tests), file=plan_path)
    for acceptance_test in plan.get("acceptance_tests", []):
        if acceptance_test.get("target_requirement") not in set(plan.get("mandatory_requirement_ids", [])):
            findings.append(_finding("PLANNING_REFERENCE", "acceptance test target_requirement references a missing requirement", file=plan_path, location=f"/{acceptance_test.get('id')}/target_requirement", remediation="Connect the fixture to a mandatory handoff requirement."))
    for deliverable in plan.get("deliverables", []):
        if deliverable.get("due_milestone_id") not in milestones:
            findings.append(_finding("PLANNING_REFERENCE", "deliverable due_milestone_id references a missing milestone", file=plan_path, location=f"/{deliverable.get('id')}/due_milestone_id", remediation="Add the milestone to the schedule."))
    _check_refs(findings, plan.get("technical_specifications", []), "source_requirement_ids", {str(item) for item in plan.get("mandatory_requirement_ids", [])}, file=plan_path)
    _check_refs(findings, plan.get("work_packages", []), "deliverable_ids", set(deliverables), file=plan_path)
    _check_refs(findings, plan.get("work_packages", []), "task_ids", set(tasks), file=plan_path)
    _check_refs(findings, plan.get("tasks", []), "required_resource_ids", set(resources), file=plan_path)
    _check_refs(findings, plan.get("tasks", []), "required_material_ids", set(materials), file=plan_path)
    _check_refs(findings, plan.get("tasks", []), "approval_requirement_ids", set(approval_requirements), file=plan_path)
    _check_refs(findings, plan.get("tasks", []), "depends_on", set(tasks), file=plan_path)
    _check_refs(findings, plan.get("work_packages", []), "depends_on", set(work_packages), file=plan_path)

    task_edges = [{"from": dependency, "to": task["id"]} for task in plan.get("tasks", []) for dependency in task.get("depends_on", [])]
    task_ids = [str(task["id"]) for task in plan.get("tasks", []) if isinstance(task, dict) and "id" in task]
    topological_order, cyclic = _cycle_or_order(task_ids, task_edges)
    if cyclic:
        findings.append(_finding("PLANNING_DAG_CYCLE", "task dependency graph contains a cycle", file=plan_path, location="/tasks", remediation="Remove a dependency edge so the task graph is acyclic."))
    graph = plan.get("dependency_graph", {})
    if graph.get("topological_order") != topological_order:
        findings.append(_finding("PLANNING_TOPOLOGICAL_ORDER", "dependency graph topological_order is not the deterministic task order", file=plan_path, location="/dependency_graph/topological_order", remediation="Regenerate the dependency graph with sorted Kahn traversal."))
    if set(graph.get("nodes", [])) != set(task_ids):
        findings.append(_finding("PLANNING_GRAPH_NODES", "dependency graph nodes do not match task IDs", file=plan_path, location="/dependency_graph/nodes", remediation="Regenerate graph nodes from the task collection."))
    if sorted(graph.get("edges", []), key=lambda edge: (edge.get("from", ""), edge.get("to", ""))) != sorted(task_edges, key=lambda edge: (edge["from"], edge["to"])):
        findings.append(_finding("PLANNING_GRAPH_EDGES", "dependency graph edges do not match task dependencies", file=plan_path, location="/dependency_graph/edges", remediation="Regenerate graph edges from task dependencies."))

    coverage = plan.get("coverage_report", {})
    if coverage.get("coverage_percent") != 100 or coverage.get("uncovered_requirement_ids"):
        blocking_gaps = [gap for gap in plan.get("gaps", []) if isinstance(gap, dict) and gap.get("blocking") is True]
        if plan.get("state") != "PLANNING" or not blocking_gaps:
            findings.append(_finding("PLANNING_COVERAGE", "mandatory handoff requirements are not fully covered without a blocking planning gap", file=plan_path, location="/coverage_report", remediation="Keep the plan in PLANNING and record a structured blocking gap until every requirement is connected."))
    if plan.get("selection_record", {}).get("status") == "PROVISIONAL" and plan.get("state") == "READY_FOR_PROTOTYPE":
        findings.append(_finding("PLANNING_SELECTION_GATE", "a provisional selection cannot produce READY_FOR_PROTOTYPE", file=plan_path, location="/state", remediation="Keep the project in PLANNING until the selection authority is resolved."))
    for task in plan.get("tasks", []):
        if task.get("status") == "READY" and task.get("effect_type") not in {"READ_ONLY", "REPOSITORY_WRITE"}:
            findings.append(_finding("PLANNING_EXTERNAL_READY", "external-effect task cannot be READY without runtime approval", file=plan_path, location=f"/tasks/{task.get('id')}/status", remediation="Use BLOCKED or BACKLOG until the approval and runtime gates exist."))
    expected_integrity = canonical_sha256({key: value for key, value in plan.items() if key != "integrity"})
    if plan.get("integrity", {}).get("content_sha256") != expected_integrity:
        findings.append(_finding("PLANNING_INTEGRITY", "plan content_sha256 does not match the canonical plan payload", file=plan_path, location="/integrity/content_sha256", remediation="Regenerate the plan so the integrity hash covers the payload without the integrity block."))
    return findings


def validate_planning_project(project_root: Path, repository: Path) -> list[Finding]:
    plan_path = project_root / "03_plan/production-plan.yaml"
    if not plan_path.is_file():
        return []
    try:
        plan = load_yaml(plan_path)
    except Exception as exc:
        return [_finding("PLANNING_INPUT", str(exc), file=plan_path, remediation="Regenerate the planning output as valid YAML.")]
    if not isinstance(plan, dict):
        return [_finding("PLANNING_OBJECT", "production-plan.yaml must be a mapping", file=plan_path, remediation="Regenerate the planning output.")]
    return validate_plan_document(plan, repository=repository, plan_path=plan_path)
