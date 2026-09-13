"""Qualify the semantic and executable link from Research to Production."""

from __future__ import annotations

import re
from typing import Any

from .canonical import canonical_sha256


CONTRACT_VERSION = "production-inspiration-alignment/v1"
RICH_HYPOTHESIS_FIELDS = ("intended_experience", "includes", "feasibility")
RESOURCE_CATEGORIES = ("MATERIAL", "TOOL", "DATA", "EQUIPMENT", "SKILL", "COST", "TIME", "PLACE")


def _strings(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [" ".join(value.split())]
    if isinstance(value, list):
        return [item for child in value for item in _strings(child)]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _strings(child)]
    return []


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item.strip()))


def _path_steps(creative_direction: str) -> list[str]:
    """Read only the explicit numbered completion path from the direction."""

    in_section = False
    result: list[str] = []
    for line in creative_direction.splitlines():
        heading = line.strip().startswith("#")
        if heading:
            in_section = "完了経路" in line or "completion path" in line.casefold()
            continue
        if not in_section:
            continue
        match = re.match(r"^\s*\d+[.)]\s+(.+?)\s*$", line)
        if match:
            result.append(" ".join(match.group(1).split()))
        elif line.strip() and line.lstrip().startswith("#"):
            break
    return result


def _task_bindings(
    prototype: dict[str, Any],
    tasks: list[dict[str, Any]],
    work_packages: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], list[str]]:
    prototype_id = str(prototype.get("id"))
    source_to_derived: dict[str, list[str]] = {}
    for task in tasks:
        trace = {str(value) for value in task.get("trace_refs", [])}
        if prototype_id not in trace:
            continue
        for source_task in prototype.get("tasks", []):
            if isinstance(source_task, dict) and str(source_task.get("id")) in trace:
                source_to_derived.setdefault(str(source_task["id"]), []).append(str(task["id"]))
    package_tasks = [
        str(value)
        for package in work_packages
        if prototype_id in {str(item) for item in package.get("trace_refs", [])}
        for value in package.get("task_ids", [])
    ]
    return source_to_derived, _unique(package_tasks)


def _source_input(prototype: dict[str, Any], marker: str) -> list[str]:
    return [item for item in _strings(prototype.get("inputs", [])) if marker.casefold() in item.casefold()]


