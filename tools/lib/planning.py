"""Planning schema loading and cross-reference validation."""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from .actionability import assess as assess_actionability
from .canonical import canonical_sha256
from .config import load_config
from .diagnostics import Finding
from .schema import load_schema, validate_instance
from .security import validate_asset_uri
from .visual_package import validate_visual_package
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
    schemas["visual_package"] = load_schema(repository / "schemas/visual-package.schema.json")
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


def _check_reference_access(findings: list[Finding], plan: dict[str, Any], *, repository: Path, file: Path) -> None:
    """Validate reference URL safety even when a plan is supplied without its builder."""
    references = plan.get("reference_access", [])
    if not isinstance(references, list):
        return
    try:
        policy = load_config(repository, "reference-policy.yaml")
    except Exception as exc:
        findings.append(_finding("PLANNING_REFERENCE_POLICY", str(exc), file=repository / "config/reference-policy.yaml", remediation="Restore the reference URL policy and retry validation."))
        return
    allowed_categories = {
        str(item.get("id"))
        for item in policy.get("categories", [])
        if isinstance(item, dict) and item.get("id")
    }
    allowed_schemes = policy.get("allowed_uri_schemes", ["https"])
    allow_query = bool(policy.get("https", {}).get("allow_query", False))
    for index, reference in enumerate(references):
        if not isinstance(reference, dict):
            continue
        location = f"/reference_access/{index}"
        categories = reference.get("reference_categories", [])
        unknown_categories = sorted(set(categories) - allowed_categories) if isinstance(categories, list) else []
        if unknown_categories:
            findings.append(_finding("PLANNING_REFERENCE_CATEGORY", f"unknown reference categories: {', '.join(unknown_categories)}", file=file, location=f"{location}/reference_categories", remediation="Use category IDs declared in config/reference-policy.yaml."))
        status = reference.get("access_status")
        url = reference.get("access_url")
        record_hash = reference.get("record_hash")
        if not isinstance(record_hash, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", record_hash) is None or set(record_hash[7:]) == {"0"}:
            findings.append(_finding("PLANNING_REFERENCE_HASH", "record_hash must be a non-zero canonical SHA-256 value", file=file, location=f"{location}/record_hash", remediation="Preserve the non-zero record_hash emitted by the accepted Research handoff."))
        if status == "AVAILABLE" and url is None:
            findings.append(_finding("PLANNING_REFERENCE_URL", "AVAILABLE reference must provide access_url", file=file, location=f"{location}/access_url", remediation="Provide a stable permanent HTTPS URL or mark the reference MISSING."))
        if status == "MISSING" and url is not None:
            findings.append(_finding("PLANNING_REFERENCE_URL", "MISSING reference must not provide access_url", file=file, location=f"{location}/access_url", remediation="Remove access_url or mark the reference AVAILABLE after URL validation."))
        if url is None:
            continue
        if not isinstance(url, str) or not url or any(character.isspace() for character in url):
            findings.append(_finding("PLANNING_REFERENCE_URL", "access_url must be a non-empty URL without whitespace", file=file, location=f"{location}/access_url", remediation="Provide a stable permanent HTTPS URL without credentials or signed parameters."))
            continue
        uri_finding = validate_asset_uri(url, allowed_schemes=allowed_schemes, allow_query=allow_query)
        parsed = urlsplit(url)
        if uri_finding is not None:
            findings.append(_finding("PLANNING_REFERENCE_URL", uri_finding.reason, file=file, location=f"{location}/access_url", remediation="Provide a stable permanent HTTPS URL without credentials, query parameters, or fragments."))
        elif not parsed.hostname:
            findings.append(_finding("PLANNING_REFERENCE_URL", "HTTPS reference URL must contain a hostname", file=file, location=f"{location}/access_url", remediation="Provide a stable permanent HTTPS URL with a hostname."))


def validate_plan_document(plan: dict[str, Any], *, repository: Path, plan_path: Path | str) -> list[Finding]:
    """Validate the aggregate plan, each domain record, and its graph references."""

    plan_path = Path(plan_path)
    findings: list[Finding] = []
    try:
        common, planning, schemas = load_planning_schemas(repository)
    except Exception as exc:
        return [_finding("PLANNING_SCHEMA_LOAD", str(exc), file=repository / "schemas", remediation="Restore every planning schema and retry validation.")]
    schema_store = [load_schema(repository / "schemas/production-method.schema.json"), planning, *schemas.values(), load_schema(repository / "schemas/asset-reference.schema.json")]
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
    _check_reference_access(findings, plan, repository=repository, file=plan_path)
    findings.extend(validate_visual_package(
        plan.get("visual_package"),
        repository=repository,
        project_root=plan_path.parent.parent,
        plan_path=plan_path,
        plan=plan,
        require_files=False,
    ))

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
    if cyclic and "task_dependency_graph" not in plan.get("readiness", {}).get("unmet", []):
        findings.append(_finding("PLANNING_DAG_CYCLE", "task dependency graph contains a cycle", file=plan_path, location="/tasks", remediation="Remove a dependency edge so the task graph is acyclic."))
    graph = plan.get("dependency_graph", {})
    if graph.get("topological_order") != topological_order:
        findings.append(_finding("PLANNING_TOPOLOGICAL_ORDER", "dependency graph topological_order is not the deterministic task order", file=plan_path, location="/dependency_graph/topological_order", remediation="Regenerate the dependency graph with sorted Kahn traversal."))
    if set(graph.get("nodes", [])) != set(task_ids):
        findings.append(_finding("PLANNING_GRAPH_NODES", "dependency graph nodes do not match task IDs", file=plan_path, location="/dependency_graph/nodes", remediation="Regenerate graph nodes from the task collection."))
    if sorted(graph.get("edges", []), key=lambda edge: (edge.get("from", ""), edge.get("to", ""))) != sorted(task_edges, key=lambda edge: (edge["from"], edge["to"])):
        findings.append(_finding("PLANNING_GRAPH_EDGES", "dependency graph edges do not match task dependencies", file=plan_path, location="/dependency_graph/edges", remediation="Regenerate graph edges from task dependencies."))
    critical_path = plan.get("critical_path_task_ids", [])
    schedule_critical_path = plan.get("schedule", {}).get("critical_path_task_ids", [])
    if not isinstance(critical_path, list):
        critical_path = []
    if not isinstance(schedule_critical_path, list):
        schedule_critical_path = []
    if critical_path != schedule_critical_path:
        findings.append(_finding("PLANNING_CRITICAL_PATH", "top-level and schedule critical paths do not match", file=plan_path, location="/critical_path_task_ids", remediation="Regenerate both critical_path_task_ids fields from the same dependency graph."))
    task_id_set = set(task_ids)
    if ((not critical_path and not cyclic) or any(task_id not in task_id_set for task_id in critical_path)):
        findings.append(_finding("PLANNING_CRITICAL_PATH", "critical path contains a missing task or no task", file=plan_path, location="/critical_path_task_ids", remediation="Regenerate the critical path from the non-empty task collection."))
    graph_edges = {(edge.get("from"), edge.get("to")) for edge in graph.get("edges", []) if isinstance(edge, dict)}
    if any((source, target) not in graph_edges for source, target in zip(critical_path, critical_path[1:])):
        findings.append(_finding("PLANNING_CRITICAL_PATH", "critical path contains consecutive tasks that are not connected by a dependency edge", file=plan_path, location="/critical_path_task_ids", remediation="Use a connected dependency path or regenerate the plan."))

    coverage = plan.get("coverage_report", {})
    coverage_items = coverage.get("requirements", []) if isinstance(coverage, dict) else []
    covered_count = sum(item.get("status") == "COVERED" for item in coverage_items if isinstance(item, dict))
    expected_uncovered = [str(item.get("requirement_id")) for item in coverage_items if isinstance(item, dict) and item.get("status") == "UNCOVERED"]
    expected_percent = round(covered_count * 100 / len(coverage_items)) if coverage_items else 0
    if coverage.get("coverage_percent") != expected_percent or coverage.get("uncovered_requirement_ids") != expected_uncovered:
        findings.append(_finding("PLANNING_COVERAGE", "coverage report does not match its requirement rows", file=plan_path, location="/coverage_report", remediation="Regenerate coverage_percent and uncovered_requirement_ids from the requirement rows."))
    if expected_uncovered and plan.get("state") == "READY_FOR_PROTOTYPE":
        findings.append(_finding("PLANNING_COVERAGE", "mandatory handoff requirements are not fully covered", file=plan_path, location="/state", remediation="Keep the plan BLOCKED until every requirement is connected to a deliverable, test, work package, and task."))
    if plan.get("selection_record", {}).get("status") == "PROVISIONAL" and plan.get("state") == "READY_FOR_PROTOTYPE":
        findings.append(_finding("PLANNING_SELECTION_GATE", "a provisional selection cannot produce READY_FOR_PROTOTYPE", file=plan_path, location="/state", remediation="Keep the project in PLANNING until the selection authority is resolved."))
    for task in plan.get("tasks", []):
        if task.get("status") == "READY" and task.get("effect_type") not in {"READ_ONLY", "REPOSITORY_WRITE"}:
            findings.append(_finding("PLANNING_EXTERNAL_READY", "external-effect task cannot be READY without runtime approval", file=plan_path, location=f"/tasks/{task.get('id')}/status", remediation="Use BLOCKED or BACKLOG until the approval and runtime gates exist."))
    if "production_method" in plan or "actionability" in plan:
        if plan.get("actionability") != assess_actionability(plan, repository):
            findings.append(_finding("PLAN_ACTIONABILITY", "content qualification differs from its inputs", file=plan_path, remediation="Regenerate the method assessment; do not set PLAN_READY manually."))
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
    findings = validate_plan_document(plan, repository=repository, plan_path=plan_path)
    method_path = project_root / "02_specification/production-method.yaml"
    if "production_method" in plan or method_path.exists() or method_path.is_symlink():
        try:
            import copy
            from .production_reuse import integrate
            expected = copy.deepcopy(plan)
            expected['production_method'] = load_yaml(method_path)
            expected.pop('knowledge_reuse', None)
            integrate(project_root, expected)
            if expected.get('knowledge_reuse') != plan.get('knowledge_reuse'):
                raise ValueError('pinned knowledge reuse differs from canonical plan')
            if method_path.is_symlink() or expected['production_method'] != plan.get("production_method"):
                raise ValueError("method input differs from its canonical aggregate")
        except Exception as exc:
            findings.append(_finding("PRODUCTION_METHOD_INPUT", str(exc), file=method_path, remediation="Regenerate from the project-local proposed method."))
    package_path = project_root / "03_plan/visual-package.yaml"
    if not package_path.is_file():
        findings.append(_finding("VISUAL_PACKAGE_METADATA_MISSING", "materialized project is missing visual-package.yaml", file=package_path, remediation="Regenerate the plan so the visual package metadata is written beside the production plan."))
    else:
        try:
            package_on_disk = load_yaml(package_path)
        except Exception as exc:
            findings.append(_finding("VISUAL_PACKAGE_METADATA", str(exc), file=package_path, remediation="Restore visual-package.yaml from the validated production plan."))
        else:
            if package_on_disk != plan.get("visual_package"):
                findings.append(_finding("VISUAL_PACKAGE_METADATA_MISMATCH", "visual-package.yaml does not match the package embedded in production-plan.yaml", file=package_path, remediation="Regenerate both projections from the accepted handoff; do not edit either projection independently."))
            findings.extend(validate_visual_package(
                package_on_disk,
                repository=repository,
                project_root=project_root,
                plan_path=package_path,
                plan=plan,
                require_files=True,
            ))
    findings.extend(validate_visual_package(
        plan.get("visual_package"),
        repository=repository,
        project_root=project_root,
        plan_path=plan_path,
        plan=plan,
        require_files=True,
    ))
    return findings
