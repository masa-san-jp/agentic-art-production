#!/usr/bin/env python3
"""Build a deterministic production plan from an accepted handoff project."""

from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation
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
from tools.lib.visual_package import build_visual_package, visual_asset_bytes
from tools.lib.yaml_io import dump_yaml, load_json, load_yaml


ARTIFACT_FILES = {
    "requirements": "production-requirements.yaml",
    "hypotheses": "production-hypotheses.yaml",
    "prototype_plans": "prototype-plans.yaml",
    "acceptance_tests": "acceptance-tests.yaml",
    "source_refs": "source-ref-index.yaml",
    "production_brief": "production-brief.yaml",
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
    # Research #49 may provide a typed visual-language handoff. It is an
    # optional additive artifact so existing accepted handoffs remain
    # consumable; when present, its words are displayed and never re-decided
    # by Production.
    visual_language_path = bundle_root / "artifacts/visual-language.yaml"
    if visual_language_path.is_file():
        artifacts["visual_language"] = _require_mapping(visual_language_path)
    source_input = {"handoff": handoff, "bundle_manifest": bundle_manifest, "artifacts": artifacts}
    return handoff, bundle_manifest, source_input, artifacts


def _trace(*values: str) -> list[str]:
    return list(dict.fromkeys(values))


def _reference_access(project_root: Path, handoff: dict[str, Any], artifacts: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize production-relevant source references and derive missing-URL gaps."""
    path = project_root / "00_handoff/source-bundle/artifacts/source-ref-index.yaml"
    references = _records(artifacts["source_refs"], "references", path)
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
    by_id = {str(record.get("id")): record for record in references if record.get("id")}
    normalized: list[dict[str, Any]] = []
    available_categories: set[str] = set()
    for source_id in referenced_ids:
        record = by_id.get(source_id)
        if record is None:
            raise DiagnosticError(_finding("PLANNING_REFERENCE_MISSING", f"source-ref index does not contain referenced ID {source_id}", file=path, location="/references", remediation="Regenerate the handoff with every referenced decision, insight, and evidence record."))
        categories = record.get("reference_categories", [])
        if not isinstance(categories, list) or not all(isinstance(value, str) for value in categories):
            raise DiagnosticError(_finding("PLANNING_REFERENCE_CATEGORY", "reference_categories must be a list of category IDs", file=path, location=f"/references/{source_id}/reference_categories", remediation="Use category IDs declared in config/reference-policy.yaml."))
        unknown = sorted(set(categories) - set(category_by_id))
        if unknown:
            raise DiagnosticError(_finding("PLANNING_REFERENCE_CATEGORY", f"unknown reference categories: {', '.join(unknown)}", file=path, location=f"/references/{source_id}/reference_categories", remediation="Use category IDs declared in config/reference-policy.yaml."))
        record_hash = record.get("record_hash")
        if not isinstance(record_hash, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", record_hash) is None or set(record_hash[7:]) == {"0"}:
            raise DiagnosticError(_finding("PLANNING_REFERENCE_HASH", "record_hash must be a non-zero canonical SHA-256 value", file=path, location=f"/references/{source_id}/record_hash", remediation="Regenerate the handoff from Research so the source reference hash is present and computed from the canonical record."))
        access_url = record.get("access_url")
        if access_url is not None:
            if not isinstance(access_url, str) or not access_url or any(character.isspace() for character in access_url):
                raise DiagnosticError(_finding("PLANNING_REFERENCE_URL", "access_url must be a non-empty URL without whitespace", file=path, location=f"/references/{source_id}/access_url", remediation="Provide a stable permanent HTTPS URL without credentials or signed parameters."))
            uri_finding = validate_asset_uri(
                access_url,
                allowed_schemes=policy.get("allowed_uri_schemes", ["https"]),
                allow_query=bool(policy.get("https", {}).get("allow_query", False)),
            )
            parsed = urlsplit(access_url)
            if uri_finding is not None or not parsed.hostname:
                reason = uri_finding.reason if uri_finding is not None else "HTTPS reference URL must contain a hostname"
                raise DiagnosticError(_finding("PLANNING_REFERENCE_URL", reason, file=path, location=f"/references/{source_id}/access_url", remediation="Provide a stable permanent HTTPS URL without credentials, query parameters, or fragments."))
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


VIEWER_ASSESSMENT_STATUSES_REQUIRING_REVIEW = {"UNKNOWN", "CONTRADICTED", "EXTERNALLY_SUPPORTED"}


def _load_viewer_assessments(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    if not path.is_file():
        raise DiagnosticError(_finding(
            "PLANNING_VIEWER_ASSESSMENT_MISSING",
            "viewer assessment input was requested but not found",
            file=path,
            remediation="Provide the committed viewer-response-assessment/v1 JSON before rebuilding the plan.",
        ))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiagnosticError(_finding(
            "PLANNING_VIEWER_ASSESSMENT_INPUT",
            f"viewer assessment JSON could not be read: {exc}",
            file=path,
            remediation="Provide one schema-valid assessment object or an assessments array.",
        )) from exc
    records = value.get("assessments") if isinstance(value, dict) and isinstance(value.get("assessments"), list) else [value]
    if not records or not all(isinstance(record, dict) for record in records):
        raise DiagnosticError(_finding(
            "PLANNING_VIEWER_ASSESSMENT_INPUT",
            "viewer assessment input must contain assessment objects",
            file=path,
            remediation="Export only validated viewer-response-assessment/v1 records.",
        ))
    allowed = {
        "schema_id", "assessment_id", "work_id", "requirement_id", "presentation_mode",
        "matching_tags", "status", "measured_sample_size", "outcome_counts",
        "confidence_interval", "source_record_ids", "external_evidence_refs", "conflict",
        "review_required", "review_kind", "source_commits",
    }
    for index, record in enumerate(records):
        if set(record) != allowed or record.get("schema_id") != "viewer-response-assessment/v1" or record.get("status") not in {"UNKNOWN", "SUPPORTED", "CONTRADICTED", "EXTERNALLY_SUPPORTED"}:
            raise DiagnosticError(_finding(
                "PLANNING_VIEWER_ASSESSMENT_SCHEMA",
                f"assessment {index} is not a closed viewer-response-assessment/v1 record",
                file=path,
                location=f"/assessments/{index}",
                remediation="Run the parent viewer response gate and use its exact output.",
            ))
    return sorted(records, key=lambda record: str(record.get("assessment_id", "")))




def _build_plan(project_root: Path, viewer_assessment_path: Path | None = None) -> dict[str, Any]:
    return _build_plan_from_handoff(project_root, viewer_assessment_path)


def _plan_text(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())
    return fallback


_BRIEF_FIELD_LABELS = {
    ("research_summary", "questions"): "調査の要約のquestions（立てた必須質問）",
    ("research_summary", "what_was_read"): "調査の要約のwhat_was_read（読んだ資料の範囲と量）",
    ("research_summary", "what_came_out"): "調査の要約のwhat_came_out（そこから出た結論）",
    ("research_summary", "what_is_not_settled"): "調査の要約のwhat_is_not_settled（まだ確かめていないこと）",
    ("completion_image", "encounter"): "完成像のencounter（鑑賞者の経験順）",
    ("completion_image", "position"): "完成像のposition（立つ位置と距離）",
    ("completion_image", "first_seconds"): "完成像のfirst_seconds",
    ("completion_image", "after_30s"): "完成像のafter_30s",
    ("completion_image", "after_3min"): "完成像のafter_3min",
    ("theme", "field"): "テーマのfield",
    ("theme", "stands_against"): "テーマのstands_against",
    ("theme", "difference"): "テーマのdifference",
    ("theme", "why_now"): "テーマのwhy_now",
    ("message", "claim"): "メッセージのclaim",
    ("message", "who_disagrees"): "メッセージのwho_disagrees（反対しうる相手）",
    ("message", "denies"): "メッセージのdenies",
    ("message", "shown_not_told"): "メッセージのshown_not_told",
    ("concept", "mechanism"): "コンセプトのmechanism",
    ("concept", "without_the_technique"): "コンセプトのwithout_the_technique",
    ("concept", "precedents"): "コンセプトのprecedents（先行作品）",
    ("concept", "self_repetition_risk"): "コンセプトのself_repetition_risk",
}

_BRIEF_MIN_LENGTHS = {
    ("theme", "stands_against"): 10,
    ("theme", "difference"): 20,
    ("theme", "why_now"): 10,
    ("message", "claim"): 15,
    ("message", "who_disagrees"): 3,
    ("message", "denies"): 10,
    ("message", "shown_not_told"): 15,
    ("concept", "mechanism"): 30,
}


def _brief_field(brief: dict[str, Any], section: str, field: str) -> Any:
    container = brief.get(section)
    return container.get(field) if isinstance(container, dict) else None


def _production_brief_gaps(brief: dict[str, Any]) -> list[tuple[str, bool]]:
    gaps: list[tuple[str, bool]] = []
    for (section, field), label in _BRIEF_FIELD_LABELS.items():
        value = _brief_field(brief, section, field)
        missing = value is None or (isinstance(value, str) and not value.strip())
        if field == "encounter":
            missing = not isinstance(value, list) or len(value) < 3
        if field == "precedents":
            if not isinstance(value, list) or not value:
                gaps.append((f"{label}が未記載です。先行作品を1件以上調査し、同じ操作との差分を記録してください。", True))
            continue
        if missing:
            gaps.append((f"{label}が未記載です。制作判断に必要な内容を記入してください。", True))
            continue
        minimum = _BRIEF_MIN_LENGTHS.get((section, field))
        if minimum is not None and isinstance(value, str) and len(value.strip()) < minimum:
            gaps.append((f"{label}が短すぎます（最低{minimum}文字）。一行の印象語ではなく、判断根拠を記述してください。", True))
    who_disagrees = _brief_field(brief, "message", "who_disagrees")
    if isinstance(who_disagrees, str) and " ".join(who_disagrees.split()).casefold() in {"なし", "特にいない", "誰も反対しない", "none", "no one", "nobody"}:
        gaps.append(("メッセージの反対しうる相手が実質的に空です。誰も反対しない文は主張になっていないため、相手となる立場を記入してください。", True))
    without_technique = _brief_field(brief, "concept", "without_the_technique")
    if without_technique == "MERELY_PLAINER":
        gaps.append(("コンセプトのwithout_the_techniqueがMERELY_PLAINERです。技術を外しても成立するため、技術実験であることをblocking gapとして記録します。", True))
    return gaps


def _duration_for_band(band: Any) -> dict[str, str]:
    return {
        "HOURS": {"value": "1", "unit": "h"},
        "DAYS": {"value": "8", "unit": "h"},
        "WEEKS": {"value": "40", "unit": "h"},
        "MONTHS": {"value": "160", "unit": "h"},
        "UNKNOWN": {"value": "1", "unit": "h"},
    }.get(str(band), {"value": "1", "unit": "h"})


_KNOWN_DURATION_BANDS = {"HOURS", "DAYS", "WEEKS", "MONTHS"}


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


def _duration_minutes(duration: dict[str, str]) -> Decimal:
    try:
        value = Decimal(str(duration["value"]))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise DiagnosticError(_finding(
            "PLANNING_DURATION",
            "task duration must contain a decimal value",
            file="03_plan/production-plan.yaml",
            location="/schedule/task_schedule/duration",
            remediation="Regenerate the plan with a valid min or h duration.",
        )) from exc
    unit = duration.get("unit")
    if unit == "h":
        return value * 60
    if unit == "min":
        return value
    raise DiagnosticError(_finding(
        "PLANNING_DURATION",
        f"unsupported task duration unit {unit!r}",
        file="03_plan/production-plan.yaml",
        location="/schedule/task_schedule/duration/unit",
        remediation="Use min or h for task durations.",
    ))


def _critical_path_task_ids(
    task_ids: list[str],
    edges: list[dict[str, str]],
    durations: dict[str, dict[str, str]],
) -> list[str]:
    """Return one deterministic maximum-duration path through a task DAG."""
    order = _topological_order(task_ids, edges)
    if len(order) != len(task_ids):
        return []
    predecessors: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
    for edge in edges:
        predecessors[edge["to"]].append(edge["from"])
    best_duration: dict[str, Decimal] = {}
    best_path: dict[str, tuple[str, ...]] = {}
    for task_id in order:
        own_duration = _duration_minutes(durations[task_id])
        candidates: list[tuple[Decimal, tuple[str, ...]]] = [(own_duration, (task_id,))]
        for predecessor in sorted(predecessors[task_id]):
            candidates.append((
                best_duration[predecessor] + own_duration,
                best_path[predecessor] + (task_id,),
            ))
        best_duration[task_id], best_path[task_id] = max(candidates, key=lambda candidate: candidate[0])
        tied = [candidate for candidate in candidates if candidate[0] == best_duration[task_id]]
        best_duration[task_id], best_path[task_id] = min(tied, key=lambda candidate: candidate[1])
    longest_duration = max(best_duration.values())
    longest = [path for task_id, path in best_path.items() if best_duration[task_id] == longest_duration]
    return list(min(longest))


def _build_plan_from_handoff(project_root: Path, viewer_assessment_path: Path | None = None) -> dict[str, Any]:
    """Build a plan from handoff records without inventing production facts."""
    handoff, _bundle_manifest, source_input, artifacts = _load_inputs(project_root)
    viewer_assessments = _load_viewer_assessments(viewer_assessment_path)
    source_input["viewer_response_assessments"] = viewer_assessments
    production_brief = artifacts["production_brief"]
    brief_gaps = _production_brief_gaps(production_brief)
    requirements = _records(
        artifacts["requirements"],
        "requirements",
        project_root / "00_handoff/source-bundle/artifacts/production-requirements.yaml",
    )
    hypotheses = _records(
        artifacts["hypotheses"],
        "hypotheses",
        project_root / "00_handoff/source-bundle/artifacts/production-hypotheses.yaml",
    )
    prototype_plans = _records(
        artifacts["prototype_plans"],
        "prototype_plans",
        project_root / "00_handoff/source-bundle/artifacts/prototype-plans.yaml",
    )
    acceptance_tests = _records(
        artifacts["acceptance_tests"],
        "acceptance_tests",
        project_root / "00_handoff/source-bundle/artifacts/acceptance-tests.yaml",
    )
    selected_hypothesis_id = str((handoff.get("selection") or {}).get("selected_hypothesis_id"))
    selected_hypothesis = next(
        (item for item in hypotheses if str(item.get("id")) == selected_hypothesis_id),
        None,
    )
    if selected_hypothesis is None:
        raise DiagnosticError(_finding(
            "PLANNING_SELECTION_REFERENCE",
            "handoff selection does not reference a bundled hypothesis",
            file=project_root / "00_handoff/production-handoff.yaml",
            location="/selection/selected_hypothesis_id",
            remediation="Re-export the handoff with the selected hypothesis snapshot.",
        ))
    if not isinstance(handoff.get("generated_at"), str):
        raise DiagnosticError(_finding(
            "PLANNING_TIMESTAMP",
            "handoff generated_at is required for deterministic planning",
            file=project_root / "00_handoff/production-handoff.yaml",
            location="/generated_at",
            remediation="Provide an RFC 3339 generated_at value in the handoff.",
        ))

    mandatory_requirements = [
        item for item in requirements if item.get("priority") == "mandatory"
    ]
    requirement_ids = [str(item["id"]) for item in mandatory_requirements]
    acceptance_by_id = {str(item["id"]): item for item in acceptance_tests}
    declared_tests_by_requirement: dict[str, list[str]] = {
        requirement_id: [] for requirement_id in requirement_ids
    }
    tests_by_requirement: dict[str, list[dict[str, Any]]] = {
        requirement_id: [] for requirement_id in requirement_ids
    }
    for requirement in mandatory_requirements:
        requirement_id = str(requirement["id"])
        for test_id in requirement.get("acceptance_test_ids", []):
            test_id = str(test_id)
            if test_id not in declared_tests_by_requirement[requirement_id]:
                declared_tests_by_requirement[requirement_id].append(test_id)
            test = acceptance_by_id.get(test_id)
            if test is not None and test not in tests_by_requirement[requirement_id]:
                tests_by_requirement[requirement_id].append(test)
    for test in acceptance_tests:
        target_requirement = str(test.get("target_requirement", ""))
        if target_requirement in tests_by_requirement and test not in tests_by_requirement[target_requirement]:
            tests_by_requirement[target_requirement].append(test)
            test_id = str(test["id"])
            if test_id not in declared_tests_by_requirement[target_requirement]:
                declared_tests_by_requirement[target_requirement].append(test_id)

    reference_access, reference_gaps = _reference_access(project_root, handoff, artifacts)
    handoff_ref = {
        "id": handoff["handoff_id"],
        "revision": handoff["revision"],
        "content_sha256": handoff["integrity"]["content_sha256"],
    }
    acceptance_test_ids = [str(item["id"]) for item in acceptance_tests]
    prototype_plan_ids = [str(item["id"]) for item in prototype_plans]
    handoff_gaps = [
        gap for gap in handoff.get("open_gaps", [])
        if isinstance(gap, dict) and gap.get("id")
    ]
    handoff_gap_ids = [str(gap["id"]) for gap in handoff_gaps]
    trace = _trace(
        handoff["handoff_id"],
        selected_hypothesis_id,
        *requirement_ids,
        *acceptance_test_ids,
        *prototype_plan_ids,
        *handoff_gap_ids,
    )

    selection_status = (
        "HUMAN_SELECTED"
        if (handoff.get("selection") or {}).get("status") == "HUMAN_SELECTED"
        else "PROVISIONAL"
    )
    selection_record = {
        "selection_id": "SL001",
        "handoff_ref": handoff_ref,
        "selected_hypothesis_id": selected_hypothesis_id,
        "status": selection_status,
        "authority": "HUMAN" if selection_status == "HUMAN_SELECTED" else "AGENT",
        "human_approval_required": bool((handoff.get("selection") or {}).get("human_approval_required", True)),
        "rationale": _plan_text(
            selected_hypothesis.get("single_hypothesis_rationale")
            or selected_hypothesis.get("proposition"),
            "Selected from the accepted handoff.",
        ),
        "trace_refs": _trace(*trace),
    }

    uncertainties = selected_hypothesis.get("uncertainties", [])
    if not isinstance(uncertainties, list):
        uncertainties = []
    assumptions = [
        {
            "id": f"AS{index:03d}",
            "statement": _plan_text(item.get("statement"), f"Uncertainty {item.get('id', index)} requires validation."),
            "validation_due": "BEFORE_PROTOTYPE",
            "status": "OPEN",
            "trace_refs": _trace(*trace, str(item.get("id", f"U{index:03d}"))),
        }
        for index, item in enumerate(uncertainties, start=1)
        if isinstance(item, dict) and item.get("external_validation_reason") is not None
    ]
    owner_capabilities = [
        _plan_text(item.get("executor_capability"), "production-owner-confirmation-required")
        for item in prototype_plans
    ]
    for test in acceptance_tests:
        capability = _plan_text(test.get("executor"), _plan_text(test.get("method"), "production-owner-confirmation-required"))
        if capability not in owner_capabilities:
            owner_capabilities.append(capability)
    if not owner_capabilities:
        owner_capabilities.append("production-owner-confirmation-required")
    owner_capability = owner_capabilities[0]

    scope_baseline = {
        "baseline_id": "SB001",
        "handoff_ref": handoff_ref,
        "selection_id": "SL001",
        "selected_hypothesis_id": selected_hypothesis_id,
        "mandatory_requirement_ids": requirement_ids,
        "prototype_plan_ids": prototype_plan_ids,
        "excluded_scope": list((handoff.get("constraints") or {}).get("prohibited_actions", [])),
        "assumption_ids": [item["id"] for item in assumptions],
        "open_gap_ids": handoff_gap_ids,
        "status": "BASELINED" if selection_status == "HUMAN_SELECTED" else "PROVISIONAL",
        "trace_refs": _trace(*trace, *(item["id"] for item in assumptions)),
    }

    # The handoff's prototype input list is intentionally not promoted to a
    # material/resource register: it has no quantity, specification, rights,
    # safety, or availability structure. Preserve the omission as a gap.
    materials: list[dict[str, Any]] = []
    resources: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    work_packages: list[dict[str, Any]] = []
    requirement_task_ids: dict[str, list[str]] = {item: [] for item in requirement_ids}
    task_duration_by_id: dict[str, dict[str, str]] = {}
    task_number = 1
    external_effects = {"PHYSICAL_EXTERNAL", "PUBLICATION", "PURCHASE", "CONTRACT", "DELETION"}
    effect_gaps: list[str] = []
    duration_gaps: list[str] = []
    missing_prototype_plan = not prototype_plans

    def new_task_id() -> str:
        nonlocal task_number
        value = f"TK{task_number:03d}"
        task_number += 1
        return value

    if prototype_plans:
        for wp_index, prototype in enumerate(prototype_plans, start=1):
            prototype_id = str(prototype["id"])
            duration_band = str(prototype.get("estimated_duration_band") or "UNKNOWN")
            if duration_band not in _KNOWN_DURATION_BANDS:
                duration_gaps.append(
                    f"Prototype plan {prototype_id} does not supply a known duration band ({duration_band}); confirm its duration before execution."
                )
            work_package_id = f"WP{wp_index:03d}"
            task_map = {
                str(item.get("id")): new_task_id()
                for item in prototype.get("tasks", [])
                if isinstance(item, dict) and item.get("id")
            }
            related_requirement_ids = [
                str(acceptance_by_id[test_id]["target_requirement"])
                for test_id in prototype.get("acceptance_test_ids", [])
                if str(test_id) in acceptance_by_id
                and str(acceptance_by_id[str(test_id)].get("target_requirement")) in requirement_ids
            ]
            related_requirement_ids = list(dict.fromkeys(related_requirement_ids))
            package_task_ids: list[str] = []
            for prototype_task in prototype.get("tasks", []):
                prototype_task_id = str(prototype_task.get("id"))
                missing_dependencies = [
                    str(value) for value in prototype_task.get("depends_on", [])
                    if str(value) not in task_map
                ]
                if missing_dependencies:
                    raise DiagnosticError(_finding(
                        "PLANNING_INPUT_REFERENCE",
                        f"prototype task {prototype_task_id} references missing task IDs: {', '.join(missing_dependencies)}",
                        file=project_root / "00_handoff/source-bundle/artifacts/prototype-plans.yaml",
                        location=f"/{prototype_id}/tasks/{prototype_task_id}/depends_on",
                        remediation="Regenerate the prototype plan with dependencies declared in the same task collection.",
                    ))
                effect_type = str(prototype_task.get("effect_type") or "PHYSICAL_EXTERNAL")
                if "effect_type" not in prototype_task:
                    effect_gaps.append(f"Task {prototype_task_id} has no effect_type in the accepted handoff; confirm its external-effect classification before execution.")
                if effect_type not in external_effects | {"READ_ONLY", "REPOSITORY_WRITE"}:
                    raise DiagnosticError(_finding(
                        "PLANNING_EFFECT_TYPE",
                        f"unsupported prototype task effect_type {effect_type!r}",
                        file=project_root / "00_handoff/source-bundle/artifacts/prototype-plans.yaml",
                        location=f"/{prototype_id}/tasks/{prototype_task_id}/effect_type",
                        remediation="Use an effect type declared by the production common vocabulary.",
                    ))
                dependency_ids = [task_map[str(value)] for value in prototype_task.get("depends_on", [])]
                task_id = task_map[prototype_task_id]
                task = {
                    "id": task_id,
                    "work_package_id": work_package_id,
                    "title": _plan_text(prototype_task.get("title"), prototype_task_id),
                    "depends_on": dependency_ids,
                    "required_resource_ids": [],
                    "required_material_ids": [],
                    "acceptance_condition": _plan_text(prototype_task.get("completion_condition"), prototype_task_id),
                    "effect_type": effect_type,
                    "approval_requirement_ids": [],
                    "duration": _duration_for_band(duration_band),
                    "status": "BLOCKED" if effect_type in external_effects else ("READY" if not dependency_ids else "BACKLOG"),
                    "trace_refs": _trace(*trace, prototype_id, prototype_task_id, task_id),
                }
                tasks.append(task)
                package_task_ids.append(task_id)
                task_duration_by_id[task_id] = task["duration"]
                for requirement_id in related_requirement_ids:
                    requirement_task_ids[requirement_id].append(task_id)
            prototype_inputs = [str(item) for item in prototype.get("inputs", []) if str(item).strip()]
            if prototype_inputs:
                effect_gaps.append(
                    f"Prototype plan {prototype_id} names its inputs but not their quantity, rights, or availability; confirm those before execution."
                )
            work_packages.append({
                "id": work_package_id,
                "title": _plan_text(prototype.get("method"), prototype_id),
                "deliverable_ids": [],
                "input_ids": prototype_inputs,
                "output_ids": [prototype_id, *[str(item) for item in prototype.get("acceptance_test_ids", [])]],
                "depends_on": [f"WP{wp_index - 1:03d}"] if wp_index > 1 else [],
                "owner_capability": _plan_text(prototype.get("executor_capability"), owner_capability),
                "review_gate_id": None,
                "task_ids": package_task_ids,
                "status": "BLOCKED" if any(item["effect_type"] in external_effects for item in tasks if item["id"] in package_task_ids) else "PLANNED",
                "trace_refs": _trace(*trace, prototype_id, work_package_id),
            })
        existing_task_ids = {task["id"] for task in tasks}
        validation_task_id = "TK004" if "TK004" not in existing_task_ids else new_task_id()
        validation_task = {
            "id": validation_task_id,
            "work_package_id": work_packages[-1]["id"],
            "title": "Validate derived plan coverage",
            "depends_on": [],
            "required_resource_ids": [],
            "required_material_ids": [],
            "acceptance_condition": "The derived plan graph and mandatory requirement coverage are valid.",
            "effect_type": "READ_ONLY",
            "approval_requirement_ids": [],
            "duration": {"value": "15", "unit": "min"},
            "status": "READY",
            "trace_refs": _trace(*trace, validation_task_id),
        }
        tasks.append(validation_task)
        work_packages[-1]["task_ids"].append(validation_task_id)
        task_duration_by_id[validation_task_id] = validation_task["duration"]
    else:
        # Keep the synthetic/minimal handoff buildable while exposing that no
        # prototype work package was supplied by Research.
        work_package_id = "WP001"
        package_task_ids: list[str] = []
        for requirement in mandatory_requirements or [{"id": "RQ000", "statement": "Validate the accepted handoff."}]:
            requirement_id = str(requirement["id"])
            first_task_id = new_task_id()
            second_task_id = new_task_id()
            review_task_id = new_task_id()
            fallback_tasks = [
                {
                    "id": first_task_id,
                    "title": f"Prepare production for {requirement_id}",
                    "depends_on": [],
                    "acceptance_condition": _plan_text(requirement.get("statement"), requirement_id),
                    "effect_type": "PHYSICAL_EXTERNAL",
                    "status": "BLOCKED",
                },
                {
                    "id": second_task_id,
                    "title": f"Record evidence for {requirement_id}",
                    "depends_on": [first_task_id],
                    "acceptance_condition": _plan_text(
                        tests_by_requirement.get(requirement_id, [{}])[0].get("evidence_to_record")
                        if tests_by_requirement.get(requirement_id) else None,
                        requirement_id,
                    ),
                    "effect_type": "PHYSICAL_EXTERNAL",
                    "status": "BLOCKED",
                },
                {
                    "id": review_task_id,
                    "title": f"Review acceptance evidence for {requirement_id}",
                    "depends_on": [second_task_id],
                    "acceptance_condition": _plan_text(
                        tests_by_requirement.get(requirement_id, [{}])[0].get("pass_condition")
                        if tests_by_requirement.get(requirement_id) else None,
                        requirement_id,
                    ),
                    "effect_type": "READ_ONLY",
                    "status": "BACKLOG",
                },
            ]
            for task in fallback_tasks:
                task.update({
                    "work_package_id": work_package_id,
                    "required_resource_ids": [],
                    "required_material_ids": [],
                    "approval_requirement_ids": [],
                    "duration": {"value": "1", "unit": "h"},
                    "trace_refs": _trace(*trace, requirement_id, task["id"]),
                })
                tasks.append(task)
                package_task_ids.append(task["id"])
                task_duration_by_id[task["id"]] = task["duration"]
            if requirement_id in requirement_task_ids:
                requirement_task_ids[requirement_id].extend([first_task_id, second_task_id, review_task_id])
        validation_task_id = new_task_id()
        validation_task = {
            "id": validation_task_id,
            "work_package_id": work_package_id,
            "title": "Validate plan coverage",
            "depends_on": [],
            "required_resource_ids": [],
            "required_material_ids": [],
            "acceptance_condition": "The plan graph and mandatory requirement coverage are valid.",
            "effect_type": "READ_ONLY",
            "approval_requirement_ids": [],
            "duration": {"value": "15", "unit": "min"},
            "status": "READY",
            "trace_refs": _trace(*trace, validation_task_id),
        }
        tasks.append(validation_task)
        package_task_ids.append(validation_task_id)
        task_duration_by_id[validation_task_id] = validation_task["duration"]
        work_packages.append({
            "id": work_package_id,
            "title": _plan_text(selected_hypothesis.get("title"), selected_hypothesis_id),
            "deliverable_ids": [],
            "input_ids": [],
            "output_ids": acceptance_test_ids,
            "depends_on": [],
            "owner_capability": owner_capability,
            "review_gate_id": None,
            "task_ids": package_task_ids,
            "status": "BLOCKED",
            "trace_refs": _trace(*trace, work_package_id),
        })

    technical_specifications = []
    for index, requirement in enumerate(mandatory_requirements, start=1):
        requirement_id = str(requirement["id"])
        tests = tests_by_requirement[requirement_id]
        technical_specifications.append({
            "id": f"TS{index:03d}",
            "deliverable_id": "DL001",
            "parameter": f"{_plan_text(requirement.get('category'), 'requirement')}-{requirement_id}",
            "target": {"kind": "QUALITATIVE", "statement": _plan_text(requirement.get("statement"), requirement_id)},
            "tolerance": None,
            "measurement_method": _plan_text(tests[0].get("method") if tests else None, "not-supplied-by-handoff"),
            "source_requirement_ids": [requirement_id],
            "status": "PROVISIONAL",
            "trace_refs": _trace(*trace, requirement_id, f"TS{index:03d}", "DL001"),
        })
    deliverable_test_ids = list(dict.fromkeys(
        test_id
        for requirement_id in requirement_ids
        for test_id in declared_tests_by_requirement[requirement_id]
    ))
    deliverable = {
        "id": "DL001",
        "title": _plan_text(selected_hypothesis.get("title"), selected_hypothesis_id),
        "type": "production-deliverable",
        "source_requirement_ids": requirement_ids,
        "technical_spec_ids": [item["id"] for item in technical_specifications],
        "acceptance_test_ids": deliverable_test_ids,
        "owner_capability": owner_capability,
        "due_milestone_id": "MS001",
        "status": "PLANNED",
        "trace_refs": _trace(*trace, selected_hypothesis_id, "DL001"),
    }
    for work_package in work_packages:
        work_package["deliverable_ids"] = ["DL001"]
        # Inputs named by the originating prototype plan are what a producer has to gather.
        # Only fall back to specification IDs when the handoff named nothing.
        if not work_package["input_ids"]:
            work_package["input_ids"] = [item["id"] for item in technical_specifications]

    milestones = []
    for index, work_package in enumerate(work_packages, start=1):
        start_id = f"MS{index * 2 - 1:03d}"
        complete_id = f"MS{index * 2:03d}"
        milestones.extend([
            {
                "id": start_id,
                "title": f"Start {work_package['title']}",
                "sequence": index * 2 - 1,
                "depends_on": [milestones[-1]["id"]] if milestones else [],
                "status": "PLANNED",
                "trace_refs": _trace(*trace, work_package["id"], start_id),
            },
            {
                "id": complete_id,
                "title": f"Complete {work_package['title']}",
                "sequence": index * 2,
                "depends_on": [start_id],
                "status": "BLOCKED" if work_package["status"] == "BLOCKED" else "PLANNED",
                "trace_refs": _trace(*trace, work_package["id"], complete_id),
            },
        ])
    deliverable["due_milestone_id"] = milestones[-1]["id"]
    deliverable["trace_refs"] = _trace(*deliverable["trace_refs"], milestones[-1]["id"])

    gaps = [
        {
            "id": str(gap["id"]),
            "rule": str(gap.get("rule") or "HANDOFF_GAP"),
            "statement": _plan_text(gap.get("statement"), str(gap["id"])),
            "impact": _plan_text(gap.get("impact"), "The affected production decision cannot be relied on until this gap is resolved."),
            "owner": _plan_text(gap.get("owner"), "production"),
            "blocking": bool(gap.get("blocking", False)),
            "resolution_condition": _plan_text(gap.get("resolution_condition"), "Resolve the handoff gap and regenerate the production plan."),
            "source_refs": [str(value) for value in gap.get("source_refs", []) if isinstance(value, str)],
        }
        for gap in handoff_gaps
    ]
    used_gap_ids = {gap["id"] for gap in gaps}
    gap_number = 1

    def append_gap(statement: str, blocking: bool, gap_id: str | None = None, *, rule: str = "PLANNING_GAP", source_refs: list[str] | None = None) -> None:
        nonlocal gap_number
        candidate = gap_id or f"PG{gap_number:03d}"
        while candidate in used_gap_ids:
            gap_number += 1
            candidate = f"PG{gap_number:03d}"
        used_gap_ids.add(candidate)
        gaps.append({
            "id": candidate,
            "rule": rule,
            "statement": statement,
            "impact": "The affected production decision cannot be relied on until this gap is resolved.",
            "owner": "production",
            "blocking": blocking,
            "resolution_condition": "Resolve the gap and regenerate the production plan.",
            "source_refs": list(source_refs or []),
        })
        gap_number += 1

    append_gap("Budget amounts and commitments are not supplied by the accepted handoff.", False)
    append_gap("Calendar dates and availability are not supplied by the accepted handoff; the schedule remains relative.", False)
    if missing_prototype_plan:
        append_gap("No prototype plan is present in the accepted handoff; production work cannot be confirmed from the input.", True)
    for statement in effect_gaps:
        append_gap(statement, False)
    for statement in duration_gaps:
        append_gap(statement, False)
    for statement, blocking in brief_gaps:
        append_gap(statement, blocking)
    for gap in reference_gaps:
        append_gap(_plan_text(gap.get("statement"), str(gap.get("id"))), bool(gap.get("blocking", False)), str(gap.get("id")))

    mandatory_ids = set(requirement_ids)
    for assessment in viewer_assessments:
        requirement_id = assessment["requirement_id"]
        if requirement_id not in mandatory_ids:
            raise DiagnosticError(_finding(
                "PLANNING_VIEWER_ASSESSMENT_REFERENCE",
                f"viewer assessment references non-mandatory requirement {requirement_id}",
                file=viewer_assessment_path or project_root,
                remediation="Assess only a mandatory requirement present in the accepted production handoff.",
            ))
        if assessment["status"] not in VIEWER_ASSESSMENT_STATUSES_REQUIRING_REVIEW:
            continue
        requirement = next(item for item in mandatory_requirements if str(item.get("id")) == requirement_id)
        test_ids = {str(value) for value in requirement.get("acceptance_test_ids", []) if isinstance(value, str)}
        review_tests = [test for test in acceptance_tests if str(test.get("id")) in test_ids and test.get("viewer_facing") is True]
        review_text = " ".join(str(test.get(key, "")) for test in review_tests for key in ("method", "pass_condition", "evidence_to_record")).lower()
        if not review_tests or not re.search(r"\bblind\b|\bframe\b", review_text):
            append_gap(
                f"Viewer assessment for {requirement_id} is {assessment['status']}; a blind or frame review acceptance test is required and the estimate cannot be accepted by itself.",
                True,
                rule="PLANNING_VIEWER_REVIEW_REQUIRED",
                source_refs=[assessment["assessment_id"], requirement_id],
            )

    task_edges = [
        {"from": dependency, "to": task["id"]}
        for task in tasks
        for dependency in task.get("depends_on", [])
    ]
    task_ids = [task["id"] for task in tasks]
    topological_order = _topological_order(task_ids, task_edges)
    graph = {
        "graph_id": "DG001",
        "nodes": task_ids,
        "edges": task_edges,
        "topological_order": topological_order,
        "trace_refs": _trace(*trace, "DG001"),
    }
    critical_path_task_ids = _critical_path_task_ids(task_ids, task_edges, task_duration_by_id)
    coverage_items = []
    for requirement_id in requirement_ids:
        declared_test_ids = declared_tests_by_requirement[requirement_id]
        resolved_test_ids = [test_id for test_id in declared_test_ids if test_id in acceptance_by_id]
        work_package_ids = [
            work_package["id"]
            for work_package in work_packages
            if all(test_id in work_package["output_ids"] for test_id in resolved_test_ids)
        ] if resolved_test_ids else []
        mapped_task_ids = list(dict.fromkeys(requirement_task_ids[requirement_id]))
        covered = bool(
            requirement_id
            and deliverable["source_requirement_ids"]
            and technical_specifications
            and declared_test_ids
            and resolved_test_ids == declared_test_ids
            and work_package_ids
            and mapped_task_ids
        )
        coverage_items.append({
            "requirement_id": requirement_id,
            "deliverable_ids": ["DL001"] if requirement_id in deliverable["source_requirement_ids"] else [],
            "technical_spec_ids": [item["id"] for item in technical_specifications if requirement_id in item["source_requirement_ids"]],
            "acceptance_test_ids": declared_test_ids,
            "work_package_ids": work_package_ids,
            "task_ids": mapped_task_ids,
            "status": "COVERED" if covered else "UNCOVERED",
        })
    uncovered_requirement_ids = [
        item["requirement_id"] for item in coverage_items if item["status"] == "UNCOVERED"
    ]
    for requirement_id in uncovered_requirement_ids:
        append_gap(f"Requirement {requirement_id} is not connected to a complete acceptance path in the handoff.", True)
    coverage_report = {
        "report_id": "CV001",
        "requirements": coverage_items,
        "coverage_percent": round(sum(item["status"] == "COVERED" for item in coverage_items) * 100 / len(coverage_items)) if coverage_items else 0,
        "uncovered_requirement_ids": uncovered_requirement_ids,
        "trace_refs": _trace(*trace, "CV001"),
    }

    risks = []
    risk_number = 1
    allowed_severities = {"LOW", "MEDIUM", "MAJOR", "CRITICAL"}
    for uncertainty in uncertainties:
        if not isinstance(uncertainty, dict):
            continue
        severity = str(uncertainty.get("severity", "MEDIUM")).upper()
        if severity not in allowed_severities:
            severity = "MEDIUM"
        risk_id = f"RK{risk_number:03d}"
        risk_number += 1
        statement = _plan_text(uncertainty.get("statement"), str(uncertainty.get("id", risk_id)))
        risks.append({
            "id": risk_id,
            "title": statement,
            "source_handoff_gap_ids": [],
            "source_requirement_ids": requirement_ids,
            "severity": severity,
            "likelihood": "UNKNOWN",
            "impact": statement,
            "mitigation": _plan_text(uncertainty.get("external_validation_reason"), "Validate the uncertainty before relying on the affected plan decision."),
            "owner_capability": owner_capability,
            "status": "OPEN",
            "blocking": uncertainty.get("external_validation_reason") is not None,
            "trace_refs": _trace(*trace, str(uncertainty.get("id", risk_id)), risk_id),
        })
    for gap in handoff_gaps:
        risk_id = f"RK{risk_number:03d}"
        risk_number += 1
        statement = _plan_text(gap.get("statement"), str(gap["id"]))
        risks.append({
            "id": risk_id,
            "title": f"Handoff gap {gap['id']}",
            "source_handoff_gap_ids": [str(gap["id"])],
            "source_requirement_ids": requirement_ids,
            "severity": "MAJOR" if gap.get("blocking") else "MEDIUM",
            "likelihood": "UNKNOWN",
            "impact": statement,
            "mitigation": "Resolve the handoff gap and regenerate the plan before relying on the affected decision.",
            "owner_capability": owner_capability,
            "status": "OPEN",
            "blocking": bool(gap.get("blocking")),
            "trace_refs": _trace(*trace, str(gap["id"]), risk_id),
        })

    external_task_ids = [task["id"] for task in tasks if task["effect_type"] in external_effects]
    approval_requirements = []
    if external_task_ids:
        approval_payload = {
            "action": "PHYSICAL_EXTERNAL",
            "target_ref": f"03_plan/task-plan.yaml#{','.join(external_task_ids)}",
            "task_ids": external_task_ids,
        }
        approval_requirements.append({
            "id": "AR001",
            "action": "PHYSICAL_EXTERNAL",
            "target_ref": approval_payload["target_ref"],
            "target_sha256": canonical_sha256(approval_payload),
            "authority": "HUMAN",
            "status": "REQUIRED",
            "reason": "Physical or external-effect tasks require explicit human approval before execution.",
            "task_ids": external_task_ids,
            "trace_refs": _trace(*trace, "AR001"),
        })
        for task in tasks:
            if task["id"] in external_task_ids:
                task["approval_requirement_ids"] = ["AR001"]
                task["trace_refs"] = _trace(*task["trace_refs"], "AR001")

    budget_policy = load_config(repository_root(), "budget-policy.yaml")
    allowed_currencies = budget_policy.get("allowed_currencies", [])
    if not isinstance(allowed_currencies, list) or not allowed_currencies:
        raise DiagnosticError(_finding(
            "PLANNING_BUDGET_POLICY",
            "budget policy must declare at least one allowed currency",
            file=repository_root() / "config/budget-policy.yaml",
            location="/allowed_currencies",
            remediation="Configure an allowed currency before building a production plan.",
        ))
    budget_items = [
        {
            "id": f"BI{index:03d}",
            "category": "prototype-plan",
            "description": f"Estimated cost band for {prototype['id']}",
            "amount": None,
            "basis": f"accepted handoff cost band: {prototype.get('estimated_cost_band', 'UNKNOWN')}",
            "confidence": "LOW",
            "status": "ESTIMATED",
            "trace_refs": _trace(*trace, str(prototype["id"]), f"BI{index:03d}"),
        }
        for index, prototype in enumerate(prototype_plans, start=1)
    ]
    budget = {
        "budget_id": "BDG001",
        "currency": str(allowed_currencies[0]),
        "baseline_total": None,
        "contingency": None,
        "approval_threshold": None,
        "items": budget_items,
        "gaps": ["No price, quote, supplier, reservation, or commitment is present in the accepted handoff."],
        "status": "ESTIMATED",
        "trace_refs": _trace(*trace, "BDG001"),
    }
    schedule = {
        "schedule_id": "SCH001",
        "mode": "RELATIVE",
        "baseline_status": "PROVISIONAL",
        "milestones": milestones,
        "task_schedule": [
            {"task_id": task["id"], "duration": task_duration_by_id[task["id"]], "start_at": None, "due_at": None}
            for task in tasks
        ],
        "critical_path_task_ids": critical_path_task_ids,
        "gaps": [gap["statement"] for gap in gaps if "Calendar dates" in gap["statement"]],
        "trace_refs": _trace(*trace, "SCH001"),
    }

    unmet: list[str] = []
    if not requirement_ids or any(item["status"] != "COVERED" for item in coverage_items):
        unmet.append("mandatory_requirements_covered")
    if not tasks or any(dependency not in set(task_ids) for task in tasks for dependency in task.get("depends_on", [])) or len(topological_order) != len(task_ids):
        unmet.append("task_dependency_graph")
    if not any(not task.get("depends_on") for task in tasks):
        unmet.append("root_task")
    if any(not isinstance(task.get("acceptance_condition"), str) or not task["acceptance_condition"].strip() for task in tasks):
        unmet.append("task_acceptance_conditions")
    approval_ids = {item["id"] for item in approval_requirements}
    if any(
        task["effect_type"] in external_effects
        and (not task.get("approval_requirement_ids") or not set(task.get("approval_requirement_ids", [])) <= approval_ids)
        for task in tasks
    ):
        unmet.append("external_task_approvals")
    if any(gap["blocking"] for gap in gaps):
        unmet.append("blocking_gaps")
    readiness = {"startable": not unmet, "unmet": unmet}
    # Plan generation records readiness; the runtime lifecycle advances only
    # after an explicit transition and any required human approval.
    state = "PLANNING"
    project_id = str(_require_mapping(project_root / "manifest.yaml")["project_id"])
    plan = {
        "schema_version": "1.0.0",
        "plan_id": "PL001",
        "plan_revision": 1,
        "project_id": project_id,
        "state": state,
        "generated_at": handoff["generated_at"],
        "handoff_ref": handoff_ref,
        "mandatory_requirement_ids": requirement_ids,
        "acceptance_test_ids": acceptance_test_ids,
        "acceptance_tests": acceptance_tests,
        "viewer_response_assessments": viewer_assessments,
        "selection_record": selection_record,
        "scope_baseline": scope_baseline,
        "assumptions": assumptions,
        "deliverables": [deliverable],
        "technical_specifications": technical_specifications,
        "materials": materials,
        "resources": resources,
        "work_packages": work_packages,
        "tasks": tasks,
        "schedule": schedule,
        "budget": budget,
        "risks": risks,
        "approval_register": {"register_id": "AGR001", "requirements": approval_requirements, "approvals": [], "trace_refs": _trace(*trace, "AGR001")},
        "coverage_report": coverage_report,
        "dependency_graph": graph,
        "critical_path_task_ids": schedule["critical_path_task_ids"],
        "reference_access": reference_access,
        "gaps": gaps,
        "readiness": readiness,
        "determinism": {"algorithm": "production-plan-v1", "source_input_sha256": canonical_sha256(source_input)},
    }
    plan["visual_package"] = build_visual_package(
        plan,
        handoff=handoff,
        brief=production_brief,
        reference_access=reference_access,
        visual_language=artifacts.get("visual_language"),
    )
    if plan["visual_package"]["status"] != "READY":
        plan["readiness"]["unmet"].append("visual_package")
        plan["readiness"]["unmet"] = list(dict.fromkeys(plan["readiness"]["unmet"]))
        plan["readiness"]["startable"] = False
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


def _latest_available_outputs(project_root: Path) -> list[dict[str, Any]]:
    """Return the newest AVAILABLE revision of every recorded output version."""
    register = project_root / "05_execution/output-versions.yaml"
    if not register.is_file():
        return []
    records = _require_mapping(register).get("records", [])
    newest: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or record.get("status") != "AVAILABLE":
            continue
        output_id = str(record.get("output_id"))
        current = newest.get(output_id)
        if current is None or int(record.get("revision", 0)) >= int(current.get("revision", 0)):
            newest[output_id] = record
    return [newest[key] for key in sorted(newest)]


def _work_package_constraints(work_package: dict[str, Any], constraints_by_prototype: dict[str, list[str]]) -> list[str]:
    """A work package carries the constraints of the prototype plan it came from."""
    return [
        constraint
        for output_id in work_package.get("output_ids", [])
        for constraint in constraints_by_prototype.get(str(output_id), [])
    ]


def _render_made_work(project_root: Path) -> list[str]:
    """Render what the project has actually produced, with the previews a producer needs to see."""
    outputs = _latest_available_outputs(project_root)
    if not outputs:
        return [
            "まだ何も作っていない。この節は、制作した物が実行台帳（`05_execution/output-versions.yaml`）に"
            "記録された時点で、現物とプレビュー画像を載せる。",
            "",
        ]
    lines: list[str] = []
    for record in outputs:
        output_id = str(record.get("output_id"))
        for preview in record.get("previews", []):
            path = str(preview.get("path"))
            caption = str(preview.get("caption"))
            if (project_root / path).is_file():
                lines.extend([f"![{output_id}]({path})", "", f"*{caption}*", ""])
            else:
                lines.extend([f"{output_id}「{caption}」: プレビュー画像が見つからない（`{path}`）。", ""])
    asset_rows = []
    for record in outputs:
        asset = record.get("asset_ref", {})
        asset_rows.append([
            record.get("output_id"),
            record.get("deliverable_id"),
            asset.get("media_type"),
            record.get("version"),
            asset.get("rights_status"),
            asset.get("uri"),
            asset.get("sha256"),
        ])
    lines.extend([
        _markdown_table(
            ["出力", "対象成果物", "媒体", "版", "権利", "置き場", "内容ハッシュ"],
            asset_rows,
        ),
        "",
    ])
    quality_rows = []
    quality_register = project_root / "05_execution/quality-results.yaml"
    if quality_register.is_file():
        for record in _require_mapping(quality_register).get("records", []):
            if not isinstance(record, dict):
                continue
            for dimension in record.get("dimensions", []):
                quality_rows.append([
                    record.get("quality_id"), record.get("output_id"), dimension.get("criterion"),
                    dimension.get("method"), dimension.get("result"), record.get("evidence_refs"),
                ])
    if quality_rows:
        lines.extend([
            "**現物で確かめたこと**",
            "",
            _markdown_table(["検査", "対象", "見たこと", "確かめ方", "結果", "証跡"], quality_rows),
            "",
        ])
    result_path = project_root / "08_runtime/production-result.yaml"
    if result_path.is_file():
        result = _require_mapping(result_path)
        lines.extend([
            "**作ってみて分かったこと**",
            "",
            _markdown_table(
                ["観察", "分かったこと", "確かめ方", "限界"],
                [
                    [item.get("id"), item.get("statement"), item.get("method"), item.get("limitations")]
                    for item in result.get("observations", [])
                ],
            ),
            "",
            "**実施済みの受入テスト**",
            "",
            _markdown_table(
                ["テスト", "結果", "条件", "限界", "証跡"],
                [
                    [
                        item.get("acceptance_test_id"), item.get("result"), item.get("conditions"),
                        item.get("limitations"), item.get("evidence_ref"),
                    ]
                    for item in result.get("test_results", [])
                ],
            ),
            "",
            "**まだ確かめていないこと**",
            "",
            _markdown_bullets([item.get("statement") for item in result.get("open_gaps", [])]),
            "",
        ])
    return lines


def _render_human_plan(project_root: Path, plan: dict[str, Any]) -> str:
    """Render the one complete production plan intended for human producers."""
    handoff = _require_mapping(project_root / "00_handoff/production-handoff.yaml")
    bundle_root = project_root / "00_handoff/source-bundle"
    requirements_path = bundle_root / "artifacts/production-requirements.yaml"
    hypotheses_path = bundle_root / "artifacts/production-hypotheses.yaml"
    brief_path = bundle_root / "artifacts/production-brief.yaml"
    prototype_plans_path = bundle_root / "artifacts/prototype-plans.yaml"
    requirements = _records(_require_mapping(requirements_path), "requirements", requirements_path)
    hypotheses = _records(_require_mapping(hypotheses_path), "hypotheses", hypotheses_path)
    prototype_constraints = {
        str(item["id"]): [str(value) for value in item.get("constraints", [])]
        for item in _records(_require_mapping(prototype_plans_path), "prototype_plans", prototype_plans_path)
        if isinstance(item, dict) and item.get("id")
    }
    production_brief = _require_mapping(brief_path)
    selected_hypothesis = next(
        item for item in hypotheses if item.get("id") == plan["selection_record"]["selected_hypothesis_id"]
    )
    coverage_by_id = {str(item["requirement_id"]): item for item in plan["coverage_report"]["requirements"]}
    approval_requirements = plan["approval_register"]["requirements"]
    critical_path = " → ".join(f"`{task_id}`" for task_id in plan["critical_path_task_ids"])
    ready_tasks = ", ".join(f"`{task['id']}`" for task in plan["tasks"] if task.get("status") == "READY") or "なし"
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

    def brief_value(section: str, field: str, missing: str | None = None) -> Any:
        value = _brief_field(production_brief, section, field)
        if value is None or (isinstance(value, str) and not value.strip()) or (field == "encounter" and not isinstance(value, list)):
            return missing or f"未記載（{_BRIEF_FIELD_LABELS[(section, field)]}が必要です）"
        if field == "encounter" and isinstance(value, list) and len(value) < 3:
            return f"未記載（{_BRIEF_FIELD_LABELS[(section, field)]}は3段階以上が必要です）"
        if field == "precedents" and isinstance(value, list) and not value:
            return "未記載（先行作品を1件以上調査し、差分を記録してください）"
        return value

    lines = [
        "# 統合制作計画書",
        "",
        "> この文書は、受理済みhandoffと検証済みの制作計画を、人間が読んで制作判断・制作実務に使える一つの計画書へ統合したものです。計画の生成は、購入・契約・公開・連絡・削除・物理作業の実行承認を意味しません。",
        "",
        "### 計画メタデータ",
        "",
        _markdown_table(["項目", "内容"], [
            ["プロジェクト", plan["project_id"]],
            ["計画", f"{plan['plan_id']} revision {plan['plan_revision']}"],
            ["計画状態", plan["state"]],
            ["制作着手可否", "着手可能" if plan["readiness"]["startable"] else "着手不可"],
            ["着手できない理由", plan["readiness"]["unmet"] or "なし"],
            ["生成日時", plan["generated_at"]],
            ["handoff", f"{plan['handoff_ref']['id']} revision {plan['handoff_ref']['revision']}"],
            ["要件カバレッジ", f"{plan['coverage_report']['coverage_percent']}%"],
            ["クリティカルパス", critical_path],
        ]),
        "",
        "",
        "## 1. 完成像",
        "",
        _markdown_table(["項目", "内容"], [
            ["experience sequence / encounter", brief_value("completion_image", "encounter")],
            ["position", brief_value("completion_image", "position")],
            ["first_seconds", brief_value("completion_image", "first_seconds")],
            ["after_30s", brief_value("completion_image", "after_30s")],
            ["after_3min", brief_value("completion_image", "after_3min")],
        ]),
        "",
        "## 2. テーマ",
        "",
        _markdown_table(["項目", "内容"], [
            ["field", brief_value("theme", "field")],
            ["stands_against", brief_value("theme", "stands_against")],
            ["difference", brief_value("theme", "difference")],
            ["why_now", brief_value("theme", "why_now")],
        ]),
        "",
        "## 3. メッセージ",
        "",
        _markdown_table(["項目", "内容"], [
            ["claim", brief_value("message", "claim")],
            ["who_disagrees", brief_value("message", "who_disagrees")],
            ["denies", brief_value("message", "denies")],
            ["shown_not_told", brief_value("message", "shown_not_told")],
        ]),
        "",
        "## 4. コンセプト",
        "",
        _markdown_table(["項目", "内容"], [
            ["mechanism", brief_value("concept", "mechanism")],
            ["without_the_technique", brief_value("concept", "without_the_technique")],
            ["precedents", brief_value("concept", "precedents")],
            ["self_repetition_risk", brief_value("concept", "self_repetition_risk")],
        ]),
        "",
        "## 5. 調査の要約",
        "",
        _markdown_table(["項目", "内容"], [
            ["questions", brief_value("research_summary", "questions")],
            ["what_was_read", brief_value("research_summary", "what_was_read")],
            ["what_came_out", brief_value("research_summary", "what_came_out")],
            ["what_is_not_settled", brief_value("research_summary", "what_is_not_settled")],
        ]),
        "",
        "### 採択内容と根拠",
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
        "### 制作リファレンス",
        "",
        "コンセプト、ビジュアル、制作手法の参照先です。URLは受理済みhandoffに含まれる恒久HTTPS URLだけを掲載し、アクセスできない参照や不足カテゴリはギャップとして残します。",
        "",
        _markdown_table(["分類", "出所ID", "種別", "概要", "アクセスURL", "状態"], reference_rows),
        "",
        "### 要件と受入の目的",
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
        "## 6. できている物",
        "",
        "### ビジュアルパッケージ",
        "",
        "boardとmockupは、受理済みhandoffから生成した閲覧用の決定論的fixtureです。外部画像素材の取得・採用、物理制作、外部検証、公開、購入、契約、Drive共有は実施していません。",
        "",
        _markdown_table(["種別", "名称", "状態", "相対リンク", "asset hash", "権利", "安全"], [
            [
                label,
                plan["visual_package"][key]["title"],
                plan["visual_package"][key]["kind"],
                f"[{plan['visual_package'][key]['relative_path']}](visual-package/{Path(plan['visual_package'][key]['relative_path']).name})",
                plan["visual_package"][key]["asset_ref"]["sha256"],
                plan["visual_package"][key]["safety"]["rights_status"],
                plan["visual_package"][key]["safety"]["safety_status"],
            ]
            for label, key in (("ビジュアルリファレンスボード", "board"), ("コンセプト・モックアップ", "mockup"))
        ]),
        "",
        _markdown_table(["mockup注記", "内容"], [[
            "表現種別", plan["visual_package"]["mockup"]["representation"]],
            ["寸法", plan["visual_package"]["mockup"]["dimensions"]],
            ["素材", plan["visual_package"]["mockup"]["materials"]],
            ["配置", plan["visual_package"]["mockup"]["placement"]],
            ["検証", plan["visual_package"]["mockup"]["physical_validation_note"]],
        ]),
        "",
        *_render_made_work(project_root),
        "## 7. 制作範囲と成果物",
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
        "## 8. 技術仕様・材料・資源",
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
        "## 9. 工程と作業手順",
        "",
        _markdown_table(["作業パッケージ", "内容", "投入物", "制約", "成果物", "タスク", "担当能力", "状態"], [
            [
                item["id"], item["title"], item["input_ids"],
                _work_package_constraints(item, prototype_constraints),
                item["deliverable_ids"], item["task_ids"], item["owner_capability"], item["status"],
            ]
            for item in plan["work_packages"]
        ]),
        "",
        _markdown_table(["タスク", "作業", "前提", "所要時間", "必要材料", "受入条件", "効果種別", "承認", "状態"], [
            [
                item["id"], item["title"], item["depends_on"], item["duration"],
                item["required_material_ids"] or f"{item['work_package_id']}の投入物",
                item["acceptance_condition"], item["effect_type"], item["approval_requirement_ids"], item["status"],
            ]
            for item in plan["tasks"]
        ]),
        "",
        f"**実施順の読み方:** クリティカルパスは {critical_path} です。READYタスクは {ready_tasks} です。物理・外部効果を伴うタスクは承認待ちです。",
        "",
        "## 10. 試作・受入評価",
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
        "## 11. 日程と予算",
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
        "## 12. リスクと未解決事項",
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
        "## 13. 承認・安全境界",
        "",
        _markdown_table(["承認ID", "対象行為", "対象", "対象hash", "権限者", "状態", "理由", "関連タスク"], [
            [item["id"], item["action"], item["target_ref"], item["target_sha256"], item["authority"], item["status"], item["reason"], item["task_ids"]]
            for item in approval_requirements
        ]),
        "",
        "この計画書は、明示的な人間承認が記録されるまで、物理作業、外部サービスへの接続、購入、契約、支払い、公開、応募、連絡、削除を許可しません。材料の権利・安全状態、会場条件、担当能力、見積、日程は制作開始前に人間が確認してください。",
        "",
        "## 14. 人間向け実行前チェックリスト",
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
        "## 15. 証跡と再現性",
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
        "ユーザーに渡す計画書はこの `03_plan/production-plan.md` 一つです。`production-plan.yaml`などの構造化ファイルと`agent-contexts/`は、検証・再生成・内部運用のためにGit外の制作projectへ保持されます。制作した物は §6 に、実行台帳へ記録済みのプレビュー画像だけを貼ります。完成作品の原寸データ、RAW、動画、音声、3D、大容量asset、credential、signed URLはこの計画書へ埋め込みません。",
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
        "03_plan/visual-package.yaml": plan["visual_package"],
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
    for relative, content in visual_asset_bytes(plan["visual_package"]).items():
        asset_path = project_root / relative
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_bytes(content)
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
    parser.add_argument("--viewer-assessment", type=Path, help="validated viewer-response-assessment/v1 JSON to display and gate")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = repository_root()
    try:
        project_root = args.project_root.resolve()
        plan = _build_plan(project_root, args.viewer_assessment.resolve() if args.viewer_assessment else None)
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
