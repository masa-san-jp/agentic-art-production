"""Derive a production plan from the accepted handoff without inventing scope."""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .canonical import canonical_sha256
from .diagnostics import DiagnosticError, Finding
from .yaml_io import load_yaml


ARTIFACT_FILES = {
    "requirements": "production-requirements.yaml",
    "hypotheses": "production-hypotheses.yaml",
    "prototype_plans": "prototype-plans.yaml",
    "acceptance_tests": "acceptance-tests.yaml",
    "source_refs": "source-ref-index.yaml",
}
EFFECT_TYPES = {
    "READ_ONLY", "REPOSITORY_WRITE", "EXTERNAL_WRITE", "PHYSICAL_EXTERNAL",
    "PURCHASE", "CONTRACT", "PUBLICATION", "DELETION",
}
EXTERNAL_EFFECTS = {"EXTERNAL_WRITE", "PHYSICAL_EXTERNAL", "PURCHASE", "CONTRACT", "PUBLICATION", "DELETION"}
APPROVAL_EFFECTS = EXTERNAL_EFFECTS
VALID_TASK_STATUSES = {"BACKLOG", "READY", "BLOCKED", "DONE", "SKIPPED"}
VALID_RESOURCE_TYPES = {"PERSON_CAPABILITY", "EQUIPMENT", "SOFTWARE", "PLACE", "TIME"}
VALID_AVAILABILITY = {"UNKNOWN", "AVAILABLE", "REQUIRES_CONFIRMATION"}
VALID_RIGHTS = {"CLEAR", "PROJECT_INTERNAL", "REVIEW_REQUIRED", "UNKNOWN"}
VALID_SAFETY = {"CLEAR", "REVIEW_REQUIRED", "UNKNOWN"}


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


def _trace(*values: Any) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value is not None and str(value)))


def _text(record: dict[str, Any] | None, *keys: str) -> str | None:
    if not isinstance(record, dict):
        return None
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _list(record: dict[str, Any] | None, *keys: str) -> list[Any]:
    if not isinstance(record, dict):
        return []
    for key in keys:
        value = record.get(key)
        if isinstance(value, list):
            return value
    return []