def _resource_matrix(
    *,
    hypothesis: dict[str, Any],
    prototype: dict[str, Any],
    bound_task_ids: list[str],
    has_physical_work: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    feasibility = hypothesis.get("feasibility") if isinstance(hypothesis.get("feasibility"), dict) else {}
    technical = str(feasibility.get("technical") or "")
    skills_match = re.search(r"required skills:\s*(.+?)(?:\.|$)", technical, re.IGNORECASE)
    skills = _unique([item.strip() for item in (skills_match.group(1).split(",") if skills_match else []) if item.strip()])
    if not skills:
        skills = [str(prototype.get("executor_capability"))] if prototype.get("executor_capability") else []

    materials = _source_input(prototype, "materials")
    data = _source_input(prototype, "03_plan/production-plan.yaml")
    venue = [item for item in _strings(prototype.get("constraints", [])) if "venue:" in item.casefold()]
    cost = str(feasibility.get("cost_band") or "UNKNOWN")
    time = str(feasibility.get("duration_band") or "UNKNOWN")
    rows: list[dict[str, Any]] = []
    gaps: list[str] = []

    def add(category: str, source: list[str], *, status: str = "BOUND", reason: str = "") -> None:
        if source:
            rows.append({"category": category, "source": source, "status": status, "task_ids": bound_task_ids, "reason": reason or "The accepted source input is bound to the derived production task."})
        elif status == "NOT_REQUIRED":
            rows.append({"category": category, "source": [], "status": status, "task_ids": [], "reason": reason})
        else:
            rows.append({"category": category, "source": [], "status": "UNRESOLVED", "task_ids": [], "reason": reason or f"No accepted {category.lower()} input was supplied."})
            gaps.append(f"{category.lower()} is not bound to a production task.")

    add("MATERIAL", materials, reason="The prototype's declared production-plan materials input is used by the derived task.")
    add("DATA", data, reason="The digital prototype reads the accepted production-plan dimensions, materials, and quantity.")
    add("EQUIPMENT", [str(prototype.get("executor_capability"))] if prototype.get("executor_capability") else [], reason="The accepted executor capability is the declared production equipment.")
    add("SKILL", skills, reason="The accepted feasibility technical field names the required skill or executor capability.")
    add("COST", [cost] if cost and cost != "UNKNOWN" else [], reason="The accepted hypothesis cost band is retained as an estimate.")
    add("TIME", [time] if time and time != "UNKNOWN" else [], reason="The accepted prototype duration band is retained as a relative schedule estimate.")
    add("PLACE", venue, status="NOT_REQUIRED" if not has_physical_work and not venue else "BOUND", reason="No physical venue is required for the simulated local digital preview." if not has_physical_work and not venue else "The accepted venue constraint is retained for the derived task.")
    add("TOOL", [], status="NOT_REQUIRED" if not has_physical_work else "UNRESOLVED", reason="The digital renderer is the declared executor; no physical tool is needed for a simulated preview." if not has_physical_work else "Physical tool requirements are not structured in the accepted handoff.")
    if has_physical_work:
        gaps.append("physical work has no complete structured tool and material availability path.")
    return rows, gaps


def build_alignment(
    *,
    hypothesis: dict[str, Any],
    prototype: dict[str, Any] | None,
    creative_direction: str,
    creative_direction_ref: str,
    tasks: list[dict[str, Any]],
    work_packages: list[dict[str, Any]],
    has_physical_work: bool,
) -> dict[str, Any] | None:
    """Build a lossless qualification record for a rich Research handoff.

    The record is deliberately additive. Legacy fixtures with no semantic
    fields are left untouched, while a rich handoff cannot silently collapse
    into a title and a selected ID.
    """

    if not all(field in hypothesis for field in RICH_HYPOTHESIS_FIELDS) or prototype is None:
        return None
    selected_id = str(hypothesis.get("id"))
    prototype_id = str(prototype.get("id"))
    source_to_derived, bound_task_ids = _task_bindings(prototype, tasks, work_packages)
    source_tasks = [item for item in prototype.get("tasks", []) if isinstance(item, dict)]
    missing_source_tasks = [str(item.get("id")) for item in source_tasks if str(item.get("id")) not in source_to_derived]
    source_task_ids = [str(item.get("id")) for item in source_tasks if item.get("id")]
    has_production_action = any(
        task.get("effect_type") in {"REPOSITORY_WRITE", "PHYSICAL_EXTERNAL", "PUBLICATION", "PURCHASE", "CONTRACT"}
        for task in tasks
        if task.get("id") in bound_task_ids
    )
    proposition = str(hypothesis.get("proposition") or "")
    included = _strings(hypothesis.get("includes"))
    direction_text = creative_direction.casefold()
    gaps: list[str] = []
    if not proposition or proposition.casefold() not in direction_text:
        gaps.append("the adopted proposition is missing from creative direction.")
    missing_elements = [item for item in included if item.casefold() not in direction_text]
    if missing_elements:
        gaps.append("creative direction does not retain included elements: " + ", ".join(missing_elements))
    if missing_source_tasks:
        gaps.append("prototype tasks are not bound to derived tasks: " + ", ".join(missing_source_tasks))
    if not has_production_action:
        gaps.append("the derived path contains READ_ONLY confirmation only; no production action is represented.")

    resource_matrix, resource_gaps = _resource_matrix(
        hypothesis=hypothesis,
        prototype=prototype,
        bound_task_ids=bound_task_ids,
        has_physical_work=has_physical_work,
    )
    gaps.extend(resource_gaps)
    path = _path_steps(creative_direction)
    completion_path: list[dict[str, Any]] = []
    for index, statement in enumerate(path, start=1):
        terms = {word.casefold() for word in re.findall(r"[\w-]{4,}", statement)}
        matching = [
            task_id for task_id in bound_task_ids
            if terms & {word.casefold() for word in re.findall(r"[\w-]{4,}", next((task.get("title", "") for task in tasks if task.get("id") == task_id), ""))}
        ]
        status = "BOUND" if matching else "UNRESOLVED"
        completion_path.append({"sequence": index, "statement": statement, "task_ids": _unique(matching), "status": status})
        if not matching:
            gaps.append(f"completion path step {index} is not bound to a derived task.")
    if not path:
        gaps.append("creative direction has no explicit completion path.")

    checks = {
        "selected_hypothesis_match": selected_id == str(hypothesis.get("id")),
        "creative_direction_match": proposition.casefold() in direction_text and not missing_elements,
        "prototype_tasks_bound": not missing_source_tasks,
        "production_action_present": has_production_action,
        "resource_bindings_complete": not resource_gaps,
        "completion_path_complete": bool(path) and all(item["status"] == "BOUND" for item in completion_path),
    }
    if not all(checks.values()):
        status = "INCOMPLETE"
    else:
        status = "MATCH"
    hypothesis_payload = {key: value for key, value in hypothesis.items() if key not in {"status", "recommendation", "single_hypothesis_rationale"}}
    return {
        "contract_version": CONTRACT_VERSION,
        "status": status,
        "selected_hypothesis_id": selected_id,
        "hypothesis_sha256": canonical_sha256(hypothesis_payload),
        "proposition": proposition,
        "intended_experience": _strings(hypothesis.get("intended_experience")),
        "included_elements": included,
        "excluded_elements": _strings(hypothesis.get("excludes")),
        "differentiation": str((hypothesis.get("differentiation") or {}).get("statement") or ""),
        "creative_direction_ref": creative_direction_ref,
        "creative_direction_sha256": canonical_sha256(creative_direction),
        "prototype_plans": [{
            "id": prototype_id,
            "hypothesis_id": selected_id,
            "method": str(prototype.get("method") or ""),
            "input_refs": _strings(prototype.get("inputs")),
            "constraint_refs": _strings(prototype.get("constraints")),
            "source_task_ids": source_task_ids,
            "bound_task_ids": bound_task_ids,
            "status": "BOUND" if not missing_source_tasks else "INCOMPLETE",
        }],
        "resource_matrix": resource_matrix,
        "completion_path": completion_path,
        "checks": checks,
        "gaps": _unique(gaps),
        "trace_refs": _unique([selected_id, prototype_id, *source_task_ids, *bound_task_ids]),
    }
