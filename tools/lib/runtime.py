"""Replayable production lifecycle runtime with an append-only event log."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import fcntl

from .canonical import canonical_sha256, event_sha256
from .config import load_config
from .diagnostics import DiagnosticError, Finding
from .evidence import resolve_evidence_refs
from .planning import validate_plan_document
from .schema import load_schema, validate_instance
from .yaml_io import dump_yaml, load_json, load_jsonl, load_yaml


RUNTIME_EVENT_SCHEMA = "runtime-event.schema.json"
RUNTIME_STATE_SCHEMA = "runtime-state.schema.json"

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "HANDOFF_VALIDATED": {"PLANNING", "BLOCKED", "CANCELLED"},
    "PLANNING": {"READY_FOR_PROTOTYPE", "READY_FOR_PRODUCTION", "BLOCKED", "CANCELLED"},
    "READY_FOR_PROTOTYPE": {"PROTOTYPING", "BLOCKED", "CANCELLED"},
    "PROTOTYPING": {"REVIEWING", "BLOCKED", "CANCELLED"},
    "REVIEWING": {"PLANNING", "READY_FOR_PRODUCTION", "BLOCKED", "CANCELLED"},
    "READY_FOR_PRODUCTION": {"PRODUCING", "BLOCKED", "CANCELLED"},
    "PRODUCING": {"READY_FOR_INSTALLATION", "VALIDATING", "BLOCKED", "CANCELLED"},
    "READY_FOR_INSTALLATION": {"INSTALLING", "BLOCKED", "CANCELLED"},
    "INSTALLING": {"VALIDATING", "BLOCKED", "CANCELLED"},
    "VALIDATING": {"COMPLETE", "COMPLETE_WITH_GAPS", "BLOCKED", "CANCELLED"},
    "BLOCKED": {"HANDOFF_VALIDATED", "PLANNING", "READY_FOR_PROTOTYPE", "PROTOTYPING", "REVIEWING", "READY_FOR_PRODUCTION", "PRODUCING", "READY_FOR_INSTALLATION", "INSTALLING", "VALIDATING", "CANCELLED"},
    "COMPLETE": {"PLANNING"},
    "COMPLETE_WITH_GAPS": {"PLANNING"},
}


def _finding(rule: str, reason: str, *, file: Path | str, location: str | None = None, remediation: str) -> Finding:
    return Finding(rule, reason, file=str(file), location=location, remediation=remediation)


def _without_state_hash(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key != "state_sha256"}


def _event_identity(event: dict[str, Any]) -> dict[str, Any]:
    return {key: event[key] for key in ("type", "actor", "idempotency_key", "payload")}


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone offset")
    return parsed


def _is_expired(expires_at: str, now: str) -> bool:
    return _timestamp(expires_at) <= _timestamp(now)


class Runtime:
    """Single-writer lifecycle runtime for one materialized project."""

    def __init__(self, project_root: Path, repository: Path):
        self.project_root = project_root.resolve()
        self.repository = repository.resolve()
        self.runtime_root = self.project_root / "08_runtime"
        self.log_path = self.runtime_root / "run-log.jsonl"
        self.state_path = self.runtime_root / "production-state.json"
        self.lock_path = self.runtime_root / ".runtime.lock"

    def _common_schema(self) -> dict[str, Any]:
        return load_schema(self.repository / "schemas/common.schema.json")

    def _event_schema(self) -> dict[str, Any]:
        return load_schema(self.repository / "schemas" / RUNTIME_EVENT_SCHEMA)

    def _state_schema(self) -> dict[str, Any]:
        return load_schema(self.repository / "schemas" / RUNTIME_STATE_SCHEMA)

    def _runtime_schema_store(self) -> list[dict[str, Any]]:
        return [
            load_schema(self.repository / "schemas/runtime-task.schema.json"),
            load_schema(self.repository / "schemas/runtime-lease.schema.json"),
            load_schema(self.repository / "schemas/runtime-effect.schema.json"),
            load_schema(self.repository / "schemas/approval.schema.json"),
            load_schema(self.repository / "schemas/evidence-record.schema.json"),
        ]

    def _project_id(self) -> str:
        if self.state_path.is_file():
            state = load_json(self.state_path)
            if isinstance(state, dict) and isinstance(state.get("project_id"), str):
                return state["project_id"]
        manifest = load_yaml(self.project_root / "manifest.yaml")
        if isinstance(manifest, dict) and isinstance(manifest.get("project_id"), str):
            return manifest["project_id"]
        raise DiagnosticError(_finding("RUNTIME_PROJECT_ID", "project_id is missing from state and manifest", file=self.project_root, remediation="Regenerate the project manifest or runtime state."))

    def _baseline_state(self) -> dict[str, Any]:
        state = {
            "schema_version": "1.0.0",
            "project_id": self._project_id(),
            "state": "HANDOFF_VALIDATED",
            "revision": 0,
            "last_event_id": None,
            "last_event_hash": None,
        }
        state["state_sha256"] = canonical_sha256(_without_state_hash(state))
        return state

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            raise DiagnosticError(_finding("RUNTIME_STATE_MISSING", "production-state.json is missing", file=self.state_path, remediation="Materialize the project runtime state before starting the runtime."))
        state = load_json(self.state_path)
        if not isinstance(state, dict):
            raise DiagnosticError(_finding("RUNTIME_STATE_OBJECT", "production-state.json must be an object", file=self.state_path, remediation="Regenerate the runtime projection."))
        findings = validate_instance(
            state,
            self._state_schema(),
            schema_path=self.repository / "schemas" / RUNTIME_STATE_SCHEMA,
            common_schema=self._common_schema(),
            schema_store=self._runtime_schema_store(),
        )
        if findings:
            raise DiagnosticError(findings[0])
        expected_hash = canonical_sha256(_without_state_hash(state))
        if state.get("state_sha256") != expected_hash:
            raise DiagnosticError(
                _finding(
                    "RUNTIME_STATE_DIVERGENCE",
                    "production-state.json state_sha256 does not match its canonical projection",
                    file=self.state_path,
                    location="/state_sha256",
                    remediation="Do not edit production-state.json; restore the projection generated from the append-only event log.",
                )
            )
        return state

    def _read_events(self) -> list[dict[str, Any]]:
        if not self.log_path.is_file() or self.log_path.stat().st_size == 0:
            return []
        raw = self.log_path.read_bytes()
        if not raw.endswith(b"\n"):
            raise DiagnosticError(_finding("RUNTIME_PARTIAL_LINE", "run-log.jsonl does not end with a complete newline-terminated event", file=self.log_path, remediation="Do not truncate the log automatically; restore the complete final event or block the runtime."))
        records = load_jsonl(self.log_path)
        events: list[dict[str, Any]] = []
        for line_number, event in records:
            findings = validate_instance(event, self._event_schema(), schema_path=self.repository / "schemas" / RUNTIME_EVENT_SCHEMA, common_schema=self._common_schema())
            if findings:
                finding = findings[0]
                raise DiagnosticError(Finding(finding.rule, finding.reason, file=str(self.log_path), line=line_number, location=finding.location, remediation=finding.remediation, context=finding.context))
            events.append(event)
        return events

    def _check_transition_guard(self, from_state: str, to_state: str, payload: dict[str, Any], actor: dict[str, str]) -> None:
        if to_state not in ALLOWED_TRANSITIONS.get(from_state, set()):
            raise DiagnosticError(_finding("RUNTIME_ILLEGAL_TRANSITION", f"transition {from_state} -> {to_state} is not allowed", file=self.state_path, location="/state", remediation="Choose a legal lifecycle transition or record a BLOCKED state with a resolution condition."))
        if to_state == "BLOCKED":
            required = {"blocker", "impact", "owner", "resume_state", "resolution_condition"}
            missing = sorted(required - set(payload))
            if missing:
                raise DiagnosticError(_finding("RUNTIME_BLOCKER_GUARD", f"BLOCKED transition is missing: {', '.join(missing)}", file=self.state_path, location="/payload", remediation="Record blocker, impact, owner, resume_state, and resolution_condition."))
            if payload["resume_state"] not in ALLOWED_TRANSITIONS:
                raise DiagnosticError(_finding("RUNTIME_RESUME_STATE", "resume_state is not a lifecycle state", file=self.state_path, location="/payload/resume_state", remediation="Use a valid non-terminal lifecycle state as resume_state."))
        if from_state == "BLOCKED":
            evidence = payload.get("resolution_evidence")
            if not isinstance(evidence, list) or not evidence:
                raise DiagnosticError(_finding("RUNTIME_RESUME_EVIDENCE", "BLOCKED resume requires non-empty resolution_evidence", file=self.state_path, location="/payload/resolution_evidence", remediation="Record evidence that every blocking condition was resolved."))
            resolve_evidence_refs(self.project_root, self.repository, evidence, expected_targets={self._project_id()}, file=self.log_path)
            if payload.get("resume_state") != to_state:
                raise DiagnosticError(_finding("RUNTIME_RESUME_TARGET", "resume_state does not match the transition target", file=self.state_path, location="/payload/resume_state", remediation="Resume only to the recorded resume_state."))
        if to_state == "CANCELLED":
            if actor.get("kind") != "HUMAN" or not payload.get("reason") or not payload.get("retention_decision"):
                raise DiagnosticError(_finding("RUNTIME_CANCEL_GUARD", "CANCELLED requires HUMAN authority, reason, and retention_decision", file=self.state_path, remediation="Record an authorized cancellation decision and retention policy."))
        if to_state == "READY_FOR_PROTOTYPE":
            control_path = self.project_root / "04_prototype/prototype-control.yaml"
            if control_path.is_file():
                control = load_yaml(control_path)
                if not isinstance(control, dict) or control.get("state") != "READY_FOR_PROTOTYPE":
                    raise DiagnosticError(_finding("RUNTIME_PROTOTYPE_GATE", "prototype control is not READY_FOR_PROTOTYPE", file=control_path, location="/state", remediation="Resolve prototype tasks, resources, tests, and approvals before advancing."))
            elif not isinstance(payload.get("prototype_skip_decision"), dict):
                raise DiagnosticError(_finding("RUNTIME_PROTOTYPE_GATE", "prototype control is missing", file=self.project_root / "04_prototype", remediation="Build prototype control or record a reasoned skip decision."))
        if from_state == "PROTOTYPING" and to_state == "REVIEWING":
            evidence = payload.get("external_evidence_refs")
            if not isinstance(evidence, list) or not evidence:
                raise DiagnosticError(_finding("RUNTIME_REVIEW_EVIDENCE", "PROTOTYPING -> REVIEWING requires external evidence references", file=self.state_path, location="/payload/external_evidence_refs", remediation="Record external validation evidence or remain in PROTOTYPING."))
            prototype_targets = {self._project_id()}
            control = self.project_root / "04_prototype/prototype-control.yaml"
            if control.is_file():
                value = load_yaml(control)
                if isinstance(value, dict):
                    prototype_targets.update(str(run.get("id")) for run in value.get("runs", []) if isinstance(run, dict) and isinstance(run.get("id"), str))
            resolve_evidence_refs(self.project_root, self.repository, evidence, expected_targets=prototype_targets, file=self.log_path)
        if to_state == "PLANNING" and from_state == "REVIEWING":
            if payload.get("review_outcome") not in {"FAIL", "DEVIATION", "CHANGE_REQUEST"}:
                raise DiagnosticError(_finding("RUNTIME_REVIEW_OUTCOME", "REVIEWING -> PLANNING requires a FAIL, DEVIATION, or CHANGE_REQUEST outcome", file=self.state_path, location="/payload/review_outcome", remediation="Record the review outcome and related change request."))
        if to_state in {"COMPLETE", "COMPLETE_WITH_GAPS"}:
            evidence = payload.get("completion_evidence")
            if not isinstance(evidence, list) or not evidence:
                raise DiagnosticError(_finding("RUNTIME_COMPLETION_GUARD", "terminal completion requires completion_evidence", file=self.state_path, location="/payload/completion_evidence", remediation="Record substantiating evidence before completion."))
            resolve_evidence_refs(self.project_root, self.repository, evidence, expected_targets={self._project_id()}, file=self.log_path)
            if to_state == "COMPLETE_WITH_GAPS" and not payload.get("open_gap_ids"):
                raise DiagnosticError(_finding("RUNTIME_GAP_GUARD", "COMPLETE_WITH_GAPS requires open_gap_ids", file=self.state_path, location="/payload/open_gap_ids", remediation="Record each non-blocking gap and its resume condition."))
        if from_state in {"COMPLETE", "COMPLETE_WITH_GAPS"} and to_state == "PLANNING":
            if actor.get("kind") not in {"HUMAN", "SYSTEM"} or not payload.get("reopen_reason") or not payload.get("change_request_id"):
                raise DiagnosticError(_finding("RUNTIME_REOPEN_GUARD", "reopen requires authority, reopen_reason, and change_request_id", file=self.state_path, remediation="Create an authorized change request before reopening a terminal project."))

    def _apply_event(self, state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        payload = event.get("payload") or {}
        event_type = event.get("type")
        next_state = deepcopy(state)
        next_state["revision"] = event["sequence"]
        next_state["last_event_id"] = event["event_id"]
        next_state["last_event_hash"] = event["event_sha256"]
        if event_type == "PROJECT_STATE_TRANSITIONED":
            from_state = payload.get("from_state")
            to_state = payload.get("to_state")
            actor = event.get("actor") or {}
            if state["state"] != from_state:
                raise DiagnosticError(_finding("RUNTIME_STATE_CHAIN", f"event expects {from_state!r} but replay state is {state['state']!r}", file=self.log_path, location=f"/events/{event.get('sequence')}/payload/from_state", remediation="Restore the missing or reordered event before replaying."))
            self._check_transition_guard(from_state, to_state, payload, actor)
            next_state["state"] = to_state
            if isinstance(payload.get("plan_id"), str):
                next_state["plan_id"] = payload["plan_id"]
            return next_state

        task_states = next_state.setdefault("task_states", {})
        effects = next_state.setdefault("effects", {})
        next_state.setdefault("approvals", [])
        if event_type == "TASK_GRAPH_REGISTERED":
            graph_hash = payload.get("task_graph_sha256")
            incoming = payload.get("task_states")
            if not isinstance(graph_hash, str) or not isinstance(incoming, dict):
                raise DiagnosticError(_finding("RUNTIME_TASK_GRAPH_EVENT", "TASK_GRAPH_REGISTERED requires task_graph_sha256 and task_states", file=self.log_path, remediation="Regenerate the task graph from the validated production plan."))
            if state.get("task_graph_sha256") and state.get("task_graph_sha256") != graph_hash:
                raise DiagnosticError(_finding("RUNTIME_TASK_GRAPH_CONFLICT", "a different task graph is already registered", file=self.log_path, remediation="Create a new plan revision before changing the runtime task graph."))
            next_state["task_graph_sha256"] = graph_hash
            next_state["plan_id"] = payload["plan_id"]
            next_state["task_states"] = deepcopy(incoming)
            return next_state

        if event_type in {"TASK_CLAIMED", "TASK_HEARTBEAT", "TASK_LEASE_RECOVERED", "TASK_RETRY_SCHEDULED", "TASK_FAILED", "TASK_COMPLETED"}:
            task_id = payload.get("task_id")
            task = task_states.get(task_id)
            if not isinstance(task_id, str) or not isinstance(task, dict):
                raise DiagnosticError(_finding("RUNTIME_TASK_UNKNOWN", f"runtime event references unknown task {task_id!r}", file=self.log_path, remediation="Register the validated task graph before appending task events."))
            if event_type == "TASK_CLAIMED":
                if task.get("status") not in {"READY", "RETRY_WAITING"}:
                    raise DiagnosticError(_finding("RUNTIME_TASK_STATE_CHAIN", "TASK_CLAIMED must follow READY or RETRY_WAITING", file=self.log_path, remediation="Claim only an eligible task."))
                task["status"] = "RUNNING"
                task["attempt"] = payload["attempt"]
                task["lease"] = deepcopy(payload["lease"])
                task["retry_after"] = None
                task["last_error"] = None
            elif event_type == "TASK_HEARTBEAT":
                lease = task.get("lease")
                if task.get("status") != "RUNNING" or not isinstance(lease, dict) or lease.get("lease_token") != payload.get("lease_token"):
                    raise DiagnosticError(_finding("RUNTIME_LEASE_TOKEN", "TASK_HEARTBEAT does not match the active lease token", file=self.log_path, remediation="Use the current lease token owned by the worker."))
                task["lease"] = deepcopy(payload["lease"])
            elif event_type == "TASK_LEASE_RECOVERED":
                lease = task.get("lease")
                if task.get("status") != "RUNNING" or not isinstance(lease, dict) or lease.get("lease_token") != payload.get("lease_token"):
                    raise DiagnosticError(_finding("RUNTIME_LEASE_RECOVERY", "lease recovery does not match the active lease", file=self.log_path, remediation="Recover only the expired lease recorded for the task."))
                task["status"] = payload["resume_status"]
                task["lease"] = None
            elif event_type == "TASK_RETRY_SCHEDULED":
                lease = task.get("lease")
                if task.get("status") != "RUNNING" or not isinstance(lease, dict) or lease.get("lease_token") != payload.get("lease_token"):
                    raise DiagnosticError(_finding("RUNTIME_RETRY_LEASE", "retry request does not match the active lease", file=self.log_path, remediation="Retry only from the current worker lease."))
                task["status"] = "RETRY_WAITING"
                task["lease"] = None
                task["retry_after"] = payload["retry_after"]
                task["last_error"] = payload["reason"]
            elif event_type == "TASK_FAILED":
                lease = task.get("lease")
                if task.get("status") != "RUNNING" or not isinstance(lease, dict) or lease.get("lease_token") != payload.get("lease_token"):
                    raise DiagnosticError(_finding("RUNTIME_FAILURE_LEASE", "failure report does not match the active lease", file=self.log_path, remediation="Report failure only from the current worker lease."))
                task["status"] = "BLOCKED"
                task["lease"] = None
                task["last_error"] = payload["reason"]
            elif event_type == "TASK_COMPLETED":
                lease = task.get("lease")
                if task.get("status") != "RUNNING" or not isinstance(lease, dict) or lease.get("lease_token") != payload.get("lease_token"):
                    raise DiagnosticError(_finding("RUNTIME_COMPLETION_LEASE", "completion report does not match the active lease", file=self.log_path, remediation="Complete only from the current worker lease."))
                task["status"] = "DONE"
                task["lease"] = None
                task["retry_after"] = None
                task["completion_evidence_refs"] = deepcopy(payload.get("evidence_refs", []))
            return next_state

        if event_type == "APPROVAL_RECORDED":
            approval = payload.get("approval")
            if not isinstance(approval, dict):
                raise DiagnosticError(_finding("RUNTIME_APPROVAL_EVENT", "APPROVAL_RECORDED requires an approval object", file=self.log_path, remediation="Record a schema-valid approval record."))
            identity = (approval.get("approval_id"), approval.get("revision"))
            if any((item.get("approval_id"), item.get("revision")) == identity for item in next_state["approvals"]):
                raise DiagnosticError(_finding("RUNTIME_APPROVAL_IMMUTABLE", "approval_id and revision already exist in the runtime", file=self.log_path, remediation="Use a new approval revision instead of overwriting an immutable record."))
            next_state["approvals"].append(deepcopy(approval))
            next_state["approvals"].sort(key=lambda item: (str(item.get("approval_id")), int(item.get("revision", 0))))
            return next_state

        if event_type == "EFFECT_STARTED":
            effect = payload.get("effect")
            if not isinstance(effect, dict):
                raise DiagnosticError(_finding("RUNTIME_EFFECT_EVENT", "EFFECT_STARTED requires an effect object", file=self.log_path, remediation="Record a schema-valid effect intent before execution."))
            effect_key = effect.get("effect_key")
            if effect_key in effects:
                raise DiagnosticError(_finding("RUNTIME_EFFECT_DUPLICATE", "effect_key already exists in the runtime", file=self.log_path, remediation="Reuse the existing effect result or choose a new effect key for a changed target."))
            effects[effect_key] = deepcopy(effect)
            return next_state

        if event_type == "EFFECT_COMPLETED":
            effect_key = payload.get("effect_key")
            effect = effects.get(effect_key)
            if not isinstance(effect, dict) or effect.get("status") != "STARTED":
                raise DiagnosticError(_finding("RUNTIME_EFFECT_STATE", "EFFECT_COMPLETED requires a STARTED effect", file=self.log_path, remediation="Start the effect once and complete that exact intent."))
            if payload.get("status") == "SUCCEEDED":
                resolve_evidence_refs(self.project_root, self.repository, payload.get("evidence_refs"), expected_targets={str(effect_key), str(effect.get("task_id"))}, file=self.log_path)
            effect["status"] = payload["status"]
            effect["completed_at"] = payload["completed_at"]
            effect["evidence_refs"] = list(payload.get("evidence_refs", []))
            if payload.get("error_class"):
                effect["error_class"] = payload["error_class"]
            return next_state

        raise DiagnosticError(_finding("RUNTIME_EVENT_TYPE", f"unsupported runtime event type {event_type!r}", file=self.log_path, remediation="Use a runtime event type supported by RUNTIME-001 or RUNTIME-002."))

    def _replay_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        state = self._baseline_state()
        previous_hash: str | None = None
        for expected_sequence, event in enumerate(events, start=1):
            if event.get("sequence") != expected_sequence:
                raise DiagnosticError(_finding("RUNTIME_SEQUENCE", f"expected event sequence {expected_sequence}, got {event.get('sequence')}", file=self.log_path, location=f"/events/{expected_sequence}/sequence", remediation="Restore contiguous event ordering; do not renumber an audit log in place."))
            if event.get("previous_event_sha256") != previous_hash:
                raise DiagnosticError(_finding("RUNTIME_HASH_CHAIN", "previous_event_sha256 does not match the preceding event", file=self.log_path, location=f"/events/{expected_sequence}/previous_event_sha256", remediation="Restore the original append-only event chain."))
            if event_sha256(event) != event.get("event_sha256"):
                raise DiagnosticError(_finding("RUNTIME_EVENT_HASH", "event_sha256 does not match the canonical event payload", file=self.log_path, location=f"/events/{expected_sequence}/event_sha256", remediation="Reject the tampered event and restore the canonical log."))
            state = self._apply_event(state, event)
            previous_hash = event["event_sha256"]
        state["state_sha256"] = canonical_sha256(_without_state_hash(state))
        return state

    def replay(self) -> dict[str, Any]:
        events = self._read_events()
        expected = self._replay_events(events)
        actual = self._read_state()
        if actual != expected:
            raise DiagnosticError(_finding("RUNTIME_STATE_DIVERGENCE", "materialized state does not match replayed event log", file=self.state_path, remediation="Do not edit production-state.json; replay the append-only log or restore the matching projection."))
        return expected

    @contextmanager
    def _writer_lock(self) -> Iterator[None]:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _write_state(self, state: dict[str, Any]) -> None:
        state = dict(state)
        state["state_sha256"] = canonical_sha256(_without_state_hash(state))
        payload = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        fd, temporary = tempfile.mkstemp(prefix=".production-state-", dir=self.runtime_root)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _sync_manifest(self, state: dict[str, Any]) -> None:
        manifest_path = self.project_root / "manifest.yaml"
        if not manifest_path.is_file():
            return
        manifest = load_yaml(manifest_path)
        if not isinstance(manifest, dict):
            return
        manifest["state"] = state["state"]
        fd, temporary = tempfile.mkstemp(prefix=".manifest-", dir=self.project_root)
        try:
            os.close(fd)
            dump_yaml(manifest, Path(temporary))
            with open(temporary, "rb") as output:
                os.fsync(output.fileno())
            os.replace(temporary, manifest_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _append_event(self, event: dict[str, Any]) -> None:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("ab") as output:
            output.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
            output.flush()
            os.fsync(output.fileno())

    def bootstrap(self, *, occurred_at: str, actor_kind: str, actor_id: str, idempotency_key: str = "runtime/bootstrap/1") -> dict[str, Any]:
        with self._writer_lock():
            events = self._read_events()
            if events:
                return self.replay()
            actual = self._read_state()
            baseline = self._baseline_state()
            if actual == baseline:
                return actual
            if actual.get("state") != "PLANNING":
                raise DiagnosticError(_finding("RUNTIME_BOOTSTRAP_STATE", "only a HANDOFF_VALIDATED or PLANNING projection can be bootstrapped", file=self.state_path, remediation="Resolve the lifecycle state explicitly before runtime bootstrap."))
            payload = {"from_state": "HANDOFF_VALIDATED", "to_state": "PLANNING", "reason": "Initialize the lifecycle event log from the accepted project and generated plan."}
            if isinstance(actual.get("plan_id"), str):
                payload["plan_id"] = actual["plan_id"]
            event = self._make_event(1, None, occurred_at, actor_kind, actor_id, idempotency_key, payload)
            self._check_transition_guard("HANDOFF_VALIDATED", "PLANNING", payload, event["actor"])
            self._append_event(event)
            projection = self._replay_events([event])
            self._write_state(projection)
            self._sync_manifest(projection)
            return projection

    def _make_event(self, sequence: int, previous_hash: str | None, occurred_at: str, actor_kind: str, actor_id: str, idempotency_key: str, payload: dict[str, Any], event_type: str = "PROJECT_STATE_TRANSITIONED") -> dict[str, Any]:
        event = {
            "event_id": f"EVT{sequence:06d}",
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

    def _checked_runtime(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        events = self._read_events()
        state = self._replay_events(events)
        actual = self._read_state()
        if actual != state:
            raise DiagnosticError(_finding("RUNTIME_STATE_DIVERGENCE", "materialized state does not match replayed event log", file=self.state_path, remediation="Do not edit production-state.json; replay the append-only log or restore the matching projection."))
        return events, state

    def _check_event_idempotency(self, events: list[dict[str, Any]], event: dict[str, Any]) -> bool:
        for existing in events:
            if existing.get("idempotency_key") != event["idempotency_key"]:
                continue
            if _event_identity(existing) == _event_identity(event):
                return True
            raise DiagnosticError(_finding("RUNTIME_IDEMPOTENCY_MISMATCH", "idempotency key was already used with different runtime content", file=self.log_path, remediation="Reuse the original request content or choose a new idempotency key."))
        return False

    def _commit_event(self, events: list[dict[str, Any]], event: dict[str, Any]) -> dict[str, Any]:
        self._append_event(event)
        projection = self._replay_events([*events, event])
        self._write_state(projection)
        self._sync_manifest(projection)
        return projection

    def _load_plan(self) -> dict[str, Any]:
        plan_path = self.project_root / "03_plan/production-plan.yaml"
        if not plan_path.is_file():
            raise DiagnosticError(_finding("RUNTIME_PLAN_MISSING", "production plan is required for task runtime", file=plan_path, remediation="Build and validate a production plan before initializing task execution."))
        plan = load_yaml(plan_path)
        if not isinstance(plan, dict):
            raise DiagnosticError(_finding("RUNTIME_PLAN_OBJECT", "production plan must be a mapping", file=plan_path, remediation="Regenerate production-plan.yaml from structured planning records."))
        findings = validate_plan_document(plan, repository=self.repository, plan_path=plan_path)
        if findings:
            raise DiagnosticError(findings[0])
        return plan

    def _task_graph(self, plan: dict[str, Any]) -> tuple[str, dict[str, dict[str, Any]]]:
        limits = load_config(self.repository, "stopping-policy.yaml")
        runtime_policy = load_config(self.repository, "runtime-policy.yaml")
        max_attempts = 1 + int(limits.get("max_retry_attempts", 0))
        default_priority = int(runtime_policy.get("default_task_priority", 100))
        task_states: dict[str, dict[str, Any]] = {}
        for task in plan.get("tasks", []):
            task_id = str(task["id"])
            initial_status = task["status"]
            # Planning marks external-effect tasks BLOCKED until runtime approval exists.
            # Runtime keeps that gate in eligibility, so the execution projection can
            # become READY without treating the approval as already granted.
            if initial_status == "BLOCKED" and task.get("approval_requirement_ids"):
                initial_status = "READY"
            task_states[task_id] = {
                "task_id": task_id,
                "status": initial_status,
                "depends_on": list(task.get("depends_on", [])),
                "required_resource_ids": list(task.get("required_resource_ids", [])),
                "required_material_ids": list(task.get("required_material_ids", [])),
                "effect_type": task["effect_type"],
                "approval_requirement_ids": list(task.get("approval_requirement_ids", [])),
                "priority": int(task.get("priority", default_priority)),
                "earliest_start": task.get("earliest_start"),
                "attempt": 0,
                "max_attempts": max_attempts,
                "retry_after": None,
                "lease": None,
                "last_error": None,
                "completion_evidence_refs": [],
            }
        graph_hash = canonical_sha256({"plan_id": plan["plan_id"], "plan_revision": plan["plan_revision"], "task_states": task_states})
        return graph_hash, task_states

    def initialize_task_graph(self, *, occurred_at: str, actor_kind: str, actor_id: str, idempotency_key: str = "runtime/task-graph/1") -> dict[str, Any]:
        with self._writer_lock():
            events, state = self._checked_runtime()
            plan = self._load_plan()
            graph_hash, task_states = self._task_graph(plan)
            if state.get("task_graph_sha256"):
                if state["task_graph_sha256"] == graph_hash:
                    return state
                raise DiagnosticError(_finding("RUNTIME_TASK_GRAPH_CONFLICT", "the task graph is already initialized for another plan revision", file=self.state_path, remediation="Create a new plan revision and explicitly reopen the runtime before changing task definitions."))
            payload = {"plan_id": plan["plan_id"], "plan_revision": plan["plan_revision"], "task_graph_sha256": graph_hash, "task_states": task_states}
            event = self._make_event(state["revision"] + 1, state["last_event_hash"], occurred_at, actor_kind, actor_id, idempotency_key, payload, "TASK_GRAPH_REGISTERED")
            if self._check_event_idempotency(events, event):
                return state
            return self._commit_event(events, event)

    def _task(self, state: dict[str, Any], task_id: str) -> dict[str, Any]:
        task = state.get("task_states", {}).get(task_id)
        if not isinstance(task, dict):
            raise DiagnosticError(_finding("RUNTIME_TASK_UNKNOWN", f"unknown task {task_id!r}", file=self.state_path, remediation="Use a task ID from the registered production plan."))
        return task

    def _plan_resource_maps(self, plan: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        resources = {str(item["id"]): item for item in plan.get("resources", [])}
        materials = {str(item["id"]): item for item in plan.get("materials", [])}
        requirements = {str(item["id"]): item for item in plan.get("approval_register", {}).get("requirements", [])}
        return resources, materials, requirements

    def _latest_approvals(self, state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for approval in state.get("approvals", []):
            approval_id = str(approval.get("approval_id"))
            if approval_id not in latest or int(approval.get("revision", 0)) > int(latest[approval_id].get("revision", 0)):
                latest[approval_id] = approval
        return latest

    def _valid_approval_ids(self, state: dict[str, Any], requirement_ids: list[str], *, occurred_at: str, plan: dict[str, Any]) -> list[str]:
        _, _, requirements = self._plan_resource_maps(plan)
        latest = self._latest_approvals(state)
        valid_ids: list[str] = []
        for requirement_id in requirement_ids:
            requirement = requirements.get(requirement_id)
            if not isinstance(requirement, dict):
                raise DiagnosticError(_finding("RUNTIME_APPROVAL_REQUIREMENT", f"approval requirement {requirement_id!r} is missing from the plan", file=self.project_root / "03_plan/production-plan.yaml", remediation="Regenerate the approval register with every task requirement."))
            candidates = [approval for approval in latest.values() if approval.get("scope", {}).get("target_ref") == requirement.get("target_ref")]
            if any(approval.get("scope", {}).get("target_sha256") != requirement.get("target_sha256") for approval in candidates):
                raise DiagnosticError(_finding("RUNTIME_APPROVAL_HASH_MISMATCH", f"approval target hash does not match requirement {requirement_id}", file=self.project_root / "03_plan/production-plan.yaml", remediation="Issue a new approval for the exact target revision hash."))
            matching = [approval for approval in candidates if approval.get("decision") == "APPROVED" and approval.get("scope", {}).get("action") == requirement.get("action") and approval.get("scope", {}).get("target_sha256") == requirement.get("target_sha256") and approval.get("approver", {}).get("authority") == requirement.get("authority")]
            if not matching:
                if any(approval.get("decision") in {"DENIED", "REVOKED"} for approval in candidates):
                    raise DiagnosticError(_finding("RUNTIME_APPROVAL_REVOKED", f"approval for requirement {requirement_id} is denied or revoked", file=self.project_root / "03_plan/production-plan.yaml", remediation="Obtain a new, non-revoked approval revision for the exact target."))
                raise DiagnosticError(_finding("RUNTIME_APPROVAL_REQUIRED", f"valid approval for requirement {requirement_id} is missing", file=self.project_root / "03_plan/production-plan.yaml", remediation="Record an unexpired approval with matching action, target_ref, target_sha256, and authority."))
            approval = matching[-1]
            if _timestamp(approval["issued_at"]) > _timestamp(occurred_at) or _timestamp(approval["expires_at"]) <= _timestamp(occurred_at):
                raise DiagnosticError(_finding("RUNTIME_APPROVAL_EXPIRED", f"approval {approval['approval_id']} is not valid at effect time", file=self.project_root / "03_plan/production-plan.yaml", remediation="Use an approval whose issued and expiry window covers the effect timestamp."))
            valid_ids.append(str(approval["approval_id"]))
        return valid_ids

    def _check_effect_target(self, task: dict[str, Any], plan: dict[str, Any], *, target_ref: str, target_sha256: str) -> None:
        """Require an effect intent to name the exact approved target revision."""
        requirement_ids = list(task.get("approval_requirement_ids", []))
        if not requirement_ids:
            return
        _, _, requirements = self._plan_resource_maps(plan)
        for requirement_id in requirement_ids:
            requirement = requirements.get(requirement_id)
            if not isinstance(requirement, dict):
                raise DiagnosticError(_finding("RUNTIME_APPROVAL_REQUIREMENT", f"approval requirement {requirement_id!r} is missing from the plan", file=self.project_root / "03_plan/production-plan.yaml", remediation="Regenerate the approval register with every task requirement."))
            if requirement.get("target_ref") != target_ref or requirement.get("target_sha256") != target_sha256:
                raise DiagnosticError(_finding("RUNTIME_APPROVAL_TARGET_MISMATCH", f"effect target does not match approval requirement {requirement_id}", file=self.state_path, remediation="Start the effect only for the exact target reference and content hash in the approved requirement."))

    def _recover_expired_lease_locked(self, events: list[dict[str, Any]], state: dict[str, Any], *, task_id: str, occurred_at: str, idempotency_key: str) -> dict[str, Any]:
        task = self._task(state, task_id)
        lease = task.get("lease") or {}
        if task.get("status") != "RUNNING" or not isinstance(lease, dict):
            raise DiagnosticError(_finding("RUNTIME_LEASE_RECOVERY", f"task {task_id} has no active lease to recover", file=self.state_path, remediation="Recover only a running task whose lease is still recorded."))
        if not _is_expired(str(lease.get("expires_at")), occurred_at):
            raise DiagnosticError(_finding("RUNTIME_LEASE_NOT_EXPIRED", f"task {task_id} lease has not expired", file=self.state_path, remediation="Wait until expires_at or use the current owner heartbeat."))
        unknown = [effect for effect in state.get("effects", {}).values() if effect.get("task_id") == task_id and effect.get("status") in {"STARTED", "UNKNOWN"}]
        if unknown:
            raise DiagnosticError(_finding("RUNTIME_EXTERNAL_OUTCOME_UNKNOWN", f"expired task {task_id} has an effect with unknown external outcome", file=self.state_path, remediation="Reconcile the external effect with evidence before retrying; automatic retry is forbidden."))
        payload = {"task_id": task_id, "lease_token": lease.get("lease_token"), "resume_status": "READY", "reason": "Lease expired without an in-flight or unknown effect."}
        event = self._make_event(state["revision"] + 1, state["last_event_hash"], occurred_at, "SYSTEM", "runtime/recovery", idempotency_key, payload, "TASK_LEASE_RECOVERED")
        if self._check_event_idempotency(events, event):
            return state
        return self._commit_event(events, event)

    def recover_expired_lease(self, *, task_id: str, occurred_at: str, idempotency_key: str) -> dict[str, Any]:
        """Release an expired lease after checking for unresolved effects."""
        if not idempotency_key:
            raise DiagnosticError(_finding("RUNTIME_IDEMPOTENCY_KEY", "idempotency_key is required", file=self.log_path, remediation="Provide a stable idempotency key for lease recovery."))
        with self._writer_lock():
            events, state = self._checked_runtime()
            return self._recover_expired_lease_locked(events, state, task_id=task_id, occurred_at=occurred_at, idempotency_key=idempotency_key)

    def _task_eligible(self, state: dict[str, Any], task: dict[str, Any], *, occurred_at: str, plan: dict[str, Any]) -> bool:
        if task.get("status") not in {"READY", "RETRY_WAITING"}:
            return False
        if task.get("retry_after") and _timestamp(task["retry_after"]) > _timestamp(occurred_at):
            return False
        task_states = state.get("task_states", {})
        if any(task_states.get(dependency, {}).get("status") not in {"DONE", "SKIPPED"} for dependency in task.get("depends_on", [])):
            return False
        resources, materials, _ = self._plan_resource_maps(plan)
        if any(resources.get(resource_id, {}).get("availability") not in {"AVAILABLE"} for resource_id in task.get("required_resource_ids", [])):
            return False
        if any(materials.get(material_id, {}).get("status") not in {"APPROVED"} for material_id in task.get("required_material_ids", [])):
            return False
        try:
            self._valid_approval_ids(state, list(task.get("approval_requirement_ids", [])), occurred_at=occurred_at, plan=plan)
        except DiagnosticError:
            return False
        if task.get("earliest_start") and _timestamp(task["earliest_start"]) > _timestamp(occurred_at):
            return False
        return True

    def next_task(self, *, occurred_at: str) -> str | None:
        events, state = self._checked_runtime()
        del events
        plan = self._load_plan()
        if not state.get("task_graph_sha256"):
            raise DiagnosticError(_finding("RUNTIME_TASK_GRAPH_MISSING", "task graph has not been initialized", file=self.state_path, remediation="Initialize the task graph from the validated production plan before selecting work."))
        eligible = [task for task in state.get("task_states", {}).values() if self._task_eligible(state, task, occurred_at=occurred_at, plan=plan)]
        eligible.sort(key=lambda task: (int(task.get("priority", 100)), task.get("earliest_start") or "\uffff", task["task_id"]))
        return eligible[0]["task_id"] if eligible else None

    def claim_task(self, *, task_id: str | None, occurred_at: str, actor_id: str, lease_token: str, expires_at: str, idempotency_key: str) -> dict[str, Any]:
        if not lease_token or not idempotency_key:
            raise DiagnosticError(_finding("RUNTIME_LEASE_INPUT", "lease_token and idempotency_key are required", file=self.log_path, remediation="Provide stable worker lease and idempotency identifiers."))
        if _timestamp(expires_at) <= _timestamp(occurred_at):
            raise DiagnosticError(_finding("RUNTIME_LEASE_EXPIRY", "lease expires_at must be after occurred_at", file=self.log_path, remediation="Set a future lease expiry or stop before claiming work."))
        with self._writer_lock():
            events, state = self._checked_runtime()
            plan = self._load_plan()
            existing = next((event for event in events if event.get("idempotency_key") == idempotency_key), None)
            if existing is not None:
                existing_payload = existing.get("payload") or {}
                existing_lease = existing_payload.get("lease") or {}
                if (
                    existing.get("type") == "TASK_CLAIMED"
                    and existing.get("actor") == {"kind": "AGENT", "id": actor_id}
                    and (task_id is None or existing_payload.get("task_id") == task_id)
                    and existing_lease.get("owner") == actor_id
                    and existing_lease.get("lease_token") == lease_token
                    and existing_lease.get("expires_at") == expires_at
                ):
                    return state
                raise DiagnosticError(_finding("RUNTIME_IDEMPOTENCY_MISMATCH", "idempotency key was already used with different task claim content", file=self.log_path, remediation="Reuse the original claim parameters or choose a new idempotency key."))
            if not state.get("task_graph_sha256"):
                raise DiagnosticError(_finding("RUNTIME_TASK_GRAPH_MISSING", "task graph has not been initialized", file=self.state_path, remediation="Initialize the task graph before claiming work."))
            if task_id is None:
                task_id = self.next_task(occurred_at=occurred_at)
                if task_id is None:
                    raise DiagnosticError(_finding("RUNTIME_NO_ELIGIBLE_TASK", "no task is currently eligible", file=self.state_path, remediation="Resolve dependencies, resources, approvals, retry windows, or blockers before claiming work."))
                # next_task replays the same immutable state; no event has been appended.
                events, state = self._checked_runtime()
            task = self._task(state, task_id)
            if task.get("status") == "RUNNING":
                if not _is_expired(str((task.get("lease") or {}).get("expires_at")), occurred_at):
                    raise DiagnosticError(_finding("RUNTIME_LEASE_CONFLICT", f"task {task_id} has an active lease", file=self.state_path, remediation="Wait for the current lease or use its heartbeat; do not steal active work."))
                state = self._recover_expired_lease_locked(events, state, task_id=task_id, occurred_at=occurred_at, idempotency_key=f"{idempotency_key}/recover")
                events, state = self._checked_runtime()
                task = self._task(state, task_id)
            if not self._task_eligible(state, task, occurred_at=occurred_at, plan=plan):
                raise DiagnosticError(_finding("RUNTIME_TASK_INELIGIBLE", f"task {task_id} is not eligible", file=self.state_path, remediation="Satisfy dependencies, resource availability, approval, retry, and safety gates before claiming."))
            attempt = int(task["attempt"]) + 1
            if attempt > int(task["max_attempts"]):
                raise DiagnosticError(_finding("RUNTIME_RETRY_LIMIT", f"task {task_id} has reached its retry limit", file=self.state_path, remediation="Record a permanent failure or create an explicit plan revision; do not exceed the configured retry limit."))
            lease = {"task_id": task_id, "owner": actor_id, "acquired_at": occurred_at, "expires_at": expires_at, "attempt": attempt, "lease_token": lease_token}
            payload = {"task_id": task_id, "attempt": attempt, "lease": lease}
            event = self._make_event(state["revision"] + 1, state["last_event_hash"], occurred_at, "AGENT", actor_id, idempotency_key, payload, "TASK_CLAIMED")
            if self._check_event_idempotency(events, event):
                return state
            return self._commit_event(events, event)

    def heartbeat(self, *, task_id: str, occurred_at: str, actor_id: str, lease_token: str, expires_at: str, idempotency_key: str) -> dict[str, Any]:
        if _timestamp(expires_at) <= _timestamp(occurred_at):
            raise DiagnosticError(_finding("RUNTIME_LEASE_EXPIRY", "heartbeat expires_at must be after occurred_at", file=self.log_path, remediation="Extend the lease into the future or stop the worker."))
        with self._writer_lock():
            events, state = self._checked_runtime()
            task = self._task(state, task_id)
            lease = task.get("lease") or {}
            if task.get("status") != "RUNNING" or lease.get("lease_token") != lease_token or lease.get("owner") != actor_id:
                raise DiagnosticError(_finding("RUNTIME_LEASE_TOKEN", "heartbeat does not match the active owner and lease token", file=self.state_path, remediation="Use the lease token and owner issued by TASK_CLAIMED."))
            if _is_expired(str(lease.get("expires_at")), occurred_at):
                raise DiagnosticError(_finding("RUNTIME_LEASE_EXPIRED", "heartbeat arrived after lease expiry", file=self.state_path, remediation="Reconcile the expired lease; a late heartbeat cannot revive ownership."))
            updated = dict(lease)
            updated["expires_at"] = expires_at
            payload = {"task_id": task_id, "lease_token": lease_token, "lease": updated}
            event = self._make_event(state["revision"] + 1, state["last_event_hash"], occurred_at, "AGENT", actor_id, idempotency_key, payload, "TASK_HEARTBEAT")
            if self._check_event_idempotency(events, event):
                return state
            return self._commit_event(events, event)

    def record_approval(self, *, approval: dict[str, Any], occurred_at: str, actor_kind: str, actor_id: str, idempotency_key: str) -> dict[str, Any]:
        approval_schema = load_schema(self.repository / "schemas/approval.schema.json")
        findings = validate_instance(approval, approval_schema, schema_path=self.repository / "schemas/approval.schema.json", common_schema=self._common_schema())
        if findings:
            raise DiagnosticError(findings[0])
        if _timestamp(approval["expires_at"]) <= _timestamp(approval["issued_at"]):
            raise DiagnosticError(_finding("RUNTIME_APPROVAL_WINDOW", "approval expires_at must be after issued_at", file=self.project_root / "07_governance", remediation="Issue an approval with a non-empty validity window."))
        target_ref = approval["scope"]["target_ref"]
        if any(marker in target_ref for marker in ("*", "?")):
            raise DiagnosticError(_finding("RUNTIME_APPROVAL_WILDCARD", "approval target_ref must identify one exact target", file=self.project_root / "07_governance", location="/scope/target_ref", remediation="Replace wildcard scope with an exact target reference and content hash."))
        expected_actor = "HUMAN" if approval["approver"]["authority"] == "HUMAN" else "SYSTEM"
        if actor_kind != expected_actor or actor_id != approval["approver"]["id"]:
            raise DiagnosticError(_finding("RUNTIME_APPROVAL_AUTHORITY", "approval actor kind does not match approver authority", file=self.project_root / "07_governance", remediation="Record HUMAN approvals through a human authority and policy approvals through SYSTEM."))
        with self._writer_lock():
            events, state = self._checked_runtime()
            payload = {"approval": deepcopy(approval)}
            event = self._make_event(state["revision"] + 1, state["last_event_hash"], occurred_at, actor_kind, actor_id, idempotency_key, payload, "APPROVAL_RECORDED")
            if self._check_event_idempotency(events, event):
                return state
            return self._commit_event(events, event)

    def _require_lease(self, state: dict[str, Any], task_id: str, actor_id: str, lease_token: str, occurred_at: str) -> dict[str, Any]:
        task = self._task(state, task_id)
        lease = task.get("lease") or {}
        if task.get("status") != "RUNNING" or lease.get("lease_token") != lease_token or lease.get("owner") != actor_id:
            raise DiagnosticError(_finding("RUNTIME_LEASE_TOKEN", "operation does not match the active owner and lease token", file=self.state_path, remediation="Use the current lease token and owner for the task."))
        if _is_expired(str(lease.get("expires_at")), occurred_at):
            raise DiagnosticError(_finding("RUNTIME_LEASE_EXPIRED", "operation arrived after lease expiry", file=self.state_path, remediation="Reconcile the expired lease before attempting further work."))
        return task

    def start_effect(self, *, task_id: str, occurred_at: str, actor_id: str, lease_token: str, effect_key: str, target_ref: str, target_sha256: str, idempotency_key: str) -> dict[str, Any]:
        with self._writer_lock():
            events, state = self._checked_runtime()
            plan = self._load_plan()
            task = self._require_lease(state, task_id, actor_id, lease_token, occurred_at)
            if task["effect_type"] not in {"READ_ONLY", "REPOSITORY_WRITE", "EXTERNAL_WRITE", "PHYSICAL_EXTERNAL", "PURCHASE", "CONTRACT", "PUBLICATION", "DELETION"}:
                raise DiagnosticError(_finding("RUNTIME_EFFECT_TYPE", "task effect type is not recognized", file=self.state_path, remediation="Use a configured effect type from the production plan."))
            existing = state.get("effects", {}).get(effect_key)
            if existing:
                if existing.get("target_ref") != target_ref or existing.get("target_sha256") != target_sha256:
                    raise DiagnosticError(_finding("RUNTIME_EFFECT_TARGET_MISMATCH", "effect key was previously used for a different target hash or reference", file=self.state_path, remediation="Do not reuse an effect key across target revisions."))
                if existing.get("status") == "SUCCEEDED":
                    return state
                raise DiagnosticError(_finding("RUNTIME_EFFECT_IN_FLIGHT", "effect key already has an unresolved result", file=self.state_path, remediation="Reconcile the existing effect before attempting another execution."))
            required = list(task.get("approval_requirement_ids", []))
            self._check_effect_target(task, plan, target_ref=target_ref, target_sha256=target_sha256)
            approval_ids = self._valid_approval_ids(state, required, occurred_at=occurred_at, plan=plan)
            policy = load_config(self.repository, "approval-policy.yaml")
            if task["effect_type"] in set(policy.get("human_required_effects", [])) and not approval_ids:
                raise DiagnosticError(_finding("RUNTIME_APPROVAL_REQUIRED", f"effect {task['effect_type']} requires a matching approval", file=self.state_path, remediation="Record and validate an explicit approval before starting the effect."))
            effect = {
                "effect_key": effect_key,
                "task_id": task_id,
                "attempt": int(task["attempt"]),
                "effect_type": task["effect_type"],
                "target_ref": target_ref,
                "target_sha256": target_sha256,
                "status": "STARTED",
                "started_at": occurred_at,
                "completed_at": None,
                "evidence_refs": [],
                "approval_ids": approval_ids,
            }
            payload = {"effect": effect}
            event = self._make_event(state["revision"] + 1, state["last_event_hash"], occurred_at, "AGENT", actor_id, idempotency_key, payload, "EFFECT_STARTED")
            if self._check_event_idempotency(events, event):
                return state
            return self._commit_event(events, event)

    def complete_effect(self, *, effect_key: str, task_id: str, occurred_at: str, actor_id: str, lease_token: str, status: str, evidence_refs: list[dict[str, Any]], idempotency_key: str, error_class: str | None = None) -> dict[str, Any]:
        if status not in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
            raise DiagnosticError(_finding("RUNTIME_EFFECT_STATUS", "effect completion status must be SUCCEEDED, FAILED, or UNKNOWN", file=self.state_path, remediation="Record the observed effect result explicitly."))
        if status == "SUCCEEDED" and not evidence_refs:
            raise DiagnosticError(_finding("RUNTIME_EFFECT_EVIDENCE", "successful effect completion requires evidence_refs", file=self.state_path, remediation="Attach an external or synthetic receipt/evidence reference."))
        if status == "UNKNOWN" and error_class != "UNKNOWN_EXTERNAL":
            raise DiagnosticError(_finding("RUNTIME_EFFECT_UNKNOWN", "UNKNOWN effect completion requires UNKNOWN_EXTERNAL classification", file=self.state_path, remediation="Stop automatic retry and reconcile the external outcome."))
        with self._writer_lock():
            events, state = self._checked_runtime()
            self._require_lease(state, task_id, actor_id, lease_token, occurred_at)
            effect = state.get("effects", {}).get(effect_key)
            if not isinstance(effect, dict) or effect.get("task_id") != task_id or effect.get("status") != "STARTED":
                raise DiagnosticError(_finding("RUNTIME_EFFECT_STATE", "effect is missing, belongs to another task, or is already completed", file=self.state_path, remediation="Complete the exact STARTED effect once."))
            if status == "SUCCEEDED":
                resolve_evidence_refs(self.project_root, self.repository, evidence_refs, expected_targets={effect_key, task_id}, file=self.log_path)
            payload = {"effect_key": effect_key, "status": status, "completed_at": occurred_at, "evidence_refs": list(evidence_refs)}
            if error_class:
                payload["error_class"] = error_class
            event = self._make_event(state["revision"] + 1, state["last_event_hash"], occurred_at, "AGENT", actor_id, idempotency_key, payload, "EFFECT_COMPLETED")
            if self._check_event_idempotency(events, event):
                return state
            return self._commit_event(events, event)

    def retry_task(self, *, task_id: str, occurred_at: str, actor_id: str, lease_token: str, retry_after: str, reason: str, idempotency_key: str) -> dict[str, Any]:
        if _timestamp(retry_after) < _timestamp(occurred_at):
            raise DiagnosticError(_finding("RUNTIME_RETRY_WINDOW", "retry_after cannot be before occurred_at", file=self.state_path, remediation="Schedule retry at or after the failure timestamp."))
        with self._writer_lock():
            events, state = self._checked_runtime()
            task = self._require_lease(state, task_id, actor_id, lease_token, occurred_at)
            if int(task["attempt"]) >= int(task["max_attempts"]):
                raise DiagnosticError(_finding("RUNTIME_RETRY_LIMIT", f"task {task_id} has reached its retry limit", file=self.state_path, remediation="Stop retrying and record a permanent failure or plan revision."))
            unknown = [effect for effect in state.get("effects", {}).values() if effect.get("task_id") == task_id and effect.get("status") in {"STARTED", "UNKNOWN"}]
            if unknown:
                raise DiagnosticError(_finding("RUNTIME_EXTERNAL_OUTCOME_UNKNOWN", f"task {task_id} has an unresolved external effect", file=self.state_path, remediation="Reconcile the effect outcome before retrying."))
            payload = {"task_id": task_id, "lease_token": lease_token, "retry_after": retry_after, "reason": reason, "classification": "TRANSIENT"}
            event = self._make_event(state["revision"] + 1, state["last_event_hash"], occurred_at, "AGENT", actor_id, idempotency_key, payload, "TASK_RETRY_SCHEDULED")
            if self._check_event_idempotency(events, event):
                return state
            return self._commit_event(events, event)

    def fail_task(self, *, task_id: str, occurred_at: str, actor_id: str, lease_token: str, reason: str, idempotency_key: str) -> dict[str, Any]:
        with self._writer_lock():
            events, state = self._checked_runtime()
            self._require_lease(state, task_id, actor_id, lease_token, occurred_at)
            unknown = [effect for effect in state.get("effects", {}).values() if effect.get("task_id") == task_id and effect.get("status") in {"STARTED", "UNKNOWN"}]
            if unknown:
                raise DiagnosticError(_finding("RUNTIME_EXTERNAL_OUTCOME_UNKNOWN", f"task {task_id} has an unresolved external effect", file=self.state_path, remediation="Reconcile the effect outcome before recording task failure."))
            payload = {"task_id": task_id, "lease_token": lease_token, "reason": reason}
            event = self._make_event(state["revision"] + 1, state["last_event_hash"], occurred_at, "AGENT", actor_id, idempotency_key, payload, "TASK_FAILED")
            if self._check_event_idempotency(events, event):
                return state
            return self._commit_event(events, event)

    def complete_task(self, *, task_id: str, occurred_at: str, actor_id: str, lease_token: str, evidence_refs: list[dict[str, Any]], idempotency_key: str) -> dict[str, Any]:
        if not evidence_refs:
            raise DiagnosticError(_finding("RUNTIME_TASK_EVIDENCE", "task completion requires evidence_refs", file=self.state_path, remediation="Record the acceptance evidence before marking the task DONE."))
        with self._writer_lock():
            events, state = self._checked_runtime()
            self._require_lease(state, task_id, actor_id, lease_token, occurred_at)
            resolve_evidence_refs(self.project_root, self.repository, evidence_refs, expected_targets={task_id}, file=self.log_path)
            task_effects = [effect for effect in state.get("effects", {}).values() if effect.get("task_id") == task_id]
            if any(effect.get("status") in {"STARTED", "UNKNOWN"} for effect in task_effects):
                raise DiagnosticError(_finding("RUNTIME_EFFECT_UNRESOLVED", f"task {task_id} has an unresolved effect", file=self.state_path, remediation="Complete or reconcile every effect before completing the task."))
            if any(effect.get("status") == "FAILED" for effect in task_effects):
                raise DiagnosticError(_finding("RUNTIME_EFFECT_FAILED", f"task {task_id} has a failed effect", file=self.state_path, remediation="Retry within the configured limit or record a permanent task failure."))
            payload = {"task_id": task_id, "lease_token": lease_token, "evidence_refs": list(evidence_refs)}
            event = self._make_event(state["revision"] + 1, state["last_event_hash"], occurred_at, "AGENT", actor_id, idempotency_key, payload, "TASK_COMPLETED")
            if self._check_event_idempotency(events, event):
                return state
            return self._commit_event(events, event)

    def transition(self, *, to_state: str, occurred_at: str, actor_kind: str, actor_id: str, idempotency_key: str, reason: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not idempotency_key:
            raise DiagnosticError(_finding("RUNTIME_IDEMPOTENCY_KEY", "idempotency_key is required", file=self.log_path, remediation="Provide a stable idempotency key for the requested transition."))
        payload = dict(payload or {})
        if any(key in payload for key in ("from_state", "to_state", "reason")):
            raise DiagnosticError(_finding("RUNTIME_PAYLOAD_RESERVED", "from_state, to_state, and reason are runtime-owned fields", file=self.log_path, remediation="Pass transition metadata through the runtime arguments."))
        with self._writer_lock():
            events = self._read_events()
            state = self._replay_events(events)
            actual = self._read_state()
            if actual != state:
                raise DiagnosticError(_finding("RUNTIME_STATE_DIVERGENCE", "materialized state does not match replayed event log", file=self.state_path, remediation="Do not edit production-state.json; replay the append-only log or restore the matching projection."))
            for event in events:
                if event["idempotency_key"] == idempotency_key:
                    existing_payload = event.get("payload") or {}
                    requested = {"from_state": existing_payload.get("from_state"), "to_state": to_state, "reason": reason, **payload}
                    existing_identity = _event_identity(event)
                    requested_identity = {"type": "PROJECT_STATE_TRANSITIONED", "actor": {"kind": actor_kind, "id": actor_id}, "idempotency_key": idempotency_key, "payload": requested}
                    if existing_identity == requested_identity:
                        return state
                    raise DiagnosticError(_finding("RUNTIME_IDEMPOTENCY_MISMATCH", "idempotency key was already used with different transition content", file=self.log_path, remediation="Use the original transition content or a new idempotency key."))
            from_state = state["state"]
            transition_payload = {"from_state": from_state, "to_state": to_state, "reason": reason, **payload}
            actor = {"kind": actor_kind, "id": actor_id}
            self._check_transition_guard(from_state, to_state, transition_payload, actor)
            event = self._make_event(state["revision"] + 1, state["last_event_hash"], occurred_at, actor_kind, actor_id, idempotency_key, transition_payload)
            self._append_event(event)
            projection = self._replay_events([*events, event])
            self._write_state(projection)
            self._sync_manifest(projection)
            return projection


def validate_runtime_project(project_root: Path, repository: Path) -> list[Finding]:
    """Validate an initialized runtime; leave pre-runtime projects to RUNTIME bootstrap."""

    log_path = project_root / "08_runtime/run-log.jsonl"
    state_path = project_root / "08_runtime/production-state.json"
    if not state_path.is_file() or not log_path.is_file() or log_path.stat().st_size == 0:
        return []
    try:
        Runtime(project_root, repository).replay()
        return []
    except DiagnosticError as exc:
        return [exc.finding]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        return [_finding("RUNTIME_VALIDATION", str(exc), file=project_root / "08_runtime", remediation="Restore the append-only log and materialized projection, then replay the runtime.")]