def _quantity(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    amount = value.get("value")
    unit = value.get("unit")
    if isinstance(amount, (str, int)) and not isinstance(amount, bool) and isinstance(unit, str) and unit:
        return {"value": str(amount), "unit": unit}
    return None


def _money(value: Any, fallback_currency: str | None = None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    amount = value.get("amount")
    currency = value.get("currency", fallback_currency)
    if isinstance(amount, (str, int)) and not isinstance(amount, bool) and isinstance(currency, str) and len(currency) == 3 and currency.isupper():
        return {"amount": str(amount), "currency": currency}
    return None


def _id(prefix: str, number: int) -> str:
    return f"{prefix}{number:03d}"


class _Gaps:
    def __init__(self, source_gaps: list[dict[str, Any]]) -> None:
        used = [int(str(item.get("id", "GP000"))[2:]) for item in source_gaps if str(item.get("id", "")).startswith("GP") and str(item.get("id", ""))[2:].isdigit()]
        self.next_number = max(100, max(used, default=0) + 1)
        self.items: list[dict[str, Any]] = []
        for source in source_gaps:
            if source.get("id"):
                self.items.append({
                    "id": str(source["id"]),
                    "rule": "HANDOFF_OPEN_GAP",
                    "statement": str(source.get("statement", "Unresolved handoff gap.")),
                    "impact": str(source.get("impact", "Production readiness cannot be confirmed.")),
                    "owner": str(source.get("resolution_owner", "production")),
                    "blocking": bool(source.get("blocking", False)),
                    "resolution_condition": str(source.get("resolution_condition", "Resolution is recorded in the accepted handoff revision.")),
                    "source_refs": _trace(source["id"]),
                })

    def add(self, rule: str, statement: str, *, impact: str, owner: str = "production", blocking: bool = True, resolution_condition: str = "The missing structured input is added to a new accepted handoff revision.", source_refs: list[Any] | None = None, category: str = "OTHER") -> str:
        gap_id = _id("GP", self.next_number)
        self.next_number += 1
        value = {
            "id": gap_id, "rule": rule, "statement": statement, "impact": impact,
            "owner": owner, "blocking": blocking, "resolution_condition": resolution_condition,
            "source_refs": _trace(*(source_refs or [])),
        }
        if category != "OTHER":
            value["category"] = category
        self.items.append(value)
        return gap_id


def _topological_order(task_ids: list[str], tasks: list[dict[str, Any]]) -> list[str]:
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree = {task_id: 0 for task_id in task_ids}
    for task in tasks:
        for dependency in task.get("depends_on", []):
            adjacency[dependency].append(task["id"])
            indegree[task["id"]] += 1
    queue = deque(sorted(task_id for task_id, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while queue:
        task_id = queue.popleft()
        order.append(task_id)
        for target in sorted(adjacency[task_id]):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return order


def _first_mapping(records: list[dict[str, Any]], *keys: str) -> Any:
    for record in records:
        for key in keys:
            value = record.get(key)
            if value not in (None, "", []):
                return value
    return None


def _normalise_effect(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    effect = value.strip().upper()
    return effect if effect in EFFECT_TYPES else None


def _source_task_records(plan: dict[str, Any]) -> list[dict[str, Any]]:
    records = _list(plan, "tasks", "prototype_tasks", "work_items")
    return sorted((record for record in records if isinstance(record, dict)), key=lambda record: str(record.get("id", "")))


def _explicit_records(record: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    values = _list(record, *keys)
    return sorted((value for value in values if isinstance(value, dict)), key=lambda item: str(item.get("id", "")))


def build_plan(project_root: Path) -> dict[str, Any]:
    """Build a plan using only accepted handoff values and explicit prototype records."""

    handoff, bundle_manifest, source_input, artifacts = _load_inputs(project_root)
    handoff_path = project_root / "00_handoff/production-handoff.yaml"
    selection_input = handoff.get("selection")
    if not isinstance(selection_input, dict) or not isinstance(selection_input.get("selected_hypothesis_id"), str):
        raise DiagnosticError(_finding("PLANNING_SELECTION_REFERENCE", "handoff selection must name a selected hypothesis", file=handoff_path, location="/selection/selected_hypothesis_id", remediation="Re-export the handoff with a selected hypothesis snapshot."))
    if not isinstance(handoff.get("generated_at"), str):
        raise DiagnosticError(_finding("PLANNING_TIMESTAMP", "handoff generated_at is required for deterministic planning", file=handoff_path, location="/generated_at", remediation="Provide an RFC 3339 generated_at value in the handoff."))

    requirements = _records(artifacts["requirements"], "requirements", project_root / "00_handoff/source-bundle/artifacts/production-requirements.yaml")
    hypotheses = _records(artifacts["hypotheses"], "hypotheses", project_root / "00_handoff/source-bundle/artifacts/production-hypotheses.yaml")
    prototype_records = _records(artifacts["prototype_plans"], "prototype_plans", project_root / "00_handoff/source-bundle/artifacts/prototype-plans.yaml")
    acceptance_tests = _records(artifacts["acceptance_tests"], "acceptance_tests", project_root / "00_handoff/source-bundle/artifacts/acceptance-tests.yaml")
    selected_id = selection_input["selected_hypothesis_id"]
    selected = next((record for record in hypotheses if record.get("id") == selected_id), None)
    if selected is None:
        raise DiagnosticError(_finding("PLANNING_SELECTION_REFERENCE", "handoff selection does not reference a bundled hypothesis", file=handoff_path, location="/selection/selected_hypothesis_id", remediation="Re-export the handoff with the selected hypothesis snapshot."))

    handoff_ref = {"id": handoff["handoff_id"], "revision": handoff["revision"], "content_sha256": handoff["integrity"]["content_sha256"]}
    mandatory = [record for record in requirements if record.get("priority") == "mandatory"]
    requirement_ids = [str(record["id"]) for record in mandatory]
    requirement_by_id = {str(record["id"]): record for record in mandatory}
    required_test_ids = list(dict.fromkeys(test_id for record in mandatory for test_id in record.get("acceptance_test_ids", []) if isinstance(test_id, str)))
    required_tests = [record for record in acceptance_tests if record.get("id") in required_test_ids]
    gaps = _Gaps([record for record in handoff.get("open_gaps", []) if isinstance(record, dict)])
    if set(required_test_ids) != {str(record["id"]) for record in required_tests}:
        missing = sorted(set(required_test_ids) - {str(record["id"]) for record in required_tests})
        gaps.add("PLANNING_ACCEPTANCE_REFERENCE", f"Mandatory requirements reference acceptance tests that are not bundled: {', '.join(missing)}.", impact="Requirement coverage cannot be verified.", source_refs=missing)

    selected_prototype_ids = [str(value) for value in handoff.get("prototype_plan_ids", []) if isinstance(value, str)]
    prototype_by_id = {str(record.get("id")): record for record in prototype_records}
    missing_prototypes = sorted(set(selected_prototype_ids) - set(prototype_by_id))
    if missing_prototypes:
        gaps.add("PLANNING_PROTOTYPE_REFERENCE", f"Handoff prototype plans are not bundled: {', '.join(missing_prototypes)}.", impact="No trustworthy work package can be derived for the missing prototype plan.", source_refs=missing_prototypes)
    prototype_plans = [prototype_by_id[prototype_id] for prototype_id in selected_prototype_ids if prototype_id in prototype_by_id]
    source_records = [selected, *prototype_plans]
    trace = _trace(handoff["handoff_id"], selected_id, *requirement_ids, *selected_prototype_ids, *required_test_ids, *(gap["id"] for gap in gaps.items))

    rationale = _text(selected, "single_hypothesis_rationale", "rationale", "proposition")
    if rationale is None:
        rationale = "UNSPECIFIED"
        gaps.add("PLANNING_SELECTION_RATIONALE", "The selected hypothesis does not provide a rationale or proposition.", impact="Human review cannot confirm why the hypothesis was selected.", source_refs=[selected_id])
    selection_status = selection_input.get("status") if selection_input.get("status") in {"PROVISIONAL", "HUMAN_SELECTED", "REJECTED"} else "PROVISIONAL"
    selection_authority = selection_input.get("authority") if selection_input.get("authority") in {"AGENT", "HUMAN"} else ("HUMAN" if selection_status == "HUMAN_SELECTED" else "AGENT")
    selection_record = {
        "selection_id": "SL001", "handoff_ref": handoff_ref, "selected_hypothesis_id": selected_id,
        "status": selection_status, "authority": selection_authority,
        "human_approval_required": selection_input["human_approval_required"] if isinstance(selection_input.get("human_approval_required"), bool) else False,
        "rationale": rationale, "trace_refs": _trace(*trace),
    }

    assumptions: list[dict[str, Any]] = []
    for assumption in _list(handoff, "assumptions"):
        if isinstance(assumption, dict) and all(key in assumption for key in ("id", "statement", "validation_due", "status")):
            assumptions.append({**assumption, "trace_refs": _trace(*(assumption.get("trace_refs", [])), handoff["handoff_id"])})
        else:
            gaps.add("PLANNING_ASSUMPTION_RECORD", "An assumption record is not complete enough to enter the assumptions register.", impact="The assumption cannot be tracked to a validation boundary.", source_refs=[handoff["handoff_id"]])

    deliverables: list[dict[str, Any]] = []
    specifications: list[dict[str, Any]] = []
    deliverable_by_requirement: dict[str, str] = {}
    specification_by_requirement: dict[str, str] = {}
    test_by_id = {str(record["id"]): record for record in required_tests}
    for index, requirement in enumerate(mandatory, 1):
        requirement_id = str(requirement["id"])
        deliverable_id = _id("DL", index)
        spec_id = _id("TS", index)
        deliverable_by_requirement[requirement_id] = deliverable_id
        specification_by_requirement[requirement_id] = spec_id
        title = _text(requirement, "deliverable_title", "title") or _text(selected, "deliverable_title", "title")
        if title is None:
            title = f"Deliverable for {requirement_id}"
            gaps.add("PLANNING_DELIVERABLE_DERIVATION", f"No deliverable title is supplied for {requirement_id}; a neutral placeholder is used.", impact="A producer must define the deliverable before work can start.", source_refs=[requirement_id])
        kind = _text(requirement, "deliverable_type", "type", "medium", "media_type", "format") or _text(selected, "deliverable_type", "type", "medium", "media_type", "format")
        if kind is None:
            kind = "UNSPECIFIED"
            gaps.add("PLANNING_DELIVERABLE_TYPE", f"No deliverable type or medium is supplied for {requirement_id}.", impact="The output form is not determined by the handoff.", source_refs=[requirement_id, selected_id])
        prototype_owner = next((_text(prototype, "owner_capability", "executor_capability") for prototype in prototype_plans if _text(prototype, "owner_capability", "executor_capability")), None)
        owner = _text(requirement, "owner_capability", "executor_capability") or _text(selected, "owner_capability", "executor_capability") or prototype_owner
        if owner is None:
            owner = "UNSPECIFIED"
            gaps.add("PLANNING_OWNER_CAPABILITY", f"No owner capability is supplied for {requirement_id}.", impact="No execution owner can be assigned safely.", source_refs=[requirement_id, selected_id])
        test_ids = [test_id for test_id in required_test_ids if test_id in set(requirement.get("acceptance_test_ids", []))]
        deliverables.append({"id": deliverable_id, "title": title, "type": kind, "source_requirement_ids": [requirement_id], "technical_spec_ids": [spec_id], "acceptance_test_ids": test_ids, "owner_capability": owner, "due_milestone_id": "MS001", "status": "PLANNED", "trace_refs": _trace(*trace, requirement_id, deliverable_id, spec_id)})
        test = test_by_id.get(test_ids[0]) if test_ids else None
        parameter = _text(requirement, "parameter", "technical_parameter")
        if parameter is None:
            parameter = f"requirement/{requirement_id}"
            gaps.add("PLANNING_TECHNICAL_PARAMETER", f"No technical parameter is supplied for {requirement_id}.", impact="The measurable or reviewable target is not fully specified.", source_refs=[requirement_id])
        method = _text(test, "method", "measurement_method")
        if method is None:
            method = "UNSPECIFIED"
            gaps.add("PLANNING_MEASUREMENT_METHOD", f"No acceptance method is supplied for {requirement_id}.", impact="Acceptance evidence cannot be collected as specified.", source_refs=[requirement_id, *(test_ids or [])])
        statement = _text(requirement, "statement") or f"Requirement {requirement_id}"
        specifications.append({"id": spec_id, "deliverable_id": deliverable_id, "parameter": parameter, "target": {"kind": "QUALITATIVE", "statement": statement}, "tolerance": None, "measurement_method": method, "source_requirement_ids": [requirement_id], "status": "PROVISIONAL", "trace_refs": _trace(*trace, requirement_id, deliverable_id, spec_id, *(test_ids or []))})

    materials: list[dict[str, Any]] = []
    resources: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    work_packages: list[dict[str, Any]] = []
    approval_requirements: list[dict[str, Any]] = []
    material_by_source: dict[str, str] = {}
    resource_by_source: dict[str, str] = {}
    source_task_to_local: dict[str, str] = {}
    task_specs: list[tuple[dict[str, Any], dict[str, Any], str, str]] = []

    for wp_index, prototype in enumerate(prototype_plans, 1):
        prototype_id = str(prototype["id"])
        source_tasks = _source_task_records(prototype)
        valid_source_tasks: list[dict[str, Any]] = []
        for source_task in source_tasks:
            source_task_id = _text(source_task, "id")
            if source_task_id is None:
                gaps.add("PLANNING_TASK_ID", f"A task in {prototype_id} has no stable source ID and is excluded.", impact="Dependency and evidence references would not be deterministic.", source_refs=[prototype_id])
                continue
            effect = _normalise_effect(source_task.get("effect_type", source_task.get("effect")))
            if effect is None:
                gaps.add("PLANNING_EFFECT_TYPE", f"Task {source_task_id} in {prototype_id} has no recognized effect_type and is excluded.", impact="The runtime safety boundary cannot be determined.", source_refs=[prototype_id, source_task_id])
                continue
            upper_prohibited = " ".join(str(item).upper() for item in _list(handoff.get("constraints", {}), "prohibited_actions"))
            prohibited_keywords = {"PURCHASE": "PURCHASE", "CONTRACT": "CONTRACT", "PUBLICATION": "PUBLISH", "DELETION": "DELETE"}
            if effect in {"PURCHASE", "CONTRACT", "PUBLICATION", "DELETION"} and prohibited_keywords[effect] in upper_prohibited:
                gaps.add("PLANNING_PROHIBITED_EFFECT", f"Task {source_task_id} requests prohibited effect {effect} and is excluded.", impact="The prohibited action cannot enter an executable production plan.", source_refs=[prototype_id, source_task_id])
                continue
            valid_source_tasks.append(source_task)
        for task_index, source_task in enumerate(valid_source_tasks, 1):
            source_task_id = str(source_task["id"])
            local_id = _id("TK", len(tasks) + len(task_specs) + 1)
            source_task_to_local[source_task_id] = local_id
            task_specs.append((prototype, source_task, local_id, prototype_id))

    for prototype, source_task, local_id, prototype_id in task_specs:
        title = _text(source_task, "title", "name")
        duration = _quantity(source_task.get("duration", source_task.get("duration_estimate")))
        condition = _text(source_task, "acceptance_condition", "pass_condition")
        effect = _normalise_effect(source_task.get("effect_type", source_task.get("effect")))
        if title is None or duration is None or condition is None or effect is None:
            gaps.add("PLANNING_TASK_FIELDS", f"Task {source_task.get('id')} does not contain title, duration, acceptance_condition, and effect_type in structured form.", impact="The task cannot be safely executed or scheduled.", source_refs=[prototype_id, source_task.get("id")])
            source_task_to_local.pop(str(source_task["id"]), None)
            continue
        status = source_task.get("status") if source_task.get("status") in VALID_TASK_STATUSES else "BLOCKED"
        if source_task.get("status") not in VALID_TASK_STATUSES:
            gaps.add("PLANNING_TASK_STATUS", f"Task {source_task['id']} has no valid execution status and remains BLOCKED.", impact="The task cannot become READY without an explicit source status and gate review.", source_refs=[prototype_id, source_task["id"]])
        dependencies = [source_task_to_local[str(value)] for value in _list(source_task, "depends_on", "dependencies") if str(value) in source_task_to_local and str(value) != str(source_task["id"])]
        required_resource_ids: list[str] = []
        required_material_ids: list[str] = []
        for key, target, prefix, collection in (("required_resource_ids", required_resource_ids, "RS", resources), ("required_material_ids", required_material_ids, "MT", materials)):
            for value in _list(source_task, key):
                source_id = str(value)
                target_id = (resource_by_source if prefix == "RS" else material_by_source).get(source_id)
                if target_id is not None:
                    target.append(target_id)
        owner = _text(source_task, "owner_capability") or _text(prototype, "owner_capability", "executor_capability") or "UNSPECIFIED"
        if owner == "UNSPECIFIED":
            gaps.add("PLANNING_OWNER_CAPABILITY", f"Task {source_task['id']} has no owner capability.", impact="The task cannot be assigned for execution.", source_refs=[prototype_id, source_task["id"]])
        approval_ids: list[str] = []
        if effect in APPROVAL_EFFECTS:
            payload = {"action": effect, "target_ref": f"03_plan/task-plan.yaml#{local_id}", "source_refs": _trace(prototype_id, source_task["id"])}
            approval_id = _id("AR", len(approval_requirements) + 1)
            approval_requirements.append({"id": approval_id, "action": effect, "target_ref": payload["target_ref"], "target_sha256": canonical_sha256(payload), "authority": "HUMAN", "status": "REQUIRED", "reason": "The effect type is external or physical and requires explicit human approval before execution.", "task_ids": [local_id], "trace_refs": _trace(*trace, prototype_id, source_task["id"], approval_id)})
            approval_ids.append(approval_id)
            if status == "READY":
                status = "BLOCKED"
                gaps.add("PLANNING_EXTERNAL_READY", f"External-effect task {source_task['id']} was downgraded to BLOCKED until approval is recorded.", impact="The runtime must not execute the task before human approval.", source_refs=[prototype_id, source_task["id"], approval_id])
        task = {"id": local_id, "work_package_id": _id("WP", next(index for index, item in enumerate(prototype_plans, 1) if str(item["id"]) == prototype_id)), "title": title, "depends_on": dependencies, "required_resource_ids": sorted(set(required_resource_ids)), "required_material_ids": sorted(set(required_material_ids)), "acceptance_condition": condition, "effect_type": effect, "approval_requirement_ids": approval_ids, "duration": duration, "status": status, "trace_refs": _trace(*trace, prototype_id, source_task["id"], local_id, *approval_ids)}
        tasks.append(task)

    # Resource and material records are accepted only when their structured definition is present.
    for prototype, source_task, local_id, prototype_id in task_specs:
        for definition in _explicit_records(source_task, "resources", "required_resources") + _explicit_records(prototype, "resources", "resource_requirements"):
            source_id = _text(definition, "id")
            quantity = _quantity(definition.get("quantity"))
            resource_type = _text(definition, "type")
            capability = _text(definition, "capability", "name")
            if source_id and quantity and resource_type in VALID_RESOURCE_TYPES and capability:
                resource_id = resource_by_source.setdefault(source_id, _id("RS", len(resources) + 1))
                availability = definition.get("availability") if definition.get("availability") in VALID_AVAILABILITY else "UNKNOWN"
                if not any(item["id"] == resource_id for item in resources):
                    resources.append({"id": resource_id, "type": resource_type, "capability": capability, "quantity": quantity, "availability": availability, "source_task_ids": [local_id], "trace_refs": _trace(*trace, prototype_id, source_id, local_id)})
                if availability != "AVAILABLE":
                    gaps.add("PLANNING_RESOURCE_AVAILABILITY", f"Resource {source_id} availability is {availability}.", impact="The resource cannot be confirmed for execution.", source_refs=[prototype_id, source_id], blocking=True, resolution_condition="Record an AVAILABLE resource or an authorized replacement in a new plan revision.")
            elif source_id:
                gaps.add("PLANNING_RESOURCE_FIELDS", f"Resource {source_id} lacks a valid type, capability, or quantity and is omitted.", impact="The task resource requirement cannot be confirmed.", source_refs=[prototype_id, source_id])
        for definition in _explicit_records(source_task, "materials", "required_materials") + _explicit_records(prototype, "materials", "material_requirements"):
            source_id = _text(definition, "id")
            quantity = _quantity(definition.get("quantity"))
            name = _text(definition, "name", "title")
            specification = _text(definition, "specification", "spec")
            rights = definition.get("rights_status") if definition.get("rights_status") in VALID_RIGHTS else None
            safety = definition.get("safety_status") if definition.get("safety_status") in VALID_SAFETY else None
            if source_id and quantity and name and specification and rights and safety:
                material_id = material_by_source.setdefault(source_id, _id("MT", len(materials) + 1))
                material_status = definition.get("status") if definition.get("status") in {"CANDIDATE", "APPROVED", "REJECTED"} else "CANDIDATE"
                if not any(item["id"] == material_id for item in materials):
                    materials.append({"id": material_id, "name": name, "specification": specification, "quantity": quantity, "rights_status": rights, "safety_status": safety, "source_prototype_plan_ids": [prototype_id], "status": material_status, "trace_refs": _trace(*trace, prototype_id, source_id, material_id)})
                if rights != "CLEAR" and rights != "PROJECT_INTERNAL" or safety != "CLEAR" or material_status != "APPROVED":
                    gaps.add("PLANNING_MATERIAL_STATUS", f"Material {source_id} is not rights, safety, and approval complete.", impact="The material cannot be adopted safely for production.", source_refs=[prototype_id, source_id], blocking=True, category="RIGHTS" if rights not in {"CLEAR", "PROJECT_INTERNAL"} else "SAFETY", resolution_condition="Record clear rights, clear safety, and APPROVED material status in a new plan revision.")
            elif source_id:
                gaps.add("PLANNING_MATERIAL_FIELDS", f"Material {source_id} lacks complete quantity/specification/rights/safety fields and is omitted.", impact="The material cannot be adopted safely.", source_refs=[prototype_id, source_id])

    # A second pass resolves references after explicit records have been collected.
    for task in tasks:
        source_task_id = next((ref for ref in task["trace_refs"] if ref.startswith("PT")), None)
        source_task = next((item[1] for item in task_specs if item[2] == task["id"]), {})
        task["required_resource_ids"] = [resource_by_source[value] for value in _list(source_task, "required_resource_ids") if value in resource_by_source]
        task["required_material_ids"] = [material_by_source[value] for value in _list(source_task, "required_material_ids") if value in material_by_source]
        for resource in resources:
            if task["id"] in resource["source_task_ids"]:
                continue

        for source_id in _list(source_task, "required_resource_ids"):
            if str(source_id) not in resource_by_source:
                gaps.add("PLANNING_REFERENCE", f"Task {source_task['id']} references missing resource definition {source_id} and the reference is omitted.", impact="The task resource requirement is incomplete.", source_refs=[source_task.get("id"), source_id])
        for source_id in _list(source_task, "required_material_ids"):
            if str(source_id) not in material_by_source:
                gaps.add("PLANNING_REFERENCE", f"Task {source_task['id']} references missing material definition {source_id} and the reference is omitted.", impact="The task material requirement is incomplete.", source_refs=[source_task.get("id"), source_id])

    for wp_index, prototype in enumerate(prototype_plans, 1):
        prototype_id = str(prototype["id"])
        wp_task_ids = [task["id"] for task in tasks if prototype_id in task["trace_refs"]]
        explicit_deliverable_ids = [value for value in _list(prototype, "deliverable_ids") if value in {item["id"] for item in deliverables}]
        explicit_requirement_ids = [str(value) for value in _list(prototype, "requirement_ids") if str(value) in deliverable_by_requirement]
        deliverable_ids = sorted(set(explicit_deliverable_ids + [deliverable_by_requirement[value] for value in explicit_requirement_ids]))
        if not deliverable_ids and wp_task_ids:
            gaps.add("PLANNING_WORK_PACKAGE_SCOPE", f"Prototype plan {prototype_id} does not identify a deliverable or requirement.", impact="Task output cannot be traced to a deliverable.", source_refs=[prototype_id])
        owner = _text(prototype, "owner_capability", "executor_capability") or "UNSPECIFIED"
        if owner == "UNSPECIFIED":
            gaps.add("PLANNING_OWNER_CAPABILITY", f"Prototype plan {prototype_id} has no owner capability.", impact="The work package cannot be assigned.", source_refs=[prototype_id])
        work_packages.append({"id": _id("WP", wp_index), "title": _text(prototype, "title", "name") or f"Work package for {prototype_id}", "deliverable_ids": deliverable_ids, "input_ids": [str(value) for value in _list(prototype, "input_ids")], "output_ids": [str(value) for value in _list(prototype, "output_ids")], "depends_on": [], "owner_capability": owner, "review_gate_id": None, "task_ids": wp_task_ids, "status": "BLOCKED" if not wp_task_ids or any(task["status"] == "BLOCKED" for task in tasks if task["id"] in wp_task_ids) else "PLANNED", "trace_refs": _trace(*trace, prototype_id, _id("WP", wp_index), *wp_task_ids)})

    if not prototype_plans:
        gaps.add("PLANNING_PROTOTYPE_UNDERIVABLE", "The accepted handoff contains no prototype plan records; no physical work package or task is generated.", impact="The plan remains non-startable until a prototype plan is accepted.", source_refs=[handoff["handoff_id"], selected_id])
    elif not tasks:
        gaps.add("PLANNING_TASKS_UNDERIVABLE", "The accepted prototype plans contain no executable task with complete structured fields.", impact="No task can become READY.", source_refs=selected_prototype_ids)

    critical_path: list[str] = []
    order = _topological_order([task["id"] for task in tasks], tasks)
    if len(order) == len(tasks) and tasks:
        distances: dict[str, tuple[int, list[str]]] = {}
        by_id = {task["id"]: task for task in tasks}
        for task_id in order:
            task = by_id[task_id]
            duration = int(task["duration"]["value"]) if str(task["duration"]["value"]).isdigit() else 0
            predecessors = [distances[dependency] for dependency in task["depends_on"] if dependency in distances]
            best = max(predecessors, key=lambda item: (item[0], item[1]), default=(0, []))
            distances[task_id] = (best[0] + duration, [*best[1], task_id])
        critical_path = max(distances.values(), key=lambda item: (item[0], item[1]))[1]

    budget_input = next((value for value in [handoff.get("budget"), selected.get("budget"), *[plan.get("budget") for plan in prototype_plans]] if isinstance(value, dict)), None)
    raw_currency = budget_input.get("currency") if isinstance(budget_input, dict) else None
    currency = raw_currency if isinstance(raw_currency, str) and len(raw_currency) == 3 and raw_currency.isupper() else None
    budget_items: list[dict[str, Any]] = []
    for item in (budget_input.get("items", []) if isinstance(budget_input, dict) and isinstance(budget_input.get("items"), list) else []):
        if not isinstance(item, dict):
            continue
        item_currency = currency or (item.get("amount", {}).get("currency") if isinstance(item.get("amount"), dict) else None)
        amount = _money(item.get("amount"), item_currency)
        if currency is None and isinstance(item_currency, str) and len(item_currency) == 3 and item_currency.isupper():
            currency = item_currency
        if not all(isinstance(item.get(key), str) and item.get(key) for key in ("id", "category", "description", "basis")) or item.get("confidence") not in {"LOW", "MEDIUM", "HIGH"} or item.get("status") not in {"ESTIMATED", "QUOTED", "RESERVED", "COMMITTED", "ACTUAL"} or (item.get("amount") is not None and amount is None):
            gaps.add("PLANNING_BUDGET_ITEM", "A supplied budget item is incomplete or uses an unsupported amount shape and is omitted.", impact="The budget baseline cannot include the malformed item.", source_refs=[handoff["handoff_id"]])
            continue
        budget_items.append({"id": item["id"], "category": item["category"], "description": item["description"], "amount": amount, "basis": item["basis"], "confidence": item["confidence"], "status": item["status"], "trace_refs": _trace(*trace, item["id"])})
    baseline_total = _money(budget_input.get("baseline_total"), currency) if isinstance(budget_input, dict) else None
    contingency = _money(budget_input.get("contingency"), currency) if isinstance(budget_input, dict) else None
    approval_threshold = _money(budget_input.get("approval_threshold"), currency) if isinstance(budget_input, dict) else None
    if budget_input is None:
        gaps.add("PLANNING_BUDGET_UNDERIVABLE", "The accepted handoff supplies no budget currency or monetary baseline.", impact="No spending authorization or variance baseline can be calculated.", blocking=False, source_refs=[handoff["handoff_id"]])
    budget = {"budget_id": "BDG001", "currency": currency, "baseline_total": baseline_total, "contingency": contingency, "approval_threshold": approval_threshold, "items": budget_items, "gaps": [gap["statement"] for gap in gaps.items if gap["rule"] in {"PLANNING_BUDGET_UNDERIVABLE", "PLANNING_BUDGET_ITEM"}], "status": "ESTIMATED", "trace_refs": _trace(*trace, "BDG001")}

    milestones = [{"id": "MS001", "title": "Planning baseline", "sequence": 1, "depends_on": [], "status": "BLOCKED" if any(gap["blocking"] for gap in gaps.items) else "PLANNED", "trace_refs": _trace(*trace, "MS001")}]
    if not isinstance(handoff.get("schedule"), dict):
        gaps.add("PLANNING_SCHEDULE_UNDERIVABLE", "Calendar dates are not supplied by the accepted handoff.", impact="The schedule baseline cannot be verified against a calendar.", blocking=False, source_refs=[handoff["handoff_id"]], resolution_condition="Record the accepted calendar schedule in a new handoff or plan revision.")
    schedule_gap_statements = [gap["statement"] for gap in gaps.items if gap["rule"] == "PLANNING_SCHEDULE_UNDERIVABLE"]
    schedule = {"schedule_id": "SCH001", "mode": "CALENDAR" if isinstance(handoff.get("schedule"), dict) and handoff["schedule"].get("mode") == "CALENDAR" else "RELATIVE", "baseline_status": "PROVISIONAL", "milestones": milestones, "task_schedule": [{"task_id": task["id"], "duration": task["duration"], "start_at": None, "due_at": None} for task in tasks], "critical_path_task_ids": critical_path, "gaps": schedule_gap_statements, "trace_refs": _trace(*trace, "SCH001")}

    risks: list[dict[str, Any]] = []
    for index, gap in enumerate(gaps.items, 1):
        if gap["id"] not in {str(item.get("id")) for item in handoff.get("open_gaps", []) if isinstance(item, dict)}:
            continue
        risks.append({"id": _id("RK", index), "title": gap["statement"], "source_handoff_gap_ids": [gap["id"]], "source_requirement_ids": requirement_ids, "severity": "MAJOR" if gap["blocking"] else "MEDIUM", "likelihood": "UNKNOWN", "impact": gap["impact"], "mitigation": gap["resolution_condition"], "owner_capability": gap["owner"], "status": "OPEN", "blocking": gap["blocking"], "trace_refs": _trace(*trace, gap["id"], _id("RK", index))})

    coverage_items: list[dict[str, Any]] = []
    work_package_by_requirement: dict[str, tuple[list[str], list[str]]] = defaultdict(lambda: ([], []))
    for work_package in work_packages:
        for deliverable_id in work_package["deliverable_ids"]:
            deliverable = next(item for item in deliverables if item["id"] == deliverable_id)
            for requirement_id in deliverable["source_requirement_ids"]:
                work_package_by_requirement[requirement_id] = (work_package_by_requirement[requirement_id][0] + [work_package["id"]], work_package_by_requirement[requirement_id][1] + work_package["task_ids"])
    for requirement_id in requirement_ids:
        work_package_ids, task_ids = work_package_by_requirement[requirement_id]
        test_ids = [test_id for test_id in required_test_ids if test_id in set(requirement_by_id[requirement_id].get("acceptance_test_ids", []))]
        complete = bool(deliverable_by_requirement.get(requirement_id) and specification_by_requirement.get(requirement_id) and test_ids and work_package_ids and task_ids)
        coverage_items.append({"requirement_id": requirement_id, "deliverable_ids": [deliverable_by_requirement[requirement_id]], "technical_spec_ids": [specification_by_requirement[requirement_id]], "acceptance_test_ids": test_ids, "work_package_ids": sorted(set(work_package_ids)), "task_ids": sorted(set(task_ids)), "status": "COVERED" if complete else "UNCOVERED"})
    covered_count = sum(item["status"] == "COVERED" for item in coverage_items)
    coverage_report = {"report_id": "CV001", "requirements": coverage_items, "coverage_percent": int(covered_count * 100 / len(coverage_items)) if coverage_items else 0, "uncovered_requirement_ids": [item["requirement_id"] for item in coverage_items if item["status"] != "COVERED"], "trace_refs": _trace(*trace, "CV001")}
    if coverage_report["uncovered_requirement_ids"]:
        gaps.add("PLANNING_COVERAGE", "Mandatory requirements are not connected to a complete deliverable, specification, acceptance test, work package, and task chain.", impact="The plan cannot become startable until requirement coverage reaches 100%.", source_refs=coverage_report["uncovered_requirement_ids"])

    task_edges = [{"from": dependency, "to": task["id"]} for task in tasks for dependency in task.get("depends_on", [])]
    graph = {"graph_id": "DG001", "nodes": [task["id"] for task in tasks], "edges": task_edges, "topological_order": order, "trace_refs": _trace(*trace, "DG001")}
    approval_register = {"register_id": "AGR001", "requirements": approval_requirements, "approvals": [], "trace_refs": _trace(*trace, "AGR001")}
    open_gap_ids = [gap["id"] for gap in gaps.items]
    scope_baseline = {"baseline_id": "SB001", "handoff_ref": handoff_ref, "selection_id": "SL001", "selected_hypothesis_id": selected_id, "mandatory_requirement_ids": requirement_ids, "prototype_plan_ids": selected_prototype_ids, "excluded_scope": [str(value) for value in _list(handoff.get("constraints", {}), "prohibited_actions")], "assumption_ids": [item["id"] for item in assumptions], "open_gap_ids": open_gap_ids, "status": "BASELINED" if selection_status == "HUMAN_SELECTED" else "PROVISIONAL", "trace_refs": _trace(*trace, *(item["id"] for item in assumptions))}
    project_manifest = _require_mapping(project_root / "manifest.yaml")
    plan = {"schema_version": "1.0.0", "plan_id": "PL001", "plan_revision": 1, "project_id": str(project_manifest["project_id"]), "state": "PLANNING", "generated_at": handoff["generated_at"], "handoff_ref": handoff_ref, "mandatory_requirement_ids": requirement_ids, "acceptance_test_ids": required_test_ids, "acceptance_tests": required_tests, "selection_record": selection_record, "scope_baseline": scope_baseline, "assumptions": assumptions, "deliverables": deliverables, "technical_specifications": specifications, "materials": materials, "resources": resources, "work_packages": work_packages, "tasks": tasks, "schedule": schedule, "budget": budget, "risks": risks, "approval_register": approval_register, "coverage_report": coverage_report, "dependency_graph": graph, "critical_path_task_ids": critical_path, "gaps": gaps.items, "determinism": {"algorithm": "production-plan-v1", "source_input_sha256": canonical_sha256(source_input)}}
    plan["integrity"] = {"content_sha256": canonical_sha256(plan)}
    return plan
