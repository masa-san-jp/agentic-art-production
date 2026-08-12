"""Replayable production lifecycle runtime with an append-only event log."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import fcntl

from .canonical import canonical_sha256, event_sha256
from .diagnostics import DiagnosticError, Finding
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
        findings = validate_instance(state, self._state_schema(), schema_path=self.repository / "schemas" / RUNTIME_STATE_SCHEMA, common_schema=self._common_schema())
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
            if not isinstance(evidence, list) or not evidence or not all(isinstance(item, str) and item for item in evidence):
                raise DiagnosticError(_finding("RUNTIME_RESUME_EVIDENCE", "BLOCKED resume requires non-empty resolution_evidence", file=self.state_path, location="/payload/resolution_evidence", remediation="Record evidence that every blocking condition was resolved."))
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
        if to_state == "PLANNING" and from_state == "REVIEWING":
            if payload.get("review_outcome") not in {"FAIL", "DEVIATION", "CHANGE_REQUEST"}:
                raise DiagnosticError(_finding("RUNTIME_REVIEW_OUTCOME", "REVIEWING -> PLANNING requires a FAIL, DEVIATION, or CHANGE_REQUEST outcome", file=self.state_path, location="/payload/review_outcome", remediation="Record the review outcome and related change request."))
        if to_state in {"COMPLETE", "COMPLETE_WITH_GAPS"}:
            evidence = payload.get("completion_evidence")
            if not isinstance(evidence, list) or not evidence:
                raise DiagnosticError(_finding("RUNTIME_COMPLETION_GUARD", "terminal completion requires completion_evidence", file=self.state_path, location="/payload/completion_evidence", remediation="Record substantiating evidence before completion."))
            if to_state == "COMPLETE_WITH_GAPS" and not payload.get("open_gap_ids"):
                raise DiagnosticError(_finding("RUNTIME_GAP_GUARD", "COMPLETE_WITH_GAPS requires open_gap_ids", file=self.state_path, location="/payload/open_gap_ids", remediation="Record each non-blocking gap and its resume condition."))
        if from_state in {"COMPLETE", "COMPLETE_WITH_GAPS"} and to_state == "PLANNING":
            if actor.get("kind") not in {"HUMAN", "SYSTEM"} or not payload.get("reopen_reason") or not payload.get("change_request_id"):
                raise DiagnosticError(_finding("RUNTIME_REOPEN_GUARD", "reopen requires authority, reopen_reason, and change_request_id", file=self.state_path, remediation="Create an authorized change request before reopening a terminal project."))

    def _apply_event(self, state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        if event.get("type") != "PROJECT_STATE_TRANSITIONED":
            raise DiagnosticError(_finding("RUNTIME_EVENT_TYPE", f"unsupported runtime event type {event.get('type')!r}", file=self.log_path, remediation="Use the lifecycle event type supported by RUNTIME-001."))
        payload = event.get("payload") or {}
        from_state = payload.get("from_state")
        to_state = payload.get("to_state")
        actor = event.get("actor") or {}
        if state["state"] != from_state:
            raise DiagnosticError(_finding("RUNTIME_STATE_CHAIN", f"event expects {from_state!r} but replay state is {state['state']!r}", file=self.log_path, location=f"/events/{event.get('sequence')}/payload/from_state", remediation="Restore the missing or reordered event before replaying."))
        self._check_transition_guard(from_state, to_state, payload, actor)
        next_state = dict(state)
        next_state["state"] = to_state
        next_state["revision"] = event["sequence"]
        next_state["last_event_id"] = event["event_id"]
        next_state["last_event_hash"] = event["event_sha256"]
        if isinstance(payload.get("plan_id"), str):
            next_state["plan_id"] = payload["plan_id"]
        return next_state

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

    def _make_event(self, sequence: int, previous_hash: str | None, occurred_at: str, actor_kind: str, actor_id: str, idempotency_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = {
            "event_id": f"EVT{sequence:06d}",
            "sequence": sequence,
            "occurred_at": occurred_at,
            "type": "PROJECT_STATE_TRANSITIONED",
            "actor": {"kind": actor_kind, "id": actor_id},
            "idempotency_key": idempotency_key,
            "previous_event_sha256": previous_hash,
            "payload": payload,
        }
        event["event_sha256"] = event_sha256(event)
        return event

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
