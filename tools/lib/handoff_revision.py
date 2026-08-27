"""Receive a superseding handoff into an existing production project.

The revision path is deliberately implemented as a staging transaction.  The
accepted bundle is validated before any project byte is changed, the previous
canonical records are copied into immutable history, and the runtime receives
one replayable handoff-revision event after the candidate plan has validated.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .bundle import Bundle, open_bundle
from .canonical import canonical_sha256, event_sha256, sha256_bytes
from .diagnostics import DiagnosticError, Finding
from .runtime import Runtime
from .schema import load_schema, validate_instance
from .yaml_io import dump_yaml, load_json, load_jsonl, load_yaml


HISTORY_SCHEMA_VERSION = "1.0.0"
HANDOFF_LOG = Path("00_handoff/handoff-log.jsonl")
HANDOFF_RECEIPTS = Path("00_handoff/handoff-receipts.yaml")
HISTORY_ROOT = Path("00_handoff/history")


def _finding(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str, context: dict[str, Any] | None = None) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation, context=context or {})


def _error(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str, context: dict[str, Any] | None = None) -> DiagnosticError:
    return DiagnosticError(_finding(rule, reason, file=file, location=location, remediation=remediation, context=context))


def _mapping(path: Path, *, rule: str) -> dict[str, Any]:
    if not path.is_file():
        raise _error(rule, "required canonical record is missing", file=path, remediation="Restore the accepted project record before receiving a handoff revision.")
    value = load_json(path) if path.suffix == ".json" else load_yaml(path)
    if not isinstance(value, dict):
        raise _error(rule, "canonical record must be a mapping", file=path, remediation="Restore the record as a YAML or JSON object.")
    return value


def _identity(handoff: dict[str, Any]) -> tuple[str, int, str]:
    integrity = handoff.get("integrity") if isinstance(handoff.get("integrity"), dict) else {}
    return str(handoff.get("handoff_id")), int(handoff.get("revision", 0)), str(integrity.get("content_sha256"))


def _key(handoff_id: str, revision: int) -> str:
    return f"{handoff_id}-r{revision}"


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.stat().st_size == 0:
        return []
    raw = path.read_bytes()
    if not raw.endswith(b"\n"):
        raise _error("HANDOFF_LOG_PARTIAL_LINE", "handoff-log.jsonl does not end with a complete newline-terminated event", file=path, remediation="Restore the complete final event; the revision transaction will not repair an append-only log.")
    records = load_jsonl(path)
    events: list[dict[str, Any]] = []
    previous_hash: str | None = None
    for expected_sequence, (line, event) in enumerate(records, start=1):
        if event.get("sequence") != expected_sequence:
            raise _error("HANDOFF_LOG_SEQUENCE", "handoff history sequence is not contiguous", file=path, location=f"/events/{line}/sequence", remediation="Restore the original append-only event order.")
        if event.get("previous_event_sha256") != previous_hash:
            raise _error("HANDOFF_LOG_HASH_CHAIN", "handoff history previous hash does not match the preceding event", file=path, location=f"/events/{line}/previous_event_sha256", remediation="Restore the original append-only hash chain.")
        if event_sha256(event) != event.get("event_sha256"):
            raise _error("HANDOFF_LOG_EVENT_HASH", "handoff history event hash does not match its canonical payload", file=path, location=f"/events/{line}/event_sha256", remediation="Restore the original event bytes; do not rebuild the hash chain in place.")
        events.append(event)
        previous_hash = event["event_sha256"]
    return events


def _write_jsonl(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n" for event in events))


def _event(sequence: int, previous_hash: str | None, occurred_at: str, actor_kind: str, actor_id: str, idempotency_key: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    event = {
        "event_id": f"HEVT{sequence:06d}",
        "sequence": sequence,
        "occurred_at": occurred_at,
        "type": event_type,
        "actor": {"kind": actor_kind, "id": actor_id},
        "idempotency_key": idempotency_key,
        "previous_event_sha256": previous_hash,
        "payload": payload,
    }
    event["event_sha256"] = event_sha256(event)
    return event


def _receipt(handoff: dict[str, Any], received_at: str, receipt_id: str) -> dict[str, Any]:
    handoff_id, revision, handoff_hash = _identity(handoff)
    return {
        "receipt_id": receipt_id,
        "receipt_status": "ACCEPTED",
        "handoff_id": handoff_id,
        "revision": revision,
        "handoff_sha256": handoff_hash,
        "schema_version": handoff["schema_version"],
        "received_at": received_at,
        "rules": [],
    }


def _receipt_projection(project_id: str, receipts: list[dict[str, Any]], current: dict[str, Any]) -> dict[str, Any]:
    value = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "project_id": project_id,
        "receipts": sorted(copy.deepcopy(receipts), key=lambda item: (str(item.get("handoff_id")), int(item.get("revision", 0)))),
        "current": {
            "handoff_id": current["handoff_id"],
            "revision": current["revision"],
            "handoff_sha256": current["handoff_sha256"],
            "receipt_id": current["receipt_id"],
        },
    }
    value["integrity"] = {"content_sha256": canonical_sha256(value)}
    return value


def _snapshot_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _snapshot_dir(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True, copy_function=shutil.copy2)


def _snapshot_tree_without_history(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name == "history":
            continue
        target = destination / item.name
        if item.is_dir() and not item.is_symlink():
            shutil.copytree(item, target, copy_function=shutil.copy2)
        elif item.is_file():
            shutil.copy2(item, target)


def _snapshot_previous(project: Path, handoff_id: str, revision: int) -> None:
    """Snapshot every mutable canonical stage relevant to replay and audit."""

    root = project / HISTORY_ROOT / _key(handoff_id, revision)
    if root.exists():
        return
    root.mkdir(parents=True, exist_ok=False)
    for relative in (
        Path("manifest.yaml"),
        Path("00_handoff/handoff-receipt.yaml"),
        Path("00_handoff/source-bundle-manifest.yaml"),
        Path("00_handoff/production-handoff.yaml"),
        Path("03_plan/production-plan.yaml"),
        Path("04_prototype/prototype-control.yaml"),
        Path("07_governance/change-requests.yaml"),
        Path("08_runtime/production-result.yaml"),
        Path("08_runtime/completion-report.json"),
    ):
        _snapshot_file(project / relative, root / relative.name)
    for directory in (Path("03_plan"), Path("04_prototype"), Path("05_execution"), Path("06_installation"), Path("07_governance"), Path("08_runtime")):
        _snapshot_tree_without_history(project / directory, root / directory.name)
    # The plan history has a specified stable location in the project topology.
    plan_history = project / "03_plan/history" / _key(handoff_id, revision)
    if not plan_history.exists():
        _snapshot_tree_without_history(project / "03_plan", plan_history)
    _snapshot_dir(project / "00_handoff/source-bundle", root / "source-bundle")
    _snapshot_dir(project / "00_handoff/source-bundle", project / "00_handoff/source-bundles" / _key(handoff_id, revision))


def _current_handoff(project: Path, repository: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    manifest = _mapping(project / "manifest.yaml", rule="HANDOFF_CURRENT_MANIFEST")
    receipt = _mapping(project / "00_handoff/handoff-receipt.yaml", rule="HANDOFF_CURRENT_RECEIPT")
    handoff = _mapping(project / "00_handoff/production-handoff.yaml", rule="HANDOFF_CURRENT_RECORD")
    events = _read_events(project / HANDOFF_LOG)
    identity = _identity(handoff)
    expected_receipt = (identity[0], identity[1], identity[2])
    actual_receipt = (receipt.get("handoff_id"), receipt.get("revision"), receipt.get("handoff_sha256"))
    actual_manifest = (
        (manifest.get("handoff") or {}).get("handoff_id"),
        (manifest.get("handoff") or {}).get("revision"),
        (manifest.get("handoff") or {}).get("content_sha256"),
    )
    if actual_receipt != expected_receipt or actual_manifest != expected_receipt or receipt.get("receipt_status") != "ACCEPTED":
        raise _error("HANDOFF_CURRENT_DIVERGENCE", "current handoff, receipt, and project manifest do not identify the same accepted revision", file=project / "00_handoff/handoff-receipt.yaml", remediation="Restore the matching accepted handoff projections before receiving a superseding bundle.", context={"handoff": identity, "receipt": actual_receipt, "manifest": actual_manifest})
    source_bundle = project / "00_handoff/source-bundle"
    if not source_bundle.is_dir():
        raise _error("HANDOFF_CURRENT_BUNDLE_MISSING", "current accepted source bundle is missing", file=source_bundle, remediation="Restore the immutable source bundle before receiving a superseding bundle.")
    with open_bundle(source_bundle, repository) as bundle:
        if _identity(bundle.handoff) != identity:
            raise _error("HANDOFF_CURRENT_BUNDLE_DIVERGENCE", "current source bundle does not match the current handoff projection", file=source_bundle, remediation="Restore the source bundle that was accepted for the current receipt.")
    if events:
        last = events[-1].get("payload") or {}
        last_identity = (last.get("handoff_id"), last.get("revision"), last.get("handoff_sha256"))
        if last_identity != identity:
            raise _error("HANDOFF_HISTORY_CURRENT_DIVERGENCE", "handoff history does not end at the current accepted revision", file=project / HANDOFF_LOG, remediation="Restore the complete handoff history before receiving another revision.", context={"history": last_identity, "current": identity})
    return manifest, receipt, handoff, events


def _supersedes(candidate: dict[str, Any], current: tuple[str, int, str]) -> None:
    value = candidate.get("supersedes")
    if isinstance(value, str):
        identity = (value, current[1], None)
    elif isinstance(value, dict):
        identity = (value.get("handoff_id") or value.get("id"), value.get("revision"), value.get("content_sha256"))
    else:
        raise _error("HANDOFF_REVISION_SUPERSEDES", "candidate handoff must explicitly identify the current handoff it supersedes", file="production-handoff.yaml", location="/supersedes", remediation="Set supersedes to the current handoff ID or its exact ID, revision, and hash.")
    if identity[0] != current[0] or identity[1] != current[1] or (identity[2] is not None and identity[2] != current[2]):
        raise _error("HANDOFF_REVISION_LINEAGE", "candidate supersedes reference does not match the current accepted handoff", file="production-handoff.yaml", location="/supersedes", remediation="Export the next consecutive revision from the current accepted handoff.", context={"candidate_supersedes": identity, "current": current})
    if candidate.get("handoff_id") != current[0] or int(candidate.get("revision", 0)) != current[1] + 1:
        rule = "HANDOFF_REVISION_SKIP" if int(candidate.get("revision", 0)) != current[1] + 1 else "HANDOFF_REVISION_IDENTITY"
        raise _error(rule, "candidate handoff must use the same handoff ID and exactly the next revision", file="production-handoff.yaml", location="/revision", remediation="Export revision current+1 and keep the handoff identity stable.", context={"current": current, "candidate": _identity(candidate)})


def _records(value: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get(key), list):
        return []
    return [item for item in value[key] if isinstance(item, dict)]


def _artifact(root: Path, relative: str) -> Any:
    path = root / relative
    if not path.is_file():
        return None
    return load_yaml(path)


def _change(kind: str, old: Any, new: Any, *, old_ids: list[str], new_ids: list[str], blocking: bool = False, severity: str = "MINOR", required_action: str = "Regenerate the production plan from the accepted handoff revision.") -> dict[str, Any] | None:
    old_hash = canonical_sha256(old) if old is not None else None
    new_hash = canonical_sha256(new) if new is not None else None
    if old_hash == new_hash:
        status = "UNCHANGED"
    elif old is None:
        status = "ADDED"
    elif new is None:
        status = "REMOVED"
    else:
        status = "CHANGED"
    return {
        "kind": kind,
        "status": status,
        "old_hash": old_hash,
        "new_hash": new_hash,
        "old_ids": sorted(set(old_ids)),
        "new_ids": sorted(set(new_ids)),
        "affected": {
            "deliverable_ids": [],
            "specification_ids": [],
            "task_ids": [],
            "output_ids": [],
            "test_ids": [],
            "evidence_ids": [],
            "observation_ids": [],
            "result_ids": [],
        },
        "severity": severity,
        "blocking": blocking,
        "required_action": required_action,
    }


def _id_list(value: Any, key: str = "id") -> list[str]:
    if isinstance(value, dict) and key in value:
        nested = value[key]
        if isinstance(nested, list):
            return _id_list(nested, "id")
        if isinstance(nested, dict):
            return _id_list(nested, "id")
        if isinstance(nested, str):
            return [nested]
    if isinstance(value, list):
        if key == "id":
            return [item if isinstance(item, str) else str(item["id"]) for item in value if isinstance(item, str) or (isinstance(item, dict) and isinstance(item.get("id"), str))]
        return [str(item[key]) for item in value if isinstance(item, dict) and isinstance(item.get(key), str)]
    return []


def _selection_ids(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    identifiers: list[str] = []
    for key in ("selected_hypothesis_id", "selected_id"):
        if isinstance(value.get(key), str):
            identifiers.append(value[key])
    for key in ("alternative_hypothesis_ids", "source_decision_ids", "acceptance_test_ids"):
        identifiers.extend(str(item) for item in value.get(key, []) if isinstance(item, str))
    return identifiers


def _impact_ids(project: Path, old_plan: dict[str, Any] | None, new_plan: dict[str, Any]) -> dict[str, list[str]]:
    raw = {
        "deliverable_ids": _id_list(old_plan, "deliverables") + _id_list(new_plan, "deliverables"),
        "specification_ids": _id_list(old_plan, "technical_specifications") + _id_list(new_plan, "technical_specifications"),
        "task_ids": _id_list(old_plan, "tasks") + _id_list(new_plan, "tasks"),
        "output_ids": _id_list(_artifact(project / "05_execution", "output-versions.yaml"), "records"),
        "test_ids": _id_list(old_plan, "acceptance_tests") + _id_list(new_plan, "acceptance_tests"),
        "evidence_ids": _id_list(_artifact(project / "05_execution", "evidence-register.yaml"), "records"),
        "observation_ids": _id_list(_artifact(project / "05_execution", "observations.yaml"), "records"),
        "result_ids": _id_list(_artifact(project / "08_runtime", "production-result.yaml"), "result_id"),
    }
    return {key: sorted(set(values)) for key, values in raw.items()}


def _impact_report(project: Path, candidate_root: Path, current_handoff: dict[str, Any], candidate: dict[str, Any], old_plan: dict[str, Any] | None, new_plan: dict[str, Any], current_manifest: dict[str, Any], candidate_manifest: dict[str, Any], *, generated_at: str, current_bundle_manifest: dict[str, Any] | None = None, prior_result_sha256: str | None = None) -> dict[str, Any]:
    old_bundle = project / HISTORY_ROOT / _key(str(current_handoff["handoff_id"]), int(current_handoff["revision"])) / "source-bundle"
    candidate_fields = {
        "requirements": _artifact(candidate_root, "artifacts/production-requirements.yaml"),
        "selected_hypothesis": _artifact(candidate_root, "artifacts/production-hypotheses.yaml"),
        "prototype_plans": _artifact(candidate_root, "artifacts/prototype-plans.yaml"),
        "acceptance_tests": _artifact(candidate_root, "artifacts/acceptance-tests.yaml"),
    }
    old_fields = {
        "requirements": _artifact(old_bundle, "artifacts/production-requirements.yaml"),
        "selected_hypothesis": _artifact(old_bundle, "artifacts/production-hypotheses.yaml"),
        "prototype_plans": _artifact(old_bundle, "artifacts/prototype-plans.yaml"),
        "acceptance_tests": _artifact(old_bundle, "artifacts/acceptance-tests.yaml"),
    }
    candidate_fields["selection"] = candidate.get("selection")
    old_fields["selection"] = current_handoff.get("selection")
    candidate_fields["constraints"] = candidate.get("constraints")
    old_fields["constraints"] = current_handoff.get("constraints")
    candidate_fields["open_gaps"] = candidate.get("open_gaps")
    old_fields["open_gaps"] = current_handoff.get("open_gaps")
    candidate_fields["source_provenance"] = {"source_commit": candidate.get("research_commit"), "schema_version": candidate.get("schema_version"), "manifest_hash": candidate_manifest.get("integrity", {}).get("file_set_sha256")}
    old_fields["source_provenance"] = {"source_commit": current_handoff.get("research_commit"), "schema_version": current_handoff.get("schema_version"), "manifest_hash": (current_bundle_manifest or {}).get("integrity", {}).get("file_set_sha256")}
    affected = _impact_ids(project, old_plan, new_plan)
    changes: list[dict[str, Any]] = []
    for kind in ("requirements", "selected_hypothesis", "prototype_plans", "acceptance_tests", "selection", "constraints", "open_gaps", "source_provenance"):
        old = old_fields[kind]
        new = candidate_fields[kind]
        if kind == "selection":
            old_ids = _selection_ids(old)
            new_ids = _selection_ids(new)
        else:
            old_ids = _id_list(old, "id") or _id_list(old, "requirements") or _id_list(old, "prototype_plans") or _id_list(old, "acceptance_tests")
            new_ids = _id_list(new, "id") or _id_list(new, "requirements") or _id_list(new, "prototype_plans") or _id_list(new, "acceptance_tests")
        is_constraint = kind == "constraints"
        changed = canonical_sha256(old) != canonical_sha256(new)
        blocking = bool(changed and (kind in {"selection", "selected_hypothesis"} or is_constraint or (kind == "requirements" and any(item.get("priority") == "mandatory" for item in _records(old, "requirements") + _records(new, "requirements")))))
        severity = "MAJOR" if blocking else "MINOR"
        if is_constraint and isinstance(old, dict) and isinstance(new, dict):
            for field in ("rights", "safety", "privacy", "prohibited_actions"):
                if not set(new.get(field, [])) >= set(old.get(field, [])):
                    blocking = True
                    severity = "CRITICAL"
        change = _change(kind, old, new, old_ids=old_ids, new_ids=new_ids, blocking=blocking, severity=severity, required_action="Human review is required before the revised baseline can become production-ready." if blocking else "Regenerate the production plan from the accepted handoff revision.")
        if change is not None:
            change["affected"] = copy.deepcopy(affected)
            changes.append(change)
    plan_change = _change("production_plan", old_plan, new_plan, old_ids=_id_list(old_plan, "tasks") + _id_list(old_plan, "gaps"), new_ids=_id_list(new_plan, "tasks") + _id_list(new_plan, "gaps"), blocking=True, severity="MAJOR", required_action="Use the candidate-derived plan and invalidate prior task readiness.")
    if plan_change is not None:
        plan_change["affected"] = {key: sorted(set(values)) for key, values in affected.items()}
        changes.append(plan_change)
    blocking_ids = [change["kind"] for change in changes if change["blocking"]]
    affected_ids = sorted({identifier for change in changes for identifier in change["old_ids"] + change["new_ids"]} | {identifier for values in affected.values() for identifier in values})
    report = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "report_id": f"IR{int(candidate.get('revision', 1)):03d}",
        "project_id": str(current_manifest.get("project_id")),
        "generated_at": generated_at,
        "current_handoff": {"handoff_id": current_handoff.get("handoff_id"), "revision": current_handoff.get("revision"), "content_sha256": current_handoff.get("integrity", {}).get("content_sha256")},
        "candidate_handoff": {"handoff_id": candidate.get("handoff_id"), "revision": candidate.get("revision"), "content_sha256": candidate.get("integrity", {}).get("content_sha256")},
        "changes": changes,
        "affected_ids": affected_ids,
        "blocking_change": bool(blocking_ids),
        "required_actions": ["Create or review the generated change request."] + (["Human review is required for safety, rights, privacy, selection, or mandatory requirement changes."] if blocking_ids else []),
        "status": "BLOCKING" if blocking_ids else "READY_FOR_REVIEW",
        "prior_result_sha256": prior_result_sha256,
    }
    report["integrity"] = {"content_sha256": canonical_sha256(report)}
    return report


def _next_cr(existing: list[dict[str, Any]]) -> str:
    numbers = [int(str(item.get("id", "CR000"))[2:]) for item in existing if str(item.get("id", "")).startswith("CR") and str(item.get("id", ""))[2:].isdigit()]
    return f"CR{(max(numbers, default=0) + 1):03d}"


def _change_request(report: dict[str, Any], plan: dict[str, Any], existing: list[dict[str, Any]]) -> dict[str, Any]:
    blocking = bool(report.get("blocking_change"))
    requirement_ids = [str(item) for item in plan.get("mandatory_requirement_ids", []) if isinstance(item, str)]
    affected = list(dict.fromkeys([str(report["report_id"]), *[str(item) for item in report.get("affected_ids", [])], str(plan.get("plan_id")), *requirement_ids]))
    return {
        "id": _next_cr(existing),
        "trigger": "OTHER",
        "requested_change": f"Accept handoff {report['candidate_handoff']['handoff_id']} revision {report['candidate_handoff']['revision']} as the new production baseline.",
        "affected_ids": affected,
        "source_requirement_ids": requirement_ids,
        "impact": {"classification": "MAJOR" if blocking else "MINOR", "artistic": "The accepted handoff selection and source baseline may change.", "technical": "The production plan and task graph are regenerated from the candidate handoff.", "budget_delta": None, "schedule_delta_days": 0, "rights_safety": "BLOCKING" if blocking else "REVIEW_REQUIRED"},
        "alternatives": [{"id": f"ALT{int(report['candidate_handoff']['revision']):03d}", "description": "Adopt the candidate handoff as a new explicitly versioned baseline.", "selected": True}],
        "research_review_required": blocking,
        "research_review_status": "PENDING" if blocking else "NOT_REQUIRED",
        "approval_required": "HUMAN" if blocking else "NONE",
        "approval_status": "PENDING" if blocking else "NOT_REQUIRED",
        "status": "PROPOSED" if blocking else "APPLIED",
        "baseline_ref": {"id": str(report["current_handoff"]["handoff_id"]), "revision": int(report["current_handoff"]["revision"]), "content_sha256": report["current_handoff"]["content_sha256"]},
        "trace_refs": list(dict.fromkeys([str(report["current_handoff"]["handoff_id"]), str(report["candidate_handoff"]["handoff_id"]), str(plan.get("plan_id")), *requirement_ids, str(report["report_id"])])),
    }


def _add_stale_baseline_gaps(old_plan: dict[str, Any] | None, new_plan: dict[str, Any]) -> list[str]:
    """Carry removed baseline dependencies into the new plan as blocking gaps."""

    if not isinstance(old_plan, dict):
        return []
    old_requirements = set(_id_list(old_plan, "mandatory_requirement_ids"))
    new_requirements = set(_id_list(new_plan, "mandatory_requirement_ids"))
    old_tasks = set(_id_list(old_plan, "tasks"))
    new_tasks = set(_id_list(new_plan, "tasks"))
    stale_requirements = sorted(old_requirements - new_requirements)
    stale_tasks = sorted(old_tasks - new_tasks)
    if not stale_requirements and not stale_tasks:
        return []
    existing_ids = [int(str(item.get("id", "GP000"))[2:]) for item in new_plan.get("gaps", []) if isinstance(item, dict) and str(item.get("id", "")).startswith("GP") and str(item.get("id", ""))[2:].isdigit()]
    gap_id = f"GP{max(existing_ids, default=100) + 1:03d}"
    source_refs = [*stale_requirements, *stale_tasks]
    new_plan.setdefault("gaps", []).append({
        "id": gap_id,
        "rule": "STALE_BASELINE",
        "statement": "The superseding handoff removed records used by the previous production baseline.",
        "impact": "The retained historical records remain auditable, but no current task may silently rely on the removed baseline.",
        "owner": "production",
        "blocking": True,
        "category": "MAJOR",
        "resolution_condition": "Review the retained prior baseline and explicitly resolve or replace every removed dependency in a later accepted revision.",
        "source_refs": sorted(set(source_refs)),
    })
    scope = new_plan.setdefault("scope_baseline", {})
    scope.setdefault("open_gap_ids", []).append(gap_id)
    scope["open_gap_ids"] = sorted(set(scope["open_gap_ids"]))
    new_plan["integrity"] = {"content_sha256": canonical_sha256({key: value for key, value in new_plan.items() if key != "integrity"})}
    return [gap_id]


def _runtime_revision_event(stage: Path, *, old_state: dict[str, Any], plan: dict[str, Any], report: dict[str, Any], occurred_at: str, actor_kind: str, actor_id: str, idempotency_key: str, change_request_id: str) -> None:
    runtime_log = stage / "08_runtime/run-log.jsonl"
    events = _read_runtime_events(runtime_log)
    from_state = str(old_state.get("state"))
    if from_state == "HANDOFF_VALIDATED":
        return
    allowed = {"PLANNING", "REVIEWING", "BLOCKED", "COMPLETE", "COMPLETE_WITH_GAPS"}
    if from_state not in allowed:
        raise _error("HANDOFF_REVISION_STATE", f"handoff revision cannot be accepted from lifecycle state {from_state!r}", file=stage / "08_runtime/production-state.json", remediation="Accept a revision only from HANDOFF_VALIDATED, PLANNING, REVIEWING, BLOCKED, COMPLETE, or COMPLETE_WITH_GAPS.")
    if from_state in {"REVIEWING", "COMPLETE", "COMPLETE_WITH_GAPS"} and actor_kind != "HUMAN":
        raise _error("HANDOFF_REVISION_AUTHORITY", "revision acceptance from review or terminal state requires a HUMAN actor", file=stage / "08_runtime/production-state.json", remediation="Have a human authority accept the superseding baseline explicitly.")
    if from_state == "BLOCKED":
        blocked = next((event for event in reversed(events) if event.get("type") == "PROJECT_STATE_TRANSITIONED" and (event.get("payload") or {}).get("to_state") == "BLOCKED"), None)
        blocked_payload = (blocked or {}).get("payload") or {}
        if blocked_payload.get("resume_state") != "PLANNING":
            raise _error("HANDOFF_REVISION_BLOCKED_RESUME", "a BLOCKED project can accept a revision only when its recorded resume_state is PLANNING", file=stage / "08_runtime/run-log.jsonl", remediation="Record a canonical BLOCKED event with resume_state=PLANNING before accepting the superseding handoff.")
        candidate_refs = set(report.get("affected_ids", [])) | {str(item) for item in plan.get("mandatory_requirement_ids", [])}
        missing = sorted(set(str(item) for item in blocked_payload.get("source_refs", [])) - candidate_refs)
        if missing:
            raise _error("HANDOFF_REVISION_BLOCKED_REFS", "candidate handoff and plan do not cover every recorded blocker source", file=stage / "08_runtime/run-log.jsonl", remediation="Include the blocker source in the candidate-derived requirement, task, gap, or impact report before resuming to planning.", context={"missing": missing})
    if from_state == "REVIEWING" and not change_request_id:
        raise _error("HANDOFF_REVISION_CHANGE_REQUEST", "revision from REVIEWING requires a generated change request", file=stage / "07_governance/change-requests.yaml", remediation="Generate and retain the revision change request before reopening the plan.")
    if from_state in {"COMPLETE", "COMPLETE_WITH_GAPS"} and not report.get("prior_result_sha256"):
        raise _error("HANDOFF_REVISION_RESULT", "terminal revision acceptance requires the retained prior result hash", file=stage / "08_runtime/production-result.yaml", remediation="Retain the prior production result and bind the revision event to its exact content hash.")
    payload = {
        "from_state": from_state,
        "to_state": "PLANNING",
        "handoff_id": report["candidate_handoff"]["handoff_id"],
        "handoff_revision": report["candidate_handoff"]["revision"],
        "old_handoff_sha256": report["current_handoff"]["content_sha256"],
        "new_handoff_sha256": report["candidate_handoff"]["content_sha256"],
        "impact_report_sha256": report["integrity"]["content_sha256"],
        "old_plan_sha256": next((change.get("old_hash") for change in report.get("changes", []) if change.get("kind") == "production_plan"), None),
        "new_plan_sha256": plan.get("integrity", {}).get("content_sha256"),
        "plan_id": plan.get("plan_id"),
        "change_request_id": change_request_id,
        "old_result_sha256": report.get("prior_result_sha256"),
        "idempotency_key": idempotency_key,
    }
    guard = {
        "schema_version": "1.0.0",
        "records": {},
    }
    for relative in ("manifest.yaml", "00_handoff/handoff-receipt.yaml", "00_handoff/production-handoff.yaml"):
        path = stage / relative
        if path == stage / "manifest.yaml":
            value = _mapping(path, rule="HANDOFF_REVISION_GUARD")
            value = {key: item for key, item in value.items() if key != "state"}
            guard["records"][relative] = canonical_sha256(value)
        else:
            value = _mapping(path, rule="HANDOFF_REVISION_GUARD")
            guard["records"][relative] = canonical_sha256(value)
    guard["records_sha256"] = canonical_sha256(guard["records"])
    payload["guard_evidence"] = guard
    event = _event(len(events) + 1, events[-1].get("event_sha256") if events else None, occurred_at, actor_kind, actor_id, idempotency_key, "HANDOFF_REVISION_ACCEPTED", payload)
    event["event_id"] = f"EVT{event['sequence']:06d}"
    event["event_sha256"] = event_sha256(event)
    _write_jsonl(runtime_log, [*events, event])
    state = _mapping(stage / "08_runtime/production-state.json", rule="HANDOFF_REVISION_RUNTIME")
    state.pop("state_sha256", None)
    state["state"] = "PLANNING"
    state["revision"] = event["sequence"]
    state["last_event_id"] = event["event_id"]
    state["last_event_hash"] = event["event_sha256"]
    state["plan_id"] = plan["plan_id"]
    state.pop("task_graph_sha256", None)
    state.pop("task_states", None)
    state["state_sha256"] = canonical_sha256({key: value for key, value in state.items() if key != "state_sha256"})
    stage_state = stage / "08_runtime/production-state.json"
    stage_state.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _read_runtime_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    return [event for _, event in load_jsonl(path)]


def _set_manifest_state(stage: Path, state: str) -> None:
    manifest = _mapping(stage / "manifest.yaml", rule="HANDOFF_REVISION_MANIFEST")
    manifest["state"] = state
    dump_yaml(manifest, stage / "manifest.yaml")


@contextmanager
def _lock(project: Path) -> Iterator[None]:
    lock_path = project.parent / f".{project.name}.handoff-revision.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _replace_project(staging: Path, target: Path) -> None:
    rollback = target.parent / f".{target.name}-handoff-rollback-{os.getpid()}"
    if rollback.exists():
        raise _error("HANDOFF_REVISION_ROLLBACK_PATH", "rollback path already exists", file=rollback, remediation="Remove the stale transaction rollback directory after inspecting it.")
    target.rename(rollback)
    try:
        staging.rename(target)
    except Exception:
        rollback.rename(target)
        raise
    shutil.rmtree(rollback)


def _validate_revision_files(stage: Path, repository: Path, report: dict[str, Any]) -> None:
    report_schema = load_schema(repository / "schemas/handoff-impact-report.schema.json")
    common = load_schema(repository / "schemas/common.schema.json")
    findings = validate_instance(report, report_schema, schema_path=repository / "schemas/handoff-impact-report.schema.json", common_schema=common)
    if findings:
        raise DiagnosticError(findings[0])
    expected = canonical_sha256({key: value for key, value in report.items() if key != "integrity"})
    if report.get("integrity", {}).get("content_sha256") != expected:
        raise _error("HANDOFF_IMPACT_INTEGRITY", "impact report hash does not match the canonical report", file=stage / "00_handoff/impact-reports", remediation="Regenerate the impact report from the candidate and current snapshots.")
    # Runtime replay is the final check for the staged event and preserved log.
    Runtime(stage, repository).replay()


def accept_handoff_revision(project: Path, candidate_path: Path, repository: Path, *, occurred_at: str, actor_kind: str, actor_id: str, idempotency_key: str) -> Path:
    project = project.resolve()
    repository = repository.resolve()
    if not project.is_dir():
        raise _error("HANDOFF_REVISION_PROJECT", "superseding handoff requires an existing production project", file=project, remediation="Create the initial project with tools/new_production.py before accepting a revision.")
    with _lock(project):
        manifest, receipt, current, events = _current_handoff(project, repository)
        with open_bundle(candidate_path, repository) as candidate_bundle:
            candidate = copy.deepcopy(candidate_bundle.handoff)
            candidate_identity = _identity(candidate)
            if candidate_identity == _identity(current):
                return project
            if candidate_identity[:2] == _identity(current)[:2] and candidate_identity[2] != _identity(current)[2]:
                raise _error("HANDOFF_IDENTITY_CONFLICT", "candidate uses the current handoff ID and revision with different content", file=candidate_path, remediation="Reuse the original bundle for an idempotent retry or export the next revision with a supersedes reference.", context={"candidate": candidate_identity, "current": _identity(current)})
            _supersedes(candidate, _identity(current))
            history_identities = []
            for event in events:
                payload = event.get("payload") or {}
                if all(key in payload for key in ("handoff_id", "revision", "handoff_sha256")):
                    history_identities.append((payload["handoff_id"], int(payload["revision"]), payload["handoff_sha256"]))
            if any(item[:2] == candidate_identity[:2] for item in history_identities):
                raise _error("HANDOFF_IDENTITY_CONFLICT", "candidate handoff identity is already present in immutable history with different content", file=candidate_path, remediation="Use the original bundle for an idempotent retry or export a new revision.", context={"candidate": candidate_identity, "history": history_identities})
        original_state = _mapping(project / "08_runtime/production-state.json", rule="HANDOFF_REVISION_RUNTIME")
        production_root = project.parent
        transaction_root = Path(tempfile.mkdtemp(prefix=f".{project.name}-handoff-revision-", dir=production_root))
        staging = transaction_root / project.name
        try:
            shutil.copytree(project, staging, copy_function=shutil.copy2)
            old_plan_path = staging / "03_plan/production-plan.yaml"
            old_plan = load_yaml(old_plan_path) if old_plan_path.is_file() else None
            if old_plan is not None and not isinstance(old_plan, dict):
                raise _error("HANDOFF_REVISION_PLAN", "current production plan must be a mapping", file=old_plan_path, remediation="Restore the current plan before receiving a revision.")
            current_bundle_manifest = load_yaml(staging / "00_handoff/source-bundle-manifest.yaml")
            _snapshot_previous(staging, current["handoff_id"], int(current["revision"]))
            history_events = _read_events(staging / HANDOFF_LOG)
            if not history_events:
                history_events = [_event(1, None, str(receipt["received_at"]), "SYSTEM", "handoff/migration", f"handoff/migration/{current['handoff_id']}/r{current['revision']}", "HANDOFF_ACCEPTED", {"handoff_id": current["handoff_id"], "revision": current["revision"], "handoff_sha256": current["integrity"]["content_sha256"], "receipt_id": receipt["receipt_id"]})]
                _write_jsonl(staging / HANDOFF_LOG, history_events)
            source_bundle = staging / "00_handoff/source-bundle"
            if source_bundle.exists():
                shutil.rmtree(source_bundle)
            with open_bundle(candidate_path, repository) as candidate_bundle:
                candidate_bundle.copy_to(source_bundle)
                candidate_manifest = copy.deepcopy(candidate_bundle.manifest)
            shutil.copyfile(source_bundle / "manifest.yaml", staging / "00_handoff/source-bundle-manifest.yaml")
            shutil.copyfile(source_bundle / candidate_manifest["entrypoint"], staging / "00_handoff/production-handoff.yaml")
            from tools.build_plan import main as build_plan_main
            from tools.build_prototype import main as build_prototype_main
            from tools.lib.planning import validate_plan_document

            plan_revision = int(old_plan.get("plan_revision", 0)) + 1 if isinstance(old_plan, dict) else 1
            if build_plan_main(["--project-root", str(staging), "--plan-revision", str(plan_revision)]) != 0:
                raise _error("HANDOFF_REVISION_PLAN_BUILD", "candidate handoff plan generation failed", file=staging / "03_plan", remediation="Correct the candidate bundle diagnostics before retrying; the existing project was not changed.")
            new_plan = _mapping(staging / "03_plan/production-plan.yaml", rule="HANDOFF_REVISION_PLAN")
            stale_gap_ids = _add_stale_baseline_gaps(old_plan, new_plan)
            if stale_gap_ids:
                from tools.build_plan import _write_outputs

                _write_outputs(staging, new_plan)
            if build_prototype_main(["--project-root", str(staging), "--control-revision", str(int((load_yaml(project / "04_prototype/prototype-control.yaml").get("control_revision", 0) if (project / "04_prototype/prototype-control.yaml").is_file() else 0)) + 1)]) != 0:
                raise _error("HANDOFF_REVISION_PROTOTYPE_BUILD", "candidate handoff prototype control generation failed", file=staging / "04_prototype", remediation="Correct the candidate plan or prototype inputs before retrying.")
            new_plan_findings = validate_plan_document(new_plan, repository=repository, plan_path=staging / "03_plan/production-plan.yaml")
            if new_plan_findings:
                raise DiagnosticError(new_plan_findings[0])
            old_result_sha256 = None
            existing_result = staging / "08_runtime/production-result.yaml"
            if existing_result.is_file():
                existing_result_value = load_yaml(existing_result)
                if isinstance(existing_result_value, dict):
                    old_result_sha256 = (existing_result_value.get("integrity") or {}).get("content_sha256")
            report = _impact_report(staging, source_bundle, current, candidate, old_plan, new_plan, manifest, candidate_manifest, generated_at=occurred_at, current_bundle_manifest=current_bundle_manifest, prior_result_sha256=old_result_sha256)
            report_path = staging / "00_handoff/impact-reports" / f"{_key(candidate_identity[0], candidate_identity[1])}.yaml"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            dump_yaml(report, report_path)
            existing_changes = _records(_mapping(staging / "07_governance/change-requests.yaml", rule="HANDOFF_REVISION_GOVERNANCE"), "change_requests") if (staging / "07_governance/change-requests.yaml").is_file() else []
            change = _change_request(report, new_plan, existing_changes)
            dump_yaml({"change_requests": [*existing_changes, change]}, staging / "07_governance/change-requests.yaml")
            receipt_id = f"RC{candidate_identity[1]:03d}"
            candidate_receipt = _receipt(candidate, occurred_at, receipt_id)
            dump_yaml(candidate_receipt, staging / "00_handoff/handoff-receipt.yaml")
            manifest = _mapping(staging / "manifest.yaml", rule="HANDOFF_REVISION_MANIFEST")
            manifest["handoff"] = {"handoff_id": candidate_identity[0], "revision": candidate_identity[1], "content_sha256": candidate_identity[2], "receipt_status": "ACCEPTED"}
            dump_yaml(manifest, staging / "manifest.yaml")
            # Preserve the previous result as history and leave an OPEN current projection.
            result = staging / "08_runtime/production-result.yaml"
            completion = staging / "08_runtime/completion-report.json"
            if result.is_file():
                _snapshot_file(result, staging / "08_runtime/history" / _key(current["handoff_id"], int(current["revision"])) / "production-result.yaml")
                result.unlink()
            if completion.is_file():
                _snapshot_file(completion, staging / "08_runtime/history" / _key(current["handoff_id"], int(current["revision"])) / "completion-report.json")
            completion.write_text(json.dumps({"status": "OPEN", "project_id": manifest["project_id"]}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            # build_plan rewrites runtime projections; restore the old append-only log
            # and state, then append the revision event that resets task readiness.
            old_runtime = staging / "00_handoff/history" / _key(current["handoff_id"], int(current["revision"])) / "08_runtime"
            for name in ("production-state.json", "run-log.jsonl"):
                source = old_runtime / name
                if source.is_file():
                    shutil.copy2(source, staging / "08_runtime" / name)
            _runtime_revision_event(staging, old_state=original_state, plan=new_plan, report=report, occurred_at=occurred_at, actor_kind=actor_kind, actor_id=actor_id, idempotency_key=idempotency_key, change_request_id=change["id"])
            if original_state.get("state") == "HANDOFF_VALIDATED":
                _set_manifest_state(staging, "HANDOFF_VALIDATED")
            else:
                _set_manifest_state(staging, "PLANNING")
            # Update the current receipt projection and append the immutable history event.
            history_events = _read_events(staging / HANDOFF_LOG)
            current_receipts: list[dict[str, Any]] = []
            if (staging / HANDOFF_RECEIPTS).is_file():
                current_receipts = _records(load_yaml(staging / HANDOFF_RECEIPTS), "receipts")
            if not current_receipts:
                current_receipts = [receipt]
            current_receipts = [item for item in current_receipts if (item.get("handoff_id"), item.get("revision")) != candidate_identity[:2]]
            current_receipts.append(candidate_receipt)
            receipt_projection = _receipt_projection(manifest["project_id"], current_receipts, {"handoff_id": candidate_identity[0], "revision": candidate_identity[1], "handoff_sha256": candidate_identity[2], "receipt_id": receipt_id})
            dump_yaml(receipt_projection, staging / HANDOFF_RECEIPTS)
            _snapshot_dir(source_bundle, staging / "00_handoff/source-bundles" / _key(candidate_identity[0], candidate_identity[1]))
            handoff_event = _event(len(history_events) + 1, history_events[-1].get("event_sha256") if history_events else None, occurred_at, actor_kind, actor_id, idempotency_key, "HANDOFF_REVISION_ACCEPTED", {"handoff_id": candidate_identity[0], "revision": candidate_identity[1], "handoff_sha256": candidate_identity[2], "supersedes": {"handoff_id": current["handoff_id"], "revision": current["revision"], "handoff_sha256": current["integrity"]["content_sha256"]}, "impact_report_sha256": report["integrity"]["content_sha256"], "plan_sha256": new_plan["integrity"]["content_sha256"]})
            _write_jsonl(staging / HANDOFF_LOG, [*history_events, handoff_event])
            _validate_revision_files(staging, repository, report)
            from tools.validate import validate_project
            findings = validate_project(staging, repository)
            if findings:
                raise DiagnosticError(findings[0])
            _replace_project(staging, project)
            shutil.rmtree(transaction_root, ignore_errors=True)
            transaction_root = Path()
            return project
        finally:
            if transaction_root != Path() and transaction_root.exists():
                shutil.rmtree(transaction_root, ignore_errors=True)


def validate_handoff_history(project: Path, repository: Path) -> list[Finding]:
    """Validate history projections when a revision transaction has created them."""

    log_path = project / HANDOFF_LOG
    receipts_path = project / HANDOFF_RECEIPTS
    if not log_path.exists() and not receipts_path.exists():
        return []
    try:
        events = _read_events(log_path)
        if not events:
            raise _error("HANDOFF_HISTORY_EMPTY", "handoff history projection exists without a canonical event", file=log_path, remediation="Restore the initial migration event or remove the incomplete transaction output.")
        receipt_projection = _mapping(receipts_path, rule="HANDOFF_RECEIPTS")
        schema = load_schema(repository / "schemas/handoff-receipts.schema.json")
        common = load_schema(repository / "schemas/common.schema.json")
        findings = validate_instance(receipt_projection, schema, schema_path=repository / "schemas/handoff-receipts.schema.json", common_schema=common)
        if findings:
            return findings
        expected_receipts_hash = canonical_sha256({key: value for key, value in receipt_projection.items() if key != "integrity"})
        if receipt_projection.get("integrity", {}).get("content_sha256") != expected_receipts_hash:
            raise _error("HANDOFF_RECEIPTS_INTEGRITY", "handoff-receipts.yaml integrity does not match its canonical projection", file=receipts_path, location="/integrity/content_sha256", remediation="Regenerate the receipt projection from the immutable handoff history; do not edit it in place.")
        manifest, receipt, handoff, _ = _current_handoff(project, repository)
        current = receipt_projection.get("current", {})
        if (current.get("handoff_id"), current.get("revision"), current.get("handoff_sha256")) != (handoff.get("handoff_id"), handoff.get("revision"), handoff.get("integrity", {}).get("content_sha256")):
            raise _error("HANDOFF_RECEIPTS_CURRENT", "handoff-receipts.yaml current projection does not match current receipt", file=receipts_path, remediation="Regenerate the receipt projection from handoff-log.jsonl.")
        history_identities = {
            (payload.get("handoff_id"), payload.get("revision"), payload.get("handoff_sha256"))
            for event in events
            for payload in [event.get("payload") or {}]
            if isinstance(payload.get("handoff_id"), str) and payload.get("revision") is not None and isinstance(payload.get("handoff_sha256"), str)
        }
        projected_identities = {(item.get("handoff_id"), item.get("revision"), item.get("handoff_sha256")) for item in receipt_projection.get("receipts", [])}
        if not projected_identities.issubset(history_identities):
            raise _error("HANDOFF_RECEIPTS_HISTORY", "receipt projection contains an identity that is absent from the append-only handoff history", file=receipts_path, remediation="Restore the receipt projection generated by the handoff writer.", context={"projected": sorted(projected_identities), "history": sorted(history_identities)})
        impact_schema = load_schema(repository / "schemas/handoff-impact-report.schema.json")
        for report_path in sorted((project / "00_handoff/impact-reports").glob("*.yaml")) if (project / "00_handoff/impact-reports").is_dir() else []:
            report = _mapping(report_path, rule="HANDOFF_IMPACT_REPORT")
            report_findings = validate_instance(report, impact_schema, schema_path=repository / "schemas/handoff-impact-report.schema.json", common_schema=common)
            if report_findings:
                return report_findings
            if report.get("integrity", {}).get("content_sha256") != canonical_sha256({key: value for key, value in report.items() if key != "integrity"}):
                raise _error("HANDOFF_IMPACT_INTEGRITY", "impact report integrity does not match its canonical content", file=report_path, location="/integrity/content_sha256", remediation="Regenerate the impact report in a staging transaction from the old and candidate baselines.")
        return []
    except DiagnosticError as exc:
        return [exc.finding]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return [_finding("HANDOFF_HISTORY_VALIDATION", str(exc), file=project / "00_handoff", remediation="Restore the immutable handoff history and replay it before continuing.")]
